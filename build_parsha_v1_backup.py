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
    fragments, Rashi, English) come from Sefaria's bulk export
    (https://github.com/Sefaria/Sefaria-Export) — their own docs say this,
    not the live API, is the right way to pull data in bulk.

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

Requires: git, and Python 3 (standard library only — no pip installs).
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
REPO_DIR = ROOT / "Sefaria-Export"
REPO_URL = "https://github.com/Sefaria/Sefaria-Export.git"
CALENDARS_URL = "https://www.sefaria.org/api/calendars?year={y}&month={m}&day={d}"
CALENDAR_CACHE_DIR = ROOT / "calendar_cache"

HEB_CH_LETTERS = {i: l for i, l in enumerate("אבגדהוזחטיכלמנסעפצקרשת", start=1)}

ALIYAH_RE = re.compile(r"^(.+?)\s+(\d+):(\d+)-(\d+):(\d+)$")


# ---------------------------------------------------------------------------
# Step 1: ask Sefaria's Calendars API which parsha, book, and aliyot apply.
# ---------------------------------------------------------------------------

def fetch_calendar(date, retries=8, max_delay=30.0):
    """Fetch (and disk-cache) Sefaria's calendar for one date. Cached results
    are reused forever — a Shabbat's parsha never changes — so re-running
    after a partial failure only hits the network for what's still missing."""
    CALENDAR_CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CALENDAR_CACHE_DIR / f"{date.isoformat()}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    url = CALENDARS_URL.format(y=date.year, m=date.month, d=date.day)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; parsha-reader-script/1.0; personal use)",
        "Accept": "application/json",
    })
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                print(f"    ({date.isoformat()}: rate limited [attempt {attempt+1}/{retries}], "
                      f"waiting {delay:.0f}s...)")
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
            else:
                raise


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_aliyah_ref(ref):
    m = ALIYAH_RE.match(ref)
    if not m:
        return None
    book, sc, sv, ec, ev = m.groups()
    return book, int(sc), int(sv), int(ec), int(ev)


def calendar_to_parsha(calendar_json):
    """Pull the 'Parashat Hashavua' entry out of a /api/calendars response."""
    for item in calendar_json.get("calendar_items", []):
        if item.get("title", {}).get("en") != "Parashat Hashavua":
            continue
        raw_aliyot = item.get("extraDetails", {}).get("aliyot", [])
        if not raw_aliyot:
            return None
        parsed = [parse_aliyah_ref(a) for a in raw_aliyot[:7]]
        if any(p is None for p in parsed):
            print("  (couldn't parse one of the aliyah strings — skipping)")
            return None
        books = {p[0] for p in parsed}
        if len(books) > 1:
            print(f"  (skipping {item['displayValue']['en']}: the 7 regular "
                  f"aliyot span multiple books ({', '.join(books)}) — "
                  f"not handled by this script)")
            return None
        book = books.pop()
        aliyot = [[p[1], p[2], p[3], p[4]] for p in parsed]
        return {
            "slug": slugify(item["displayValue"]["en"]),
            "title": "פרשת " + item["displayValue"].get("he", item["displayValue"]["en"]),
            "title_en": item["displayValue"]["en"],
            "date": calendar_json.get("date", ""),
            "book": book,
            "start": aliyot[0][:2],
            "end": aliyot[-1][2:],
            "aliyot": aliyot,
        }
    return None


def upcoming_saturday(from_date=None):
    d = from_date or datetime.date.today()
    days_ahead = (5 - d.weekday()) % 7  # Monday=0 ... Saturday=5
    return d + datetime.timedelta(days=days_ahead)


def find_parsha_for_date(date_str):
    d = datetime.date.fromisoformat(date_str)
    cal = fetch_calendar(d)
    parsha = calendar_to_parsha(cal)
    if not parsha:
        sys.exit(f"No regular weekly parsha found for {date_str} "
                  f"(might be a holiday reading, not handled by this script).")
    return parsha


def list_upcoming(weeks=12):
    start = upcoming_saturday()
    print(f"Upcoming Torah readings (next {weeks} weeks):\n")
    seen = set()
    for i in range(weeks):
        if i > 0:
            time.sleep(1.5)
        d = start + datetime.timedelta(weeks=i)
        cal = fetch_calendar(d)
        parsha = calendar_to_parsha(cal)
        if parsha and parsha["slug"] not in seen:
            seen.add(parsha["slug"])
            print(f"  {d.isoformat()}  {parsha['title_en']:25s} ({parsha['title']})")
        elif not parsha:
            print(f"  {d.isoformat()}  (holiday reading or unhandled — skipped)")


# ---------------------------------------------------------------------------
# Step 2: pull the actual texts from Sefaria's bulk export, given a book name.
# ---------------------------------------------------------------------------

