#!/usr/bin/env python3
"""
Publication-aware PressReader helper.

This wraps the browser-backed TextView exporter with publication/date handling
and can generate an importable Calibre recipe for a specific PressReader title.
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


PROJECT_DIR = Path(__file__).resolve().parent.parent
HELPER_SCRIPT = PROJECT_DIR / "scripts" / "pressreader_textview_prefetch.py"
MANIFEST_RECIPE = PROJECT_DIR / "recipes" / "pressreader_textview.recipe"
GENERIC_RECIPE = PROJECT_DIR / "recipes" / "pressreader_publication.recipe"
MACOS_EBOOK_CONVERT = "/Applications/calibre.app/Contents/MacOS/ebook-convert"


@dataclass(frozen=True)
class Publication:
    path: str
    issue_date: str
    title: str
    publisher: str
    category: str

    @property
    def slug(self) -> str:
        return slugify(self.path.split("/")[-1] or self.title, fallback="pressreader-publication")

    @property
    def textview_url(self) -> str:
        quoted_path = "/".join(quote(part, safe="") for part in self.path.split("/") if part)
        return f"https://www.pressreader.com/{quoted_path}/{self.issue_date}/textview"


def slugify(value: str, fallback: str = "pressreader") -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return value[:80] or fallback


def validate_issue_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return datetime.now().strftime("%Y%m%d")
    if not re.match(r"^\d{8}$", value):
        raise ValueError(f"Issue date must be YYYYMMDD, got: {value}")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as err:
        raise ValueError(f"Issue date is not a valid calendar date: {value}") from err
    return value


def normalise_publication_path(reference: str, country: str = "") -> str:
    reference = (reference or "").strip()
    country = (country or "").strip().strip("/")
    if not reference:
        return ""

    if "://" in reference:
        parsed = urlparse(reference)
        if not parsed.netloc.endswith("pressreader.com"):
            raise ValueError(f"Expected a pressreader.com URL, got: {reference}")
        path = parsed.path
    else:
        path = reference
        path = re.sub(r"^https?://(?:www\.)?pressreader\.com/", "", path)
        path = re.sub(r"^(?:www\.)?pressreader\.com/", "", path)
        path = path.split("?", 1)[0].split("#", 1)[0]

    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if parts and parts[-1].lower() == "textview":
        parts.pop()
    if parts and re.match(r"^\d{8}$", parts[-1]):
        parts.pop()
    if len(parts) == 1 and country:
        parts.insert(0, country)
    if not parts:
        return ""
    return "/".join(parts)


def title_from_path(publication_path: str) -> str:
    slug = (publication_path or "").strip("/").split("/")[-1]
    words = re.split(r"[-_\s]+", slug)
    small_words = {"and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "via"}
    rendered = []
    for index, word in enumerate(words):
        if not word:
            continue
        lower = word.lower()
        if index and lower in small_words:
            rendered.append(lower)
        elif len(word) <= 3 and word.isupper():
            rendered.append(word)
        else:
            rendered.append(lower.capitalize())
    return " ".join(rendered) or "PressReader Publication"


def resolve_publication(args: argparse.Namespace) -> Publication:
    reference = args.publication_path or args.publication_url or args.publication_ref or args.publication_slug or ""
    path = normalise_publication_path(reference, args.country)
    if not path:
        raise ValueError(
            "A PressReader publication is required. Use a URL, a path like uk/the-daily-telegraph, "
            "or --country uk --publication the-daily-telegraph."
        )
    issue_date = validate_issue_date(args.issue_date)
    title = args.publication_title or title_from_path(path)
    publisher = args.publisher or "PressReader"
    category = args.category or "newspaper, pressreader"
    return Publication(path=path, issue_date=issue_date, title=title, publisher=publisher, category=category)


def default_profile_dir(publication: Publication) -> str:
    return f"~/.pressreader-{publication.slug}-profile"


def default_out_dir(publication: Publication) -> str:
    return f"/tmp/pressreader-{publication.slug}-{publication.issue_date}"


def find_ebook_convert() -> str:
    if Path(MACOS_EBOOK_CONVERT).exists():
        return MACOS_EBOOK_CONVERT
    found = shutil.which("ebook-convert")
    if found:
        return found
    raise ValueError("Could not find ebook-convert. Install Calibre or pass --ebook-convert.")


def manifest_from_stdout(stdout: str, out_dir: str) -> str:
    for line in reversed((stdout or "").splitlines()):
        value = line.strip()
        if value.endswith("manifest.json"):
            return value
    return str((Path(out_dir).expanduser() / "manifest.json").resolve())


def run_command(cmd: list[str], dry_run: bool = False, capture_stdout: bool = False) -> subprocess.CompletedProcess[str]:
    print("+ " + shlex.join(cmd), file=sys.stderr)
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "")
    if capture_stdout:
        return subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=False)
    return subprocess.run(cmd, text=True, check=False)


def run_fetch(args: argparse.Namespace) -> int:
    publication = resolve_publication(args)
    env_file = str(Path(args.env_file).expanduser())
    out_dir = args.out_dir or default_out_dir(publication)
    profile_dir = args.profile_dir or default_profile_dir(publication)

    if args.login_only and args.auth_mode == "none":
        raise ValueError("--login-only requires --auth-mode library, account, or manual")
    if args.auth_mode == "manual" and args.headless:
        raise ValueError("--auth-mode manual needs --headed so the browser can show the login flow")

    cmd = [
        args.python,
        str(HELPER_SCRIPT),
        "--env-file",
        env_file,
        "--url",
        publication.textview_url,
        "--out-dir",
        out_dir,
        "--profile-dir",
        profile_dir,
        "--channel",
        args.channel,
        "--publication-title",
        publication.title,
        "--publisher",
        publication.publisher,
        "--category",
        publication.category,
        "--issue-date",
        publication.issue_date,
        "--image-scale",
        str(args.image_scale),
        "--cover-width",
        str(args.cover_width),
    ]
    if args.headless:
        cmd.append("--headless")
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.debug:
        cmd.append("--debug")
    if args.login_only:
        cmd.append("--login-only")

    if args.auth_mode == "library":
        cmd.append("--library-login")
    elif args.auth_mode == "account":
        cmd.append("--auto-login")
    elif args.auth_mode == "manual":
        cmd.append("--login")
    elif args.auth_mode != "none":
        raise ValueError(f"Unknown auth mode: {args.auth_mode}")

    completed = run_command(cmd, dry_run=args.dry_run, capture_stdout=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode:
        return completed.returncode

    manifest = manifest_from_stdout(completed.stdout or "", out_dir)
    if args.ebook_output:
        ebook_output = Path(args.ebook_output).expanduser()
        if not args.dry_run:
            ebook_output.parent.mkdir(parents=True, exist_ok=True)
        ebook_convert = args.ebook_convert or ("ebook-convert" if args.dry_run else find_ebook_convert())
        recipe = str(Path(args.manifest_recipe).expanduser())
        ebook_cmd = [
            ebook_convert,
            recipe,
            str(ebook_output),
            "--recipe-specific-option",
            f"manifest:{manifest}",
        ]
        ebook_completed = run_command(ebook_cmd, dry_run=args.dry_run)
        return ebook_completed.returncode
    return 0


def replace_defaults(template: str, defaults: dict[str, object]) -> str:
    lines = ["# BEGIN PUBLICATION DEFAULTS"]
    for key, value in defaults.items():
        lines.append(f"{key} = {value!r}")
    lines.append("# END PUBLICATION DEFAULTS")
    block = "\n".join(lines)
    updated, count = re.subn(
        r"# BEGIN PUBLICATION DEFAULTS\n.*?\n# END PUBLICATION DEFAULTS",
        block,
        template,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise ValueError(f"Could not find publication defaults block in {GENERIC_RECIPE}")
    return updated


def write_recipe(args: argparse.Namespace) -> int:
    publication = resolve_publication(args)
    output = Path(args.output or PROJECT_DIR / "recipes" / f"{publication.slug}_pressreader.recipe").expanduser()
    if output.exists() and not args.force:
        raise ValueError(f"Recipe already exists: {output}. Pass --force to overwrite.")
    if not GENERIC_RECIPE.exists():
        raise ValueError(f"Generic recipe template does not exist: {GENERIC_RECIPE}")

    defaults = {
        "DEFAULT_PUBLICATION_PATH": publication.path,
        "DEFAULT_PUBLICATION_TITLE": publication.title,
        "DEFAULT_PUBLISHER": publication.publisher,
        "DEFAULT_CATEGORY": publication.category,
        "DEFAULT_PROJECT_DIR": str(PROJECT_DIR),
        "DEFAULT_ENV_FILE": args.env_file,
        "DEFAULT_PROFILE_DIR": args.profile_dir or default_profile_dir(publication),
        "DEFAULT_AUTH_MODE": args.auth_mode,
        "DEFAULT_CHANNEL": args.channel,
        "DEFAULT_HEADLESS": "true" if args.headless else "false",
        "DEFAULT_IMAGE_SCALE": str(args.image_scale),
        "DEFAULT_COVER_WIDTH": str(args.cover_width),
    }
    content = replace_defaults(GENERIC_RECIPE.read_text(encoding="utf-8"), defaults)
    content = content.replace(
        "# vim:fileencoding=utf-8\n",
        "# vim:fileencoding=utf-8\n# Generated by scripts/pressreader_publication.py. Do not store credentials in this file.\n",
        1,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(str(output.resolve()))
    return 0


def add_publication_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("publication_ref", nargs="?", help="PressReader publication URL/path, e.g. uk/the-daily-telegraph.")
    parser.add_argument("--publication-url", help="PressReader publication or issue URL.")
    parser.add_argument("--publication-path", help="PressReader path, e.g. uk/the-daily-telegraph.")
    parser.add_argument("--country", help="Country/path prefix when --publication is only a slug, e.g. uk.")
    parser.add_argument("--publication", "--publication-slug", dest="publication_slug", help="Publication slug, e.g. the-daily-telegraph.")
    parser.add_argument("--date", "--issue-date", dest="issue_date", help="Issue date in YYYYMMDD. Defaults to today.")
    parser.add_argument("--title", dest="publication_title", help="Calibre publication title.")
    parser.add_argument("--publisher", help="Calibre publisher metadata. Defaults to PressReader.")
    parser.add_argument("--category", help="Calibre tags/category metadata. Defaults to newspaper, pressreader.")


def add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=str(PROJECT_DIR / ".env.pressreader"), help="Credential env file path.")
    parser.add_argument("--profile-dir", help="Persistent Chromium profile directory.")
    parser.add_argument("--auth-mode", choices=("library", "account", "manual", "none"), default="library", help="PressReader authentication mode.")
    parser.add_argument("--channel", default="chrome", help="Playwright Chromium channel.")
    parser.add_argument("--image-scale", type=int, default=200, help="PressReader article image scale.")
    parser.add_argument("--cover-width", type=int, default=1000, help="PressReader front-page cover width.")
    parser.set_defaults(headless=True)
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run browser headless. Default.")
    parser.add_argument("--headed", dest="headless", action="store_false", help="Show the browser window.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch or generate Calibre recipes for PressReader publications.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Prefetch a PressReader issue, optionally converting it with Calibre.")
    add_publication_args(fetch)
    add_runtime_args(fetch)
    fetch.add_argument("--python", default=sys.executable, help="Python executable with Playwright installed.")
    fetch.add_argument("--out-dir", help="Prefetch output directory. Defaults to /tmp/pressreader-<publication>-<date>.")
    fetch.add_argument("--limit", type=int, default=0, help="Maximum articles to fetch for testing.")
    fetch.add_argument("--debug", action="store_true", help="Keep rendered page and API debug artefacts.")
    fetch.add_argument("--login-only", action="store_true", help="Open/sign in and exit without exporting.")
    fetch.add_argument("--ebook-output", help="Optional EPUB/MOBI/AZW3 output path to build with ebook-convert.")
    fetch.add_argument("--ebook-convert", help="Path to ebook-convert. Defaults to Calibre.app or PATH.")
    fetch.add_argument("--manifest-recipe", default=str(MANIFEST_RECIPE), help="Manifest-only recipe used for --ebook-output.")
    fetch.add_argument("--dry-run", action="store_true", help="Print commands without running PressReader or Calibre.")
    fetch.set_defaults(func=run_fetch)

    recipe = subparsers.add_parser("recipe", help="Generate an importable Calibre recipe for one publication.")
    add_publication_args(recipe)
    add_runtime_args(recipe)
    recipe.add_argument("--output", help="Recipe output path. Defaults to recipes/<publication>_pressreader.recipe.")
    recipe.add_argument("--force", action="store_true", help="Overwrite an existing recipe.")
    recipe.set_defaults(func=write_recipe)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as err:
        print(f"pressreader-publication: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
