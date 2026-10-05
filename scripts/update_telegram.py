#!/usr/bin/env python3
"""Refresh the "Latest from Telegram" block in README.md.

Reads the public web preview of a Telegram channel (https://t.me/s/<channel>),
takes the newest posts, and rewrites the lines between
<!-- TELEGRAM:START --> and <!-- TELEGRAM:END -->.

Standard library only. If Telegram is unreachable or returns nothing usable,
the README is left untouched and the script exits 0 with a warning.

Environment:
  TG_CHANNEL   channel username            (default: startupbaseuz)
  TG_LIMIT     number of posts to show      (default: 5)
  TG_LANG      "en": when a post (or its twin post) has an English headline, show it;
               "any": always show the post's first headline
  README_PATH  file to update               (default: README.md)
  TG_HTML_FILE read this file instead of fetching (for local tests)
"""
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

CHANNEL = os.environ.get("TG_CHANNEL", "startupbaseuz")
LIMIT = int(os.environ.get("TG_LIMIT", "5"))
PREFER = os.environ.get("TG_LANG", "en").lower()
README = os.environ.get("README_PATH", "README.md")
START, END = "<!-- TELEGRAM:START -->", "<!-- TELEGRAM:END -->"
TASHKENT = timezone(timedelta(hours=5))
MAX_TITLE = 120


def warn(msg):
    print(f"::warning::{msg}")


def fetch(url, tries=3):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # network hiccups, 5xx, 429
            if i == tries - 1:
                raise
            print(f"fetch failed ({e}), retrying")
            time.sleep(5 * (i + 1))


class ChannelParser(HTMLParser):
    """Collects posts from the t.me/s/ preview markup."""

    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts = []
        self.msg = None
        self.depth = 0
        self.text_depth = None
        self.inline = []  # stack of (tag, kind) inside the text block

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.msg is None:
            if tag == "div" and "data-post" in a and re.search(r"\btgme_widget_message\b", cls):
                self.msg = {"post": a["data-post"], "segments": None, "dt": None, "hrefs": [],
                            "service": "service_message" in cls}
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
            if "tgme_widget_message_text" in cls and "js-message_text" in cls:
                # the last text block of a post is its own text (earlier ones may be quotes)
                self.msg["segments"] = []
                self.msg["hrefs"] = []
                self.text_depth = self.depth
                self.inline = []
            return
        if tag == "time" and self.msg["dt"] is None and a.get("datetime"):
            self.msg["dt"] = a["datetime"]
        if self.text_depth is None:
            return
        if tag == "br":
            self.msg["segments"].append(("\n", False, False))
            return
        if tag in self.VOID:
            return
        if tag == "i" and "emoji" in cls:
            kind = "emoji"
        elif tag in ("b", "strong"):
            kind = "bold"
        elif tag == "a":
            kind = "link"
            if a.get("href"):
                self.msg["hrefs"].append(a["href"])
        else:
            kind = "other"
        self.inline.append((tag, kind))

    def handle_endtag(self, tag):
        if self.msg is None:
            return
        if tag == "div":
            if self.text_depth is not None and self.depth == self.text_depth:
                self.text_depth = None
                self.inline = []
            self.depth -= 1
            if self.depth == 0:
                self.posts.append(self.msg)
                self.msg = None
            return
        if self.text_depth is not None:
            for i in range(len(self.inline) - 1, -1, -1):
                if self.inline[i][0] == tag:
                    del self.inline[i:]
                    break

    def handle_data(self, data):
        if self.msg is None or self.text_depth is None:
            return
        kinds = [k for _, k in self.inline]
        bold = "bold" in kinds and "emoji" not in kinds
        self.msg["segments"].append((data, bold, "link" in kinds))


LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
WORD = re.compile(r"[^\W_][\w\u02bb\u2018\u2019'’-]*", re.UNICODE)
HASHTAG = re.compile(r"#[\w\u02bb\u2018\u2019'’]+", re.UNICODE)
UZ_APOSTROPHE = re.compile(r"[oOgG][\u02bb\u2018\u2019'`’]")
UZ_WORDS = set("""va bilan uchun kuni yil yili tomonidan haqida mavjud batafsil yangi bugun eslatma
uchrashuv natijalari ishtirok tadbir tanlov ariza qabul bormi qanday nega nima nimalar""".split())
UZ_SUFFIXES = ("lar", "lari", "larni", "ning", "dagi", "moqda", "ilgan", "lgan", "ini", "idan",
               "likni", "ligi", "lardan", "larga", "mizda", "ingiz")
EN_WORDS = set("""the and for with of to in on from your is are will a an at by how what who why new
meet our this that into across about join its their be has have wins now""".split())


def words(text):
    return WORD.findall(text)


