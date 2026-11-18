"""Shared utilities for deliverable generators.

Every ``generate_*.py`` script imports from here rather than
reimplementing package loading, type mapping, or CLI boilerplate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Progress messages contain non-ASCII (e.g. "→"). When output is piped or
# redirected on Windows, stdout falls back to cp1252 and printing crashes.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Package loading
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scope: deliverables cover the in-scope apps plus what they use
# ---------------------------------------------------------------------------
#
# The in-scope modules come from project.json / the package manifest (the same
# scope as check_dependencies.py). Other extracted modules (added to INPUTS to
# satisfy dependencies) are included only for the elements the scope reaches,
# traced by os2native.usage. ``--all-modules`` turns this off.

_ALL_MODULES = False
_SCOPES: dict[Path, Any] = {}


class _Scope:
    def __init__(self, modules: set[str], source: str, usage: dict[str, Any]) -> None:
        self.modules = modules
        self.source = source
        self.usage = usage


def _scope_for(pass_dir: Path) -> _Scope | None:
    """The deliverable scope of ``pass_dir``, or ``None`` when unscoped."""
    if _ALL_MODULES:
        return None
    key = pass_dir.resolve()
    if key not in _SCOPES:
        try:
            from os2native.usage import deliverable_usage

            modules, source, usage = deliverable_usage(pass_dir)
            _SCOPES[key] = None if source == "all modules" else _Scope(modules, source, usage)
        except (ImportError, OSError, ValueError) as exc:
            print(f"WARNING: scope not applied ({exc}); generating for all modules")
            _SCOPES[key] = None
    return _SCOPES[key]


def module_in_deliverables(pass_dir: Path, module: str) -> bool:
    scope = _scope_for(pass_dir)
    return scope is None or module in scope.modules or module in scope.usage


def load_pass_packages(pass_dir: Path) -> list[dict[str, Any]]:
    """Load the ``package.json`` files of the modules in the deliverables.

    That is the in-scope modules plus the modules they use (see the scope
    note above), or every module with ``--all-modules``.
    """
    extraction_dir = pass_dir / "extraction"
    if not extraction_dir.is_dir():
        raise FileNotFoundError(f"Extraction directory not found: {extraction_dir}")
    packages: list[dict[str, Any]] = []
    skipped: list[str] = []
    for pf in sorted(extraction_dir.glob("*/package.json")):
        try:
            pkg = json.loads(pf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping {pf}: {exc}")
            continue
        name = pkg.get("module_name") or pf.parent.name
        if module_in_deliverables(pass_dir, name):
            packages.append(pkg)
        else:
            skipped.append(name)
    scope = _scope_for(pass_dir)
    if scope is not None:
        used = sorted(m for m in scope.usage if m not in scope.modules)
        print(f"Scope ({scope.source}): {len(scope.modules)} module(s) in full, "
              f"used parts of {len(used)} other(s): {', '.join(used) or '-'}")
        if skipped:
            print(f"  not used by the scope, left out: {', '.join(skipped)}")
    return packages


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _filter_used(rel: str, data: Any, usage: Any) -> Any:
    """Keep only the items of an out-of-scope module's file that the scope uses."""
    from os2native import usage as u

    def by(field: str, names: set[str]) -> list[Any]:
        return [x for x in data if isinstance(x, dict) and x.get(field) in names]

    entities = usage.names(u.ENTITY)
    actions = usage.names(u.ACTION) | usage.names(u.SERVICE)
    blocks = usage.names(u.BLOCK)
    if rel == "dependencies/graph.json" and isinstance(data, dict):
        targets = {m for m, _ in usage.outbound}
        return {**data, "edges": [e for e in data.get("edges", []) if e.get("target") in targets]}
    if not isinstance(data, list):
        return data
    if rel == "data/entities.json":
        return by("name", entities)
    if rel == "data/relationships.json":
        return by("from_entity", entities)
    if rel == "data/static-data.json":
        return by("entity_name", entities)
    if rel == "data/structures.json":
        return by("name", usage.names(u.STRUCTURE))
    if rel == "logic/server-actions.json":
        return by("name", usage.names(u.ACTION))
    if rel == "logic/service-actions.json":
        return by("name", usage.names(u.SERVICE))
    if rel in ("logic/client-actions.json", "logic/actions.json"):
        clients = usage.names(u.CLIENT)
        kept = []
        for x in data:
            source = x.get("source") if isinstance(x, dict) else None
            if isinstance(source, dict):
                if source.get("block_name") in blocks:
                    kept.append(x)
            elif isinstance(x, dict) and (x.get("name") in clients or (
                    rel == "logic/actions.json" and x.get("name") in actions)):
                kept.append(x)
        return kept
    if rel == "logic/advanced-sql.json":
        return by("action", actions)
    if rel == "interfaces/screens.json":
        return []
    if rel == "interfaces/blocks.json":
        return by("name", blocks)
    if rel == "interfaces/themes.json":
        themes = usage.names(u.THEME)
        return [x for x in data if isinstance(x, dict)
                and (x.get("is_reference") or x.get("name") in themes)]
    if rel in ("integrations/external-systems.json", "integrations/consumed-apis.json"):
        return by("name", usage.names(u.EXTERNAL))
    if rel == "integrations/exposed-apis.json":
        return []
    if rel == "integrations/public-actions.json":
        return by("name", usage.names(u.ACTION))
    if rel == "integrations/dependency-actions.json":
        return [x for x in data if isinstance(x, dict)
                and (x.get("module"), x.get("name")) in usage.outbound]
    return data


def load_domain_file(
    pass_dir: Path,
    module: str,
    domain: str,
    filename: str,
) -> Any:
    """Load a domain-specific JSON file for a module.

    Returns the parsed JSON (list or dict), or an empty list if the
    file does not exist or cannot be parsed. For a module outside the scope,
    only the elements the scope uses are returned.
    """
    data = _read_json(pass_dir / "extraction" / module / domain / filename)
    scope = _scope_for(pass_dir)
    if scope is None or module in scope.modules:
        return data
    usage = scope.usage.get(module)
    if usage is None:
        return [] if isinstance(data, list) else data
    return _filter_used(f"{domain}/{filename}", data, usage)


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

class _GeneratorArgParser(argparse.ArgumentParser):
    """Applies ``--all-modules`` when the generator parses its arguments."""

    def parse_args(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        global _ALL_MODULES
        ns = super().parse_args(*args, **kwargs)
        _ALL_MODULES = bool(getattr(ns, "all_modules", False))
        return ns


def make_arg_parser(description: str) -> argparse.ArgumentParser:
    """Return a standard argparse parser for a deliverable generator."""
    parser = _GeneratorArgParser(description=description)
    parser.add_argument(
        "pass_dir",
        type=Path,
        help="Path to the project's OUTPUT folder (e.g. ..\\projects\\MyProject\\OUTPUT)",
    )
    parser.add_argument(
        "--all-modules",
        action="store_true",
        help="Cover every extracted module, not just the scope in project.json "
             "(or the package manifest) and the parts of other modules it uses",
    )
    return parser


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def md_cell(value: Any) -> str:
    """One table cell: line breaks collapsed to spaces, pipes escaped."""
    return " ".join(str(value).split()).replace("|", "\\|")


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple Markdown table (cells made safe with ``md_cell``)."""
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(md_cell(c) for c in row) + " |")
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
