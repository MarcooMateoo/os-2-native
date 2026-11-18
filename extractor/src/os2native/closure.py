"""Dependency-closure utilities for entry-scoped extraction passes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from os2native.orchestrator import extract_dependency_references
from os2native.xml_loader import OmlDocument, load_oml_xml


@dataclass(frozen=True)
class ModuleXml:
    """Loaded XML plus the source file used to create it."""

    name: str
    xml_path: Path
    source_oml: Path | None
    doc: OmlDocument


class ClosureError(ValueError):
    """Raised when a closure cannot be built from the provided inputs."""


def build_closure_manifest(
    entry_module: str,
    xml_dir: Path,
    oml_dir: Path | None = None,
    *,
    closure_walk_at: str | None = None,
) -> dict[str, Any]:
    """Build a manifest for the entry module's dependency closure."""
    modules = index_xml_modules(xml_dir, oml_dir)
    if not modules:
        raise ClosureError(f"No XML files found in {xml_dir}")

    aliases = _build_aliases(modules)
    entry_name = _resolve_module_name(entry_module, modules, aliases)
    if entry_name is None:
        raise ClosureError(f"Entry module not found in XML set: {entry_module}")

    depths: dict[str, int] = {entry_name: 0}
    parents: dict[str, str | None] = {entry_name: None}
    references_by_module: dict[str, list[str]] = {}
    resolved_by_module: dict[str, list[str]] = {}
    unresolved_seen: set[tuple[str, str]] = set()
    unresolved_references: list[dict[str, Any]] = []
    pending: deque[str] = deque([entry_name])

    while pending:
        source_name = pending.popleft()
        source = modules[source_name]
        references = _direct_reference_names(source.doc)
        references_by_module[source_name] = references

        resolved_refs: list[str] = []
        for target_name in references:
            resolved_name = _resolve_module_name(target_name, modules, aliases)
            if resolved_name is None:
                key = (source_name, target_name)
                if key not in unresolved_seen:
                    unresolved_seen.add(key)
                    unresolved_references.append({
                        "source": source_name,
                        "target": target_name,
                        "depth": depths[source_name] + 1,
                        "reason": "missing_oml",
                    })
                continue

            resolved_refs.append(resolved_name)
            if resolved_name not in depths:
                depths[resolved_name] = depths[source_name] + 1
                parents[resolved_name] = source_name
                pending.append(resolved_name)

        resolved_by_module[source_name] = _unique_sorted(resolved_refs)

    closure_order = [entry_name]
    closure_order.extend(
        sorted(
            (name for name in depths if name != entry_name),
            key=lambda name: (depths[name], name.casefold()),
        )
    )
    closure_set = set(closure_order)
    excluded = sorted(
        (name for name in modules if name not in closure_set),
        key=str.casefold,
    )

    walk_at = closure_walk_at or _utc_now()
    manifest = {
        "entry_module": entry_name,
        "requested_entry_module": entry_module,
        "oml_source": oml_dir.name if oml_dir else xml_dir.name,
        "oml_source_path": str(oml_dir) if oml_dir else None,
        "xml_source": str(xml_dir),
        "closure_walk_at": walk_at,
        "modules_in_closure": [
            {
                "name": name,
                "depth": depths[name],
                "references": references_by_module.get(name, []),
                "resolved_references": resolved_by_module.get(name, []),
                "referenced_by": parents.get(name),
                "path": _reference_path(name, parents),
                "source_oml": _source_name(modules[name]),
            }
            for name in closure_order
        ],
        "modules_excluded": [
            {
                "name": name,
                "reason": "not_in_closure",
                "source_oml": _source_name(modules[name]),
            }
            for name in excluded
        ],
        "unresolved_references": sorted(
            unresolved_references,
            key=lambda item: (
                item["depth"],
                item["source"].casefold(),
                item["target"].casefold(),
            ),
        ),
        "summary": {
            "total_oml_count": len(modules),
            "closure_size": len(closure_order),
            "excluded_count": len(excluded),
            "max_depth": max(depths.values()) if depths else 0,
            "unresolved_reference_count": len(unresolved_references),
        },
    }
    return manifest


def index_xml_modules(xml_dir: Path, oml_dir: Path | None = None) -> dict[str, ModuleXml]:
    """Load all XML files from *xml_dir*, indexed by OML module name."""
    xml_dir = Path(xml_dir)
    source_by_stem = _source_oml_by_stem(oml_dir)
    modules: dict[str, ModuleXml] = {}
    duplicates: dict[str, list[Path]] = {}

    for xml_path in sorted(xml_dir.glob("*.xml"), key=lambda p: p.name.casefold()):
        doc = load_oml_xml(xml_path)
        name = doc.module_name
        if name in modules:
            duplicates.setdefault(name, [modules[name].xml_path]).append(xml_path)
            continue
        modules[name] = ModuleXml(
            name=name,
            xml_path=xml_path,
            source_oml=source_by_stem.get(xml_path.stem.casefold()),
            doc=doc,
        )

    if duplicates:
        rendered = ", ".join(
            f"{name}: {', '.join(str(path) for path in paths)}"
            for name, paths in sorted(duplicates.items())
        )
        raise ClosureError(f"Duplicate module names in XML set: {rendered}")
    return modules


