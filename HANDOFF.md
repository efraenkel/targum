# Handoff note: Shnayim Mikra / Targum reader (Oct 4, 2026)

## What this project is
`build_parsha.py` builds one web page per weekly parsha. Each page shows the
Hebrew text with Onkelos, Pseudo-Jonathan, Targum Yerushalmi and Rashi, and
English where Sefaria has it. The script also builds an `index.html` with a
"this week" button. The pages are published with GitHub Pages from the
`docs/` folder of https://github.com/efraenkel/targum.

## Current state
- The script works. A run on Oct 4, 2026 built 50 parsha pages, Bereshit
  (Oct 10, 2026) through Ha'Azinu (Oct 9, 2027), plus `docs/index.html`.
- All of `docs/` is committed and pushed (commit b807b2a, "rename site to docs
  for GitHub Pages"). The local and GitHub copies match.
- The old script is kept as `build_parsha_v1_backup.py`.

## Still to do
1. Turn on GitHub Pages: repo Settings → Pages → "Deploy from a branch",
   branch `main`, folder `/docs`. The site should then be at
   https://efraenkel.github.io/targum/. On a free GitHub account the repo
   must be public.
2. Open the site on the phone and check that the "this week" button works.
3. Optional:
   - Delete the unused `Sefaria-Export/` folder.
   - Add `sefaria_texts/` to `.gitignore` and remove it from the repo with
     `git rm -r --cached sefaria_texts`. The website does not need those
     14 MB.

## What was fixed (summary)
- Sefaria moved its texts from the GitHub repo Sefaria/Sefaria-Export to a
  public Google storage bucket in September 2026. The script now downloads
  the files it needs from `https://storage.googleapis.com/sefaria-export/`
  into `sefaria_texts/`, once, instead of using git.
- The Rashi path was wrong (`Commentary` instead of `Rishonim on Tanakh`), so
  Rashi had always been blank.
- Sefaria lists holiday readings ("Sukkot I", "Shavuot II") as the weekly
  parsha. The script now accepts only the 54 parsha names and skips the rest.
- Hebrew chapter numbers above 10 were wrong. Chapter 11 now shows י״א.
- Single-week builds keep their ‹ › links. The "this week" button uses local
  time. Rashi English is included. Network errors are retried.
- Full details are in `README.md` and in the comments in `build_parsha.py`.

## Things to know
- The calendar follows the diaspora schedule.
- `calendar_cache/` keeps Sefaria's answer for each Shabbat. It is safe to
  keep and is not in git.
- On this Windows PC, Claude's shell could not open the folder (the error
  blamed a September 2026 Windows update). Installing Windows updates should
  fix that.
- Instructions for rebuilding next year are in `NEXT_YEAR.md`.
