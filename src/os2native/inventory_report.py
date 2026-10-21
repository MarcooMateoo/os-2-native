"""Itemised inventory: one row per counted element with its Service Studio location.

Every row's ``category`` is an ``inventory.json`` count key, so the number of
rows per category for a module equals that module's count in
``inventory.json``. Written next to ``inventory.json`` as
``inventory-details.csv`` (opens in Excel) and ``inventory-details.md``.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime

from os2native.run_log import log
from pathlib import Path
from typing import Any

# category key -> label shown in the report, in display order
CATEGORY_LABELS: dict[str, str] = {
    "screens": "Screen",
    "blocks": "Block",
    "screen_actions": "Screen/Block action",
    "themes": "Theme",
    "themes_consumed": "Theme (consumed)",
    "stylesheets": "Style sheet",
    "stylesheets_consumed": "Style sheet (consumed)",
    "client_actions": "Client action",
    "server_actions": "Server action",
    "service_actions": "Service action",
    "public_actions": "Public action",
    "dependency_actions": "Dependency action",
    "advanced_sql": "Advanced SQL",
    "entities": "Entity",
    "structures": "Structure",
    "site_properties": "Site property",
    "roles": "Role",
    "user_exceptions": "Exception",
    "processes": "Process",
    "timers": "Timer",
    "rest_consumed": "REST API (consumed)",
    "rest_exposed": "REST API (exposed)",
    "soap_consumed": "SOAP service (consumed)",
    "soap_exposed": "SOAP service (exposed)",
    "extensions": "Extension (.xif)",
    "extension_actions": "Extension action",
    "extension_structures": "Extension structure",
}

COLUMNS = ["module", "category", "name", "ownership", "location", "details", "key", "source"]


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _path(*parts: str | None) -> str:
    return " > ".join(p for p in parts if p)


def _flags(item: dict[str, Any], *names: tuple[str, str]) -> str:
    return ", ".join(label for field, label in names if item.get(field))


def nested_action_owners(pkg_root: Path) -> dict[str, tuple[str, str, str]]:
    """Map client-action key -> (kind, ui_flow, owner name) for screen/block actions."""
    owners: dict[str, tuple[str, str, str]] = {}
    for kind, rel in (("Screen", "interfaces/screens.json"), ("Block", "interfaces/blocks.json")):
        for owner in _load(pkg_root / rel):
            for action in owner.get("client_actions") or []:
                if action.get("key"):
                    owners[action["key"]] = (kind, owner.get("flow", ""), owner.get("name", ""))
    return owners


def module_rows(pkg_root: Path) -> list[dict[str, str]]:
    """Build the itemised rows for one extracted module package."""
    module = pkg_root.name
    rows: list[dict[str, str]] = []

    def add(category: str, name: str, location: str, source: str, *,
            ownership: str = "Own", details: str = "", key: str = "") -> None:
        rows.append({
            "module": module, "category": category, "name": name or "(unnamed)",
            "ownership": ownership, "location": location, "details": details,
            "key": key or "", "source": source,
        })

    # --- Interface -----------------------------------------------------------
    screens = _load(pkg_root / "interfaces/screens.json")
    blocks = _load(pkg_root / "interfaces/blocks.json")
    flow_of = {s.get("name"): s.get("flow", "") for s in screens + blocks}

    for category, items, src in (("screens", screens, "interfaces/screens.json"),
                                 ("blocks", blocks, "interfaces/blocks.json")):
        for s in items:
            add(category, s.get("name", ""),
                _path("Interface", "UI Flows", s.get("flow"), s.get("name")), src,
                details=_flags(s, ("is_public", "Public")), key=s.get("key", ""))

    action_location: dict[str, str] = {}
    owners = nested_action_owners(pkg_root)
    for items, src in ((screens, "interfaces/screens.json"), (blocks, "interfaces/blocks.json")):
        for owner in items:
            for a in owner.get("client_actions") or []:
                loc = _path("Interface", "UI Flows", owner.get("flow"), owner.get("name"), a.get("name"))
                action_location[a.get("key", "")] = loc
                add("screen_actions", a.get("name", ""), loc, src,
                    details=_flags(a, ("is_async", "Async")), key=a.get("key", ""))

    for t in _load(pkg_root / "interfaces/themes.json"):
        if t.get("is_reference"):
            source_module = t.get("source_module") or "(dependency)"
            add("themes_consumed", t.get("name", ""),
                _path("Interface", "Themes", source_module, t.get("name")),
                "interfaces/themes.json", ownership=f"Consumed from {source_module}",
                key=t.get("key", ""))
        else:
            add("themes", t.get("name", ""), _path("Interface", "Themes", t.get("name")),
                "interfaces/themes.json", key=t.get("key", ""))

    for s in _load(pkg_root / "interfaces/assets.json"):
        if not s.get("css_length"):
            continue
        owner, owner_kind = s.get("owner", ""), s.get("owner_kind", "")
        folder = "Themes" if "Theme" in owner_kind else "UI Flows"
        size = f"{s.get('css_length', 0):,} chars"
        if s.get("is_reference"):
            source_module = s.get("source_module") or "(dependency)"
            add("stylesheets_consumed", owner,
                _path("Interface", folder, source_module, owner, "Style Sheet"),
                "interfaces/assets.json", ownership=f"Consumed from {source_module}",
                details=f"{owner_kind}; {size}", key=s.get("key", ""))
        else:
            add("stylesheets", owner,
                _path("Interface", folder, flow_of.get(owner) if folder == "UI Flows" else None,
                      owner, "Style Sheet"),
                "interfaces/assets.json", details=f"{owner_kind}; {size}", key=s.get("key", ""))

    # --- Logic ---------------------------------------------------------------
    for a in _load(pkg_root / "logic/client-actions.json"):
        if a.get("key") in owners:
            continue  # counted under screen_actions
        loc = _path("Logic", "Client Actions", a.get("folder"), a.get("name"))
        action_location[a.get("key", "")] = loc
        add("client_actions", a.get("name", ""), loc, "logic/client-actions.json",
            details=a.get("action_type", ""), key=a.get("key", ""))

    server_names: dict[str, str] = {}
    for category, rel, tree in (("server_actions", "logic/server-actions.json", "Server Actions"),
                                ("service_actions", "logic/service-actions.json", "Service Actions")):
        for a in _load(pkg_root / rel):
            loc = _path("Logic", tree, a.get("folder"), a.get("name"))
            action_location[a.get("key", "")] = loc
            server_names[a.get("key", "")] = a.get("name", "")
            add(category, a.get("name", ""), loc, rel,
                details=_flags(a, ("is_public", "Public"), ("is_function", "Function")),
                key=a.get("key", ""))

    public_loc = {
        a.get("name"): action_location.get(a.get("key", ""))
        for a in _load(pkg_root / "logic/server-actions.json")
    }
    for a in _load(pkg_root / "integrations/public-actions.json"):
        add("public_actions", a.get("name", ""),
            public_loc.get(a.get("name")) or _path("Logic", "Server Actions", a.get("name")),
            "integrations/public-actions.json",
            details=_flags(a, ("is_function", "Function")))

    for a in _load(pkg_root / "integrations/dependency-actions.json"):
        dep = a.get("module", "")
        add("dependency_actions", a.get("name", ""),
            _path("Manage Dependencies", dep, a.get("name")),
            "integrations/dependency-actions.json", ownership=f"Consumed from {dep}")

    for q in _load(pkg_root / "logic/advanced-sql.json"):
        base = action_location.get(q.get("action_key", "")) or q.get("action", "")
        sql = " ".join((q.get("sql") or "").split())
        add("advanced_sql", q.get("node", ""), _path(base, q.get("node")),
            "logic/advanced-sql.json", details=sql[:120] + ("..." if len(sql) > 120 else ""),
            key=q.get("node_key", ""))

    for r in _load(pkg_root / "data/roles-and-permissions.json"):
        add("roles", r.get("name", ""), _path("Logic", "Roles", r.get("name")),
            "data/roles-and-permissions.json",
            details=_flags(r, ("is_public", "Public"), ("is_persistent", "Persistent")))

    for e in _load(pkg_root / "data/user-exceptions.json"):
        add("user_exceptions", e.get("name", ""), _path("Logic", "Exceptions", e.get("name")),
            "data/user-exceptions.json", key=e.get("key", ""))

    for api_file, direction in (("integrations/consumed-apis.json", "consumed"),
                                ("integrations/exposed-apis.json", "exposed")):
        for api in _load(pkg_root / api_file):
            kind = str(api.get("kind", "")).upper()
            if kind not in ("REST", "SOAP"):
                continue
            add(f"{kind.lower()}_{direction}", api.get("name", ""),
                _path("Logic", "Integrations", kind, api.get("name")), api_file,
                details=api.get("endpoint_url", "") or api.get("description", ""))

    # --- Data ----------------------------------------------------------------
    for e in _load(pkg_root / "data/entities.json"):
        add("entities", e.get("name", ""), _path("Data", "Entities", "Database", e.get("name")),
            "data/entities.json",
            details=_flags(e, ("is_static", "Static"), ("is_public", "Public")))

    for s in _load(pkg_root / "data/structures.json"):
        add("structures", s.get("name", ""), _path("Data", "Structures", s.get("name")),
            "data/structures.json", details=_flags(s, ("is_public", "Public")))

    for p in _load(pkg_root / "data/site-properties.json"):
        if p.get("is_platform"):
            continue
        add("site_properties", p.get("name", ""), _path("Data", "Site Properties", p.get("name")),
            "data/site-properties.json", details=_flags(p, ("is_secret", "Secret")))

    # --- Processes -----------------------------------------------------------
    for p in _load(pkg_root / "processes/transitions.json"):
        add("processes", p.get("name", ""), _path("Processes", "Processes", p.get("name")),
            "processes/transitions.json", key=p.get("key", ""))

    for t in _load(pkg_root / "processes/timers.json"):
        action_key = (t.get("action") or "").rsplit(".", 1)[-1]
        action = server_names.get(action_key, "")
        details = ", ".join(x for x in (
            f"runs {action}" if action else "",
            f"schedule {t['schedule']}" if t.get("schedule") else "",
        ) if x)
        add("timers", t.get("name", ""), _path("Processes", "Timers", t.get("name")),
            "processes/timers.json", details=details, key=t.get("key", ""))

    order = {c: i for i, c in enumerate(CATEGORY_LABELS)}
    rows.sort(key=lambda r: (order.get(r["category"], len(order)), r["location"].lower()))
    return rows


def extension_rows(ext_dir: Path) -> list[dict[str, str]]:
    """Rows for one extension folder written by ``os2native.extension``."""
    info_path = ext_dir / "extension.json"
    if not info_path.is_file():
        return []
    info = json.loads(info_path.read_text(encoding="utf-8"))
    name = info.get("name") or ext_dir.name
    src = "extension.json"
    base = _path("Integration Studio", name)

    def row(category: str, item: str, location: str, details: str = "", key: str = "") -> dict[str, str]:
        return {"module": name, "category": category, "name": item, "ownership": "Extension",
                "location": location, "details": details, "key": key, "source": src}

    sources = len(info.get("source_files_written", []))
    rows = [row("extensions", name, base,
                f"{info.get('server_type', '')}; {sources} source files; {info.get('description', '')}".strip("; "),
                info.get("key", ""))]
    for a in info.get("actions", []):
        ins = ", ".join(p["name"] for p in a.get("input_parameters", []))
        outs = ", ".join(p["name"] for p in a.get("output_parameters", []))
        rows.append(row("extension_actions", a.get("name", ""), _path(base, "Actions", a.get("name")),
                        f"in: {ins or '-'}; out: {outs or '-'}", a.get("key", "")))
    for s in info.get("structures", []):
        rows.append(row("extension_structures", s.get("name", ""),
                        _path(base, "Structures", s.get("name")),
                        f"{len(s.get('attributes', []))} attributes", s.get("key", "")))
    return rows


def extension_roots(output: Path) -> list[Path]:
    ext = output / "extensions"
    return sorted(p for p in ext.iterdir() if (p / "extension.json").is_file()) if ext.is_dir() else []


def build_rows(package_roots: list[Path], ext_roots: list[Path] = ()) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for root in sorted(package_roots, key=lambda p: p.name.lower()):
        rows.extend(module_rows(root))
    for ext_dir in sorted(ext_roots, key=lambda p: p.name.lower()):
        rows.extend(extension_rows(ext_dir))
    return rows


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def write_report(rows: list[dict[str, str]], output: Path) -> tuple[Path, Path]:
    """Write ``inventory-details.csv`` and ``inventory-details.md`` into ``output``."""
    csv_path = output / "inventory-details.csv"
    try:
        # utf-8-sig so Excel detects UTF-8
        fh = open(csv_path, "w", encoding="utf-8-sig", newline="")
    except PermissionError:
        # Windows locks a CSV that is open in Excel; don't fail the whole run.
        locked = csv_path
        csv_path = output / f"inventory-details-{datetime.now():%Y%m%d-%H%M%S}.csv"
        log.warning(
            "[report] %s is open in another program (e.g. Excel); wrote %s instead",
            locked.name, csv_path.name,
        )
        fh = open(csv_path, "w", encoding="utf-8-sig", newline="")
    with fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            folder = "extensions" if row["ownership"] == "Extension" else "extraction"
            writer.writerow({
                **row,
                "category": CATEGORY_LABELS.get(row["category"], row["category"]),
                "source": f"{folder}/{row['module']}/{row['source']}",
            })

    totals = Counter(r["category"] for r in rows)
    lines = [
        "# Inventory details",
        "",
        "Every counted item with its Service Studio location. Counts match `inventory.json`.",
        "",
        "## Totals",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    lines += [
        f"| {label} | {totals[cat]} |" for cat, label in CATEGORY_LABELS.items() if totals[cat]
    ]
    for module in sorted({r["module"] for r in rows}, key=str.lower):
        mod_rows = [r for r in rows if r["module"] == module]
        lines += ["", f"## {module}", ""]
        for cat, label in CATEGORY_LABELS.items():
            cat_rows = [r for r in mod_rows if r["category"] == cat]
            if not cat_rows:
                continue
            lines += [f"### {label} ({len(cat_rows)})", "",
                      "| Name | Location | Ownership | Details | Source |",
                      "|---|---|---|---|---|"]
            lines += [
                "| " + " | ".join(_md_cell(x) for x in (
                    r["name"], r["location"], r["ownership"], r["details"], r["source"])) + " |"
                for r in cat_rows
            ]
            lines.append("")
    md_path = output / "inventory-details.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path
