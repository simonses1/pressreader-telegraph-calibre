# PressReader Telegraph Calibre

Calibre recipes and a browser-backed prefetch helper for building a Kindle/EPUB edition of **The Daily Telegraph** from PressReader TextView.

The bundled Telegraph recipe runs the helper automatically:

1. Loads credentials from `.env.pressreader`.
2. Opens PressReader with Playwright using a persistent Chromium profile.
3. Signs in through PressReader's **Library or Group** flow.
4. Fetches the issue TextView article bodies from PressReader's JSON services.
5. Writes a local manifest and local article HTML files.
6. Lets Calibre build the final ebook with inline images and a PressReader front-page cover.

## Files

- `recipes/telegraph_pressreader.recipe` - one-step Calibre recipe for The Daily Telegraph.
- `recipes/pressreader_textview.recipe` - manifest-only recipe for debugging an already-prefetched issue.
- `scripts/pressreader_textview_prefetch.py` - Playwright helper that handles PressReader sign-in and export.
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

If you do not already have Chrome available, install Playwright's Chromium browser and pass `--channel chromium` when using the helper directly:

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

`.env.pressreader` is ignored by Git.

## One-Step Calibre Use

Run today's Telegraph issue:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/telegraph_pressreader.recipe \
  outputs/telegraph-pressreader-$(date +%Y%m%d).epub
```

Run a specific issue:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/telegraph_pressreader.recipe \
  outputs/telegraph-pressreader-20260623.epub \
  --recipe-specific-option issue_date:20260623
```

Useful recipe-specific options:

- `issue_date:YYYYMMDD` - PressReader issue date. Defaults to today.
- `env_file:/path/to/.env.pressreader` - credential env file.
- `profile_dir:/path/to/browser-profile` - persistent browser profile.
- `out_dir:/tmp/pressreader-telegraph-YYYYMMDD` - prefetch output directory.
- `image_scale:200` - inline image quality. `100` is smaller; `300` is larger.
- `cover_width:1000` - front-page cover width in pixels.
- `limit:3` - fetch only a few articles for testing.
- `debug:true` - keep rendered page and API debug artefacts.

Example higher image quality:

```sh
/Applications/calibre.app/Contents/MacOS/ebook-convert \
  recipes/telegraph_pressreader.recipe \
  outputs/telegraph-pressreader-$(date +%Y%m%d)-hq.epub \
  --recipe-specific-option image_scale:300 \
  --recipe-specific-option cover_width:1264
```

## Debugging the Prefetch Step

Run the helper directly:

```sh
python3 scripts/pressreader_textview_prefetch.py \
  --env-file .env.pressreader \
  --library-login \
  --headless \
  --profile-dir ~/.pressreader-surrey-library-profile \
  --out-dir /tmp/pressreader-telegraph-20260623 \
  --url https://www.pressreader.com/uk/the-daily-telegraph/20260623/textview \
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
- The helper currently uses PressReader TextView and the authenticated article JSON endpoint. The field mask `4095` is required for paragraph bodies in authorised HotSpot sessions.
- Generated EPUBs, browser profiles, cookies, and env files are deliberately ignored.
