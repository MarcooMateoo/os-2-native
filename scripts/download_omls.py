#!/usr/bin/env python3
"""Download all OML module files from an OutSystems 11 ServiceCenter environment.

Uses only Python stdlib (no external dependencies).

Usage:
    python scripts/download_omls.py --url https://outsystems-dev.starhub.com \\
        --username admin --output-dir ./omls

    # List modules without downloading
    python scripts/download_omls.py --url https://outsystems-dev.starhub.com \\
        --username admin --dry-run

    # Download only modules matching a pattern
    python scripts/download_omls.py --url https://outsystems-dev.starhub.com \\
        --username admin --output-dir ./omls --filter "CBG_*"
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import re
import string
import sys
import urllib.request
from pathlib import Path

try:
    from os2native.shared.servicecenter import (
        build_opener as _build_opener,
        encode_form as _encode_form,
        extract_hidden_fields as _extract_hidden_fields,
        fetch as _fetch,
        fetch_binary_post as _fetch_binary_post,
        find_input as _find_input,
        login as _sc_login,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from os2native.shared.servicecenter import (
        build_opener as _build_opener,
        encode_form as _encode_form,
        extract_hidden_fields as _extract_hidden_fields,
        fetch as _fetch,
        fetch_binary_post as _fetch_binary_post,
        find_input as _find_input,
        login as _sc_login,
    )

try:
    from os2native.shared.credential_store import resolve_password as _resolve_password
except ImportError:
    _resolve_password = None


# ---------------------------------------------------------------------------
# ServiceCenter operations
# ---------------------------------------------------------------------------

def _login(opener: urllib.request.OpenerDirector, base_url: str,
           username: str, password: str) -> None:
    """Authenticate against ServiceCenter via the Login.aspx form POST.

    OutSystems 11 ServiceCenter uses ASP.NET-style form auth. On success
    the server sets a ``ServiceCenter.sid`` cookie (it does NOT redirect
    via HTTP 302 — it returns 200 with the cookie set).
    """
    _sc_login(opener, base_url, username, password)
    print(f"  Authenticated as '{username}'")


def _get_modules(opener: urllib.request.OpenerDirector,
                 base_url: str) -> list[dict[str, str]]:
    """Enumerate all modules (eSpaces) using two complementary sources.

    1. Applications_List.aspx  — visit each app's detail page to find its
       constituent eSpace modules.  Pagination is AJAX-based so we can only
       reliably scrape the first page of results.
    2. eSpaces_List.aspx       — POST the Filter form with single-letter
       prefixes (a-z, 0-9) to find modules by name.

    Results from both sources are merged and de-duplicated by eSpace ID.
    """
    modules: dict[str, str] = {}  # id -> name (deduped)

    # --- Source 1: Applications list (first page) --------------------------
    url = f"{base_url}/ServiceCenter/Applications_List.aspx"
    body, final_url = _fetch(opener, url)

    if "Login.aspx" in final_url:
        raise RuntimeError("Session expired — redirected to login")

    app_ids = list(dict.fromkeys(
        re.findall(r"Application_Edit\.aspx\?ApplicationId=(\d+)", body)
    ))

    if app_ids:
        for idx, app_id in enumerate(app_ids, 1):
            sys.stdout.write(
                f"\r  Scanning application {idx}/{len(app_ids)}...  "
            )
            sys.stdout.flush()
            app_url = (
                f"{base_url}/ServiceCenter/"
                f"Application_Edit.aspx?ApplicationId={app_id}"
            )
            app_body, _ = _fetch(opener, app_url)
            for m in re.finditer(
                r"eSpace_Edit\.aspx\?EspaceId=(\d+)[^>]*>([^<]+)",
                app_body,
            ):
                mid, mname = m.group(1), m.group(2).strip()
                if mid not in modules:
                    modules[mid] = mname
        print(f"\r  Applications: found {len(modules)} modules from "
              f"{len(app_ids)} apps")

    # --- Source 2: eSpaces_List filter search (letter-by-letter) -----------
    es_url = f"{base_url}/ServiceCenter/eSpaces_List.aspx"
    es_body, _ = _fetch(opener, es_url)

    # Extract hidden fields from the eSpaces_List page
    es_hidden = _extract_hidden_fields(es_body)

    # Find the filter input and button names dynamically
    filter_input = _find_input(es_body, "text", class_pattern=None)
    # Prefer the filter-area input (not the global search)
    for m in re.finditer(r"<input\b([^>]+)>", es_body, re.IGNORECASE):
        attrs = m.group(1)
        type_m = re.search(r'type="([^"]+)"', attrs)
        name_m = re.search(r'name="([^"]+)"', attrs)
        if (type_m and type_m.group(1).lower() == "text" and name_m
                and "Filters" in name_m.group(1)):
            filter_input = name_m.group(1)
            break

    filter_btn = _find_input(es_body, "submit", value_pattern="Filter")

    if filter_input and filter_btn:
        prefixes = list(string.ascii_lowercase) + list(string.digits) + ["_"]
        before = len(modules)
        for i, prefix in enumerate(prefixes, 1):
            sys.stdout.write(
                f"\r  Searching modules '{prefix}*' "
                f"({i}/{len(prefixes)})...  "
            )
            sys.stdout.flush()

            payload = dict(es_hidden)
            payload[filter_input] = prefix
            payload[filter_btn] = "Filter"
            encoded = _encode_form(payload)
            try:
                result, _ = _fetch(opener, es_url, data=encoded,
                                   referer=es_url)
                for m in re.finditer(
                    r"eSpace_Edit\.aspx\?EspaceId=(\d+)[^>]*>([^<]+)",
                    result,
                ):
                    mid, mname = m.group(1), m.group(2).strip()
                    if mid not in modules:
                        modules[mid] = mname
            except Exception:
                pass  # best-effort; some prefixes may return errors
        new = len(modules) - before
        print(f"\r  eSpaces search: found {new} additional modules"
              + " " * 20)

    return [{"id": mid, "name": mname} for mid, mname in modules.items()]


def _download_oml(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    module_id: str,
    module_name: str,
    output_dir: Path,
) -> Path:
    """Download a single OML via the 'Download Published Version' button.

    This works by:
    1. GET the eSpace_Edit page to obtain hidden form fields.
    2. Find the 'Download Published Version' submit button name.
    3. POST the form back — the server responds with an
       ``application/octet-stream`` body containing the OML.
    """
    edit_url = (
        f"{base_url}/ServiceCenter/eSpace_Edit.aspx?EspaceId={module_id}"
    )
    page_html, _ = _fetch(opener, edit_url)

    # Gather hidden fields
    payload = _extract_hidden_fields(page_html)

    # Find the download button
    dl_btn = _find_input(
        page_html, "submit", value_pattern="Download Published Version"
    )
    if not dl_btn:
        raise RuntimeError(
            f"No 'Download Published Version' button found for {module_name}"
        )
    payload[dl_btn] = "Download Published Version"

    encoded = _encode_form(payload)
    raw, headers = _fetch_binary_post(
        opener, edit_url, encoded, referer=edit_url
    )

    ct = headers.get("content-type", "")
    if "octet-stream" not in ct:
        raise RuntimeError(
            f"Unexpected response Content-Type '{ct}' — expected octet-stream"
        )

    # Determine filename from Content-Disposition, fall back to module name
    cd = headers.get("content-disposition", "")
    fname_match = re.search(r'filename="?([^";\n]+)"?', cd)
    if fname_match:
        filename = fname_match.group(1).strip()
    else:
        filename = f"{module_name}.oml"

    # Sanitize
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    if not filename.endswith(".oml"):
        filename += ".oml"

    output_path = output_dir / filename
    output_path.write_bytes(raw)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download OML files from an OutSystems 11 ServiceCenter",
    )
    parser.add_argument(
        "--url", required=True,
        help="Base URL (e.g. https://outsystems-dev.starhub.com)",
    )
    parser.add_argument("--username", required=True, help="ServiceCenter username")
    parser.add_argument(
        "--password", default=None,
        help="Password (if omitted, will prompt interactively)",
    )
    parser.add_argument(
        "--store-password", action="store_true",
        help="Store the password in macOS Keychain for future use",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("omls"),
        help="Directory to save OML files (default: ./omls)",
    )
    parser.add_argument(
        "--filter", default=None,
        help="Glob pattern to filter module names (e.g. 'CBG_*')",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List modules without downloading",
    )
    parser.add_argument(
        "--no-verify-ssl", action="store_true",
        help="Disable SSL certificate verification (for corporate/internal CAs)",
    )
    args = parser.parse_args(argv)

    base_url = args.url.rstrip("/")
    if _resolve_password is not None:
        password = _resolve_password(base_url, args.username, args.password, store=args.store_password)
    else:
        password = args.password or getpass.getpass(
            f"Password for {args.username}@{base_url}: "
        )

    # --- Authenticate -------------------------------------------------------
    print(f"Connecting to {base_url}...")
    opener = _build_opener(verify_ssl=not args.no_verify_ssl)
    try:
        _login(opener, base_url, args.username, password)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # --- List modules -------------------------------------------------------
    print("Fetching module list (enumerating applications)...")
    try:
        modules = _get_modules(opener, base_url)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not modules:
        print("No modules found. Check your permissions.", file=sys.stderr)
        return 1

    print(f"  Found {len(modules)} modules")

    # --- Apply filter -------------------------------------------------------
    if args.filter:
        modules = [m for m in modules if fnmatch.fnmatch(m["name"], args.filter)]
        print(f"  {len(modules)} modules match filter '{args.filter}'")

    if not modules:
        print("No modules match the filter.", file=sys.stderr)
        return 1

    modules.sort(key=lambda m: m["name"].lower())

    # --- Dry run: just list -------------------------------------------------
    if args.dry_run:
        print(f"\n{'Module Name':<50} {'ID':>8}")
        print("-" * 60)
        for m in modules:
            print(f"  {m['name']:<48} {m['id']:>8}")
        print(f"\n{len(modules)} modules total")
        return 0

    # --- Download -----------------------------------------------------------
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nDownloading {len(modules)} modules to {args.output_dir}/\n")

    downloaded = 0
    failed: list[tuple[str, str]] = []

    for i, mod in enumerate(modules, 1):
        name = mod["name"]
        mid = mod["id"]
        prefix = f"  [{i}/{len(modules)}]"

        try:
            path = _download_oml(opener, base_url, mid, name, args.output_dir)
            size_kb = path.stat().st_size / 1024
            print(f"{prefix} {name} -> {path.name} ({size_kb:.0f} KB)")
            downloaded += 1
        except Exception as e:
            print(f"{prefix} {name} FAILED: {e}", file=sys.stderr)
            failed.append((name, str(e)))

    # --- Summary ------------------------------------------------------------
    print(f"\nDone: {downloaded} downloaded, {len(failed)} failed")
    if failed:
        print("\nFailed modules:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
