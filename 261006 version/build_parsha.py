#!/usr/bin/env python3
"""
build_parsha.py — generate the parsha/targumim reading page, fully standalone.

Single data provider, start to finish: Sefaria.
  - WHICH parsha, which book, and the 7 aliyah boundaries come from Sefaria's
    own Calendars API (https://www.sefaria.org/api/calendars) — the same data
    that powers the "this week's parasha" quick link on sefaria.org's
    homepage. Documented at developers.sefaria.org, with the exact response
    shape confirmed from their own tutorial.
  - The actual texts (Hebrew, Onkelos, Pseudo-Jonathan, Targum Yerushalmi
    fragments, Rashi, English) come from Sefaria's bulk export. Since
    September 2026 the GitHub repo (Sefaria/Sefaria-Export) holds only an
    index (books.json); the texts themselves live in a public Google Cloud
    Storage bucket (https://storage.googleapis.com/sefaria-export/...).
    Each needed file is downloaded once and kept under ./sefaria_texts/.

Both are free, public, and need no account or API key.

There is no Hebrew-paraphrase-of-the-Aramaic feature in this version: Sefaria
doesn't have that data, so it means someone writing it by hand, verse by
verse, which this script can't do for you. Onkelos/Pseudo-Jonathan/Targum
Yerushalmi (Aramaic), Rashi (Hebrew), and English (Sefaria's own
translations, where they have them) are all included.

Usage:
    python3 build_parsha.py --list                 # show upcoming parshiot + dates
    python3 build_parsha.py --next                 # build this/next Shabbat's parsha
    python3 build_parsha.py --date 2026-10-10       # build the parsha read that week

    python3 build_parsha.py --year                 # build a year of pages + index

Requires: Python 3 (standard library only — no pip installs, no git).
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CALENDARS_URL = "https://www.sefaria.org/api/calendars?year={y}&month={m}&day={d}"
CALENDAR_CACHE_DIR = ROOT / "calendar_cache"

# Sefaria's bulk export: texts in a public GCS bucket, index on GitHub.
EXPORT_BASE_URL = "https://storage.googleapis.com/sefaria-export/"
BOOKS_JSON_URL = "https://raw.githubusercontent.com/Sefaria/Sefaria-Export/master/books.json"
TEXT_CACHE_DIR = ROOT / "sefaria_texts"
BOOKS_JSON_MAX_AGE_DAYS = 30

USER_AGENT = "Mozilla/5.0 (compatible; parsha-reader-script/1.1; personal use)"
POLITE_DELAY = 1.5  # seconds between calls to Sefaria's calendar API

TORAH_BOOKS = ("Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy")

ALIYAH_RE = re.compile(r"^(.+?)\s+(\d+):(\d+)-(\d+):(\d+)$")

# Sefaria's calendar returns holiday readings (e.g. "Sukkot I",
# "Pesach Shabbat Chol haMoed", "Shavuot II") in the same "Parashat Hashavua"
# slot as ordinary parshiot. Only names on this list are treated as weekly
# parshiot; anything else is reported and skipped. Spellings are Sefaria's.
REGULAR_PARSHIOT = {
    "Bereshit", "Noach", "Lech-Lecha", "Vayera", "Chayei Sara", "Toldot",
    "Vayetzei", "Vayishlach", "Vayeshev", "Miketz", "Vayigash", "Vayechi",
    "Shemot", "Vaera", "Bo", "Beshalach", "Yitro", "Mishpatim", "Terumah",
    "Tetzaveh", "Ki Tisa", "Vayakhel", "Pekudei",
    "Vayikra", "Tzav", "Shmini", "Tazria", "Metzora", "Achrei Mot",
    "Kedoshim", "Emor", "Behar", "Bechukotai",
    "Bamidbar", "Nasso", "Beha'alotcha", "Sh'lach", "Korach", "Chukat",
    "Balak", "Pinchas", "Matot", "Masei",
    "Devarim", "Vaetchanan", "Eikev", "Re'eh", "Shoftim", "Ki Teitzei",
    "Ki Tavo", "Nitzavim", "Vayeilech", "Ha'Azinu", "V'Zot HaBerachah",
    # combined weeks
    "Vayakhel-Pekudei", "Tazria-Metzora", "Achrei Mot-Kedoshim",
    "Behar-Bechukotai", "Chukat-Balak", "Matot-Masei", "Nitzavim-Vayeilech",
}


def _norm_name(name):
    return re.sub(r"[^a-z]", "", name.lower())


_REGULAR_NORM = {_norm_name(n) for n in REGULAR_PARSHIOT}


def is_regular_parsha(name):
    return name in REGULAR_PARSHIOT or _norm_name(name) in _REGULAR_NORM


def hebrew_numeral(n):
    """1 -> א׳, 11 -> י״א, 15 -> ט״ו, 50 -> נ׳ (chapters go up to 50)."""
    ones, tens, hundreds = "אבגדהוזחט", "יכלמנסעפצ", "קרשת"
    s = ""
    while n >= 400:
        s += "ת"
        n -= 400
    if n >= 100:
        s += hundreds[n // 100 - 1]
        n %= 100
    if n in (15, 16):
        s += "ט" + ("ו" if n == 15 else "ז")
        n = 0
    if n >= 10:
        s += tens[n // 10 - 1]
        n %= 10
    if n:
        s += ones[n - 1]
    return s + "׳" if len(s) == 1 else s[:-1] + "״" + s[-1]


HEB_CH_LETTERS = {i: hebrew_numeral(i) for i in range(1, 151)}


# ---------------------------------------------------------------------------
# Step 1: ask Sefaria's Calendars API which parsha, book, and aliyot apply.
# ---------------------------------------------------------------------------

class NotFound(Exception):
    pass


def http_get(url, what, retries=8, max_delay=30.0, timeout=60):
    """GET with retries for rate limits (429), server errors (5xx) and
    dropped connections. A 404 raises NotFound immediately."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from None
            transient = e.code == 429 or e.code >= 500
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            transient = True
            reason = str(getattr(e, "reason", e))
        if not transient or attempt == retries - 1:
            sys.exit(f"Could not download {what}: {reason}\n  URL: {url}")
        print(f"    ({what}: {reason} [attempt {attempt+1}/{retries}], waiting {delay:.0f}s...)")
        time.sleep(delay)
        delay = min(delay * 2, max_delay)


_last_calendar_call = 0.0


