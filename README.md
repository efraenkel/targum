# Parsha + Targumim reader — Sefaria-only builder

Same reading page as before (Hebrew text, Onkelos, Pseudo-Jonathan, Targum
Yerushalmi fragments, Rashi, English where Sefaria has it, font/size
controls, dark mode, aliyah jump-nav). This version gets everything —
*which* parsha, the aliyah boundaries, and the actual texts — from a single
source: **Sefaria**. No second provider, no config file to fill in.

## Why Sefaria alone

Their own homepage shows a "this week's parasha" quick link, so they
obviously have the aliyah data internally — and it turns out it's public:
Sefaria's **Calendars API** (`/api/calendars`) returns the current parsha,
its verse range, and all 7 aliyot directly, as part of the same data that
powers the homepage link. Confirmed against Sefaria's own published example
in their developer tutorial (a real response for Parashat Mishpatim) — this
script's parsing was tested against that exact example and reproduces it
correctly.

The actual texts come from Sefaria's **bulk data export**. Since September
2026 the `Sefaria/Sefaria-Export` GitHub repo holds only an index
(`books.json`); the texts themselves are in a public Google Cloud Storage
bucket (`https://storage.googleapis.com/sefaria-export/...`). The script
downloads the 37 files it needs from there, once.

## One-time setup

Install **Python 3**. No `pip install` and no git needed.

If you ran an earlier version, you can delete the old `Sefaria-Export/`
folder; nothing uses it any more.

## Running it

For building everything at once (the intended once-a-year workflow):

```
python3 build_parsha.py --year
```

Builds the next 55 weeks of parshiot (enough to cover a full annual cycle
with room to spare), each as its own page, plus an `index.html` with:
- a **"this week's parsha"** button at the top (computed client-side by
  comparing today's date against each page's date — works correctly however
  long ago you ran the script, as long as it's within the year you built), and
- a plain list of every parsha, for picking one manually.

Every individual page also gets a thin nav row: a link back to the index,
and ‹ previous / next › links to move between adjacent weeks — so once
you're on a page you don't need to go back to the index to move around.

First run downloads the Torah, Onkelos, Pseudo-Jonathan, Targum
Yerushalmi and Rashi files (Hebrew and English, about 14 MB) into
`./sefaria_texts/`. Later runs reuse them; delete that folder to force a
fresh download. If Sefaria moves a file again, the script looks up its new
location in Sefaria's `books.json` index. Output lands in `site/` — one
`.html` per parsha, plus `index.html`.

**Rate limiting:** pulling ~55 weeks hits Sefaria's calendar API 55 times.
Each successful lookup is cached to disk under `calendar_cache/` (a Shabbat's
parsha never changes, so this is safe to keep forever) — if a run fails
partway through, re-running only fetches what's still missing. If you get
HTTP 429 errors, the script retries automatically with increasing delays
(2s, 4s, 8s... capped at 30s, up to 8 attempts per date). If it still fails
immediately on a fresh run, Sefaria's cooldown window may be longer than a
few seconds — wait 10–15 minutes before trying again rather than re-running
repeatedly.

Other modes, if you want a single week instead of the whole year:
```
python3 build_parsha.py --list              # see what's coming up, with dates
python3 build_parsha.py --next              # build just this/next Shabbat's parsha
python3 build_parsha.py --date 2026-10-10   # build whatever week that date falls in
```
(These write a single file and don't touch `index.html` — only `--year`
builds/updates the index. The single page still gets its ‹ previous /
next › links. Any date in the week works; it is mapped to that Shabbat.)

## Reading one page offline (e.g. on a flight)

There's a ✈️ button on every page that shows a reminder, because there's no
way for a webpage's own code to trigger this — it has to go through your
browser: in **Chrome on Android**, open the page while you still have
signal, tap the **⋮ menu → Download**. Chrome saves a full offline copy you
can reopen later from the Downloads app, no connection needed. Do this
before you lose signal, not after.

This is deliberately not an automatic "works offline always" setup (you
said you don't need that routinely) — it's just making sure the one-page,
occasional case is easy to remember.

## What was tested (October 2026 revision)

- **Calendar parsing and holiday skipping** — run against the 55 real
  responses in `calendar_cache/` (Oct 10, 2026 – Oct 23, 2027). It builds
  the 50 weekly parshiot of that year and skips the 5 holiday Shabbatot
  (Pesach Chol haMoed, Shavuot II, Rosh Hashana I, Sukkot I, Shmini
  Atzeret).
- **Texts** — run against Sefaria's real export files. Every page has every
  verse of its parsha, all 7 aliyot in order, and Onkelos, Pseudo-Jonathan
  and Rashi wherever Sefaria has them (Bereshit: 146 verses, 98 verses with
  Rashi).
- **Pages** — loaded in Chrome: no script errors, Hebrew chapter numbers
  correct (e.g. י״א), aliyah buttons and ‹ › links work.
- **Not tested**: a live download from the storage bucket and a live call
  to Sefaria's calendar API from this exact script. The URLs are the ones
  Sefaria documents; `--list` and then `--next` are quick first checks.

## Known limitations, by design

- **No "תרגום לעברית" (Hebrew paraphrase) toggle.** No data source for it —
  it would mean someone translating the Aramaic by hand, verse by verse.
  English (Sefaria's own translations) is shown for Onkelos,
  Pseudo-Jonathan, Targum Yerushalmi and Rashi.
- **Shabbatot that fall on a holiday** (Sefaria lists the festival reading,
  e.g. "Sukkot I", in place of the weekly parsha) are skipped, and the run
  ends with a list of the skipped weeks. The weekly parshiot are only
  recognized by their Sefaria names (listed in `REGULAR_PARSHIOT` at the
  top of the script); if Sefaria ever renames one, it shows up in that
  skipped list.
- **Diaspora schedule.** Sefaria's calendar defaults to the diaspora
  reading cycle, which differs from Israel's in some weeks.
- **Weeks where the 7 regular aliyot span more than one Torah book** aren't
  handled — the script prints a message and skips rather than guessing.
  This does not happen in the regular cycle.

## Hosting it on your phone — GitHub Pages

GitHub Pages can publish only a repository's root or its `docs/` folder,
so build into `docs/` when you plan to host there.

One-time setup:
1. Create a repo on GitHub.
2. In this project folder:
   ```
   python3 build_parsha.py --year --out docs
   git init
   git remote add origin <your-repo-url>
   git add docs/
   git commit -m "parsha pages"
   git push -u origin main
   ```
3. Repo settings → **Pages** → source: *Deploy from a branch*, branch
   `main`, folder `/docs`.

Each week after (optional — the `--year` build already has every week):
```
python3 build_parsha.py --next --out docs
git add docs/
git commit -m "weekly parsha"
git push
```

Open `https://<you>.github.io/<repo>/` on your phone; `index.html` opens on
this week's parsha button.

Prefer zero git commands? Drag the `site/` folder onto **netlify.com**'s
drop-zone for an instant live URL instead.
