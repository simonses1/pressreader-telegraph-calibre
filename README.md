# PressReader Calibre Tools

Browser-backed tools for building Calibre/Kindle editions from PressReader TextView publications.

The code is now publication-aware: pass a PressReader publication path, issue URL, or country/slug pair, then either fetch an issue on demand or generate an importable Calibre recipe for repeated use.

## Files

- `scripts/pressreader_publication.py` - CLI for publication/date handling, on-demand pulls, EPUB conversion, and recipe generation.
- `recipes/pressreader_publication.recipe` - generic Calibre recipe for any PressReader publication.
- `scripts/pressreader_textview_prefetch.py` - Playwright helper that handles PressReader sign-in and TextView export.
- `recipes/pressreader_textview.recipe` - manifest-only Calibre recipe for debugging an already-prefetched issue.
- `recipes/telegraph_pressreader.recipe` - existing Telegraph-specific recipe kept for compatibility.
- `.env.pressreader.example` - credential template; copy it to `.env.pressreader`.

## Requirements

- macOS or Linux with Python 3.10+.
- Calibre CLI, especially `ebook-convert`.
- Playwright Python package.
- A browser Playwright can launch. The helper defaults to the Chrome channel because it behaves closest to a normal user browser session.

Install Python dependencies:

```sh
python3 -m pip install -r requirements.txt
```

If you do not already have Chrome available, install Playwright's Chromium browser and pass `--channel chromium`:

```sh
python3 -m playwright install chromium
```

## Credentials

Create a private env file from the template:

```sh
cp .env.pressreader.example .env.pressreader
chmod 600 .env.pressreader
```

Fill in your PressReader account and library credentials:

```sh
PRESSREADER_USERNAME=
PRESSREADER_PASSWORD=
PRESSREADER_LIBRARY_NAME=Surrey Libraries
PRESSREADER_LIBRARY_ID=
PRESSREADER_LIBRARY_PIN=
```

`.env.pressreader` is ignored by Git. Generated recipes only store paths and metadata, not credential values.

## Publication References

The generic tooling accepts any of these forms:

```sh
uk/the-daily-telegraph
https://www.pressreader.com/uk/the-daily-telegraph
https://www.pressreader.com/uk/the-daily-telegraph/20260623/textview
--country uk --publication the-daily-telegraph
```

Dates use PressReader's `YYYYMMDD` issue format. If omitted, the tools use today's date.

## Pull On Demand

Fetch a specific issue and build an EPUB:

```sh
python3 scripts/pressreader_publication.py fetch uk/the-daily-telegraph \
  --date 20260623 \
  --title "The Daily Telegraph" \
  --ebook-output outputs/telegraph-20260623.epub
```

Fetch today's issue without converting it:

```sh
python3 scripts/pressreader_publication.py fetch uk/the-daily-telegraph \
  --title "The Daily Telegraph" \
  --out-dir /tmp/pressreader-telegraph
```

Bootstrap or refresh a library login in a visible browser:

```sh
python3 scripts/pressreader_publication.py fetch uk/the-daily-telegraph \
  --title "The Daily Telegraph" \
  --auth-mode library \
  --headed \
  --login-only
```

Useful fetch options:

- `--date YYYYMMDD` - issue date. Defaults to today.
- `--auth-mode library|account|manual|none` - authentication flow. Defaults to `library`.
- `--profile-dir PATH` - persistent browser profile.
- `--out-dir PATH` - prefetch manifest and article HTML output.
- `--ebook-output PATH` - optional EPUB/MOBI/AZW3 path built with Calibre.
- `--limit N` - fetch only a few articles for testing.
- `--image-scale 200` - inline image quality. `100` is smaller; `300` is larger.
- `--cover-width 1000` - front-page cover width in pixels.
- `--debug` - keep rendered page and API debug artefacts.
- `--dry-run` - print the helper/Calibre commands without running them.

## Generate an Importable Recipe

Create a publication-specific Calibre recipe with defaults baked in:

```sh
python3 scripts/pressreader_publication.py recipe uk/the-daily-telegraph \
  --title "The Daily Telegraph" \
  --output recipes/daily_telegraph_pressreader.recipe
```

Use the generated recipe:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/daily_telegraph_pressreader.recipe \
  outputs/telegraph-$(date +%Y%m%d).epub
```

Run a specific issue with the generated recipe:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/daily_telegraph_pressreader.recipe \
  outputs/telegraph-20260623.epub \
  --recipe-specific-option issue_date:20260623
```

You can also use the generic recipe directly. Because Calibre copies recipes into a temp directory while running them, pass `project_dir` unless `PRESSREADER_CALIBRE_PROJECT_DIR` is already set:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/pressreader_publication.recipe \
  outputs/pressreader-20260623.epub \
  --recipe-specific-option project_dir:"$(pwd)" \
  --recipe-specific-option publication_path:uk/the-daily-telegraph \
  --recipe-specific-option title:"The Daily Telegraph" \
  --recipe-specific-option issue_date:20260623
```

## Debugging the Prefetch Step

Run the lower-level helper directly when you need to inspect TextView extraction:

```sh
python3 scripts/pressreader_textview_prefetch.py \
  --env-file .env.pressreader \
  --library-login \
  --headless \
  --profile-dir ~/.pressreader-surrey-library-profile \
  --out-dir /tmp/pressreader-telegraph-20260623 \
  --url https://www.pressreader.com/uk/the-daily-telegraph/20260623/textview \
  --publication-title "The Daily Telegraph" \
  --issue-date 20260623 \
  --debug
```

Then convert the manifest with the manifest-only recipe:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/pressreader_textview.recipe \
  outputs/telegraph-debug.epub \
  --recipe-specific-option manifest:/tmp/pressreader-telegraph-20260623/manifest.json
```

## Notes

- This project does not bypass PressReader access controls. It requires a valid PressReader/library session.
- The helper uses PressReader TextView and the authenticated article JSON endpoint. The field mask `4095` is required for paragraph bodies in authorised HotSpot sessions.
- Generated EPUBs, browser profiles, cookies, debug captures, and env files are deliberately ignored.
