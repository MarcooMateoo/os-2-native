#!/usr/bin/env python3
"""Scrape runtime-effective Site Properties from OutSystems ServiceCenter.

The OML extraction remains the source of defaults in
``data/site-properties.json``. This script enriches a completed pass with
ServiceCenter-effective runtime values in
``data/site-properties-runtime.json`` and a pass-level
``runtime/site-properties-summary.json``.

Values may include visible secrets and are written only to JSON artifacts. This
script never prints property values to stdout/stderr.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from os2native.shared.servicecenter import (
        ServiceCenterError,
        build_opener,
        encode_form,
        extract_hidden_fields,
        fetch_with_retries,
        find_input,
        login,
        normalize_base_url,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from os2native.shared.servicecenter import (
        ServiceCenterError,
        build_opener,
        encode_form,
        extract_hidden_fields,
        fetch_with_retries,
        find_input,
        login,
        normalize_base_url,
    )

try:
    from os2native.shared.credential_store import (
        get_from_keychain as _get_from_keychain,
        store_in_keychain as _store_in_keychain,
    )
except ImportError:  # pragma: no cover - script can run outside editable install
    _get_from_keychain = None
    _store_in_keychain = None


SCHEMA_VERSION = "1.0"
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_NOT_APPLICABLE = "not_applicable"
FAILURE_CODES = {
    "login_failed",
    "permission_denied",
    "module_not_found",
    "tenant_parse_failed",
    "site_properties_not_loaded",
    "parse_failed",
    "request_failed",
    "missing_servicecenter_config",
}


@dataclass(frozen=True)
class ScraperConfig:
    project_root: Path
    pass_dir: Path
    oml_dir: Path | None
    servicecenter_url: str | None
    username: str | None
    password: str | None
    verify_ssl: bool
    delay: float
    retries: int
    environment: str | None


@dataclass(frozen=True)
class ModuleTarget:
    module_name: str
    module_dir: Path
    espace_id: str | None
    oml_properties: list[dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', attrs, re.IGNORECASE)
    if not m:
        return None
    return html.unescape(m.group(2) or m.group(3) or m.group(4) or "")


def _clean_text(value: str) -> str:
    value = html.unescape(value.replace("&nbsp;", " ").replace("\xa0", " "))
    return re.sub(r"\s+", " ", value).strip()


def _cell_text(fragment: str) -> str:
    """Extract visible/control text from a table cell.

    For inputs and textareas, ServiceCenter often stores the visible value in
    HTML attributes rather than text nodes. Preserve those values for artifacts,
    but callers must not print them.
    """
    replacements: list[str] = []
    for m in re.finditer(r"<textarea\b[^>]*>(.*?)</textarea>", fragment, re.IGNORECASE | re.DOTALL):
        replacements.append(_clean_text(m.group(1)))
    for m in re.finditer(r"<input\b([^>]+)>", fragment, re.IGNORECASE | re.DOTALL):
        attrs = m.group(1)
        input_type = (_attr(attrs, "type") or "").lower()
        if input_type in {"checkbox", "radio"}:
            replacements.append("true" if re.search(r"\bchecked\b", attrs, re.IGNORECASE) else "false")
        else:
            replacements.append(_attr(attrs, "value") or "")
    for m in re.finditer(r"<option\b([^>]*)>(.*?)</option>", fragment, re.IGNORECASE | re.DOTALL):
        if re.search(r"\bselected\b", m.group(1), re.IGNORECASE):
            replacements.append(_clean_text(m.group(2)))
    without_controls = re.sub(r"<(input|textarea|select|option)\b.*?</\1>|<input\b[^>]*>", " ", fragment, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", " ", without_controls, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    pieces = [_clean_text(text)] + [_clean_text(item) for item in replacements if _clean_text(item)]
    return _clean_text(" ".join(p for p in pieces if p))


def _table_rows(table_html: str) -> list[dict[str, list[str]]]:
    rows: list[dict[str, list[str]]] = []
    for row_m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
        row_html = row_m.group(1)
        header_cells = re.findall(r"<th\b[^>]*>(.*?)</th>", row_html, re.IGNORECASE | re.DOTALL)
        data_cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        rows.append({
            "html": row_html,
            "headers": [_cell_text(c) for c in header_cells],
            "cells": [_cell_text(c) for c in data_cells],
            "cell_html": data_cells,
        })
    return rows


def _bool_from_text(value: str, row_html: str = "") -> bool:
    lowered = value.lower()
    if any(token in lowered for token in ("yes", "true", "enabled", "checked")):
        return True
    if any(token in lowered for token in ("no", "false", "disabled")):
        return False
    if "osicon-tick" in row_html or "text-success" in row_html:
        return True
    return False


def _header_index(headers: list[str], *needles: str, exclude: tuple[str, ...] = ()) -> int | None:
    for idx, header in enumerate(headers):
        h = header.lower()
        if all(needle in h for needle in needles) and not any(x in h for x in exclude):
            return idx
    return None


def _first_index(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def contains_loading_site_properties(page_html: str) -> bool:
    return "Loading Site Properties" in page_html


def classify_servicecenter_page(page_html: str, final_url: str) -> str | None:
    if "Login.aspx" in final_url or re.search(r"<title>\s*Login", page_html, re.IGNORECASE):
        return "login_failed"
    lowered = page_html.lower()
    if "permission denied" in lowered or "permissiondenied" in final_url.lower():
        return "permission_denied"
    if "module not found" in lowered or "espace not found" in lowered:
        return "module_not_found"
    return None


def parse_tenants(page_html: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse the Tenants tab from an eSpace page."""
    warnings: list[str] = []
    tenants: list[dict[str, Any]] = []

    table_match = re.search(
        r"<table\b[^>]*(?:id=\"[^\"]*ListTenants[^\"]*\"|data-name=\"[^\"]*tenant[^\"]*\")[^>]*>(.*?)</table>",
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    table_html = table_match.group(1) if table_match else None
    if not table_html:
        for table in re.findall(r"<table\b[^>]*>(.*?)</table>", page_html, re.IGNORECASE | re.DOTALL):
            rows = _table_rows(table)
            headers: list[str] = []
            for row in rows:
                if row["headers"]:
                    headers = row["headers"]
                    break
            if _header_index(headers, "tenant") is not None and _header_index(headers, "enabled") is not None:
                table_html = table
                break
    if not table_html:
        # Some single-tenant or system modules do not render a tenant list on
        # eSpace pages. Use the default tenant as the minimum effective scope.
        return [{"tenant_id": "default", "tenant_name": "Default Tenant", "enabled": True, "tenant_index": 0}], warnings

    rows = _table_rows(table_html)
    headers: list[str] = []
    for row in rows:
        if row["headers"]:
            headers = row["headers"]
            break
    tenant_idx = _header_index(headers, "tenant")
    enabled_idx = _header_index(headers, "enabled")

    seen: set[str] = set()
    for row in rows:
        cells = row["cells"]
        if not cells or any("no tenants" in c.lower() for c in cells):
            continue
        row_html = row["html"]
        link = re.search(
            r'href="[^"]*Tenant_Id=(\d+)[^"]*"[^>]*>(.*?)</a>',
            row_html,
            re.IGNORECASE | re.DOTALL,
        )
        if link:
            tenant_id = link.group(1)
            tenant_name = _cell_text(link.group(2))
        elif tenant_idx is not None and tenant_idx < len(cells):
            tenant_id = cells[tenant_idx]
            tenant_name = cells[tenant_idx]
        else:
            continue
        if not tenant_name:
            continue
        if tenant_id in seen:
            continue
        seen.add(tenant_id)
        enabled = True
        if enabled_idx is not None and enabled_idx < len(cells):
            enabled = _bool_from_text(cells[enabled_idx], row_html)
        tenants.append({
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "enabled": enabled,
            "tenant_index": len(tenants),
        })

    if not tenants:
        warnings.append("tenant_parse_failed")
    return tenants, warnings


def _site_properties_tables(page_html: str) -> list[str]:
    tables = re.findall(r"<table\b[^>]*>(.*?)</table>", page_html, re.IGNORECASE | re.DOTALL)
    likely: list[str] = []
    for table in tables:
        text = _clean_text(re.sub(r"<[^>]+>", " ", table))
        lowered = text.lower()
        if "site propert" in lowered:
            likely.append(table)
            continue
        if "name" in lowered and ("effective value" in lowered or "value" in lowered) and "tenant" not in lowered:
            likely.append(table)
    return likely or tables


def _property_type_name(prop: dict[str, Any]) -> str | None:
    ptype = prop.get("type")
    if isinstance(ptype, dict):
        return ptype.get("name") or ptype.get("kind") or ptype.get("raw")
    if ptype is None:
        return None
    return str(ptype)


def _expected_property_map(oml_properties: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(p.get("name")): p for p in oml_properties if p.get("name")}


def _tenant_context_records(
    expected: dict[str, dict[str, Any]],
    seen_names: set[str],
    tenant: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    tenant_data = tenant or {}
    tenant_id = str(tenant_data.get("tenant_id", "default"))
    tenant_name = str(tenant_data.get("tenant_name", "Default Tenant"))
    values = {
        "TenantId": tenant_id,
        "TenantName": tenant_name,
    }
    records: list[dict[str, Any]] = []
    for name in ("TenantId", "TenantName"):
        if name in seen_names or name not in expected:
            continue
        expected_prop = expected[name]
        oml_default = expected_prop.get("default")
        effective = values[name]
        records.append({
            "name": name,
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "type": _property_type_name(expected_prop),
            "read_only": bool(expected_prop.get("read_only")),
            "is_secret": bool(expected_prop.get("is_secret")),
            "oml_default": oml_default,
            "effective_value": effective,
            "is_overridden": None if oml_default is None else str(effective) != str(oml_default),
            "source": "servicecenter_tenant_context",
        })
    return records


def parse_site_properties(
    page_html: str,
    oml_properties: list[dict[str, Any]],
    *,
    tenant: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse ServiceCenter Site Properties rows from a loaded tab."""
    warnings: list[str] = []
    if classify_servicecenter_page(page_html, ""):
        return [], ["parse_failed"]
    if contains_loading_site_properties(page_html):
        return [], ["site_properties_not_loaded"]
    expected = _expected_property_map(oml_properties)
    if re.search(r"no\s+site\s+properties\s+to\s+show", page_html, re.IGNORECASE):
        return _tenant_context_records(expected, set(), tenant), warnings

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for table in _site_properties_tables(page_html):
        rows = _table_rows(table)
        headers: list[str] = []
        for row in rows:
            if row["headers"]:
                headers = row["headers"]
                break
        if not headers:
            continue
        header_l = [h.lower() for h in headers]
        if not any("name" in h for h in header_l):
            continue

        name_idx = _header_index(headers, "name")
        tenant_idx = _header_index(headers, "tenant")
        type_idx = _header_index(headers, "type")
        value_idx = _first_index(
            _header_index(headers, "effective", "value"),
            _header_index(headers, "value", exclude=("default",)),
            _header_index(headers, "current"),
        )
        default_idx = _header_index(headers, "default")
        readonly_idx = _header_index(headers, "read", "only")
        secret_idx = _header_index(headers, "secret")
        if name_idx is None or value_idx is None:
            continue

        for row in rows:
            cells = row["cells"]
            if not cells or len(cells) <= max(name_idx, value_idx):
                continue
            name = cells[name_idx]
            if not name or name.lower() in {"name", "property"}:
                continue
            if expected and name not in expected:
                # Keep unknown ServiceCenter rows out of readiness accounting and
                # artifacts because the runtime contract is anchored to OML props.
                continue
            tenant_id = str((tenant or {}).get("tenant_id", "default"))
            tenant_name = str((tenant or {}).get("tenant_name", "Default Tenant"))
            if tenant_idx is not None and tenant_idx < len(cells) and cells[tenant_idx]:
                tenant_name = cells[tenant_idx]
                tenant_id = tenant_name
            key = (tenant_id, name)
            if key in seen:
                continue
            seen.add(key)
            expected_prop = expected.get(name, {})
            oml_default = expected_prop.get("default")
            effective = cells[value_idx]
            ptype = cells[type_idx] if type_idx is not None and type_idx < len(cells) else _property_type_name(expected_prop)
            read_only = (
                _bool_from_text(cells[readonly_idx], row["html"])
                if readonly_idx is not None and readonly_idx < len(cells)
                else bool(expected_prop.get("read_only"))
            )
            is_secret = (
                _bool_from_text(cells[secret_idx], row["html"])
                if secret_idx is not None and secret_idx < len(cells)
                else bool(expected_prop.get("is_secret"))
            )
            if default_idx is not None and default_idx < len(cells) and oml_default in (None, ""):
                oml_default = cells[default_idx]
            records.append({
                "name": name,
                "tenant_id": tenant_id,
                "tenant_name": tenant_name,
                "type": ptype,
                "read_only": read_only,
                "is_secret": is_secret,
                "oml_default": oml_default,
                "effective_value": effective,
                "is_overridden": None if oml_default is None else str(effective) != str(oml_default),
                "source": "servicecenter_site_properties",
            })

    records.extend(_tenant_context_records(expected, {r["name"] for r in records}, tenant))
    if expected and not records and "Site Properties" in page_html:
        warnings.append("parse_failed")
    return records, warnings


def build_ajax_payload(page_html: str, target_tab: str = "tab-SiteProperties") -> tuple[bytes, str] | None:
    """Build a best-effort OutSystems tab-change Ajax payload."""
    trigger_name = find_input(page_html, "submit", value_pattern="Trigger")
    if not trigger_name:
        m = re.search(r'name="([^"]*wtOnTabChange[^"]*wtTriggerEvent)"', page_html)
        trigger_name = m.group(1) if m else None
    if not trigger_name:
        return None

    payload = extract_hidden_fields(page_html)
    active_tab_m = re.search(r'data-active-tab="([^"]+)"', page_html, re.IGNORECASE)
    active_tab = active_tab_m.group(1) if active_tab_m else ""
    for name in list(payload):
        if name.endswith("$wtOnTabChange$wtin1"):
            payload[name] = target_tab
        elif name.endswith("$wtOnTabChange$wtin2"):
            payload[name] = active_tab
        elif name.endswith("$wtOnTabChange$wtin3"):
            payload[name] = ""
        elif name.endswith("$wtOnTabChange$wtin4"):
            payload[name] = ""
        elif name.endswith("$wtOnTabChange$wtin5"):
            payload[name] = ""
    payload[trigger_name] = "Trigger"
    return encode_form(payload), trigger_name


def _edit_url(base_url: str, espace_id: str, *, tenant_index: int | None = None, tab: str | None = None) -> str:
    params: dict[str, str] = {"EspaceId": str(espace_id)}
    if tenant_index is not None:
        params["TenantIdx"] = str(tenant_index)
    if tab:
        params["Tab"] = tab
    return f"{base_url}/ServiceCenter/eSpace_Edit.aspx?{urllib.parse.urlencode(params)}"


def _load_site_properties_tab(
    opener: urllib.request.OpenerDirector,
    config: ScraperConfig,
    espace_id: str,
    *,
    tenant_index: int | None,
) -> tuple[str, int, list[str]]:
    base_url = normalize_base_url(config.servicecenter_url or "")
    direct_url = _edit_url(base_url, espace_id, tenant_index=tenant_index, tab="SiteProperties")
    body, final_url, retry_count = fetch_with_retries(
        opener,
        direct_url,
        retries=config.retries,
        delay=config.delay,
    )
    classified = classify_servicecenter_page(body, final_url)
    if classified:
        raise ServiceCenterError(classified, classified.replace("_", " "))

    warnings: list[str] = []
    if not contains_loading_site_properties(body):
        return body, retry_count, warnings

    ajax = build_ajax_payload(body)
    if not ajax:
        warnings.append("site_properties_not_loaded")
        return body, retry_count, warnings

    payload, _trigger = ajax
    ajax_body, ajax_final, ajax_retries = fetch_with_retries(
        opener,
        direct_url,
        data=payload,
        referer=direct_url,
        retries=config.retries,
        delay=config.delay,
        accept="text/plain, */*",
    )
    classified = classify_servicecenter_page(ajax_body, ajax_final)
    if classified:
        raise ServiceCenterError(classified, classified.replace("_", " "))
    retry_count += ajax_retries
    if contains_loading_site_properties(ajax_body):
        warnings.append("site_properties_not_loaded")
    return ajax_body, retry_count, warnings


def read_conf(conf_path: Path, key: str, section: str) -> str | None:
    if not conf_path.exists():
        return None
    in_section = False
    for raw_line in conf_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line == f"[{section}]"
            continue
        if in_section and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return None


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _as_float(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _as_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def resolve_config(args: argparse.Namespace) -> ScraperConfig:
    project_root = args.project_root.resolve()
    pass_dir = args.pass_dir.resolve() if args.pass_dir else (project_root / "passes" / "latest").resolve()
    conf_path = project_root / "catalyst.conf"

    conf_url = read_conf(conf_path, "url", "servicecenter")
    conf_username = read_conf(conf_path, "username", "servicecenter")
    conf_verify = read_conf(conf_path, "verify_ssl", "servicecenter")
    conf_delay = read_conf(conf_path, "delay", "servicecenter")
    conf_retries = read_conf(conf_path, "retries", "servicecenter")
    conf_environment = read_conf(conf_path, "environment", "servicecenter")

    oml_dir = _resolve_project_path(project_root, args.oml_dir) if args.oml_dir else None
    url = args.servicecenter_url or os.environ.get("SERVICECENTER_URL") or conf_url
    username = args.servicecenter_username or os.environ.get("SERVICECENTER_USERNAME") or conf_username
    password = args.servicecenter_password or os.environ.get("SERVICECENTER_PASSWORD")
    verify_ssl = False if args.no_verify_ssl else _as_bool(conf_verify, True)
    delay = args.servicecenter_delay if args.servicecenter_delay is not None else _as_float(conf_delay, 0.5)
    retries = args.servicecenter_retries if args.servicecenter_retries is not None else _as_int(conf_retries, 2)
    environment = args.servicecenter_environment or conf_environment

    if url and username and not password and _get_from_keychain is not None:
        password = _get_from_keychain(url, username)
    if url and username and password and args.store_servicecenter_password and _store_in_keychain is not None:
        _store_in_keychain(url, username, password)

    return ScraperConfig(
        project_root=project_root,
        pass_dir=pass_dir,
        oml_dir=oml_dir,
        servicecenter_url=normalize_base_url(url) if url else None,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        delay=delay,
        retries=retries,
        environment=environment,
    )


def _normalize_module_name(name: str) -> str:
    return re.sub(r"_v\d+$", "", name)


def _resolve_project_path(project_root: Path, value: str | Path | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _pass_oml_dir(pass_dir: Path, project_root: Path) -> Path | None:
    metadata_path = pass_dir / "pass-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    source = metadata.get("oml_source")
    if not isinstance(source, str):
        return None
    return _resolve_project_path(project_root, source)


def _scan_dirs(project_root: Path, pass_dir: Path | None = None, oml_dir: Path | None = None) -> list[Path]:
    conf_path = project_root / "catalyst.conf"
    configured = read_conf(conf_path, "oml_dir", "paths")
    candidates: list[Path] = []
    if oml_dir:
        candidates.append(oml_dir)
    if pass_dir:
        pass_source = _pass_oml_dir(pass_dir, project_root)
        if pass_source:
            candidates.append(pass_source)
    if configured:
        path = Path(configured)
        candidates.append(path if path.is_absolute() else project_root / path)
    candidates.extend([project_root / "oml", project_root / "omls"])
    candidates.extend(sorted(project_root.glob("*-oml")))
    seen: set[Path] = set()
    resolved: list[Path] = []
    for candidate in candidates:
        c = candidate.resolve()
        if c not in seen and c.is_dir():
            seen.add(c)
            resolved.append(c)
    return resolved


def load_espace_ids(project_root: Path, pass_dir: Path | None = None, oml_dir: Path | None = None) -> dict[str, str]:
    """Build module name -> eSpace ID mapping from manifest and scan JSON files."""
    mapping: dict[str, str] = {}
    id_files: list[Path] = []
    for scan_dir in _scan_dirs(project_root, pass_dir=pass_dir, oml_dir=oml_dir):
        manifest = scan_dir / "download_manifest.json"
        if manifest.is_file():
            id_files.append(manifest)
        id_files.extend(sorted(scan_dir.glob("scan_*.json")))
    if not id_files:
        for scan_file in project_root.rglob("scan_*.json"):
            if "passes" not in scan_file.parts:
                id_files.append(scan_file)

    def add(name: str | None, eid: str | None) -> None:
        if not name or not eid:
            return
        for key in {name, _normalize_module_name(name)}:
            mapping.setdefault(key, str(eid))

    def add_item(item: dict[str, Any], fallback_eid: str | None = None) -> None:
        add(
            item.get("name") or item.get("Name") or item.get("module_name") or item.get("ModuleName"),
            item.get("eid")
            or item.get("EID")
            or item.get("id")
            or item.get("Id")
            or item.get("espace_id")
            or item.get("EspaceId")
            or fallback_eid,
        )

    for id_file in id_files:
        try:
            data = json.loads(id_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            modules = data.get("modules")
            if isinstance(modules, list):
                for item in modules:
                    if isinstance(item, dict):
                        add_item(item)
            for key, value in data.items():
                if isinstance(value, dict):
                    add_item(value, key)
                elif isinstance(value, (str, int)):
                    add(key, str(value))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    add_item(item)
    return mapping


def discover_targets(pass_dir: Path, project_root: Path, oml_dir: Path | None = None) -> list[ModuleTarget]:
    extraction_dir = pass_dir / "extraction"
    if not extraction_dir.is_dir():
        raise RuntimeError(f"Extraction directory not found: {extraction_dir}")
    espace_ids = load_espace_ids(project_root, pass_dir=pass_dir, oml_dir=oml_dir)
    targets: list[ModuleTarget] = []
    for package_file in sorted(extraction_dir.glob("*/package.json")):
        module_dir = package_file.parent
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            package = {}
        module_name = package.get("module_name") or module_dir.name
        sp_path = module_dir / "data" / "site-properties.json"
        try:
            oml_properties = json.loads(sp_path.read_text(encoding="utf-8")) if sp_path.exists() else []
        except (json.JSONDecodeError, OSError):
            oml_properties = []
        eid = espace_ids.get(module_name) or espace_ids.get(_normalize_module_name(module_name)) or espace_ids.get(module_dir.name)
        targets.append(ModuleTarget(module_name, module_dir, eid, oml_properties if isinstance(oml_properties, list) else []))
    return targets


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    if code not in FAILURE_CODES:
        code = "request_failed"
    data = {"code": code, "message": message}
    data.update(extra)
    return data


def _base_artifact(target: ModuleTarget, config: ScraperConfig, status: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "module_name": target.module_name,
        "espace_id": target.espace_id,
        "servicecenter_url": config.servicecenter_url,
        "environment": config.environment,
        "scraped_at": utc_now(),
        "tenant_scope": "all",
        "status": status,
        "expected_property_count": len(target.oml_properties),
        "expected_value_count": 0,
        "captured_value_count": 0,
        "tenants": [],
        "properties": [],
        "errors": [],
    }


def _write_sensitive_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _artifact_path(target: ModuleTarget) -> Path:
    return target.module_dir / "data" / "site-properties-runtime.json"


def build_config_failure_artifacts(targets: list[ModuleTarget], config: ScraperConfig) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for target in targets:
        if not target.oml_properties:
            artifact = _base_artifact(target, config, STATUS_NOT_APPLICABLE)
        else:
            artifact = _base_artifact(target, config, STATUS_FAILED)
            artifact["expected_value_count"] = len(target.oml_properties)
            artifact["errors"].append(_error("missing_servicecenter_config", "Missing ServiceCenter URL, username, or password"))
        _write_sensitive_json(_artifact_path(target), artifact)
        artifacts.append(artifact)
    return artifacts


def scrape_module(
    opener: urllib.request.OpenerDirector,
    config: ScraperConfig,
    target: ModuleTarget,
) -> tuple[dict[str, Any], int, list[str]]:
    artifact = _base_artifact(target, config, STATUS_FAILED)
    retry_count = 0
    parser_warnings: list[str] = []

    if not target.oml_properties:
        artifact["status"] = STATUS_NOT_APPLICABLE
        _write_sensitive_json(_artifact_path(target), artifact)
        return artifact, retry_count, parser_warnings
    if not target.espace_id:
        artifact["expected_value_count"] = len(target.oml_properties)
        artifact["errors"].append(_error("module_not_found", "No eSpace ID found in scan JSON"))
        _write_sensitive_json(_artifact_path(target), artifact)
        return artifact, retry_count, parser_warnings

    base_url = normalize_base_url(config.servicecenter_url or "")
    edit_url = _edit_url(base_url, target.espace_id)
    try:
        page_html, final_url, retries = fetch_with_retries(
            opener,
            edit_url,
            retries=config.retries,
            delay=config.delay,
        )
        retry_count += retries
    except ServiceCenterError as exc:
        artifact["expected_value_count"] = len(target.oml_properties)
        artifact["errors"].append(_error(exc.code, str(exc)))
        _write_sensitive_json(_artifact_path(target), artifact)
        return artifact, retry_count, parser_warnings

    classified = classify_servicecenter_page(page_html, final_url)
    if classified:
        artifact["expected_value_count"] = len(target.oml_properties)
        artifact["errors"].append(_error(classified, classified.replace("_", " ")))
        _write_sensitive_json(_artifact_path(target), artifact)
        return artifact, retry_count, parser_warnings

    tenants, tenant_warnings = parse_tenants(page_html)
    parser_warnings.extend(tenant_warnings)
    if not tenants:
        artifact["expected_value_count"] = len(target.oml_properties)
        artifact["errors"].append(_error("tenant_parse_failed", "Could not parse enabled tenants"))
        _write_sensitive_json(_artifact_path(target), artifact)
        return artifact, retry_count, parser_warnings

    artifact["tenants"] = [
        {"tenant_id": t["tenant_id"], "tenant_name": t["tenant_name"], "enabled": bool(t.get("enabled", True))}
        for t in tenants
    ]
    enabled_tenants = [t for t in tenants if t.get("enabled", True)]
    artifact["expected_value_count"] = len(target.oml_properties) * len(enabled_tenants)

    all_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    expected_names = {p.get("name") for p in target.oml_properties if p.get("name")}

    for tenant in enabled_tenants:
        try:
            loaded_html, retries, load_warnings = _load_site_properties_tab(
                opener,
                config,
                target.espace_id,
                tenant_index=int(tenant.get("tenant_index", 0)),
            )
            retry_count += retries
            parser_warnings.extend(load_warnings)
            records, parse_warnings = parse_site_properties(
                loaded_html,
                target.oml_properties,
                tenant=tenant,
            )
            parser_warnings.extend(parse_warnings)
            tenant_found = {r["name"] for r in records}
            missing = sorted(expected_names - tenant_found)
            if missing:
                errors.append(_error(
                    "parse_failed",
                    f"Missing {len(missing)} site property values for tenant",
                    tenant_id=tenant["tenant_id"],
                    missing_count=len(missing),
                ))
            all_records.extend(records)
        except ServiceCenterError as exc:
            errors.append(_error(exc.code, str(exc), tenant_id=tenant["tenant_id"]))

    artifact["properties"] = all_records
    artifact["captured_value_count"] = len({
        (r.get("tenant_id"), r.get("name"))
        for r in all_records
        if r.get("name") in expected_names and r.get("effective_value") not in (None, "")
    })
    artifact["errors"] = errors
    expected_values = artifact["expected_value_count"]
    captured_values = artifact["captured_value_count"]
    if expected_values > 0 and captured_values >= expected_values and not errors:
        artifact["status"] = STATUS_COMPLETE
    elif captured_values > 0:
        artifact["status"] = STATUS_PARTIAL
    else:
        artifact["status"] = STATUS_FAILED

    _write_sensitive_json(_artifact_path(target), artifact)
    return artifact, retry_count, parser_warnings


def build_summary(
    artifacts: list[dict[str, Any]],
    *,
    servicecenter_url: str | None,
    environment: str | None,
    retry_count: int,
    parser_warnings: list[str],
) -> dict[str, Any]:
    applicable = [a for a in artifacts if a.get("status") != STATUS_NOT_APPLICABLE]
    complete = [a for a in applicable if a.get("status") == STATUS_COMPLETE]
    partial = [a for a in applicable if a.get("status") == STATUS_PARTIAL]
    failed = [a for a in applicable if a.get("status") == STATUS_FAILED]
    failed_tenants: list[dict[str, Any]] = []
    failure_counts: dict[str, int] = {}
    for artifact in artifacts:
        for err in artifact.get("errors", []):
            code = err.get("code", "request_failed")
            failure_counts[code] = failure_counts.get(code, 0) + 1
            if err.get("tenant_id"):
                failed_tenants.append({
                    "module_name": artifact.get("module_name"),
                    "tenant_id": err.get("tenant_id"),
                    "reason": code,
                })
    expected_values = sum(int(a.get("expected_value_count") or 0) for a in applicable)
    captured_values = sum(int(a.get("captured_value_count") or 0) for a in applicable)
    tenants = {
        (t.get("tenant_id"), t.get("tenant_name"))
        for a in artifacts
        for t in a.get("tenants", [])
        if t.get("enabled", True)
    }
    secret_values = sum(
        1
        for a in artifacts
        for p in a.get("properties", [])
        if p.get("is_secret") and p.get("effective_value") not in (None, "")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "servicecenter_url": servicecenter_url,
        "environment": environment,
        "scraped_at": utc_now(),
        "tenant_scope": "all",
        "totals": {
            "modules": len(artifacts),
            "applicable_modules": len(applicable),
            "not_applicable_modules": len(artifacts) - len(applicable),
            "complete_modules": len(complete),
            "partial_modules": len(partial),
            "failed_modules": len(failed),
            "expected_values": expected_values,
            "captured_values": captured_values,
            "coverage_pct": round((captured_values / expected_values) * 100, 1) if expected_values else 100.0,
            "tenant_count": len(tenants),
            "secret_value_count": secret_values,
        },
        "failed_modules": [a.get("module_name") for a in failed],
        "partial_modules": [a.get("module_name") for a in partial],
        "failed_tenants": failed_tenants,
        "retry_count": retry_count,
        "failure_counts": failure_counts,
        "parser_warnings": sorted(set(parser_warnings)),
    }


def write_summary(config: ScraperConfig, artifacts: list[dict[str, Any]], retry_count: int, parser_warnings: list[str], output: Path | None) -> dict[str, Any]:
    summary = build_summary(
        artifacts,
        servicecenter_url=config.servicecenter_url,
        environment=config.environment,
        retry_count=retry_count,
        parser_warnings=parser_warnings,
    )
    out_path = output or (config.pass_dir / "runtime" / "site-properties-summary.json")
    _write_sensitive_json(out_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape runtime Site Properties from OutSystems ServiceCenter")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Consumer project root")
    parser.add_argument("--pass-dir", type=Path, default=None, help="Extraction pass directory")
    parser.add_argument("--oml-dir", type=Path, default=None, help="OML directory used for this pass")
    parser.add_argument("--output", type=Path, default=None, help="Pass-level summary output path")
    parser.add_argument("--servicecenter-url", "--url", dest="servicecenter_url", default=None, help="ServiceCenter base URL")
    parser.add_argument("--servicecenter-username", "--username", dest="servicecenter_username", default=None, help="ServiceCenter username")
    parser.add_argument("--servicecenter-password", "--password", dest="servicecenter_password", default=None, help="ServiceCenter password")
    parser.add_argument("--store-servicecenter-password", action="store_true", help="Store password in macOS Keychain")
    parser.add_argument("--servicecenter-delay", type=float, default=None, help="Delay between requests in seconds")
    parser.add_argument("--servicecenter-retries", type=int, default=None, help="Transient request retry count")
    parser.add_argument("--servicecenter-environment", default=None, help="Environment label for artifacts")
    parser.add_argument("--no-verify-ssl", action="store_true", help="Disable SSL verification")
    args = parser.parse_args(argv)

    try:
        config = resolve_config(args)
        targets = discover_targets(config.pass_dir, config.project_root, oml_dir=config.oml_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not targets:
        print("Runtime site properties: no extracted modules found")
        return 0

    missing_config = not (config.servicecenter_url and config.username and config.password)
    if missing_config:
        artifacts = build_config_failure_artifacts(targets, config)
        summary = write_summary(config, artifacts, retry_count=0, parser_warnings=[], output=args.output)
        totals = summary["totals"]
        print(
            "Runtime site properties: "
            f"{totals['complete_modules']}/{totals['applicable_modules']} applicable modules complete "
            f"({totals['failed_modules']} failed; missing ServiceCenter config)"
        )
        return 2 if totals["failed_modules"] else 0

    opener = build_opener(verify_ssl=config.verify_ssl)
    try:
        login(opener, config.servicecenter_url or "", config.username or "", config.password or "")
    except ServiceCenterError as exc:
        artifacts = []
        for target in targets:
            if not target.oml_properties:
                artifact = _base_artifact(target, config, STATUS_NOT_APPLICABLE)
            else:
                artifact = _base_artifact(target, config, STATUS_FAILED)
                artifact["expected_value_count"] = len(target.oml_properties)
                artifact["errors"].append(_error(exc.code, str(exc)))
            _write_sensitive_json(_artifact_path(target), artifact)
            artifacts.append(artifact)
        summary = write_summary(config, artifacts, retry_count=0, parser_warnings=[], output=args.output)
        totals = summary["totals"]
        print(
            "Runtime site properties: "
            f"{totals['complete_modules']}/{totals['applicable_modules']} applicable modules complete "
            f"({totals['failed_modules']} failed; login failed)"
        )
        return 2 if totals["failed_modules"] else 0

    artifacts: list[dict[str, Any]] = []
    retry_count = 0
    parser_warnings: list[str] = []
    applicable_total = sum(1 for t in targets if t.oml_properties)
    for idx, target in enumerate(targets, 1):
        if target.oml_properties:
            print(f"  [{idx}/{len(targets)}] {target.module_name}: scraping runtime Site Properties")
        else:
            print(f"  [{idx}/{len(targets)}] {target.module_name}: no OML Site Properties")
        artifact, retries, warnings = scrape_module(opener, config, target)
        artifacts.append(artifact)
        retry_count += retries
        parser_warnings.extend(warnings)

    summary = write_summary(config, artifacts, retry_count=retry_count, parser_warnings=parser_warnings, output=args.output)
    totals = summary["totals"]
    print(
        "Runtime site properties: "
        f"{totals['complete_modules']}/{applicable_total} applicable modules complete, "
        f"{totals['partial_modules']} partial, {totals['failed_modules']} failed, "
        f"{totals['coverage_pct']}% coverage"
    )
    return 0 if totals["failed_modules"] == 0 and totals["partial_modules"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