def fetch_calendar(date):
    """Fetch (and disk-cache) Sefaria's calendar for one date. Cached results
    are reused forever — a Shabbat's parsha never changes — so re-running
    after a partial failure only hits the network for what's still missing."""
    global _last_calendar_call
    CALENDAR_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CALENDAR_CACHE_DIR / f"{date.isoformat()}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    wait = POLITE_DELAY - (time.monotonic() - _last_calendar_call)
    if wait > 0:
        time.sleep(wait)  # only network calls are throttled, not cache hits
    url = CALENDARS_URL.format(y=date.year, m=date.month, d=date.day)
    raw = http_get(url, f"Sefaria calendar for {date.isoformat()}")
    _last_calendar_call = time.monotonic()
    data = json.loads(raw.decode("utf-8"))
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_aliyah_ref(ref):
    m = ALIYAH_RE.match(ref)
    if not m:
        return None
    book, sc, sv, ec, ev = m.groups()
    return book, int(sc), int(sv), int(ec), int(ev)


def calendar_to_parsha(calendar_json, date):
    """Pull the 'Parashat Hashavua' entry out of a /api/calendars response.
    Returns (parsha_dict, None) or (None, reason_it_was_skipped)."""
    for item in calendar_json.get("calendar_items", []):
        if item.get("title", {}).get("en") != "Parashat Hashavua":
            continue
        name = item["displayValue"]["en"]
        if not is_regular_parsha(name):
            return None, f"{name} — a holiday reading, not a weekly parsha"
        raw_aliyot = item.get("extraDetails", {}).get("aliyot", [])
        if len(raw_aliyot) < 7:
            return None, f"{name} — Sefaria gave {len(raw_aliyot)} aliyot, expected 7"
        parsed = [parse_aliyah_ref(a) for a in raw_aliyot[:7]]  # 8th = maftir
        if any(p is None for p in parsed):
            return None, f"{name} — couldn't parse aliyot {raw_aliyot[:7]}"
        books = {p[0] for p in parsed}
        if len(books) > 1:
            return None, f"{name} — aliyot span several books ({', '.join(sorted(books))})"
        book = books.pop()
        if book not in TORAH_BOOKS:
            return None, f"{name} — unexpected book {book!r}"
        aliyot = [[p[1], p[2], p[3], p[4]] for p in parsed]
        return {
            "slug": slugify(name),
            "title": "פרשת " + item["displayValue"].get("he", name),
            "title_en": name,
            "date": calendar_json.get("date") or date.isoformat(),
            "book": book,
            "start": aliyot[0][:2],
            "end": aliyot[-1][2:],
            "aliyot": aliyot,
        }, None
    return None, "no 'Parashat Hashavua' entry in Sefaria's calendar"


def upcoming_saturday(from_date=None):
    d = from_date or datetime.date.today()
    days_ahead = (5 - d.weekday()) % 7  # Monday=0 ... Saturday=5
    return d + datetime.timedelta(days=days_ahead)


def find_parsha_for_date(date_str):
    # Any day of the week maps to that week's Shabbat, so the cache key and
    # the page's date are always the Shabbat itself.
    d = upcoming_saturday(datetime.date.fromisoformat(date_str))
    parsha, why = calendar_to_parsha(fetch_calendar(d), d)
    if not parsha:
        sys.exit(f"No weekly parsha built for Shabbat {d.isoformat()}: {why}.")
    return parsha


def list_upcoming(weeks=12):
    start = upcoming_saturday()
    print(f"Upcoming Torah readings (next {weeks} weeks):\n")
    for i in range(weeks):
        d = start + datetime.timedelta(weeks=i)
        parsha, why = calendar_to_parsha(fetch_calendar(d), d)
        if parsha:
            print(f"  {d.isoformat()}  {parsha['title_en']:25s} ({parsha['title']})")
        else:
            print(f"  {d.isoformat()}  (skipped: {why})")


# ---------------------------------------------------------------------------
# Step 2: pull the actual texts from Sefaria's bulk export, given a book name.
# ---------------------------------------------------------------------------

def book_titles(book):
    """Sefaria's titles for the texts used with one Torah book."""
    return {
        "hebrew": book,
        "onkelos": f"Onkelos {book}",
        "pj": f"Targum Jonathan on {book}",
        "yerushalmi": "Targum Jerusalem",   # one text covering all five books
        "rashi": f"Rashi on {book}",
    }


def known_export_path(title, language):
    """Bucket path for each title, as listed in books.json on 2026-10-02.
    If Sefaria moves a file again, fetch_text() falls back to books.json."""
    if title in TORAH_BOOKS:
        folder = f"Torah/{title}"
    elif title.startswith("Onkelos "):
        folder = f"Targum/Onkelos/Torah/{title}"
    elif title.startswith("Targum Jonathan on "):
        folder = f"Targum/Targum Jonathan/Torah/{title}"
    elif title == "Targum Jerusalem":
        folder = "Targum/Targum Jerusalem/Targum Jerusalem"
    elif title.startswith("Rashi on "):
        folder = f"Rishonim on Tanakh/Rashi/Torah/{title}"
    else:
        raise ValueError(title)
    return f"json/Tanakh/{folder}/{language}/merged.json"


_books_index = None


def books_json_lookup(title, language):
    """Find a text's current bucket path in Sefaria's books.json index."""
    global _books_index
    if _books_index is None:
        local = TEXT_CACHE_DIR / "books.json"
        fresh = local.exists() and (time.time() - local.stat().st_mtime) < BOOKS_JSON_MAX_AGE_DAYS * 86400
        if not fresh:
            print("  Downloading Sefaria's books.json index (~20 MB)...")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(http_get(BOOKS_JSON_URL, "books.json"))
        _books_index = json.loads(local.read_text(encoding="utf-8"))["books"]
    for b in _books_index:
        if (b.get("title") == title and b.get("language") == language
                and b.get("versionTitle") == "merged" and b.get("json_url")):
            return urllib.parse.unquote(b["json_url"].split("/sefaria-export/", 1)[1])
    return None


def fetch_text(title, language, required=False):
    """Return the 'text' field of Sefaria's merged JSON for one title/language,
    downloading it into ./sefaria_texts/ the first time it is needed."""
    rel = known_export_path(title, language)
    local = TEXT_CACHE_DIR / rel
    if not local.exists():
        print(f"  Downloading {title} ({language})...")
        try:
            raw = http_get(EXPORT_BASE_URL + urllib.parse.quote(rel), f"{title} ({language})")
        except NotFound:
            new_rel = books_json_lookup(title, language)
            raw = None
            if new_rel and new_rel != rel:
                print(f"    (moved in Sefaria's export; now at {new_rel})")
                try:
                    raw = http_get(EXPORT_BASE_URL + urllib.parse.quote(new_rel), f"{title} ({language})")
                except NotFound:
                    pass
            if raw is None:
                if required:
                    sys.exit(f"Sefaria's export has no {language} text for {title!r} "
                             f"(tried {rel} and books.json).")
                print(f"    (not available — {title} {language} will be left blank)")
                return []
        json.loads(raw.decode("utf-8"))  # don't cache a truncated or corrupt file
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".part")
        tmp.write_bytes(raw)
        os.replace(tmp, local)
    return json.loads(local.read_text(encoding="utf-8"))["text"]