def book_paths(book):
    """Sefaria's folder-naming pattern, verified against Genesis/Exodus/
    Leviticus/Numbers/Deuteronomy — same shape for all five Torah books."""
    return {
        "hebrew": f"json/Tanakh/Torah/{book}",
        "onkelos": f"json/Tanakh/Targum/Onkelos/Torah/Onkelos {book}",
        "pj": f"json/Tanakh/Targum/Targum Jonathan/Torah/Targum Jonathan on {book}",
        "yerushalmi": "json/Tanakh/Targum/Targum Jerusalem/Targum Jerusalem",
        "rashi": f"json/Tanakh/Commentary/Rashi/Torah/Rashi on {book}",
    }


def run(cmd, cwd=None):
    print("  $", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_repo(paths_needed):
    if not REPO_DIR.exists():
        print(f"Cloning Sefaria-Export into {REPO_DIR} (one-time, sparse)...")
        run(["git", "clone", "--depth", "1", "--filter=blob:none",
             "--sparse", REPO_URL, str(REPO_DIR)])
        run(["git", "sparse-checkout", "init", "--cone"], cwd=REPO_DIR)
    print("Making sure needed texts are checked out...")
    run(["git", "sparse-checkout", "add", *paths_needed], cwd=REPO_DIR)


def load_json(rel_path, filename):
    p = REPO_DIR / rel_path / filename
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_text_array(rel_path, hebrew=True, required=False):
    sub = "Hebrew" if hebrew else "English"
    d = load_json(f"{rel_path}/{sub}", "merged.json")
    if d is None:
        if required:
            sys.exit(f"Expected text not found: {rel_path}/{sub}/merged.json")
        return []
    return d["text"]


def load_yerushalmi(rel_path, book, hebrew=True):
    sub = "Hebrew" if hebrew else "English"
    d = load_json(f"{rel_path}/{sub}", "merged.json")
    if not d:
        return []
    t = d["text"]
    return t.get(book, []) if isinstance(t, dict) else []


def load_rashi(rel_path):
    d = load_json(f"{rel_path}/Hebrew", "merged.json")
    return d["text"] if d else []


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

    def for_book(self, book, paths):
        if book not in self._cache:
            self._cache[book] = {
                "heb": load_text_array(paths["hebrew"], required=True),
                "onk": load_text_array(paths["onkelos"]),
                "onk_en": load_text_array(paths["onkelos"], hebrew=False),
                "pj": load_text_array(paths["pj"]),
                "pj_en": load_text_array(paths["pj"], hebrew=False),
                "tj": load_yerushalmi(paths["yerushalmi"], book),
                "tj_en": load_yerushalmi(paths["yerushalmi"], book, hebrew=False),
                "rashi": load_rashi(paths["rashi"]),
            }
        return self._cache[book]


def build_data(parsha, paths, cache=None):
    (sc, sv), (ec, ev) = parsha["start"], parsha["end"]
    cache = cache or TextCache()
    t = cache.for_book(parsha["book"], paths)
    heb, onk, onk_en, pj, pj_en, tj, tj_en, rashi = (
        t["heb"], t["onk"], t["onk_en"], t["pj"], t["pj_en"], t["tj"], t["tj_en"], t["rashi"])

    data = []
    for ch in range(sc, ec + 1):
        v_start = sv if ch == sc else 1
        v_end = ev if ch == ec else (len(heb[ch - 1]) if ch - 1 < len(heb) else 0)
        for v in range(v_start, v_end + 1):
            data.append({
                "ch": ch, "v": v, "aliyah": aliyah_of(ch, v, parsha["aliyot"]),
                "heb": safe_get(heb, ch, v),
                "onk": safe_get(onk, ch, v), "onk_en": safe_get(onk_en, ch, v),
                "pj": safe_get(pj, ch, v), "pj_en": safe_get(pj_en, ch, v),
                "tj": safe_get(tj, ch, v), "tj_en": safe_get(tj_en, ch, v),
                "rashi": safe_get(rashi, ch, v),
            })
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
.chip[data-on="false"] { color: var(--chip-off-ink); border-color: var(--chip-off-bg); background: var(--chip-off-bg); }
.aliyah-nav { display: flex; gap: 6px; overflow-x: auto; padding: 0 0 10px; -ms-overflow-style: none; scrollbar-width: none; }
.aliyah-nav::-webkit-scrollbar { display: none; }
.aliyah-btn { flex: none; border: 1px solid var(--rule); background: var(--bg); color: var(--ink-soft); border-radius: 8px; padding: 5px 11px; font-size: 0.78rem; font-family: 'Frank Ruhl Libre', serif; cursor: pointer; }
.aliyah-btn:active { background: var(--rule); }
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
.chapter-head { margin: 26px 0 8px; text-align: center; color: var(--ink-soft); font-size: 0.78rem; }
.aliyah-head { margin: 10px 0 4px; text-align: center; color: var(--ink-soft); font-size: 0.72rem; padding: 3px 0; border-top: 1px dashed var(--rule); border-bottom: 1px dashed var(--rule); scroll-margin-top: var(--header-h, 170px); }
.verse { padding: 14px 0; border-bottom: 1px solid var(--rule); }
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
</style>
</head>
<body>
<header>
  <div class="title-row">
    <h1>__TITLE__</h1>
    <div class="icon-btns">
      <button class="icon-btn" id="offlineBtn" aria-label="שמירה לקריאה ללא אינטרנט">✈️</button>
      <button class="icon-btn" id="fontBtn" aria-label="הגדרות גופן">Aa</button>
      <button class="icon-btn" id="themeBtn" aria-label="החלף ערכת צבעים">🌓</button>
    </div>
  </div>
  <div class="offline-tip" id="offlineTip">
    לשמירה לקריאה בלי אינטרנט (למשל לפני טיסה): בכרום, לחצו על התפריט (⋮)
    למעלה ובחרו <b>הורדה</b> — הדף יישמר ויהיה זמין גם בלי חיבור.
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
  <div class="aliyah-nav" id="aliyahNav"></div>
</header>
<div id="spacer"></div>
<main id="main"></main>
<footer>__FOOTER__</footer>

<script>
const DATA = __DATA_JSON__;
const LABELS = { onk: "אונקלוס", pj: "יונתן", yer: "ירושלמי", rashi: "רש״י" };
const ALIYAH_LETTERS = {1:'א',2:'ב',3:'ג',4:'ד',5:'ה',6:'ו',7:'ז',8:'ח',9:'ט',10:'י'};
const CH_LETTERS = __CH_LETTERS_JSON__;

function loadJSON(key, fallback) { try { const r = localStorage.getItem(key); if (r) return JSON.parse(r); } catch(e) {} return fallback; }
function saveJSON(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch(e) {} }

let prefs = loadJSON('targum-prefs-v6', { onk: true, pj: true, yer: true, rashi: false, en: false });
document.querySelectorAll('.chip').forEach(btn => { btn.dataset.on = prefs[btn.dataset.key] ? 'true' : 'false'; });

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
const aliyahCount = Math.max(...DATA.map(r => r.aliyah));
for (let i = 1; i <= aliyahCount; i++) {
  const b = document.createElement('button');
  b.className = 'aliyah-btn';
  b.textContent = 'עלייה ' + (ALIYAH_LETTERS[i] || i);
  b.addEventListener('click', () => { const el = document.getElementById('aliyah-' + i); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  nav.appendChild(b);
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

function render() {
  const main = document.getElementById('main');
  main.innerHTML = '';
  let lastCh = null, lastAliyah = null;
  for (const row of DATA) {
    if (row.aliyah !== lastAliyah) {
      lastAliyah = row.aliyah;
      const a = document.createElement('div');
      a.className = 'aliyah-head'; a.id = 'aliyah-' + row.aliyah;
      a.textContent = 'עלייה ' + (ALIYAH_LETTERS[row.aliyah] || row.aliyah);
      main.appendChild(a);
    }
    if (row.ch !== lastCh) {
      lastCh = row.ch;
      const h = document.createElement('div');
      h.className = 'chapter-head'; h.textContent = 'פרק ' + (CH_LETTERS[row.ch] || row.ch);
      main.appendChild(h);
    }
    const v = document.createElement('div');
    v.className = 'verse';
    const vr = document.createElement('div');
    vr.className = 'verse-row';
    vr.innerHTML = '<div class="vnum">' + row.v + '</div><div class="heb">' + row.heb + '</div>';
    v.appendChild(vr);
    if (prefs.onk && row.onk) v.appendChild(targumEl('onk', row.onk, row.onk_en));
    if (prefs.pj && row.pj) v.appendChild(targumEl('pj', row.pj, row.pj_en));
    if (prefs.yer && row.tj) v.appendChild(targumEl('yer', row.tj, row.tj_en));
    if (prefs.rashi && row.rashi) v.appendChild(targumEl('rashi', row.rashi, ''));
    main.appendChild(v);
  }
}

document.querySelectorAll('.chip').forEach(btn => {
  const key = btn.dataset.key;
  btn.addEventListener('click', () => {
    prefs[key] = !prefs[key];
    btn.dataset.on = prefs[key] ? 'true' : 'false';
    saveJSON('targum-prefs-v6', prefs);
    render();
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
setTimeout(syncHeaderHeight, 150);
</script>
</body>
</html>
"""


def render_html(title, subtitle, data, footer, prev_parsha=None, next_parsha=None):
    html = TEMPLATE
    html = html.replace("__TITLE__", title)
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__FOOTER__", footer)
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__CH_LETTERS_JSON__", json.dumps(HEB_CH_LETTERS, ensure_ascii=False))
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
    return html


def verify_js(html):
    m = re.search(r"const DATA = (\[.*?\]);\n", html, re.S)
    json.loads(m.group(1))


def list_year_parshiot(weeks=55, start=None):
    """Walk forward week by week collecting each Shabbat's parsha. Weeks with
    no regular 'Parashat Hashavua' entry (holiday readings) are skipped."""
    start = start or upcoming_saturday()
    parshiot, seen = [], set()
    for i in range(weeks):
        if i > 0:
            time.sleep(1.5)  # be polite to Sefaria's API across ~55 calls
        d = start + datetime.timedelta(weeks=i)
        cache_hit = (CALENDAR_CACHE_DIR / f"{d.isoformat()}.json").exists()
        tag = "(cached)" if cache_hit else "(fetching)"
        print(f"  [{i+1}/{weeks}] {d.isoformat()} {tag}...")
        cal = fetch_calendar(d)
        parsha = calendar_to_parsha(cal)
        if parsha and parsha["slug"] not in seen:
            seen.add(parsha["slug"])
            parshiot.append(parsha)
            print(f"      → {parsha['title_en']} ({parsha['title']})")
        elif not parsha:
            print(f"      (no regular parsha this week — holiday reading? skipped)")
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

const today = new Date().toISOString().slice(0, 10);
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
    return INDEX_TEMPLATE.replace("__INDEX_JSON__", json.dumps(index_data, ensure_ascii=False))


def build_one(parsha, cache, out_dir, prev_parsha=None, next_parsha=None):
    paths = book_paths(parsha["book"])
    data = build_data(parsha, paths, cache)
    (sc, sv), (ec, ev) = parsha["start"], parsha["end"]
    subtitle = f"{parsha['book']} {sc}:{sv} – {ec}:{ev} · מתוך ספריית ספריא"
    footer = f"נוצר אוטומטית · {parsha['title_en']}"
    html = render_html(parsha["title"], subtitle, data, footer, prev_parsha, next_parsha)
    verify_js(html)
    out_path = out_dir / f"{parsha['slug']}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_year(weeks, out_dir):
    print(f"Collecting the next {weeks} weeks of Torah readings from Sefaria...")
    parshiot = list_year_parshiot(weeks)
    print(f"  {len(parshiot)} distinct parshiot found")

    books_needed = {p["book"] for p in parshiot}
    all_paths = set()
    for book in books_needed:
        all_paths.update(book_paths(book).values())
    ensure_repo(sorted(all_paths))

    out_dir.mkdir(parents=True, exist_ok=True)
    cache = TextCache()
    for i, parsha in enumerate(parshiot):
        prev_p = parshiot[i - 1] if i > 0 else None
        next_p = parshiot[i + 1] if i + 1 < len(parshiot) else None
        print(f"  [{i+1}/{len(parshiot)}] {parsha['title_en']} ({parsha['book']})...")
        build_one(parsha, cache, out_dir, prev_p, next_p)

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
                     help="for --date/--next: output file path. For --year: output directory (default: site/)")
    args = ap.parse_args()

    if args.list:
        list_upcoming(args.weeks or 12)
        return

    if args.year:
        out_dir = Path(args.out) if args.out else ROOT / "site"
        build_year(args.weeks or 55, out_dir)
        return

    date_str = args.date or upcoming_saturday().isoformat()
    print(f"Looking up the Torah reading for {date_str}...")
    parsha = find_parsha_for_date(date_str)
    print(f"  {parsha['title_en']} ({parsha['title']}) — {parsha['book']} "
          f"{parsha['start'][0]}:{parsha['start'][1]}-{parsha['end'][0]}:{parsha['end'][1]}, "
          f"{len(parsha['aliyot'])} aliyot")

    paths = book_paths(parsha["book"])
    ensure_repo(paths.values())

    print("Extracting texts...")
    data = build_data(parsha, paths)
    print(f"  {len(data)} verses")

    (sc, sv), (ec, ev) = parsha["start"], parsha["end"]
    subtitle = f"{parsha['book']} {sc}:{sv} – {ec}:{ev} · מתוך ספריית ספריא"
    footer = f"נוצר אוטומטית · {parsha['title_en']}"

    html = render_html(parsha["title"], subtitle, data, footer)
    verify_js(html)

    out_path = Path(args.out) if args.out else ROOT / "site" / f"{parsha['slug']}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} ({len(html.encode('utf-8'))} bytes)")


if __name__ == "__main__":
    main()