def is_english(text):
    """Positive evidence only: English function words and no Uzbek markers."""
    letters = LETTER.findall(text)
    if not letters or sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff") / len(letters) > 0.3:
        return False
    ws = [w.lower() for w in words(text)]
    uz = 2 * len(UZ_APOSTROPHE.findall(text)) + sum(w in UZ_WORDS for w in ws)
    uz += sum(1 for w in ws if len(w) > 4 and w.endswith(UZ_SUFFIXES))
    en = sum(w in EN_WORDS for w in ws) + sum(1 for w in words(text) if w.islower() and "w" in w)
    return en >= 1 and uz == 0


def lines_of(segments):
    lines, cur = [], []
    for text, bold, link in segments:
        parts = text.split("\n")
        for i, p in enumerate(parts):
            if i:
                lines.append(cur)
                cur = []
            if p:
                cur.append((p, bold, link))
    lines.append(cur)
    return lines


def clean(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" :—-–|")


def strip_hashtags(text):
    """'📰 #News: Headline' -> '📰 Headline'; rubric-only lines become empty."""
    t = HASHTAG.sub("", text)
    lead = re.match(r"^[^\w]*", t).group(0)
    rest = t[len(lead):]
    lead = re.sub(r"[:|–—-]", "", lead)
    return clean(lead + rest)


def headlines(segments):
    """Bold lines, the way this channel writes headlines; skips link rows
    ('LinkedIn | Facebook | ...', 'Read more...') and rubric hashtags."""
    out = []
    for line in lines_of(segments):
        text = "".join(t for t, _, _ in line)
        total = len(LETTER.findall(text))
        if not total:
            continue
        linked = len(LETTER.findall("".join(t for t, _, l in line if l)))
        bold = len(LETTER.findall("".join(t for t, b, _ in line if b)))
        if linked / total >= 0.5 or bold / total < 0.6:
            continue
        t = strip_hashtags(text)
        if len(words(t)) >= 3 and len(LETTER.findall(t)) >= 15:
            out.append(t)
    return out


def page_key(hrefs):
    """Same startupbase.uz page in two languages -> the posts are twins."""
    for h in hrefs:
        m = re.match(r"https?://(?:www\.)?startupbase\.uz/([^?#]*)", h)
        if not m:
            continue
        parts = [x for x in m.group(1).split("/") if x]
        if parts and parts[0] in ("uz", "en", "ru", "oz"):
            parts = parts[1:]
        if len(parts) >= 2:
            return "/".join(parts)
    return None


def shorten(s, n=MAX_TITLE):
    if len(s) <= n:
        return s
    cut = s[: n - 1].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return cut + "…"


def md_escape(s):
    s = s.replace("\\", "\\\\")
    for ch in "[]*_`":
        s = s.replace(ch, "\\" + ch)
    return s.replace("<", "&lt;").replace(">", "&gt;")


def pick(posts):
    items, by_key, seen = [], {}, set()
    for p in sorted(posts, key=lambda p: int(p["post"].rsplit("/", 1)[-1]), reverse=True):
        if p["service"] or not p["segments"] or not p["dt"]:
            continue
        heads = headlines(p["segments"])
        if not heads:
            continue  # no headline (digests of links, dictionary cards, media-only)
        title = heads[0]
        if PREFER == "en":
            title = next((h for h in heads if is_english(h)), title)
        item = {"post": p["post"], "dt": p["dt"], "title": title, "en": is_english(title)}
        key = page_key(p.get("hrefs", []))
        if key and key in by_key:
            twin = by_key[key]
            if PREFER == "en" and item["en"] and not twin["en"]:
                twin.update(title=item["title"], post=item["post"], en=True)
            continue
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        if key:
            by_key[key] = item
        items.append(item)
    return items[:LIMIT]


def render(items):
    rows = []
    for it in items:
        when = datetime.fromisoformat(it["dt"]).astimezone(TASHKENT).strftime("%d %b")
        url = "https://t.me/" + it["post"]
        rows.append(f"- `{when}` [{md_escape(shorten(it['title']))}]({url})")
    return "\n".join(rows)


def main():
    src = os.environ.get("TG_HTML_FILE")
    try:
        page = open(src, encoding="utf-8").read() if src else fetch(f"https://t.me/s/{CHANNEL}")
    except Exception as e:
        warn(f"Could not load t.me/s/{CHANNEL}: {e}. README left as is.")
        return 0
    parser = ChannelParser()
    parser.feed(page)
    items = pick(parser.posts)
    if not items:
        warn(f"No usable posts found on t.me/s/{CHANNEL} ({len(parser.posts)} parsed). README left as is.")
        return 0

    readme = open(README, encoding="utf-8").read()
    if START not in readme or END not in readme:
        print(f"::error::Markers {START} / {END} not found in {README}")
        return 1
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = f"{head}{START}\n{render(items)}\n{END}{tail}"
    if updated != readme:
        open(README, "w", encoding="utf-8").write(updated)
        print(f"Feed updated with {len(items)} posts.")
    else:
        print("Feed unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