def safe_get(arr, ch, v):
    try:
        val = arr[ch - 1][v - 1]
    except (IndexError, TypeError):
        return ""
    if isinstance(val, list):  # Rashi: list of comments for one verse
        return "<br><br>".join(c for c in val if c)
    return val.strip() if isinstance(val, str) else ""


def aliyah_of(ch, v, aliyot):
    for i, (sc, sv, ec, ev) in enumerate(aliyot, start=1):
        if (ch > sc or (ch == sc and v >= sv)) and (ch < ec or (ch == ec and v <= ev)):
            return i
    return None


class TextCache:
    """Avoids reloading the same book's merged.json once per parsha when
    building a whole year (several parshiot usually share a book)."""
    def __init__(self):
        self._cache = {}

    def for_book(self, book):
        if book not in self._cache:
            t = book_titles(book)
            yer = fetch_text(t["yerushalmi"], "Hebrew")
            yer_en = fetch_text(t["yerushalmi"], "English")
            self._cache[book] = {
                "heb": fetch_text(t["hebrew"], "Hebrew", required=True),
                "onk": fetch_text(t["onkelos"], "Hebrew"),
                "onk_en": fetch_text(t["onkelos"], "English"),
                "pj": fetch_text(t["pj"], "Hebrew"),
                "pj_en": fetch_text(t["pj"], "English"),
                # Targum Jerusalem is one text keyed by book name
                "tj": yer.get(book, []) if isinstance(yer, dict) else [],
                "tj_en": yer_en.get(book, []) if isinstance(yer_en, dict) else [],
                "rashi": fetch_text(t["rashi"], "Hebrew"),
                "rashi_en": fetch_text(t["rashi"], "English"),
            }
        return self._cache[book]


def build_data(parsha, cache=None):
    (sc, sv), (ec, ev) = parsha["start"], parsha["end"]
    cache = cache or TextCache()
    t = cache.for_book(parsha["book"])
    heb = t["heb"]

    data = []
    for ch in range(sc, ec + 1):
        v_start = sv if ch == sc else 1
        v_end = ev if ch == ec else (len(heb[ch - 1]) if ch - 1 < len(heb) else 0)
        for v in range(v_start, v_end + 1):
            row = {"ch": ch, "v": v, "aliyah": aliyah_of(ch, v, parsha["aliyot"])}
            for key in ("heb", "onk", "onk_en", "pj", "pj_en", "tj", "tj_en", "rashi", "rashi_en"):
                row[key] = safe_get(t[key], ch, v)
            if not row["heb"]:
                sys.exit(f"{parsha['book']} {ch}:{v} is missing from Sefaria's Hebrew text — "
                         f"aliyot {parsha['aliyot']} don't match the text.")
            data.append(row)
    return data