def default_entry_pass_name(entry_module: str, oml_dir: Path, timestamp: str) -> str:
    """Return the default pass name for an entry-scoped pass."""
    module_slug = _slug(entry_module.lower())
    env_slug = _derive_environment_name(Path(oml_dir))
    return f"entry-{module_slug}-{env_slug}-{timestamp}"


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write a closure manifest as stable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_extraction_list(path: Path, manifest: dict[str, Any], xml_dir: Path) -> None:
    """Write a tab-separated module/XML list for shell orchestration."""
    modules = index_xml_modules(xml_dir, None)
    lines = []
    for item in manifest["modules_in_closure"]:
        name = item["name"]
        lines.append(f"{name}\t{modules[name].xml_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_module_list(path: Path, manifest: dict[str, Any]) -> None:
    """Write included module names, one per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(item["name"] for item in manifest["modules_in_closure"]) + "\n",
        encoding="utf-8",
    )


def _direct_reference_names(doc: OmlDocument) -> list[str]:
    references = [
        ref.get("module_name", "").strip()
        for ref in extract_dependency_references(doc)
    ]
    return _unique_sorted(name for name in references if name)


def _resolve_module_name(
    name: str,
    modules: dict[str, ModuleXml],
    aliases: dict[str, str],
) -> str | None:
    if name in modules:
        return name
    key = name.casefold()
    for module_name in modules:
        if module_name.casefold() == key:
            return module_name
    return aliases.get(key)


def _build_aliases(modules: dict[str, ModuleXml]) -> dict[str, str]:
    counts: dict[str, list[str]] = {}
    module_keys = {module_name.casefold() for module_name in modules}
    for name in modules:
        if "_" not in name:
            continue
        suffix = name.split("_", 1)[1].casefold()
        if suffix and suffix not in module_keys:
            counts.setdefault(suffix, []).append(name)
    return {
        suffix: names[0]
        for suffix, names in counts.items()
        if len(names) == 1
    }


def _reference_path(name: str, parents: dict[str, str | None]) -> list[str]:
    path = [name]
    current = parents.get(name)
    while current is not None:
        path.append(current)
        current = parents.get(current)
    return list(reversed(path))


def _unique_sorted(values: Any) -> list[str]:
    return sorted(set(values), key=str.casefold)


def _source_oml_by_stem(oml_dir: Path | None) -> dict[str, Path]:
    if oml_dir is None:
        return {}
    return {
        path.stem.casefold(): path
        for path in Path(oml_dir).glob("*.oml")
    }


def _source_name(module: ModuleXml) -> str | None:
    return module.source_oml.name if module.source_oml else None


def _derive_environment_name(oml_dir: Path) -> str:
    name = oml_dir.name.lower()
    parts = [part for part in re.split(r"[-_]+", name) if part and part not in {"oml", "omls"}]
    if parts:
        return _slug(parts[-1])
    return _slug(name) or "oml"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "-", value.lower()).strip("-")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an entry-module dependency closure manifest")
    parser.add_argument("--entry-module", required=True, help="Entry OML module name")
    parser.add_argument("--xml-dir", type=Path, help="Directory containing Stage 1 XML files")
    parser.add_argument("--oml-dir", type=Path, default=None, help="Source OML directory")
    parser.add_argument("--manifest", type=Path, help="Manifest output path")
    parser.add_argument("--extraction-list", type=Path, help="Write module/XML TSV for scoped extraction")
    parser.add_argument("--module-list", type=Path, help="Write included module names")
    parser.add_argument("--timestamp", default=None, help="Timestamp to use for generated pass names")
    parser.add_argument("--print-pass-name", action="store_true", help="Print the default scoped pass name and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.print_pass_name:
        timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        print(default_entry_pass_name(args.entry_module, args.oml_dir or Path("oml"), timestamp))
        return 0

    if args.xml_dir is None:
        parser.error("--xml-dir is required unless --print-pass-name is set")
    if args.manifest is None:
        parser.error("--manifest is required unless --print-pass-name is set")

    try:
        manifest = build_closure_manifest(args.entry_module, args.xml_dir, args.oml_dir)
        write_manifest(args.manifest, manifest)
        if args.extraction_list:
            write_extraction_list(args.extraction_list, manifest, args.xml_dir)
        if args.module_list:
            write_module_list(args.module_list, manifest)
    except ClosureError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for unresolved in manifest["unresolved_references"]:
        print(
            json.dumps({
                "level": "warning",
                "type": "missing_reference",
                "source": unresolved["source"],
                "target": unresolved["target"],
                "reason": unresolved["reason"],
            }, sort_keys=True),
            file=sys.stderr,
        )

    print(
        f"Closure size: {manifest['summary']['closure_size']} "
        f"of {manifest['summary']['total_oml_count']} modules"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
