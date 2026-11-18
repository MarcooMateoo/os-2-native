"""Catalogue of every site property, for the converter handover.

Reads ``extraction/<Module>/data/site-properties.json`` for the modules in the
deliverables (the scope, plus the modules it uses) and writes
``deliverables/configuration/``:

- ``site-properties.md``   — one table with every site property: module,
  name, type, description, default (masked when sensitive), whether it's a
  secret, where it goes in the target stack, its configuration key, and
  whether its runtime value was collected from Service Center
- ``site-properties.json`` — the same rows, for tools

Platform site properties (``TenantId``, ``TenantName``, read-only and in every
module) are counted but not listed.

Values are never copied when they may be sensitive: secrets (``is_secret``, or
a name such as ``ClientSecret``, ``APIKey``, ``Password``, ``Token``), credential
identifiers (``AppId``, ``ClientId``, ``TenantId``), email addresses, and anything
in a property whose name suggests a person or recipient. Those rows say only whether a default is set. Runtime values
collected by ``scrape_site_properties.py`` are never copied either; the table
only says whether they were collected.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from generator_utils import (
    deliverables_dir,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    write_json,
    write_text,
)

_SECRET_NAME = re.compile(
    r"secret|password|passwd|pwd|token|api_?key|apikey|private|credential|cryptokey|(^|_)key$|(?-i:[a-z0-9]Key$)",
    re.IGNORECASE,
)
_PERSONAL_NAME = re.compile(r"e-?mail|recipient|user|phone|mobile|ip$|windowsid", re.IGNORECASE)
# Credential identifiers (the public half of a key pair): not secrets, but their
# values are not copied either.
_CREDENTIAL_ID_NAME = re.compile(r"(app|client|tenant|consumer)_?id$", re.IGNORECASE)
_EMAIL_VALUE = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[a-z]{2,}", re.IGNORECASE)

MASKED = "(set, not shown)"
NO_DEFAULT = "-"
LONG_DEFAULT = 120  # longer defaults (e.g. an editor config) are shortened in the table


def _type_name(type_ref: Any) -> str:
    if isinstance(type_ref, dict):
        return type_ref.get("name") or type_ref.get("kind") or "?"
    return str(type_ref or "?")


def is_secret(prop: dict[str, Any]) -> bool:
    """Flagged secret in OutSystems, or named like one (OutSystems 11 rarely sets the flag)."""
    return bool(prop.get("is_secret")) or bool(_SECRET_NAME.search(prop.get("name") or ""))


def shown_default(prop: dict[str, Any]) -> str:
    """The default value as it may appear in the handover."""
    value = prop.get("default")
    if value in (None, "", '""', "''"):
        return NO_DEFAULT
    text = str(value)
    name = prop.get("name") or ""
    if (is_secret(prop) or _PERSONAL_NAME.search(name) or _CREDENTIAL_ID_NAME.search(name)
            or _EMAIL_VALUE.search(text)):
        return MASKED
    if _type_name(prop.get("type")) == "Email":
        return MASKED
    flat = " ".join(text.split())
    if len(flat) > LONG_DEFAULT:
        return f"{flat[:LONG_DEFAULT - 20]}… ({len(text):,} chars, see the module)"
    return text


def target_store(prop: dict[str, Any]) -> str:
    return "Secrets Manager" if is_secret(prop) else "SSM Parameter Store / AppConfig"


def config_key(module: str, name: str) -> str:
    """Environment-variable form; .NET maps ``__`` to ``:`` (``Module:Name``)."""
    return f"{module}__{name}"


def _runtime_status(pass_dir: Path, module: str, name: str) -> str:
    path = pass_dir / "extraction" / module / "data" / "site-properties-runtime.json"
    if not path.is_file():
        return "not collected"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "not collected"
    return "collected" if f'"{name}"' in text else "not found"


def collect(pass_dir: Path, in_scope: set[str] | None) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    platform = 0
    for pkg in load_pass_packages(pass_dir):
        module = pkg.get("module_name", "")
        props = load_domain_file(pass_dir, module, "data", "site-properties.json")
        for prop in props if isinstance(props, list) else []:
            if prop.get("is_platform"):
                platform += 1
                continue
            name = prop.get("name") or "?"
            rows.append({
                "module": module,
                "scope": "in scope" if in_scope is None or module in in_scope else "used dependency",
                "name": name,
                "type": _type_name(prop.get("type")),
                "description": (prop.get("description") or "").replace("\n", " ").strip(),
                "default": shown_default(prop),
                "secret": is_secret(prop),
                "flagged_secret_in_outsystems": bool(prop.get("is_secret")),
                "target": target_store(prop),
                "config_key": config_key(module, name),
                "runtime_value": _runtime_status(pass_dir, module, name),
            })
    rows.sort(key=lambda r: (r["scope"] != "in scope", r["module"].lower(), r["name"].lower()))
    return rows, platform


def _in_scope_modules(pass_dir: Path) -> set[str] | None:
    try:
        from generator_utils import _scope_for  # set up by the deliverable scope

        scope = _scope_for(pass_dir)
        return None if scope is None else set(scope.modules)
    except (ImportError, AttributeError):
        return None


def render(rows: list[dict[str, Any]], platform: int) -> str:
    secrets = sum(r["secret"] for r in rows)
    unflagged = sum(r["secret"] and not r["flagged_secret_in_outsystems"] for r in rows)
    collected = sum(r["runtime_value"] == "collected" for r in rows)
    modules = len({r["module"] for r in rows})
    lines = [
        "# Site properties",
        "",
        f"- **Site properties**: {len(rows)} in {modules} module(s) "
        f"(+ {platform} platform properties, TenantId/TenantName, not listed)",
        f"- **Secrets**: {secrets} ({unflagged} recognised by name, not flagged secret in OutSystems) "
        "→ Secrets Manager; values never shown",
        f"- **Runtime values collected from Service Center**: {collected} of {len(rows)} "
        "(`scripts/scrape_site_properties.py`); the values themselves are never copied here",
        "",
        "Defaults are the design-time values in the module. The values that run in each environment "
        "are set in Service Center and can differ: collect them before go-live. "
        f"`{MASKED}` means a default exists but may be sensitive (secret, email, person).",
        "",
        "In the target stack each property becomes configuration, not code: the config key is the "
        "environment-variable form (`.NET` reads `Module__Name` as `Module:Name`).",
        "",
    ]
    table = [[r["module"], r["scope"], f"`{r['name']}`", r["type"], r["description"] or "-",
              r["default"], "yes" if r["secret"] else "no", r["target"], f"`{r['config_key']}`",
              r["runtime_value"]] for r in rows]
    lines.append(md_table(["Module", "Scope", "Name", "Type", "Description", "Default", "Secret",
                           "Target", "Config key", "Runtime value"], table))
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = make_arg_parser("Catalogue every site property for the converter handover")
    args = parser.parse_args()
    pass_dir: Path = args.pass_dir
    rows, platform = collect(pass_dir, _in_scope_modules(pass_dir))
    out = deliverables_dir(pass_dir, "configuration")
    write_text(out / "site-properties.md", render(rows, platform))
    write_json(out / "site-properties.json", {"platform_properties": platform, "site_properties": rows})
    secrets = sum(r["secret"] for r in rows)
    print(f"{len(rows)} site properties ({secrets} secrets) in {len({r['module'] for r in rows})} modules; "
          f"{platform} platform properties not listed")


if __name__ == "__main__":
    sys.exit(main())