# ---------------------------------------------------------------------------
# Step 3: render the page.
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@400;500;700&family=David+Libre:wght@400;500;700&family=Noto+Sans+Hebrew:wght@400;500;700&family=Noto+Rashi+Hebrew:wght@400;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #FDFBF6; --ink: #1c1712; --ink-soft: #55493c; --rule: #d8cbb3; --torah: #16120d;
  --onk: #184f46; --onk-bg: #e2eeea; --onk-border: #184f46;
  --pj: #7a3317; --pj-bg: #f7e9de; --pj-border: #7a3317;
  --yer: #4e4a16; --yer-bg: #f1eed9; --yer-border: #4e4a16;
  --rashi: #3d4566; --rashi-bg: #e9eaf3; --rashi-border: #3d4566;
  --he: #2c261d; --he-bg: #efe9d8;
  --bm: #8a6d1f; --bm-bg: #f5ecd0;
  --chip-off-bg: #efe8d9; --chip-off-ink: #7a6d59;
  --sticky-bg: #FDFBF6; --fsize: 1; --font-main: 'David Libre', serif;
}
:root[data-theme="dark"] {
  --bg: #0e0d0a; --ink: #f5ecd9; --ink-soft: #c9bc9f; --rule: #433a2b; --torah: #fbf4e6;
  --onk: #9fe0d1; --onk-bg: #113330; --onk-border: #9fe0d1;
  --pj: #f3b58a; --pj-bg: #3a2416; --pj-border: #f3b58a;
  --yer: #e7df9e; --yer-bg: #332f17; --yer-border: #e7df9e;
  --rashi: #aab2e0; --rashi-bg: #22253b; --rashi-border: #aab2e0;
  --he: #efe6d4; --he-bg: #221e14;
  --bm: #e8c766; --bm-bg: #332c15;
  --chip-off-bg: #221d14; --chip-off-ink: #948563; --sticky-bg: #0e0d0a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0e0d0a; --ink: #f5ecd9; --ink-soft: #c9bc9f; --rule: #433a2b; --torah: #fbf4e6;
    --onk: #9fe0d1; --onk-bg: #113330; --onk-border: #9fe0d1;
    --pj: #f3b58a; --pj-bg: #3a2416; --pj-border: #f3b58a;
    --yer: #e7df9e; --yer-bg: #332f17; --yer-border: #e7df9e;
    --rashi: #aab2e0; --rashi-bg: #22253b; --rashi-border: #aab2e0;
    --he: #efe6d4; --he-bg: #221e14;
    --bm: #e8c766; --bm-bg: #332c15;
    --chip-off-bg: #221d14; --chip-off-ink: #948563; --sticky-bg: #0e0d0a;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { height: 100%; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--font-main); padding-bottom: env(safe-area-inset-bottom, 0px); }
header { position: fixed; top: 0; left: 0; right: 0; z-index: 10; padding: calc(12px + env(safe-area-inset-top, 0px)) 16px 0; background: var(--sticky-bg); border-bottom: 1px solid var(--rule); }
.title-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
h1 { margin: 0; font-size: 1.4rem; font-weight: 700; color: var(--torah); font-family: 'Frank Ruhl Libre', serif; }
.icon-btns { display: flex; gap: 6px; flex: none; }
.icon-btn { border: 1.5px solid var(--rule); background: none; color: var(--ink); border-radius: 50%; width: 34px; height: 34px; font-size: 1rem; cursor: pointer; }
.subtitle { margin: 2px 0 10px; font-size: 0.8rem; color: var(--ink-soft); }
.chips { display: flex; gap: 7px; flex-wrap: wrap; padding-bottom: 10px; }
.chip { border: 1.5px solid currentColor; border-radius: 999px; padding: 5px 12px; font-size: 0.8rem; font-family: 'Frank Ruhl Libre', serif; cursor: pointer; background: transparent; white-space: nowrap; font-weight: 600; }
.chip[data-on="true"].chip-onk { color: var(--onk); background: var(--onk-bg); }
.chip[data-on="true"].chip-pj  { color: var(--pj);  background: var(--pj-bg); }
.chip[data-on="true"].chip-yer { color: var(--yer); background: var(--yer-bg); }
.chip[data-on="true"].chip-rashi { color: var(--rashi); background: var(--rashi-bg); }
.chip[data-on="true"].chip-en  { color: var(--he);  background: var(--he-bg); border-style: dotted; }
.chip[data-on="true"].chip-bm  { color: var(--bm);  background: var(--bm-bg); }
.chip[data-on="false"] { color: var(--chip-off-ink); border-color: var(--chip-off-bg); background: var(--chip-off-bg); }
.bm-btn {
  flex: none; border: none; background: none; cursor: pointer;
  width: 30px; height: 30px; margin-inline-start: auto; padding: 0;
  font-size: 1.15rem; line-height: 1; color: var(--ink-soft); opacity: 0.55;
  display: inline-flex; align-items: center; justify-content: center;
}
.bm-btn.on { color: var(--bm); opacity: 1; }
.bm-btn:active { opacity: 0.8; }
.empty-bm-msg { text-align: center; color: var(--ink-soft); font-size: 0.88rem; padding: 40px 20px; line-height: 1.6; }
.export-row { display: flex; gap: 8px; margin-bottom: 8px; }
.export-btn { flex: 1; border: 1.5px solid var(--rule); background: var(--bg); color: var(--ink); border-radius: 8px; padding: 9px 10px; font-size: 0.85rem; font-family: 'Frank Ruhl Libre', serif; cursor: pointer; }
.export-btn:active { background: var(--rule); }
.export-msg { font-size: 0.78rem; color: var(--ink-soft); min-height: 1.2em; text-align: center; }
.aliyah-nav, .chapter-nav { display: flex; gap: 6px; overflow-x: auto; padding: 0 0 8px; -ms-overflow-style: none; scrollbar-width: none; }
.aliyah-nav::-webkit-scrollbar, .chapter-nav::-webkit-scrollbar { display: none; }
.aliyah-btn, .chapter-btn { flex: none; border: 1px solid var(--rule); background: var(--bg); color: var(--ink-soft); border-radius: 8px; padding: 5px 11px; font-size: 0.78rem; font-family: 'Frank Ruhl Libre', serif; cursor: pointer; }
.chapter-btn { padding: 4px 9px; font-size: 0.73rem; opacity: 0.85; }
.aliyah-btn:active, .chapter-btn:active { background: var(--rule); }
.aliyah-btn.active, .chapter-btn.active { background: var(--torah); color: var(--bg); border-color: var(--torah); font-weight: 700; opacity: 1; }
.pos-indicator { font-size: 0.72rem; color: var(--ink-soft); padding: 0 0 6px; min-height: 1.2em; }
.topnav { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 2px 0 8px; font-size: 0.78rem; }
.topnav a, .topnav span.disabled { font-family: 'Frank Ruhl Libre', serif; color: var(--ink-soft); text-decoration: none; border: 1px solid var(--rule); border-radius: 8px; padding: 4px 10px; white-space: nowrap; }
.topnav span.disabled { opacity: 0.35; }
.topnav a:active { background: var(--rule); }
.topnav .idx { flex: none; }
.offline-tip { display: none; margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: var(--chip-off-bg); color: var(--ink-soft); font-size: 0.78rem; line-height: 1.5; }
.offline-tip.open { display: block; }
.panel { display: none; padding: 12px 0 14px; border-top: 1px solid var(--rule); }
.panel.open { display: block; }
.panel-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
.panel-row:last-child { margin-bottom: 0; }
.panel-label { font-size: 0.82rem; color: var(--ink-soft); flex: none; }
select#fontSelect { font-family: var(--font-main); font-size: 0.95rem; color: var(--ink); background: var(--bg); border: 1.5px solid var(--rule); border-radius: 8px; padding: 6px 8px; flex: 1; }
.size-stepper { display: flex; gap: 6px; }
.size-btn { border: 1.5px solid var(--rule); background: var(--bg); color: var(--ink); border-radius: 8px; width: 36px; height: 34px; font-size: 1rem; cursor: pointer; font-family: 'Frank Ruhl Libre', serif; }
.size-btn:active { background: var(--rule); }
#spacer { width: 100%; }
main { max-width: 640px; margin: 0 auto; padding: 8px 16px 60px; }
.chapter-head { margin: 26px 0 8px; text-align: center; color: var(--ink-soft); font-size: 0.78rem; scroll-margin-top: var(--header-h, 170px); }
.aliyah-head { margin: 10px 0 4px; text-align: center; color: var(--ink-soft); font-size: 0.72rem; padding: 3px 0; border-top: 1px dashed var(--rule); border-bottom: 1px dashed var(--rule); scroll-margin-top: var(--header-h, 170px); }
.verse { padding: 14px 0; border-bottom: 1px solid var(--rule); scroll-margin-top: var(--header-h, 170px); }
.verse-row { display: flex; gap: 9px; align-items: baseline; }
.vnum { flex: none; font-size: calc(0.7rem * var(--fsize)); color: var(--ink-soft); min-width: 1.4em; text-align: center; padding-top: 3px; }
.heb { font-size: calc(1.22rem * var(--fsize)); line-height: 1.65; color: var(--torah); font-weight: 600; font-family: 'Frank Ruhl Libre', serif; }
.targum-block { margin: 8px 0 0 1.85em; padding: 8px 12px; border-radius: 8px; border-right: 3px solid; font-family: var(--font-main); font-size: calc(1.08rem * var(--fsize)); line-height: 1.6; font-weight: 500; }
.targum-block.onk { background: var(--onk-bg); color: var(--onk); border-color: var(--onk-border); }
.targum-block.pj  { background: var(--pj-bg);  color: var(--pj);  border-color: var(--pj-border); }
.targum-block.yer { background: var(--yer-bg); color: var(--yer); border-color: var(--yer-border); }
.targum-block.rashi { background: var(--rashi-bg); color: var(--rashi); border-color: var(--rashi-border); }
.targum-label { display: block; font-family: 'Frank Ruhl Libre', serif; font-size: calc(0.7rem * var(--fsize)); font-weight: 700; opacity: 0.85; margin-bottom: 2px; }
.translation { margin-top: 6px; padding-top: 6px; border-top: 1px dashed currentColor; opacity: 0.9; font-size: calc(0.92rem * var(--fsize)); color: var(--he); font-weight: 400; }
.translation-en { font-family: 'Georgia', 'Times New Roman', serif; direction: ltr; text-align: left; }
.translation.missing { opacity: 0.5; font-style: italic; font-family: 'Frank Ruhl Libre', serif; direction: rtl; text-align: right; font-size: calc(0.8rem * var(--fsize)); }
footer { text-align: center; color: var(--ink-soft); font-size: 0.73rem; padding: 10px 16px 30px; }
.footnote-marker { font-size: 0.6em; color: var(--ink-soft); }
.footnote { font-size: 0.72em; font-weight: 400; font-style: normal; color: var(--ink-soft); }
</style>
</head>
<body>
<header>
  <div class="title-row">
    <h1>__TITLE__</h1>
    <div class="icon-btns">
      <button class="icon-btn" id="exportBtn" aria-label="ייצוא קטעים מסומנים">📤</button>
      <button class="icon-btn" id="offlineBtn" aria-label="שמירה לקריאה ללא אינטרנט">✈️</button>
      <button class="icon-btn" id="fontBtn" aria-label="הגדרות גופן">Aa</button>
      <button class="icon-btn" id="themeBtn" aria-label="החלף ערכת צבעים">🌓</button>
    </div>
  </div>
  <div class="offline-tip" id="offlineTip">
    לשמירה לקריאה בלי אינטרנט (למשל לפני טיסה): בכרום, לחצו על התפריט (⋮)
    למעלה ובחרו <b>הורדה</b> — הדף יישמר ויהיה זמין גם בלי חיבור.
  </div>
  <div class="panel" id="exportPanel">
    <div class="export-row">
      <button class="export-btn" id="copyBtn">העתק ללוח</button>
      <button class="export-btn" id="downloadBtn">הורד כקובץ</button>
    </div>
    <div class="export-msg" id="exportMsg"></div>
  </div>
  <div class="topnav">
    <a class="idx" href="index.html">⌂ כל הפרשות</a>
    __PREV_LINK__
    __NEXT_LINK__
  </div>
  <p class="subtitle">__SUBTITLE__</p>
  <div class="chips">
    <button class="chip chip-onk" data-key="onk" data-on="true">אונקלוס</button>
    <button class="chip chip-pj" data-key="pj" data-on="true">יונתן (פסבדו)</button>
    <button class="chip chip-yer" data-key="yer" data-on="true">ירושלמי</button>
    <button class="chip chip-rashi" data-key="rashi" data-on="false">רש״י</button>
    <button class="chip chip-en" data-key="en" data-on="false">English</button>
    <button class="chip chip-bm" data-key="bm" data-on="false">★ מסומנים</button>
  </div>
  <div class="panel" id="panel">
    <div class="panel-row">
      <span class="panel-label">גופן</span>
      <select id="fontSelect">
        <option value="'David Libre', serif">David Libre (ברירת מחדל, קריא)</option>
        <option value="'Frank Ruhl Libre', serif">Frank Ruhl Libre (אחיד עם הפסוק)</option>
        <option value="'Noto Sans Hebrew', sans-serif">Noto Sans (ללא תגים, הכי ברור)</option>
        <option value="'Noto Rashi Hebrew', serif">Noto Rashi (כתב רש״י מסורתי)</option>
      </select>
    </div>
    <div class="panel-row">
      <span class="panel-label">גודל טקסט</span>
      <div class="size-stepper">
        <button class="size-btn" id="sizeDown">א-</button>
        <button class="size-btn" id="sizeUp">א+</button>
      </div>
    </div>
  </div>
  <div class="pos-indicator" id="posIndicator"></div>
  <div class="chapter-nav" id="chapterNav"></div>
  <div class="aliyah-nav" id="aliyahNav"></div>
</header>
<div id="spacer"></div>
<main id="main"></main>
<footer>__FOOTER__</footer>

<script>
const SLUG = __SLUG_JSON__;
const DATA = __DATA_JSON__;
const LABELS = { onk: "אונקלוס", pj: "יונתן", yer: "ירושלמי", rashi: "רש״י" };
const ALIYAH_LETTERS = {1:'א',2:'ב',3:'ג',4:'ד',5:'ה',6:'ו',7:'ז',8:'ח',9:'ט',10:'י'};
const CH_LETTERS = __CH_LETTERS_JSON__;

function loadJSON(key, fallback) { try { const r = localStorage.getItem(key); if (r) return JSON.parse(r); } catch(e) {} return fallback; }
function saveJSON(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch(e) {} }

let prefs = loadJSON('targum-prefs-v6', { onk: true, pj: true, yer: true, rashi: false, en: false, bm: false });
document.querySelectorAll('.chip').forEach(btn => { btn.dataset.on = prefs[btn.dataset.key] ? 'true' : 'false'; });

let bookmarks = new Set(loadJSON('bookmarks-' + SLUG, []));
function saveBookmarks() { saveJSON('bookmarks-' + SLUG, [...bookmarks]); }

let theme = null;
try { theme = localStorage.getItem('theme'); } catch(e) {}
if (theme) document.documentElement.setAttribute('data-theme', theme);
document.getElementById('themeBtn').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch(e) {}
});

