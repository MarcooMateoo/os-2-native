"""Shared stdlib-only helpers for OutSystems ServiceCenter scripts."""

from __future__ import annotations

import html
import http.cookiejar
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import re
from dataclasses import dataclass
from typing import Mapping

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ServiceCenterError(RuntimeError):
    """Categorized ServiceCenter failure safe to print in logs."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    """Response text plus the final URL after redirects."""

    body: str
    final_url: str


def normalize_base_url(url: str) -> str:
    """Return ServiceCenter base URL without a trailing slash."""
    return url.rstrip("/")


def build_opener(*, verify_ssl: bool = True) -> urllib.request.OpenerDirector:
    """Build a urllib opener with cookies and configurable SSL verification."""
    cookie_jar = http.cookiejar.CookieJar()
    if verify_ssl:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar),
        urllib.request.HTTPSHandler(context=ctx),
    )


def get_cookie_jar(opener: urllib.request.OpenerDirector) -> http.cookiejar.CookieJar:
    """Extract the cookie jar from an opener."""
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.HTTPCookieProcessor):
            return handler.cookiejar
    raise ServiceCenterError("request_failed", "No cookie processor found")


def _request(
    url: str,
    data: bytes | None,
    *,
    referer: str | None,
    accept: str | None = None,
) -> urllib.request.Request:
    req = urllib.request.Request(url, data=data)
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if accept:
        req.add_header("Accept", accept)
    if referer:
        req.add_header("Referer", referer)
    return req


def fetch(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: bytes | None = None,
    *,
    referer: str | None = None,
    timeout: int = 60,
    accept: str | None = None,
) -> tuple[str, str]:
    """Fetch a text URL and return ``(body_text, final_url)``."""
    req = _request(url, data, referer=referer, accept=accept)
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return body, resp.url


def fetch_binary_post(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: bytes,
    *,
    referer: str | None = None,
    timeout: int = 60,
) -> tuple[bytes, dict[str, str]]:
    """POST a form and return raw response bytes plus lowercase headers."""
    req = _request(url, data, referer=referer)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return raw, headers


def fetch_with_retries(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: bytes | None = None,
    *,
    referer: str | None = None,
    timeout: int = 60,
    retries: int = 0,
    delay: float = 0.5,
    accept: str | None = None,
) -> tuple[str, str, int]:
    """Fetch with retry/backoff for transient request errors.

    Returns ``(body_text, final_url, retry_count)``. The raised error message
    intentionally excludes form values and response bodies.
    """
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            body, final_url = fetch(
                opener,
                url,
                data,
                referer=referer,
                timeout=timeout,
                accept=accept,
            )
            return body, final_url, attempt
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt >= attempts - 1:
                raise ServiceCenterError("request_failed", f"HTTP request failed with status {exc.code}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= attempts - 1:
                raise ServiceCenterError("request_failed", "ServiceCenter request failed") from exc
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(delay * (2 ** attempt))
    raise ServiceCenterError("request_failed", "ServiceCenter request failed") from last_error


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', attrs, re.IGNORECASE)
    if not m:
        return None
    return html.unescape(m.group(2) or m.group(3) or m.group(4) or "")


def extract_hidden_fields(page_html: str) -> dict[str, str]:
    """Extract all hidden input fields from an HTML page."""
    fields: dict[str, str] = {}
    for m in re.finditer(r"<input\b([^>]+)>", page_html, re.IGNORECASE):
        attrs = m.group(1)
        if (_attr(attrs, "type") or "").lower() != "hidden":
            continue
        name = _attr(attrs, "name")
        if name:
            fields[name] = _attr(attrs, "value") or ""
    return fields


def find_input(
    page_html: str,
    input_type: str,
    *,
    class_pattern: str | None = None,
    value_pattern: str | None = None,
) -> str | None:
    """Find an input element name by type and optional class/value filters."""
    for m in re.finditer(r"<input\b([^>]+)>", page_html, re.IGNORECASE):
        attrs = m.group(1)
        if (_attr(attrs, "type") or "").lower() != input_type.lower():
            continue
        if class_pattern and class_pattern not in (_attr(attrs, "class") or ""):
            continue
        if value_pattern and value_pattern not in (_attr(attrs, "value") or ""):
            continue
        name = _attr(attrs, "name")
        if name:
            return name
    return None


def login(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    username: str,
    password: str,
) -> None:
    """Authenticate against ServiceCenter via the Login.aspx form POST."""
    base_url = normalize_base_url(base_url)
    login_url = f"{base_url}/ServiceCenter/Login.aspx"
    try:
        page_html, _ = fetch(opener, login_url)
    except Exception as exc:  # pragma: no cover - exact urllib subtype varies
        raise ServiceCenterError("request_failed", "Could not load ServiceCenter login page") from exc

    payload = extract_hidden_fields(page_html)

    user_field = find_input(page_html, "text", class_pattern="Mandatory")
    if not user_field:
        raise ServiceCenterError("login_failed", "Could not find username field on login page")

    pass_field = find_input(page_html, "password")
    if not pass_field:
        raise ServiceCenterError("login_failed", "Could not find password field on login page")

    login_btn = find_input(page_html, "submit", value_pattern="Log in")
    if login_btn:
        payload[login_btn] = "Log in"

    payload[user_field] = username
    payload[pass_field] = password

    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        fetch(opener, login_url, data=encoded, referer=login_url)
    except Exception as exc:  # pragma: no cover - exact urllib subtype varies
        raise ServiceCenterError("request_failed", "ServiceCenter login request failed") from exc

    cj = get_cookie_jar(opener)
    if not any(c.name == "ServiceCenter.sid" for c in cj):
        raise ServiceCenterError("login_failed", "Login failed: no ServiceCenter session cookie received")


def encode_form(payload: Mapping[str, str]) -> bytes:
    """URL-encode a form payload."""
    return urllib.parse.urlencode(payload).encode("utf-8")
