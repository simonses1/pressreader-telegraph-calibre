#!/usr/bin/env python3
"""
Browser-backed PressReader textview exporter for Calibre experiments.

PressReader's textview route is a JavaScript app and may require a signed-in
session. This helper uses a persistent Chromium profile so credentials are
typed into the browser, not passed through this script.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse


DEFAULT_URL = "https://www.pressreader.com/uk/the-daily-telegraph/20260623/textview"
DEFAULT_PROFILE = "~/.pressreader-calibre-profile"
DEFAULT_ENV_FILE = ".env.pressreader"
PRESSREADER_API_ORIGIN = "https://ingress.pressreader.com"
# 4095 is the mask that returns paragraph bodies for authorised HotSpot sessions.
FULL_ARTICLE_FIELDS = 4095


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a PressReader textview issue with a real browser.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Optional KEY=VALUE env file for credentials. Default: .env.pressreader")
    parser.add_argument("--url", default=DEFAULT_URL, help="PressReader textview issue URL.")
    parser.add_argument("--out-dir", default="/tmp/pressreader-textview", help="Directory for exported HTML/manifest.")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE, help="Persistent Chromium profile directory.")
    parser.add_argument("--channel", default="chrome", help="Playwright Chromium channel, e.g. chrome or msedge.")
    parser.add_argument("--headless", action="store_true", help="Run browser headless. Login usually needs headful mode.")
    parser.add_argument("--login", action="store_true", help="Open PressReader and pause for interactive login.")
    parser.add_argument("--login-only", action="store_true", help="Open PressReader for login, then exit.")
    parser.add_argument("--auto-login", action="store_true", help="Attempt automated PressReader email/password sign-in.")
    parser.add_argument("--username", help="PressReader username/email for --auto-login.")
    parser.add_argument("--username-env", default="PRESSREADER_USERNAME", help="Environment variable containing the PressReader username.")
    parser.add_argument("--password-env", default="PRESSREADER_PASSWORD", help="Environment variable containing the PressReader password.")
    parser.add_argument("--password-stdin", action="store_true", help="Read the PressReader password from stdin.")
    parser.add_argument("--library-login", action="store_true", help="Attempt automated PressReader Library or Group sign-in.")
    parser.add_argument("--library-name", help="Library or Group search text/name. Defaults to PRESSREADER_LIBRARY_NAME.")
    parser.add_argument("--library-name-env", default="PRESSREADER_LIBRARY_NAME", help="Environment variable containing the library/group name.")
    parser.add_argument("--library-id-env", default="PRESSREADER_LIBRARY_ID", help="Environment variable containing the library card/id.")
    parser.add_argument("--library-pin-env", default="PRESSREADER_LIBRARY_PIN", help="Environment variable containing the library PIN/password.")
    parser.add_argument("--timeout-ms", type=int, default=60000, help="Navigation timeout.")
    parser.add_argument("--settle-ms", type=int, default=4000, help="Wait after page load for the JS app.")
    parser.add_argument("--scrolls", type=int, default=8, help="Scroll passes to trigger lazy-loaded text.")
    parser.add_argument("--scroll-wait-ms", type=int, default=600, help="Wait after each scroll pass.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum articles to export. 0 means no cap.")
    parser.add_argument("--image-scale", type=int, default=200, help="PressReader region image scale. 100 is low-res; 200 is a good Kindle default.")
    parser.add_argument("--cover-width", type=int, default=1000, help="PressReader front-page cover width in pixels.")
    parser.add_argument("--debug", action="store_true", help="Keep rendered page HTML, screenshot, and API JSON captures.")
    return parser.parse_args()


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Missing dependency: playwright. Install with:\n"
            "  python3 -m pip install playwright\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sync_playwright


def require_bs4():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    return BeautifulSoup


def load_env_file(path: str) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return
    loaded = 0
    for line_number, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env file line {line_number} in {env_path}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise ValueError(f"Invalid env file key on line {line_number} in {env_path}: {key!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        print(f"Loaded {loaded} PressReader env values from {env_path}", file=sys.stderr)


def slugify(value: str, fallback: str = "article") -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return value[:80] or fallback


def text_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]


def normalise_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xad", "").replace("\u200b", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_title(value: str) -> str:
    value = normalise_text(value)
    value = re.sub(r"^Article\s+", "", value, flags=re.I)
    return value


def looks_like_article_text(text: str) -> bool:
    text = normalise_text(text)
    if len(text) < 350:
        return False
    words = text.split()
    if len(words) < 70:
        return False
    return bool(re.search(r"[.!?][\"')\]]?\s+[A-Z0-9]", text))


def title_from_text(text: str, index: int) -> str:
    lines = [normalise_text(x) for x in re.split(r"[\r\n]+", text or "") if normalise_text(x)]
    for line in lines[:8]:
        if 8 <= len(line) <= 160 and len(line.split()) <= 18:
            return line
    first = normalise_text(text)[:80].strip()
    return first or f"PressReader Article {index}"


def split_paragraphs(text: str) -> list[str]:
    raw = [normalise_text(x) for x in re.split(r"(?:\n\s*){2,}|[\r\n]+", text or "")]
    paras = []
    seen = set()
    for paragraph in raw:
        if len(paragraph) < 20:
            continue
        key = paragraph.lower()
        if key in seen:
            continue
        seen.add(key)
        paras.append(paragraph)
    if len(paras) <= 1:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalise_text(text))
        paras = []
        buf = []
        for sentence in sentences:
            buf.append(sentence)
            if sum(len(x) for x in buf) > 350:
                paras.append(" ".join(buf))
                buf = []
        if buf:
            paras.append(" ".join(buf))
    return paras


def image_urls_from_article(article: dict, image_scale: int = 200) -> list[str]:
    urls = []
    seen = set()
    image_scale = max(50, min(400, int(image_scale or 200)))
    for image in article.get("images") or []:
        if not isinstance(image, dict):
            continue
        image_id = image.get("id") or image.get("Id") or image.get("key") or image.get("Key")
        if not image_id:
            continue
        url = f"https://t.prcdn.co/img?regionKey={quote(str(image_id), safe='')}&scale={image_scale}"
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def issue_key_from_responses(responses: list[dict]) -> str:
    for item in responses:
        url = item.get("url", "")
        match = re.search(r"(?:[?&]issue=|[?&]file=)(\d{16,})", url)
        if match:
            return match.group(1)
        data = item.get("json")
        if isinstance(data, dict):
            for key in ("id", "issue", "issueId", "issueKey"):
                value = data.get(key)
                if isinstance(value, (str, int)) and re.match(r"^\d{16,}$", str(value)):
                    return str(value)
    return ""


def cover_url_from_responses(responses: list[dict], cover_width: int = 1000) -> str:
    issue_key = issue_key_from_responses(responses)
    if not issue_key:
        return ""
    cover_width = max(400, min(2200, int(cover_width or 1000)))
    return f"https://t.prcdn.co/img?file={quote(issue_key, safe='')}&page=1&width={cover_width}&retina=2"


def paragraph_text(paragraph) -> str:
    if isinstance(paragraph, str):
        return normalise_text(paragraph)
    if not isinstance(paragraph, dict):
        return ""
    if paragraph.get("type") and paragraph.get("type") != "text":
        return ""
    for key in ("text", "body", "content", "value"):
        value = paragraph.get(key)
        if isinstance(value, str):
            return normalise_text(re.sub(r"<[^>]+>", " ", value))
    return ""


def article_paragraphs(article: dict) -> list[str]:
    paragraphs = []
    seen = set()
    for paragraph in article.get("paragraphs") or []:
        text = paragraph_text(paragraph)
        if len(text) < 20:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        paragraphs.append(text)
    return paragraphs


def article_html(
    title: str,
    paragraphs: list[str],
    source_url: str,
    byline: str = "",
    subtitle: str = "",
    section: str = "",
    published: str = "",
    images: list[str] | None = None,
) -> str:
    images = images or []
    parts = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\">",
        f"<title>{html.escape(title)}</title>",
        "<style>body{font-family:serif;line-height:1.35} h1{font-size:1.4em} "
        ".source,.meta{font-size:.8em;color:#666}.subtitle{font-weight:bold} "
        "figure{margin:1em 0} img{max-width:100%;height:auto}</style>",
        "</head><body>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class=\"meta\">{html.escape(' | '.join(x for x in (section, byline, published) if x))}</p>" if any((section, byline, published)) else "",
        f"<p class=\"subtitle\">{html.escape(subtitle)}</p>" if subtitle else "",
        f"<p class=\"source\">Source: {html.escape(source_url)}</p>",
    ]
    if images:
        parts.append(f"<figure><img src=\"{html.escape(images[0])}\" alt=\"{html.escape(title)}\"></figure>")
    for index, paragraph in enumerate(paragraphs):
        parts.append(f"<p>{html.escape(paragraph)}</p>")
        if index == 1:
            for image_url in images[1:]:
                parts.append(f"<figure><img src=\"{html.escape(image_url)}\" alt=\"\"></figure>")
    parts.append("</body></html>")
    return "\n".join(part for part in parts if part)


def save_debug(page, out_dir: Path, responses: list[dict]) -> None:
    debug_dir = out_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "rendered.html").write_text(page.content(), encoding="utf-8")
    try:
        page.screenshot(path=str(debug_dir / "rendered.png"), full_page=True)
    except Exception:
        pass
    (debug_dir / "service-responses.json").write_text(json.dumps(responses, indent=2), encoding="utf-8")


def read_login_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return os.environ.get(args.password_env or "", "")


def read_login_username(args: argparse.Namespace) -> str:
    return args.username or os.environ.get(args.username_env or "", "")


def read_library_name(args: argparse.Namespace) -> str:
    return args.library_name or os.environ.get(args.library_name_env or "", "")


def read_library_id(args: argparse.Namespace) -> str:
    return os.environ.get(args.library_id_env or "", "")


def read_library_pin(args: argparse.Namespace) -> str:
    return os.environ.get(args.library_pin_env or "", "")


def dismiss_cookie_dialog(page) -> None:
    for selector in (
        "#CybotCookiebotDialogBodyButtonDecline",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "button:has-text(\"Deny\")",
        "button:has-text(\"Allow all\")",
    ):
        try:
            page.wait_for_selector(selector, timeout=2500)
            locator = page.locator(selector)
            if locator.count():
                locator.first.click(timeout=3000, force=True)
                page.wait_for_timeout(750)
                return
        except Exception:
            continue


def visible_login_error(page) -> str:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
    interesting = []
    for line in re.split(r"[\r\n]+", text):
        line = normalise_text(line)
        if not line:
            continue
        if re.search(r"invalid|incorrect|password|captcha|verification|verify|locked|try again|error", line, re.I):
            interesting.append(line)
    return " | ".join(interesting[:4])


def perform_auto_login(page, username: str, password: str) -> None:
    if not username:
        raise ValueError("PressReader --auto-login requires --username")
    if not password:
        raise ValueError("PressReader --auto-login requires a password via --password-stdin or --password-env")
    dismiss_cookie_dialog(page)
    try:
        page.wait_for_selector(
            '[data-testid="loginButton"], input[type="email"], input[placeholder*="Email"], [class*="account"], [class*="profile"]',
            timeout=20000,
        )
    except Exception:
        error = visible_login_error(page)
        raise ValueError(f"PressReader login controls did not load. {error or 'No login/account selector appeared.'}")
    login_button = page.locator('[data-testid="loginButton"]')
    if not login_button.count():
        print("PressReader login button not present; assuming browser profile is already signed in.", file=sys.stderr)
        return
    login_button.first.click(timeout=10000)
    page.wait_for_selector('input[type="email"], input[placeholder*="Email"]', timeout=15000)
    page.locator('input[type="email"], input[placeholder*="Email"]').last.fill(username)
    page.locator('input[type="password"], input[placeholder*="Password"]').last.fill(password)
    submit = page.locator('a:has-text("Sign in"), button:has-text("Sign in"), input[type="submit"]').last
    submit.click(timeout=10000)
    try:
        page.wait_for_function(
            """
            () => !document.querySelector('[data-testid="loginButton"]') ||
                  !document.querySelector('input[type="password"]')
            """,
            timeout=20000,
        )
    except Exception:
        error = visible_login_error(page)
        raise ValueError(f"PressReader automated sign-in did not complete. {error or 'Login form was still visible.'}")
    page.wait_for_timeout(2500)
    if page.locator('input[type="password"]').count():
        error = visible_login_error(page)
        raise ValueError(f"PressReader automated sign-in failed. {error or 'Password field remained visible.'}")
    print("PressReader automated sign-in completed.", file=sys.stderr)


def first_visible(locator):
    for index in range(locator.count()):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def visible_text(locator) -> str:
    try:
        return normalise_text(locator.inner_text(timeout=1000))
    except Exception:
        return ""


def open_login_dialog(page) -> None:
    dismiss_cookie_dialog(page)
    try:
        login = first_visible(page.locator('[data-testid="loginButton"], button:has-text("Sign in")'))
    except Exception:
        login = None
    if not login:
        if page.locator('a:has-text("Library or Group"), button:has-text("Library or Group")').count():
            return
        raise ValueError(
            "PressReader login button was not visible. Use a fresh --profile-dir or sign out of the existing profile "
            "before running --library-login."
        )
    login.click(timeout=10000)
    page.wait_for_selector('input[type="email"], a:has-text("Library or Group"), button:has-text("Library or Group")', timeout=15000)


def has_hotspot_session(page) -> bool:
    try:
        if first_visible(page.locator("button.btn-hotspot.on, .btn-hotspot.on")):
            return True
    except Exception:
        pass
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False
    return bool(re.search(r"This service is brought to you by|complimentary access|Hotspot", text, re.I))


def has_signed_in_session(page) -> bool:
    try:
        if first_visible(page.locator('[data-testid="myProfileButton"], .btn-account, button[aria-label="My Profile"]')):
            return True
    except Exception:
        pass
    return False


def library_search_query(library_name: str) -> str:
    # PressReader lists the UK service as "Library - Public - Surrey Library (Offsite)".
    if re.search(r"\bSurrey\b", library_name, re.I):
        return "Surrey"
    return library_name


def select_library_result(page, library_name: str) -> None:
    query = library_search_query(library_name)
    search = first_visible(page.locator('input[placeholder="Search Libraries and Groups"], input[aria-label="Search Libraries and Groups"]'))
    if not search:
        raise ValueError("PressReader library search field did not appear.")
    search.fill(query)
    page.wait_for_timeout(3000)

    results = page.locator("a.fli")
    if not results.count():
        raise ValueError(f"PressReader returned no library results for {query!r}.")

    preferred_patterns = []
    if re.search(r"\bSurrey\b", library_name, re.I):
        preferred_patterns = [
            r"Library\s*-\s*Public\s*-\s*Surrey Library\b.*United Kingdom",
            r"Surrey Library\b.*United Kingdom",
            r"Surrey Library\b",
        ]
    else:
        preferred_patterns = [re.escape(library_name)]

    fallback = None
    visible_results = []
    for index in range(results.count()):
        result = results.nth(index)
        try:
            if not result.is_visible():
                continue
        except Exception:
            continue
        text = visible_text(result)
        if not text:
            continue
        visible_results.append(text)
        if fallback is None:
            fallback = result
        for pattern in preferred_patterns:
            if re.search(pattern, text, re.I):
                result.click(timeout=10000)
                page.wait_for_timeout(1500)
                print(f"Selected PressReader library/group: {text.splitlines()[0][:120]}", file=sys.stderr)
                return

    if not fallback:
        raise ValueError(f"PressReader returned no visible library results for {query!r}.")
    fallback_text = visible_text(fallback)
    fallback.click(timeout=10000)
    page.wait_for_timeout(1500)
    print(f"Selected first PressReader library/group result: {fallback_text.splitlines()[0][:120]}", file=sys.stderr)


def perform_library_login(page, library_name: str, library_id: str, library_pin: str) -> None:
    if not library_name:
        raise ValueError("PressReader --library-login requires --library-name or PRESSREADER_LIBRARY_NAME")
    if not library_id:
        raise ValueError("PressReader --library-login requires a library id via PRESSREADER_LIBRARY_ID")
    if not library_pin:
        raise ValueError("PressReader --library-login requires a library PIN via PRESSREADER_LIBRARY_PIN")

    page.wait_for_timeout(3000)
    if has_hotspot_session(page):
        print("PressReader library/HotSpot session already active.", file=sys.stderr)
        return
    if has_signed_in_session(page) and not first_visible(page.locator('[data-testid="loginButton"], button:has-text("Sign in")')):
        print("PressReader signed-in session already active; continuing to verify full-text access.", file=sys.stderr)
        return

    open_login_dialog(page)
    library_link = first_visible(page.locator('a:has-text("Library or Group"), button:has-text("Library or Group")'))
    if not library_link:
        raise ValueError("PressReader Library or Group sign-in option did not appear.")
    library_link.click(timeout=10000)
    page.wait_for_selector('input[placeholder="Search Libraries and Groups"], input[aria-label="Search Libraries and Groups"]', timeout=15000)
    select_library_result(page, library_name)

    page.wait_for_selector('input[placeholder="Required"], input.control-input', timeout=15000)
    fields = []
    controls = page.locator('input[placeholder="Required"], input.control-input')
    for index in range(controls.count()):
        control = controls.nth(index)
        try:
            if control.is_visible():
                fields.append(control)
        except Exception:
            continue
    if len(fields) < 2:
        raise ValueError("PressReader library card/PIN fields did not appear after selecting the library.")

    fields[0].fill(library_id)
    fields[1].fill(library_pin)

    agree = first_visible(page.locator('label:has-text("I agree"), span:has-text("I agree")'))
    if agree:
        agree.click(timeout=5000, force=True)
    else:
        checkbox = first_visible(page.locator('input[type="checkbox"]'))
        if checkbox:
            checkbox.check(timeout=5000, force=True)

    submit = first_visible(page.locator('button[type="submit"]:has-text("Log In"), button:has-text("Log In")'))
    if not submit:
        raise ValueError("PressReader library Log In button did not appear.")
    submit.click(timeout=10000, force=True)
    try:
        page.wait_for_function(
            """
            () => !document.body.innerText.includes('Library or Group Sign In') &&
                  !document.body.innerText.includes('Library card number')
            """,
            timeout=30000,
        )
    except Exception:
        error = visible_login_error(page)
        raise ValueError(f"PressReader library sign-in did not complete. {error or 'Library form was still visible.'}")
    page.wait_for_timeout(3000)
    print("PressReader library sign-in completed.", file=sys.stderr)


def extract_dom_candidates(page) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => {
            const bad = /cookie|modal|dialog|menu|toolbar|header|footer|nav|advert|subscribe|login|search/i;
            const selectors = [
                'article[data-articleid]',
                'article',
                '[role="article"]',
                '[data-article-id]'
            ];
            const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
            const out = [];
            for (const el of nodes) {
                const label = [el.className || '', el.id || '', el.getAttribute('data-article-id') || '', el.getAttribute('data-articleid') || ''].join(' ');
                if (bad.test(label)) continue;
                const text = (el.innerText || '').trim();
                if (!text || text.length < 250) continue;
                const heading = el.querySelector('h1,h2,h3,[class*="title"],[class*="Title"],[class*="headline"],[class*="Headline"]');
                out.push({
                    title: heading ? (heading.innerText || '').trim() : '',
                    text,
                    id: el.getAttribute('data-article-id') || el.getAttribute('data-articleid') || el.id || ''
                });
            }
            return out;
        }
        """
    )