const panel = document.getElementById('panel');
document.getElementById('fontBtn').addEventListener('click', () => { panel.classList.toggle('open'); setTimeout(syncHeaderHeight, 0); });

const offlineTip = document.getElementById('offlineTip');
document.getElementById('offlineBtn').addEventListener('click', () => { offlineTip.classList.toggle('open'); setTimeout(syncHeaderHeight, 0); });

const exportPanel = document.getElementById('exportPanel');
document.getElementById('exportBtn').addEventListener('click', () => { exportPanel.classList.toggle('open'); setTimeout(syncHeaderHeight, 0); });

function stripHtml(s) {
  return (s || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '');
}

function buildExportText() {
  const title = document.querySelector('h1').textContent;
  const lines = [title + ' — קטעים שסומנו', ''];
  let any = false;
  for (const row of DATA) {
    const key = row.ch + '-' + row.v;
    if (!bookmarks.has(key)) continue;
    any = true;
    lines.push('פרק ' + (CH_LETTERS[row.ch] || row.ch) + ', פסוק ' + row.v);
    lines.push(row.heb);
    const add = (label, text, en) => {
      if (!text) return;
      lines.push('  ' + label + ': ' + stripHtml(text));
      if (prefs.en && en) lines.push('    (EN: ' + stripHtml(en) + ')');
    };
    if (prefs.onk) add('אונקלוס', row.onk, row.onk_en);
    if (prefs.pj) add('יונתן', row.pj, row.pj_en);
    if (prefs.yer) add('ירושלמי', row.tj, row.tj_en);
    if (prefs.rashi) add('רש״י', row.rashi, row.rashi_en);
    lines.push('');
  }
  return any ? lines.join('\n') : null;
}

