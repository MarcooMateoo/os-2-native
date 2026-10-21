"""Shared utilities for deliverable generators.

Every ``generate_*.py`` script imports from here rather than
reimplementing package loading, type mapping, or CLI boilerplate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Package loading
# ---------------------------------------------------------------------------

def load_pass_packages(pass_dir: Path) -> list[dict[str, Any]]:
    """Load all ``package.json`` files from an extraction pass."""
    extraction_dir = pass_dir / "extraction"
    if not extraction_dir.is_dir():
        raise FileNotFoundError(f"Extraction directory not found: {extraction_dir}")
    packages: list[dict[str, Any]] = []
    for pf in sorted(extraction_dir.glob("*/package.json")):
        try:
            packages.append(json.loads(pf.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping {pf}: {exc}")
    return packages


def load_domain_file(
    pass_dir: Path,
    module: str,
    domain: str,
    filename: str,
) -> Any:
    """Load a domain-specific JSON file for a module.

    Returns the parsed JSON (list or dict), or an empty list if the
    file does not exist or cannot be parsed.
    """
    path = pass_dir / "extraction" / module / domain / filename
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def deliverables_dir(pass_dir: Path, deliverable_type: str) -> Path:
    """Create and return ``<pass_dir>/deliverables/<type>/``."""
    out = pass_dir / "deliverables" / deliverable_type
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_json(path: Path, data: Any) -> None:
    """Write *data* as pretty-printed JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    print(f"  wrote {path}")


def write_text(path: Path, text: str) -> None:
    """Write *text* to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  wrote {path}")


# ---------------------------------------------------------------------------
# Type / enum helpers
# ---------------------------------------------------------------------------

# OutSystems primitive → PostgreSQL column type
OS_TO_PG_TYPE: dict[str, str] = {
    "Text": "TEXT",
    "Integer": "INTEGER",
    "Long Integer": "BIGINT",
    "Boolean": "BOOLEAN",
    "DateTime": "TIMESTAMPTZ",
    "Date": "DATE",
    "Time": "TIME",
    "Decimal": "NUMERIC",
    "Currency": "NUMERIC(19,4)",
    "Email": "TEXT",
    "Phone Number": "TEXT",
    "Binary Data": "BYTEA",
}

# OutSystems http_method enum → standard verb
_HTTP_METHOD_MAP: dict[str, str] = {
    "1": "PUT",
    "2": "POST",
    "3": "DELETE",
}


def decode_http_method(raw: str | None) -> str:
    """Decode an OutSystems ``http_method`` enum value to a verb."""
    if raw is None or raw == "":
        return "GET"
    return _HTTP_METHOD_MAP.get(str(raw), "GET")


def pg_type(type_ref: dict[str, Any] | str) -> str:
    """Map an OutSystems TypeRef dict to a PostgreSQL type string."""
    if isinstance(type_ref, str):
        return OS_TO_PG_TYPE.get(type_ref, "TEXT")
    kind = type_ref.get("kind", "unknown")
    name = type_ref.get("name", "")
    if kind == "primitive":
        return OS_TO_PG_TYPE.get(name, "TEXT")
    if kind == "entity_identifier":
        return "BIGINT"
    return "TEXT"


# OutSystems primitive → Go type
OS_TO_GO_TYPE: dict[str, str] = {
    "Text": "string",
    "Integer": "int32",
    "Long Integer": "int64",
    "Boolean": "bool",
    "DateTime": "time.Time",
    "Date": "time.Time",
    "Time": "time.Time",
    "Decimal": "float64",
    "Currency": "float64",
    "Email": "string",
    "Phone Number": "string",
    "Binary Data": "[]byte",
}

# OutSystems primitive → Dart/Flutter type
OS_TO_DART_TYPE: dict[str, str] = {
    "Text": "String",
    "Integer": "int",
    "Long Integer": "int",
    "Boolean": "bool",
    "DateTime": "DateTime",
    "Date": "DateTime",
    "Time": "DateTime",
    "Decimal": "double",
    "Currency": "double",
    "Email": "String",
    "Phone Number": "String",
    "Binary Data": "Uint8List",
}


def go_type(type_ref: dict[str, Any] | str) -> str:
    """Map an OutSystems TypeRef dict to a Go type string."""
    if isinstance(type_ref, str):
        return OS_TO_GO_TYPE.get(type_ref, "string")
    kind = type_ref.get("kind", "unknown")
    name = type_ref.get("name", "")
    if kind == "primitive":
        return OS_TO_GO_TYPE.get(name, "string")
    if kind == "entity_identifier":
        entity = type_ref.get("entity_name", "")
        return f"int64 /* FK → {entity} */" if entity else "int64"
    return "string"


def dart_type(type_ref: dict[str, Any] | str) -> str:
    """Map an OutSystems TypeRef dict to a Dart type string."""
    if isinstance(type_ref, str):
        return OS_TO_DART_TYPE.get(type_ref, "String")
    kind = type_ref.get("kind", "unknown")
    name = type_ref.get("name", "")
    if kind == "primitive":
        return OS_TO_DART_TYPE.get(name, "String")
    if kind == "entity_identifier":
        return "int"
    return "String"


# ---------------------------------------------------------------------------
# Module classification
# ---------------------------------------------------------------------------

_MODULE_SUFFIXES: dict[str, str] = {
    "_CS": "core_service",
    "_BL": "business_logic",
    "_IS": "integration_service",
    "_CW": "core_widget",
    "_Lib": "library",
    "_API": "exposed_api",
    "_TH": "theme",
    "_Ops": "operations_portal",
    "_Timer": "timer",
    "Theme": "theme",
    "Plugin": "mobile_plugin",
}


def classify_module(name: str) -> str:
    """Classify a module by its naming suffix.

    OutSystems modules without a recognized suffix are typically top-level
    application modules (UI entry points), not miscellaneous "other".
    """
    for suffix, kind in _MODULE_SUFFIXES.items():
        if name.endswith(suffix):
            return kind
    return "application"


# ---------------------------------------------------------------------------
# CLI boilerplate
# ---------------------------------------------------------------------------

def make_arg_parser(description: str) -> argparse.ArgumentParser:
    """Return a standard argparse parser for a deliverable generator."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "pass_dir",
        type=Path,
        help="Path to the extraction pass directory (e.g. passes/pass-2026-04-24T05-55-45)",
    )
    return parser


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple Markdown table."""
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def normalize_widgets(widgets: Any) -> list[dict[str, Any]]:
    """Ensure *widgets* is a list of dicts.

    In extraction output, a screen's ``widgets`` field is often a single
    root container dict rather than a list.  This helper normalizes both
    shapes so callers can always iterate safely.
    """
    if isinstance(widgets, dict):
        return [widgets]
    if isinstance(widgets, list):
        return widgets
    return []


def slugify(value: str) -> str:
    """Convert a string to a URL-safe slug."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"
