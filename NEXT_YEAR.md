# Rebuilding the parsha pages for next year

**When:** In the week after Simchat Torah, before Shabbat Bereshit. In 2027,
that is Monday, October 25 through Friday, October 29. The current pages run
out after Ha'Azinu (October 9, 2027).

**Steps:**

1. Open the Ubuntu (WSL) terminal.

2. Go to the project folder:

   ```
   cd "/mnt/c/Users/fraenkel/Dropbox (Personal)/Personal/DivreiTorah/Shnayim Mikra v1 Targum project"
   ```

3. Delete last year's pages, then build the new year:

   ```
   rm docs/*.html
   python3 build_parsha.py --year --out docs
   ```

   This takes a few minutes. It should end with
   "Wrote 50 parsha pages + index.html" (the number may differ by a few).

4. Put the new pages on the website:

   ```
   git add -A docs
   git commit -m "parsha pages for next year"
   git push
   ```

5. Wait two minutes, then open https://efraenkel.github.io/targum/ on your
   phone and check that the button shows Bereshit.

**If something goes wrong:**
- "rate limited" messages are normal; the script waits and retries.
- If the script stops partway, run step 3's `python3` line again. It
  continues from where it stopped.
- If it says a text could not be downloaded, Sefaria may have changed
  something. Give the error message and `HANDOFF.md` to Claude.