function showExportMsg(text) {
  const el = document.getElementById('exportMsg');
  el.textContent = text;
  setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, 4000);
}

document.getElementById('copyBtn').addEventListener('click', async () => {
  const text = buildExportText();
  if (!text) { showExportMsg('אין קטעים מסומנים עדיין'); return; }
  try {
    await navigator.clipboard.writeText(text);
    showExportMsg('הועתק ללוח!');
  } catch (e) {
    showExportMsg('ההעתקה נכשלה — נסו את כפתור ההורדה');
  }
});

document.getElementById('downloadBtn').addEventListener('click', () => {
  const text = buildExportText();
  if (!text) { showExportMsg('אין קטעים מסומנים עדיין'); return; }
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = SLUG + '-bookmarks.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showExportMsg('ההורדה החלה');
});

const fontSelect = document.getElementById('fontSelect');
let savedFont = null;
try { savedFont = localStorage.getItem('reading-font'); } catch(e) {}
if (savedFont) { document.documentElement.style.setProperty('--font-main', savedFont); fontSelect.value = savedFont; }
fontSelect.addEventListener('change', () => {
  document.documentElement.style.setProperty('--font-main', fontSelect.value);
  try { localStorage.setItem('reading-font', fontSelect.value); } catch(e) {}
});

const SIZES = [0.85, 1, 1.15, 1.3, 1.45];
let sizeIdx = loadJSON('reading-size-idx', 1);
function applySize() { document.documentElement.style.setProperty('--fsize', SIZES[sizeIdx]); }
applySize();
document.getElementById('sizeUp').addEventListener('click', () => { sizeIdx = Math.min(sizeIdx + 1, SIZES.length - 1); applySize(); saveJSON('reading-size-idx', sizeIdx); });
document.getElementById('sizeDown').addEventListener('click', () => { sizeIdx = Math.max(sizeIdx - 1, 0); applySize(); saveJSON('reading-size-idx', sizeIdx); });

