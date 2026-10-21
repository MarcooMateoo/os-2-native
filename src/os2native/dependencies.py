"""Dependencies that extracted modules use but the package did not include.

Reads ``<out>/extraction/<Module>/dependencies/graph.json`` for every module
and classifies each referenced module that is neither extracted nor provided
by a parsed extension (``<out>/extensions/<Name>/``):

- ``platform``: OutSystems built-ins and mobile plugins (expected to be absent)
- ``forge``:    known Forge components (absent, but need a native replacement)
- ``unknown``:  not in scripts/known-external.json; probably a module or
                extension that should have been in the package
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KNOWN_EXTERNAL = Path(__file__).resolve().parent.parent.parent / "scripts" / "known-external.json"


def load_known(path: Path = KNOWN_EXTERNAL) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def classify(target: str, known: dict) -> str:
    if target in set(known.get("platform_builtins", [])) | set(known.get("mobile_plugins", [])):
        return "platform"
    if target in set(known.get("forge_extensions", [])):
        return "forge"
    return "unknown"


def check(out: Path, known: dict) -> dict[str, Any]:
    root = out / "extraction"
    if not root.is_dir():
        raise FileNotFoundError(f"Extraction directory not found: {root}")
    present = sorted(p.name for p in root.iterdir() if p.is_dir())
    ext_root = out / "extensions"
    extensions_present = sorted(
        p.name for p in ext_root.iterdir() if (p / "extension.json").is_file()
    ) if ext_root.is_dir() else []

    used_by: dict[str, set[str]] = {}
    for module in present:
        graph_path = root / module / "dependencies" / "graph.json"
        if not graph_path.is_file():
            continue
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        for edge in graph.get("edges", []):
            if edge.get("direction") == "outbound":
                used_by.setdefault(edge["target"], set()).add(module)

    missing = [
        {"module": target, "kind": classify(target, known), "used_by": sorted(users)}
        for target, users in sorted(used_by.items())
        if target not in present and target not in extensions_present
    ]
    extensions = []
    inventory_path = out / "inventory.json"
    if inventory_path.is_file():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        extensions = inventory.get("extensions_not_extracted", [])

    return {
        "modules_in_package": present,
        "extensions_in_package": extensions_present,
        "missing_dependencies": missing,
        "unknown_count": sum(1 for m in missing if m["kind"] == "unknown"),
        "forge_count": sum(1 for m in missing if m["kind"] == "forge"),
        "extensions_not_extracted": extensions,
    }


def write_check(out: Path, known: dict | None = None) -> dict[str, Any]:
    """Run ``check`` and write ``<out>/dependency-check.json``."""
    report = check(out, load_known() if known is None else known)
    (out / "dependency-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
