# Security Notes

This repository is designed to be public.

Do not commit:

- `.env.pressreader`
- browser profiles
- cookies
- generated EPUBs
- debug JSON captures

The Calibre recipe reads credentials from an env file at runtime and never needs credentials on the command line. Command-line arguments are visible to other local processes, so keep secrets in `.env.pressreader` or environment variables instead.

If a debug run is shared, inspect `debug/service-responses.json` first. PressReader responses can include bearer/session material.
