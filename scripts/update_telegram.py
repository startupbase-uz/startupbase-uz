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
  TG_LANG      "en" to prefer English titles, "any" to take posts as they come
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
                self.msg = {"post": a["data-post"], "segments": None, "dt": None,
                            "service": "service_message" in cls}
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
            if "tgme_widget_message_text" in cls and "js-message_text" in cls:
                # the last text block of a post is its own text (earlier ones may be quotes)
                self.msg["segments"] = []
                self.text_depth = self.depth
                self.inline = []
            return
        if tag == "time" and self.msg["dt"] is None and a.get("datetime"):
            self.msg["dt"] = a["datetime"]
        if self.text_depth is None:
            return
        if tag == "br":
            self.msg["segments"].append(("\n", False))
            return
        if tag in self.VOID:
            return
        if tag == "i" and "emoji" in cls:
            kind = "emoji"
        elif tag in ("b", "strong"):
            kind = "bold"
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
        self.msg["segments"].append((data, bold))


LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
UZ_APOSTROPHE = re.compile(r"[oOgG][\u02bb\u2018\u2019'`]")
UZ_WORDS = set("""va bilan uchun kuni yil yili tomonidan startap startaplar startapi startaplarni dasturi
dasturida dasturlari uchrashuv uchrashuvi natijalari haqida mavjud batafsil saytimizda loyiha loyihalar
asoschisi asoschilari sessiyasi sessiyalari tashkil etildi etiladi imkoniyat imkoniyatlari mintaqaviy
bosqichida hamkorlik hamkorlikni davom tadbir tadbiri tanlov tanlovi ariza arizalar qabul yangi
ishtirok ishtirokchilari bo'yicha bo‘yicha boʻyicha o'tkazildi o‘tkazildi oʻtkazildi""".split())
EN_WORDS = set("""the and for with of to in on from your is are will a an at by how new meet our this
that into across about join who what why its their be has have""".split())


def language(text):
    letters = LETTER.findall(text)
    if not letters:
        return "unknown"
    cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff")
    if cyr / len(letters) > 0.3:
        return "cyrillic"
    words = re.findall(r"[a-z\u02bb\u2018\u2019']+", text.lower())
    uz = sum(w in UZ_WORDS for w in words) + 2 * len(UZ_APOSTROPHE.findall(text))
    en = sum(w in EN_WORDS for w in words)
    return "uz" if uz > en else "en"


def lines_of(segments):
    lines, cur = [], []
    for text, bold in segments:
        parts = text.split("\n")
        for i, p in enumerate(parts):
            if i:
                lines.append(cur)
                cur = []
            if p:
                cur.append((p, bold))
    lines.append(cur)
    return lines


def clean(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" :—-–|")


def title_candidates(segments):
    """Lines that are (mostly) bold: how this channel writes its headlines."""
    out = []
    for line in lines_of(segments):
        text = clean("".join(t for t, _ in line))
        total = len(LETTER.findall(text))
        boldl = len(LETTER.findall("".join(t for t, b in line if b)))
        if total >= 12 and boldl / total >= 0.6:
            out.append(text)
    return out


def first_line(segments):
    for line in lines_of(segments):
        text = clean("".join(t for t, _ in line))
        if len(LETTER.findall(text)) >= 12:
            return text
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
    items, seen = [], set()
    for p in sorted(posts, key=lambda p: int(p["post"].rsplit("/", 1)[-1]), reverse=True):
        if p["service"] or not p["segments"] or not p["dt"]:
            continue
        cands = title_candidates(p["segments"])
        en = [c for c in cands if language(c) == "en"]
        fallback = first_line(p["segments"])
        title_en = en[0] if en else (fallback if fallback and language(fallback) == "en" else None)
        title_any = cands[0] if cands else fallback
        if not title_any:
            continue
        key = (title_en or title_any).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({"post": p["post"], "dt": p["dt"], "en": title_en, "any": title_any})
    if PREFER == "en":
        english = [dict(i, title=i["en"]) for i in items if i["en"]]
        if len(english) >= min(LIMIT, 3):
            return english[:LIMIT]
    return [dict(i, title=i["en"] or i["any"]) for i in items][:LIMIT]


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
