"""Dependencies that extracted modules use but the package did not include.

Reads ``<out>/extraction/<Module>/dependencies/graph.json`` for every module
of the last ``package`` run and classifies each referenced module that is
neither extracted nor provided by a parsed extension
(``<out>/extensions/<Name>/``):

- ``platform``:     OutSystems built-ins and mobile plugins (expected to be absent)
- ``forge``:        known Forge components (absent, but need a native replacement)
- ``accepted``:     the user decided it isn't needed (``accepted_missing`` in
                    the project's ``project.json``)
- ``indirect``:     only used by modules outside the scope, e.g. a dependency
                    module added to INPUTS that has dependencies of its own;
                    reported, never asked about
- ``unknown``:      used directly by an in-scope module and not explained by any
                    of the above; probably a module or extension missing from
                    the export

Only direct use counts. Adding a missing module brings its own dependencies,
and asking about those too never ends: they matter only if the in-scope app
uses them itself.

Project decisions live in ``projects/<Project>/project.json`` (next to
``INPUTS`` and ``OUTPUT``)::

    {
      "scope": {"applications": ["Sales Portal", "Sales Portal Core"], "modules": []},
      "accepted_missing": {"SVGUtilities": "Icons only; replaced natively"}
    }

``scope.applications`` are names from ``<out>/applications.json`` (read from
the package manifest). With no scope in ``project.json``, the scope is every
application in the package manifests: loose ``.oml``/``.xif`` files added to
INPUTS are dependencies, not scope. Without any manifest, every module is in
scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KNOWN_EXTERNAL = Path(__file__).resolve().parent.parent.parent / "scripts" / "known-external.json"
PROJECT_CONFIG = "project.json"
MAX_USED_ELEMENTS = 15


def load_known(path: Path = KNOWN_EXTERNAL) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def default_project_config(out: Path) -> Path:
    """``projects/<Project>/project.json`` for ``projects/<Project>/OUTPUT``."""
    return out.resolve().parent / PROJECT_CONFIG


def load_project(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def classify(target: str, known: dict) -> str:
    if target in set(known.get("platform_builtins", [])) | set(known.get("mobile_plugins", [])):
        return "platform"
    if target in set(known.get("forge_extensions", [])):
        return "forge"
    return "unknown"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_modules(out: Path) -> list[str]:
    """Modules of the last ``package`` run.

    ``extraction/`` isn't cleared between runs, so folders left from an
    earlier package are not part of this one. ``inventory.json`` lists the
    modules the run extracted; without it every folder counts.
    """
    root = out / "extraction"
    folders = sorted(p.name for p in root.iterdir() if p.is_dir())
    inventory = _load_json(out / "inventory.json") or {}
    listed = [m.get("module") for m in inventory.get("modules", []) if m.get("module")]
    if not listed:
        return folders
    return sorted(m for m in listed if m in set(folders))


def resolve_scope(
    project: dict[str, Any],
    applications: list[dict[str, Any]],
    present: list[str],
) -> tuple[set[str] | None, list[str]]:
    """Modules named by ``project["scope"]``; ``None`` when there is no scope.

    Returns the module set and the scope names that matched nothing.
    """
    scope = project.get("scope") or {}
    apps = scope.get("applications") or []
    mods = scope.get("modules") or []
    if not apps and not mods:
        return None, []
    by_app = {a["name"].casefold(): a.get("modules", []) for a in applications}
    present_cf = {m.casefold(): m for m in present}
    selected: set[str] = set()
    unmatched: list[str] = []
    for app in apps:
        modules = by_app.get(app.casefold())
        if modules is None:
            unmatched.append(app)
            continue
        selected.update(present_cf[m.casefold()] for m in modules if m.casefold() in present_cf)
    for mod in mods:
        if mod.casefold() in present_cf:
            selected.add(present_cf[mod.casefold()])
        else:
            unmatched.append(mod)
    return selected, unmatched


def project_scope(
    out: Path,
    project: dict[str, Any] | None = None,
    present: list[str] | None = None,
) -> tuple[set[str], str, list[str]]:
    """In-scope modules of a project's ``OUTPUT``: ``(modules, source, unmatched)``.

    ``project.json`` scope first, then every app in the package manifest, then
    every module of the run. Shared by the dependency check and the deliverable
    generators so both use the same scope.
    """
    if project is None:
        project = load_project(default_project_config(out))
    if present is None:
        present = run_modules(out)
    applications = _load_json(out / "applications.json") or []
    modules, unmatched = resolve_scope(project, applications, present)
    if modules is not None:
        return modules, "project.json", unmatched
    modules = default_scope(applications, present)
    if modules is not None:
        return modules, "package manifest", unmatched
    return set(present), "all modules", unmatched


def default_scope(applications: list[dict[str, Any]], present: list[str]) -> set[str] | None:
    """Modules of the manifest applications; ``None`` when there is no manifest."""
    manifest_apps = [a for a in applications if a.get("package")]
    if not manifest_apps:
        return None
    names = {m.casefold() for a in manifest_apps for m in a.get("modules", [])}
    return {m for m in present if m.casefold() in names}


def _used_elements(root: Path, users: list[str], target: str) -> list[str]:
    """What the users take from ``target`` (e.g. ``structure PaymentAPI.Receipt``)."""
    found: list[str] = []
    for module in users:
        refs = _load_json(root / module / "dependencies" / "references.json") or []
        for ref in refs if isinstance(refs, list) else []:
            if ref.get("source_module") == target:
                item = f"{ref.get('type', '?')} {ref.get('reference_id', '?')}"
                if item not in found:
                    found.append(item)
    return found[:MAX_USED_ELEMENTS]


def check(
    out: Path,
    known: dict,
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = out / "extraction"
    if not root.is_dir():
        raise FileNotFoundError(f"Extraction directory not found: {root}")
    project = project or {}
    present = run_modules(out)
    stale = sorted(
        p.name for p in root.iterdir() if p.is_dir() and p.name not in set(present)
    )
    ext_root = out / "extensions"
    extensions_present = sorted(
        p.name for p in ext_root.iterdir() if (p / "extension.json").is_file()
    ) if ext_root.is_dir() else []

    edges: dict[str, set[str]] = {}
    used_by: dict[str, set[str]] = {}
    for module in present:
        graph = _load_json(root / module / "dependencies" / "graph.json") or {}
        targets = {
            e["target"] for e in graph.get("edges", []) if e.get("direction") == "outbound"
        }
        edges[module] = targets
        for target in targets:
            used_by.setdefault(target, set()).add(module)

    applications = _load_json(out / "applications.json") or []
    scope_modules, scope_source, unmatched = project_scope(out, project, present)
    in_scope = scope_modules
    accepted = {
        name.casefold(): reason
        for name, reason in (project.get("accepted_missing") or {}).items()
    }

    missing = []
    for target, users in sorted(used_by.items()):
        if target in present or target in extensions_present:
            continue
        scoped_users = sorted(users & in_scope)
        kind = classify(target, known)
        if kind == "unknown":
            if target.casefold() in accepted:
                kind = "accepted"
            elif not scoped_users:
                kind = "indirect"
        entry: dict[str, Any] = {
            "module": target,
            "kind": kind,
            "used_by": sorted(users),
            "in_scope_users": scoped_users,
        }
        if kind == "accepted":
            entry["reason"] = accepted[target.casefold()]
        if kind in ("unknown", "forge"):
            entry["used_elements"] = _used_elements(root, scoped_users or sorted(users), target)
        missing.append(entry)

    inventory = _load_json(out / "inventory.json") or {}
    return {
        "modules_in_package": present,
        "extensions_in_package": extensions_present,
        "scope": {
            "source": scope_source,
            "applications": (project.get("scope") or {}).get("applications", [])
            if scope_source == "project.json"
            else [a["name"] for a in applications if a.get("package")],
            "modules": sorted(in_scope),
            "unmatched": unmatched,
        },
        "stale_extraction_folders": stale,
        "missing_dependencies": missing,
        "unknown_count": sum(1 for m in missing if m["kind"] == "unknown"),
        "forge_count": sum(1 for m in missing if m["kind"] == "forge"),
        "indirect_count": sum(1 for m in missing if m["kind"] == "indirect"),
        "accepted_count": sum(1 for m in missing if m["kind"] == "accepted"),
        "extensions_not_extracted": inventory.get("extensions_not_extracted", []),
    }


def write_check(
    out: Path,
    known: dict | None = None,
    project_config: Path | None = None,
) -> dict[str, Any]:
    """Run ``check`` and write ``<out>/dependency-check.json``."""
    config = project_config if project_config is not None else default_project_config(out)
    report = check(out, load_known() if known is None else known, load_project(config))
    report["project_config"] = str(config) if config.is_file() else None
    (out / "dependency-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