const nav = document.getElementById('aliyahNav');
const aliyahCount = DATA.reduce((m, r) => Math.max(m, r.aliyah || 0), 0);
for (let i = 1; i <= aliyahCount; i++) {
  const b = document.createElement('button');
  b.className = 'aliyah-btn';
  b.dataset.aliyah = i;
  b.textContent = 'עלייה ' + (ALIYAH_LETTERS[i] || i);
  b.addEventListener('click', () => { const el = document.getElementById('aliyah-' + i); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  nav.appendChild(b);
}

const chapterNav = document.getElementById('chapterNav');
const chapters = [...new Set(DATA.map(r => r.ch))];
if (chapters.length > 1) {
  for (const ch of chapters) {
    const b = document.createElement('button');
    b.className = 'chapter-btn';
    b.dataset.ch = ch;
    b.textContent = 'פרק ' + (CH_LETTERS[ch] || ch);
    b.addEventListener('click', () => { const el = document.getElementById('chapter-' + ch); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    chapterNav.appendChild(b);
  }
} else {
  chapterNav.style.display = 'none';
}

function targumEl(key, text, english) {
  const d = document.createElement('div');
  d.className = 'targum-block ' + key;
  let html = '<span class="targum-label">' + LABELS[key] + '</span>' + text;
  if (prefs.en) {
    if (english) html += '<div class="translation translation-en">' + english + '</div>';
    else html += '<div class="translation translation-en missing">No English available for this verse</div>';
  }
  d.innerHTML = html;
  return d;
}

function bookmarkBtn(key) {
  const bm = document.createElement('button');
  bm.className = 'bm-btn' + (bookmarks.has(key) ? ' on' : '');
  bm.dataset.key = key;
  bm.textContent = bookmarks.has(key) ? '★' : '☆';
  bm.setAttribute('aria-label', 'סמן קטע זה');
  bm.addEventListener('click', (e) => {
    e.stopPropagation();
    if (bookmarks.has(key)) bookmarks.delete(key); else bookmarks.add(key);
    saveBookmarks();
    if (prefs.bm) {
      // filtering by bookmarks: unmarking must remove this verse from view
      const anchor = currentAnchorKey();
      render();
      restoreAnchor(anchor);
      updatePosition();
    } else {
      bm.classList.toggle('on');
      bm.textContent = bookmarks.has(key) ? '★' : '☆';
    }
  });
  return bm;
}

const main = document.getElementById('main');
function render() {
  main.innerHTML = '';
  let lastCh = null, lastAliyah = null, shown = 0;
  for (const row of DATA) {
    const key = row.ch + '-' + row.v;
    if (prefs.bm && !bookmarks.has(key)) continue;
    shown++;
    if (row.aliyah !== lastAliyah) {
      lastAliyah = row.aliyah;
      const a = document.createElement('div');
      a.className = 'aliyah-head'; a.id = 'aliyah-' + row.aliyah;
      a.dataset.aliyah = row.aliyah;
      a.textContent = 'עלייה ' + (ALIYAH_LETTERS[row.aliyah] || row.aliyah);
      main.appendChild(a);
    }
    if (row.ch !== lastCh) {
      lastCh = row.ch;
      const h = document.createElement('div');
      h.className = 'chapter-head'; h.id = 'chapter-' + row.ch;
      h.dataset.ch = row.ch;
      h.textContent = 'פרק ' + (CH_LETTERS[row.ch] || row.ch);
      main.appendChild(h);
    }
    const v = document.createElement('div');
    v.className = 'verse';
    v.dataset.key = key;
    v.dataset.ch = row.ch;
    v.dataset.aliyah = row.aliyah;
    const vr = document.createElement('div');
    vr.className = 'verse-row';
    vr.innerHTML = '<div class="vnum">' + row.v + '</div><div class="heb">' + row.heb + '</div>';
    vr.appendChild(bookmarkBtn(key));
    v.appendChild(vr);
    if (prefs.onk && row.onk) v.appendChild(targumEl('onk', row.onk, row.onk_en));
    if (prefs.pj && row.pj) v.appendChild(targumEl('pj', row.pj, row.pj_en));
    if (prefs.yer && row.tj) v.appendChild(targumEl('yer', row.tj, row.tj_en));
    if (prefs.rashi && row.rashi) v.appendChild(targumEl('rashi', row.rashi, row.rashi_en));
    main.appendChild(v);
  }
  if (prefs.bm && shown === 0) {
    const msg = document.createElement('div');
    msg.className = 'empty-bm-msg';
    msg.textContent = 'אין קטעים מסומנים בפרשה הזו. לחצו על ☆ ליד פסוק כדי לסמן אותו.';
    main.appendChild(msg);
  }
}

// --- keep your place in the text across toggles (no more jumping) ---
function currentAnchorKey() {
  const headerH = document.querySelector('header').offsetHeight;
  for (const v of main.querySelectorAll('.verse')) {
    if (v.getBoundingClientRect().bottom > headerH) return v.dataset.key;
  }
  return null;
}
function restoreAnchor(key) {
  const el = key ? main.querySelector('[data-key="' + CSS.escape(key) + '"]') : null;
  const prevBehavior = document.documentElement.style.scrollBehavior;
  document.documentElement.style.scrollBehavior = 'auto';  // instant, not animated
  if (el) {
    el.scrollIntoView({ block: 'start' });
  } else {
    // anchor verse no longer rendered (e.g. hidden by the bookmark filter) —
    // go to top rather than leave the page at an unrelated scroll position
    window.scrollTo(0, 0);
  }
  document.documentElement.style.scrollBehavior = prevBehavior;
}

// --- "where am I" indicator + nav highlighting, kept in sync while scrolling ---
let lastActiveCh = null, lastActiveAliyah = null;
function updatePosition() {
  const headerH = document.querySelector('header').offsetHeight;
  let curCh = null, curAliyah = null;
  main.querySelectorAll('.chapter-head').forEach(el => {
    if (el.getBoundingClientRect().top <= headerH + 2) curCh = el.dataset.ch;
  });
  main.querySelectorAll('.aliyah-head').forEach(el => {
    if (el.getBoundingClientRect().top <= headerH + 2) curAliyah = el.dataset.aliyah;
  });
  const posEl = document.getElementById('posIndicator');
  if (posEl) {
    const parts = [];
    if (curCh) parts.push('פרק ' + (CH_LETTERS[curCh] || curCh));
    if (curAliyah) parts.push('עלייה ' + (ALIYAH_LETTERS[curAliyah] || curAliyah));
    posEl.textContent = parts.join(' · ');
  }
  if (curCh !== lastActiveCh) {
    lastActiveCh = curCh;
    document.querySelectorAll('.chapter-btn').forEach(b => {
      const on = b.dataset.ch === curCh;
      b.classList.toggle('active', on);
      if (on) b.scrollIntoView({ inline: 'center', block: 'nearest' });
    });
  }
  if (curAliyah !== lastActiveAliyah) {
    lastActiveAliyah = curAliyah;
    document.querySelectorAll('.aliyah-btn').forEach(b => {
      const on = b.dataset.aliyah === curAliyah;
      b.classList.toggle('active', on);
      if (on) b.scrollIntoView({ inline: 'center', block: 'nearest' });
    });
  }
}
window.addEventListener('scroll', () => requestAnimationFrame(updatePosition), { passive: true });

document.querySelectorAll('.chip').forEach(btn => {
  const key = btn.dataset.key;
  btn.addEventListener('click', () => {
    prefs[key] = !prefs[key];
    btn.dataset.on = prefs[key] ? 'true' : 'false';
    saveJSON('targum-prefs-v6', prefs);
    const anchor = currentAnchorKey();
    render();
    restoreAnchor(anchor);
    updatePosition();
  });
});

function syncHeaderHeight() {
  const h = document.querySelector('header').offsetHeight;
  document.getElementById('spacer').style.height = h + 'px';
  document.documentElement.style.setProperty('--header-h', h + 'px');
}
window.addEventListener('resize', syncHeaderHeight);

render();
syncHeaderHeight();
updatePosition();
setTimeout(syncHeaderHeight, 150);
</script>
</body>
</html>
"""


def script_json(obj):
    """JSON for embedding inside <script>: '</' is escaped so a stray
    '</script>' in Sefaria's text can't end the script block early."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render_html(title, subtitle, data, footer, slug, prev_parsha=None, next_parsha=None):
    html = TEMPLATE
    html = html.replace("__TITLE__", title)
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__FOOTER__", footer)
    html = html.replace("__SLUG_JSON__", script_json(slug))
    html = html.replace("__CH_LETTERS_JSON__", script_json(HEB_CH_LETTERS))
    if prev_parsha:
        prev_link = f'<a href="{prev_parsha["slug"]}.html">‹ {prev_parsha["title_en"]}</a>'
    else:
        prev_link = '<span class="disabled">‹</span>'
    if next_parsha:
        next_link = f'<a href="{next_parsha["slug"]}.html">{next_parsha["title_en"]} ›</a>'
    else:
        next_link = '<span class="disabled">›</span>'
    html = html.replace("__PREV_LINK__", prev_link)
    html = html.replace("__NEXT_LINK__", next_link)
    # data last, so text that happens to contain a placeholder is never rewritten
    html = html.replace("__DATA_JSON__", script_json(data))
    return html


def verify_js(html):
    m = re.search(r"const DATA = (\[.*?\]);\n", html, re.S)
    if not m:
        raise RuntimeError("generated page has no DATA block")
    rows = json.loads(m.group(1))
    if not rows:
        raise RuntimeError("generated page has no verses")


def list_year_parshiot(weeks=55, start=None):
    """Walk forward week by week collecting each Shabbat's parsha. Holiday
    readings are skipped; a parsha already seen (next year's cycle) is not
    built twice."""
    start = start or upcoming_saturday()
    parshiot, seen, skipped = [], set(), []
    for i in range(weeks):
        d = start + datetime.timedelta(weeks=i)
        cache_hit = (CALENDAR_CACHE_DIR / f"{d.isoformat()}.json").exists()
        tag = "(cached)" if cache_hit else "(fetching)"
        print(f"  [{i+1}/{weeks}] {d.isoformat()} {tag}...")
        parsha, why = calendar_to_parsha(fetch_calendar(d), d)
        if not parsha:
            skipped.append(f"{d.isoformat()}: {why}")
            print(f"      (skipped: {why})")
        elif parsha["slug"] in seen:
            print(f"      ({parsha['title_en']} already built from an earlier week)")
        else:
            seen.add(parsha["slug"])
            parshiot.append(parsha)
            print(f"      → {parsha['title_en']} ({parsha['title']})")
    if skipped:
        print(f"\n  Skipped {len(skipped)} week(s):")
        for s in skipped:
            print(f"    {s}")
    return parshiot


INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>כל הפרשות</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@400;500;700&family=David+Libre:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root { --bg:#FDFBF6; --ink:#1c1712; --ink-soft:#55493c; --rule:#d8cbb3; --torah:#16120d; --accent:#7a3317; --accent-bg:#f7e9de; }
:root[data-theme="dark"] { --bg:#0e0d0a; --ink:#f5ecd9; --ink-soft:#c9bc9f; --rule:#433a2b; --torah:#fbf4e6; --accent:#f3b58a; --accent-bg:#3a2416; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg:#0e0d0a; --ink:#f5ecd9; --ink-soft:#c9bc9f; --rule:#433a2b; --torah:#fbf4e6; --accent:#f3b58a; --accent-bg:#3a2416; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:'David Libre', serif; padding: calc(16px + env(safe-area-inset-top,0px)) 16px calc(24px + env(safe-area-inset-bottom,0px)); }
h1 { font-family:'Frank Ruhl Libre', serif; font-size:1.5rem; margin: 0 0 14px; }
.this-week { display:block; text-align:center; background:var(--accent-bg); color:var(--accent); border:1.5px solid var(--accent); border-radius:12px; padding:14px; font-family:'Frank Ruhl Libre', serif; font-weight:700; font-size:1.05rem; text-decoration:none; margin-bottom:20px; }
.this-week .sub { display:block; font-weight:400; font-size:0.8rem; margin-top:3px; opacity:0.85; }
ul { list-style:none; padding:0; margin:0; max-width:480px; }
li { border-bottom:1px solid var(--rule); }
li a { display:flex; justify-content:space-between; gap:10px; padding:12px 4px; color:var(--ink); text-decoration:none; }
li a .en { color:var(--ink-soft); font-size:0.82rem; }
li a:active { background: var(--accent-bg); }
</style>
</head>
<body>
<h1>כל הפרשות</h1>
<a class="this-week" id="thisWeek" href="#">טוען...</a>
<ul id="list"></ul>
<script>
const PARSHIOT = __INDEX_JSON__;
let theme = null;
try { theme = localStorage.getItem('theme'); } catch(e) {}
if (theme) document.documentElement.setAttribute('data-theme', theme);

const list = document.getElementById('list');
for (const p of PARSHIOT) {
  const li = document.createElement('li');
  li.innerHTML = '<a href="' + p.slug + '.html"><span>' + p.title + '</span>' +
                 '<span class="en">' + p.title_en + ' · ' + p.date + '</span></a>';
  list.appendChild(li);
}

const now = new Date();  // local date, not UTC (UTC flips to tomorrow on Saturday evening in the US)
const today = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
let current = PARSHIOT.find(p => p.date >= today) || PARSHIOT[PARSHIOT.length - 1];
const tw = document.getElementById('thisWeek');
if (current) {
  tw.href = current.slug + '.html';
  tw.innerHTML = '📖 ' + current.title + '<span class="sub">' + current.title_en + ' · ' + current.date + '</span>';
} else {
  tw.textContent = 'לא נמצאו פרשות';
}
</script>
</body>
</html>
"""


def render_index(parshiot):
    index_data = [{"slug": p["slug"], "title": p["title"], "title_en": p["title_en"], "date": p["date"]}
                  for p in parshiot]
    return INDEX_TEMPLATE.replace("__INDEX_JSON__", script_json(index_data))


def build_one(parsha, cache, out_path, prev_parsha=None, next_parsha=None):
    data = build_data(parsha, cache)
    (sc, sv), (ec, ev) = parsha["start"], parsha["end"]
    subtitle = f"{parsha['book']} {sc}:{sv} – {ec}:{ev} · מתוך ספריית ספריא"
    footer = f"נוצר אוטומטית · {parsha['title_en']} · {parsha['date']}"
    html = render_html(parsha["title"], subtitle, data, footer, parsha["slug"], prev_parsha, next_parsha)
    verify_js(html)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path, len(data)


def neighbor_parsha(d, step, max_weeks=4):
    """Nearest weekly parsha before (step=-1) or after (step=+1) Shabbat d,
    skipping holiday weeks, so a single-week build keeps its ‹ › links."""
    for k in range(1, max_weeks + 1):
        nd = d + datetime.timedelta(weeks=step * k)
        p, _ = calendar_to_parsha(fetch_calendar(nd), nd)
        if p:
            return p
    return None


def build_year(weeks, out_dir):
    print(f"Collecting the next {weeks} weeks of Torah readings from Sefaria...")
    parshiot = list_year_parshiot(weeks)
    print(f"  {len(parshiot)} weekly parshiot to build")
    if not parshiot:
        sys.exit("Nothing to build.")

    cache = TextCache()
    for i, parsha in enumerate(parshiot):
        prev_p = parshiot[i - 1] if i > 0 else None
        next_p = parshiot[i + 1] if i + 1 < len(parshiot) else None
        print(f"  [{i+1}/{len(parshiot)}] {parsha['title_en']} ({parsha['book']})...")
        build_one(parsha, cache, out_dir / f"{parsha['slug']}.html", prev_p, next_p)

    index_path = out_dir / "index.html"
    index_path.write_text(render_index(parshiot), encoding="utf-8")
    print(f"\nWrote {len(parshiot)} parsha pages + index.html to {out_dir}/")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="any date (YYYY-MM-DD) within the week to build")
    g.add_argument("--next", action="store_true", help="build this/next Shabbat's parsha")
    g.add_argument("--list", action="store_true", help="list upcoming parshiot and dates, then exit")
    g.add_argument("--year", action="store_true",
                   help="build a full year of parshiot + an index.html, into site/")
    ap.add_argument("--weeks", type=int, default=None,
                     help="how many weeks ahead (default: 12 for --list, 55 for --year)")
    ap.add_argument("--out", default=None,
                     help="output folder (default: site/); for --date/--next it may also be a file path")
    args = ap.parse_args()

    if args.date:
        try:
            datetime.date.fromisoformat(args.date)
        except ValueError:
            ap.error(f"--date must look like 2026-10-10, got {args.date!r}")
    if args.weeks is not None and args.weeks < 1:
        ap.error("--weeks must be at least 1")

    if args.list:
        list_upcoming(args.weeks or 12)
        return

    if args.year:
        out_dir = Path(args.out) if args.out else ROOT / "site"
        build_year(args.weeks or 55, out_dir)
        return

    date_str = args.date or upcoming_saturday().isoformat()
    print(f"Looking up the Torah reading for the week of {date_str}...")
    parsha = find_parsha_for_date(date_str)
    print(f"  {parsha['title_en']} ({parsha['title']}), Shabbat {parsha['date']} — {parsha['book']} "
          f"{parsha['start'][0]}:{parsha['start'][1]}-{parsha['end'][0]}:{parsha['end'][1]}, "
          f"{len(parsha['aliyot'])} aliyot")

    shabbat = datetime.date.fromisoformat(parsha["date"])
    prev_p, next_p = neighbor_parsha(shabbat, -1), neighbor_parsha(shabbat, +1)

    print("Extracting texts...")
    out_path = Path(args.out) if args.out else ROOT / "site"
    if out_path.suffix.lower() not in (".html", ".htm"):
        out_path = out_path / f"{parsha['slug']}.html"   # --out names a folder
    out_path, n = build_one(parsha, TextCache(), out_path, prev_p, next_p)
    print(f"  {n} verses")
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