def extract_issue_article_refs(responses: list[dict]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen = set()

    def add_ref(article: dict, page: dict | None = None) -> None:
        article_id = article.get("Id") or article.get("ArticleId") or article.get("RootArticleId") or article.get("id")
        if not article_id:
            return
        article_id = str(article_id)
        if article_id in seen:
            return
        seen.add(article_id)
        page = page or {}
        refs.append(
            {
                "id": article_id,
                "title": clean_title(article.get("Title") or article.get("title") or ""),
                "byline": normalise_text(article.get("Byline") or article.get("author") or ""),
                "section": normalise_text(page.get("UnhyphenatedSectionName") or page.get("SectionName") or article.get("SectionName") or ""),
                "page": str(page.get("PageNumber") or article.get("Page") or article.get("PageNumber") or ""),
                "short": normalise_text(article.get("Text") or article.get("shortContent") or article.get("Subtitle") or ""),
            }
        )

    for item in responses:
        data = item.get("json")
        url = item.get("url", "")
        if "/services/toc/" in url and isinstance(data, dict):
            for page in data.get("Pages") or []:
                if not isinstance(page, dict):
                    continue
                for article in page.get("Articles") or []:
                    if isinstance(article, dict):
                        add_ref(article, page)
        elif "pagesMetadata" in url and isinstance(data, list):
            for page in data:
                if not isinstance(page, dict):
                    continue
                for article in page.get("Articles") or []:
                    if isinstance(article, dict):
                        add_ref(article, page)
    return refs


def extract_auth_token(responses: list[dict]) -> str:
    for item in reversed(responses):
        if "authentication/v1/initialize" not in item.get("url", ""):
            continue
        data = item.get("json")
        if isinstance(data, dict) and isinstance(data.get("bearerToken"), str):
            return data["bearerToken"]
    return ""


def api_origin_from_responses(responses: list[dict]) -> str:
    for item in responses:
        url = item.get("url", "")
        if "/services/" not in url:
            continue
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return PRESSREADER_API_ORIGIN


def fetch_full_article_candidates(page, refs: list[dict[str, str]], responses: list[dict], limit: int, image_scale: int) -> tuple[list[dict], list[str]]:
    token = extract_auth_token(responses)
    origin = api_origin_from_responses(responses)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    candidates = []
    errors = []
    selected_refs = refs[:limit] if limit else refs
    for ref in selected_refs:
        article_id = ref["id"]
        detail_url = (
            f"{origin}/services/v1/articles/{quote(article_id, safe='')}/"
            f"?articleFields={FULL_ARTICLE_FIELDS}&isHyphenated=false&fullBody=true"
        )
        try:
            response = page.context.request.get(detail_url, headers=headers, timeout=30000)
        except Exception as err:
            errors.append(f"{article_id}: detail request failed: {err}")
            continue
        if not response.ok:
            errors.append(f"{article_id}: detail request returned HTTP {response.status}")
            continue
        try:
            article = response.json()
        except Exception as err:
            errors.append(f"{article_id}: detail response was not JSON: {err}")
            continue
        if not isinstance(article, dict):
            errors.append(f"{article_id}: detail response was {type(article).__name__}, not an object")
            continue
        paragraphs = article_paragraphs(article)
        if not paragraphs:
            errors.append(
                "%s: no full paragraphs returned (access=%s, availableFields=%s, textLength=%s, shortContentLen=%s)"
                % (
                    article_id,
                    article.get("access"),
                    article.get("availableFields"),
                    article.get("textLength"),
                    len(article.get("shortContent") or ""),
                )
            )
            continue
        title = clean_title(article.get("title") or ref.get("title") or "")
        subtitle = normalise_text(article.get("subtitle") or "")
        byline = normalise_text(article.get("author") or ref.get("byline") or "")
        issue = article.get("issue") if isinstance(article.get("issue"), dict) else {}
        section = ref.get("section") or normalise_text(issue.get("sectionName") or "")
        candidates.append(
            {
                "id": article_id,
                "title": title,
                "subtitle": subtitle,
                "byline": byline,
                "section": section,
                "published": normalise_text(article.get("date") or ""),
                "paragraphs": paragraphs,
                "images": image_urls_from_article(article, image_scale),
                "text": "\n\n".join(paragraphs),
                "detail_url": detail_url,
            }
        )
    return candidates, errors


def extract_service_candidates(responses: list[dict], image_scale: int = 200) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []

    def walk(node):
        if isinstance(node, dict):
            paragraphs = article_paragraphs(node)
            if paragraphs:
                candidates.append(
                    {
                        "title": clean_title(node.get("title") or node.get("Title") or ""),
                        "text": "\n\n".join(paragraphs),
                        "paragraphs": paragraphs,
                        "id": str(node.get("Id") or node.get("id") or ""),
                        "byline": normalise_text(node.get("author") or node.get("Byline") or ""),
                        "subtitle": normalise_text(node.get("subtitle") or ""),
                        "images": image_urls_from_article(node, image_scale),
                    }
                )
                return
            keys = {str(k).lower(): k for k in node.keys()}
            title = ""
            text = ""
            for name in ("title", "headline", "heading", "name"):
                if name in keys and isinstance(node[keys[name]], str):
                    title = node[keys[name]]
                    break
            chunks = []
            for name in ("body", "text", "content", "articletext", "fulltext", "description"):
                if name in keys and isinstance(node[keys[name]], str):
                    chunks.append(node[keys[name]])
            if chunks:
                text = "\n\n".join(chunks)
            if looks_like_article_text(text):
                candidates.append({"title": normalise_text(title), "text": text, "id": str(node.get("Id") or node.get("id") or "")})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for item in responses:
        data = item.get("json")
        if data is not None:
            walk(data)
    return candidates


def dedupe_candidates(candidates: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    unique = []
    seen = set()
    for candidate in candidates:
        text = candidate.get("text") or ""
        if not looks_like_article_text(text):
            continue
        key = text_hash(normalise_text(text)[:2000])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if limit and len(unique) >= limit:
            break
    return unique


def export_articles(candidates: list[dict[str, str]], out_dir: Path, source_url: str) -> list[dict]:
    articles_dir = out_dir / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    articles = []
    for index, candidate in enumerate(candidates, 1):
        text = candidate.get("text") or ""
        title = normalise_text(candidate.get("title") or "") or title_from_text(text, index)
        paragraphs = candidate.get("paragraphs") or split_paragraphs(text)
        if not paragraphs:
            continue
        name = f"{index:03d}-{slugify(title)}-{text_hash(text)}.html"
        path = articles_dir / name
        path.write_text(
            article_html(
                title,
                paragraphs,
                source_url,
                byline=candidate.get("byline") or "",
                subtitle=candidate.get("subtitle") or "",
                section=candidate.get("section") or "",
                published=candidate.get("published") or "",
                images=candidate.get("images") or [],
            ),
            encoding="utf-8",
        )
        articles.append({
            "title": title,
            "url": f"{source_url}#article-{index}",
            "html": str(path.resolve()),
            "base_url": path.resolve().as_uri(),
            "paragraphs": len(paragraphs),
            "source": "pressreader-textview",
            "pressreader_id": candidate.get("id") or "",
            "section": candidate.get("section") or "",
            "byline": candidate.get("byline") or "",
            "published": candidate.get("published") or "",
        })
    return articles


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    if args.login_only and not (args.auto_login or args.library_login):
        args.login = True

    sync_playwright = require_playwright()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    responses: list[dict] = []

    def capture_response(response):
        url = response.url
        if "pressreader" not in url and "prcdn" not in url:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower() and "/services/" not in url:
            return
        try:
            body = response.text()
        except Exception:
            return
        item = {"url": url, "status": response.status, "content_type": content_type, "text": body[:200000]}
        try:
            item["json"] = json.loads(body)
            item.pop("text", None)
        except Exception:
            pass
        responses.append(item)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel=args.channel,
            headless=args.headless,
            viewport={"width": 1440, "height": 1400},
            locale="en-GB",
        )
        page = context.new_page()
        page.on("response", capture_response)
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            if args.auto_login:
                perform_auto_login(page, read_login_username(args), read_login_password(args))
                if args.login_only and not args.library_login:
                    return 0
                page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            if args.library_login:
                perform_library_login(page, read_library_name(args), read_library_id(args), read_library_pin(args))
                if args.login_only:
                    return 0
                page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            if args.login:
                print("Log in/complete PressReader access, then press Enter here to continue.", file=sys.stderr)
                input()
                if args.login_only:
                    return 0
                page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(max(0, args.settle_ms))
            for _ in range(max(0, args.scrolls)):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(max(0, args.scroll_wait_ms))
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)

            article_refs = extract_issue_article_refs(responses)
            detail_errors = []
            if article_refs:
                print(f"Found {len(article_refs)} PressReader issue articles in TOC order.", file=sys.stderr)
                full_candidates, detail_errors = fetch_full_article_candidates(page, article_refs, responses, args.limit, args.image_scale)
                candidates = dedupe_candidates(full_candidates, args.limit)
            else:
                service_candidates = extract_service_candidates(responses, args.image_scale)
                dom_candidates = extract_dom_candidates(page) if not service_candidates else []
                candidates = dedupe_candidates(service_candidates + dom_candidates, args.limit)
            if args.debug or not candidates:
                save_debug(page, out_dir, responses)
            articles = export_articles(candidates, out_dir, args.url)
        finally:
            context.close()

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": args.url,
        "title": "PressReader TextView",
        "cover_url": cover_url_from_responses(responses, args.cover_width),
        "requested_count": len(candidates) if "candidates" in locals() else 0,
        "fetched_count": len(articles) if "articles" in locals() else 0,
        "toc_count": len(article_refs) if "article_refs" in locals() else 0,
        "articles": articles if "articles" in locals() else [],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Exported {manifest['fetched_count']} PressReader textview articles.", file=sys.stderr)
    if manifest["fetched_count"] == 0:
        if "detail_errors" in locals() and detail_errors:
            print("Full article extraction failed. First detail errors:", file=sys.stderr)
            for error in detail_errors[:8]:
                print(f"  - {error}", file=sys.stderr)
        print(f"No article text was extracted. Debug artifacts: {out_dir / 'debug'}", file=sys.stderr)
    print(str(manifest_path))
    return 0 if manifest["fetched_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
