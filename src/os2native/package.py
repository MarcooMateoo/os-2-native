"""Solution/application package handling (.osp / .oap) and estate inventory.

OutSystems solution (.osp) and application (.oap) packages are ZIP archives
holding one ``.oml`` per module plus ``.xif`` files for Integration Studio
extensions. This module unpacks them, runs the extraction pipeline on every
module, and aggregates per-module counts into ``inventory.json``.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from os2native.inventory_report import (
    CATEGORY_LABELS,
    build_rows,
    extension_roots,
    extension_rows,
    module_rows,
    write_report,
)
from os2native.run_log import issue_log, log, log_coverage_issues

PACKAGE_SUFFIXES = {".osp", ".oap"}
MODULE_SUFFIX = ".oml"
EXTENSION_SUFFIX = ".xif"


def unpack_package(package_path: Path, dest_dir: Path) -> dict[str, list[Path]]:
    """Extract ``.oml`` and ``.xif`` members of a package into ``dest_dir``.

    Nested packages (an .osp containing .oap files) are unpacked recursively.
    Member paths are flattened to their basename, so a crafted archive cannot
    write outside ``dest_dir``.

    Returns ``{"modules": [...oml paths], "extensions": [...xif paths]}``.
    """
    if not zipfile.is_zipfile(package_path):
        raise ValueError(f"{package_path} is not a ZIP-based OutSystems package")

    dest_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, list[Path]] = {"modules": [], "extensions": []}

    with zipfile.ZipFile(package_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename.replace("\\", "/")).name
            suffix = Path(name).suffix.lower()
            if suffix not in {MODULE_SUFFIX, EXTENSION_SUFFIX} | PACKAGE_SUFFIXES:
                continue
            target = dest_dir / name
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            if suffix == MODULE_SUFFIX:
                found["modules"].append(target)
            elif suffix == EXTENSION_SUFFIX:
                found["extensions"].append(target)
            else:
                nested = unpack_package(target, dest_dir / Path(name).stem)
                found["modules"].extend(nested["modules"])
                found["extensions"].extend(nested["extensions"])

    found["modules"].sort()
    found["extensions"].sort()
    return found


def _load(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def module_inventory(pkg_root: Path) -> dict[str, Any]:
    """Count the main artifact types in one extracted module package.

    Counts are the row counts of the itemised report (inventory_report), so
    ``inventory.json`` and ``inventory-details.*`` can never disagree. The
    rules that align them with Service Studio (own vs consumed, platform site
    properties, CSS-only stylesheets, ...) live in ``module_rows``.
    """
    per_category = Counter(row["category"] for row in module_rows(pkg_root))
    counts = {key: per_category.get(key, 0) for key in CATEGORY_LABELS}
    deps = _load(pkg_root / "dependencies" / "graph.json")
    return {"module": pkg_root.name, "counts": counts, "dependency_graph": deps}


def extension_inventory(ext_dir: Path) -> dict[str, Any]:
    per_category = Counter(row["category"] for row in extension_rows(ext_dir))
    counts = {key: per_category.get(key, 0) for key in CATEGORY_LABELS}
    return {"extension": ext_dir.name, "counts": counts}


def build_inventory(
    package_roots: list[Path],
    extensions: list[Path],
    failures: dict[str, str],
    ext_roots: list[Path] = (),
) -> dict[str, Any]:
    """Aggregate counts. ``extensions`` are .xif files that could not be parsed."""
    modules = [module_inventory(root) for root in package_roots]
    exts = [extension_inventory(root) for root in ext_roots]
    totals: dict[str, int] = {key: 0 for key in CATEGORY_LABELS}
    for item in modules + exts:
        for key, value in item["counts"].items():
            totals[key] += value
    return {
        "module_count": len(modules),
        "extension_count": len(exts),
        "totals": totals,
        "modules": [{"module": m["module"], "counts": m["counts"]} for m in modules],
        "extensions": [
            {"extension": e["extension"], "counts": {k: v for k, v in e["counts"].items() if v}}
            for e in exts
        ],
        "extensions_not_extracted": [p.name for p in extensions],
        "failed_modules": failures,
    }


def collect_inputs(inputs: list[Path], source_dir: Path) -> dict[str, list[Path]]:
    """Gather modules and extensions from packages and loose .oml/.xif files.

    Packages are unpacked into ``source_dir``; loose files are copied there
    (unless they already live there), so ``_source/`` holds every input.
    """
    found: dict[str, list[Path]] = {"modules": [], "extensions": []}
    source_dir.mkdir(parents=True, exist_ok=True)
    for item in inputs:
        suffix = item.suffix.lower()
        if not item.is_file():
            log.error("[input] %s: file not found", item)
        elif suffix in PACKAGE_SUFFIXES:
            try:
                unpacked = unpack_package(item, source_dir)
            except (ValueError, zipfile.BadZipFile, OSError) as exc:
                log.error("[input] %s: could not unpack package: %s", item.name, exc)
                continue
            found["modules"].extend(unpacked["modules"])
            found["extensions"].extend(unpacked["extensions"])
        elif suffix in (MODULE_SUFFIX, EXTENSION_SUFFIX):
            target = source_dir / item.name
            if item.resolve() != target.resolve():
                shutil.copyfile(item, target)
            found["modules" if suffix == MODULE_SUFFIX else "extensions"].append(target)
        else:
            log.error("[input] %s: unsupported file type (expected .osp, .oap, .oml or .xif)", item.name)
    for key in found:
        found[key] = sorted(set(found[key]))
    return found


def run_package(inputs: list[Path] | Path, output: Path, xml_only: bool = False) -> int:
    """Extract every module and extension in ``inputs``; write inventory + report.

    Errors and warnings go to ``output/extraction-issues.log``.
    """
    inputs = [inputs] if isinstance(inputs, Path) else list(inputs)
    names = " ".join(p.name for p in inputs)
    with issue_log(output, f"package {names}", fresh=True) as issues:
        try:
            failed = _run_package(inputs, output, xml_only, issues)
        except Exception as exc:  # anything not handled per module
            log.error("[run] package run stopped: %s", exc, exc_info=True)
            failed = True
    return 1 if failed or issues.errors else 0


def _run_package(inputs: list[Path], output: Path, xml_only: bool, issues) -> bool:
    from os2native.extension import extract_extension
    from os2native.pipeline import _run_oml_to_xml, extract_xml

    found = collect_inputs(inputs, output / "_source")
    print(
        f"Found {len(found['modules'])} module(s) and "
        f"{len(found['extensions'])} extension(s) in {', '.join(p.name for p in inputs)}"
    )

    xml_dir = output / "_xml"
    package_roots: list[Path] = []
    failures: dict[str, str] = {}
    for oml in found["modules"]:
        print(f"[{oml.stem}]")
        try:
            xml_path = _run_oml_to_xml(oml, xml_dir)
        except Exception as exc:  # keep going; one bad module shouldn't stop the estate
            failures[oml.name] = str(exc)
            log.error("[%s] OML to XML conversion failed: %s", oml.stem, exc, exc_info=True)
            continue
        if xml_only:
            continue
        try:
            # extraction/<module>/ is the layout scripts/generator_utils.py reads
            pkg_root = extract_xml(xml_path, output / "extraction")
        except Exception as exc:
            failures[oml.name] = str(exc)
            log.error("[%s] extraction failed: %s", oml.stem, exc, exc_info=True)
            continue
        package_roots.append(pkg_root)
        log_coverage_issues(pkg_root)

    if xml_only:
        return bool(failures)

    ext_roots: list[Path] = []
    unparsed: list[Path] = []
    for xif in found["extensions"]:
        print(f"[{xif.stem}] (extension)")
        try:
            ext_roots.append(extract_extension(xif, output / "extensions"))
        except Exception as exc:
            unparsed.append(xif)
            failures[xif.name] = str(exc)
            log.error("[%s] extension could not be parsed: %s", xif.stem, exc, exc_info=True)

    inventory = build_inventory(package_roots, unparsed, failures, ext_roots)
    inv_path = output / "inventory.json"
    inv_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    csv_path, md_path = write_report(build_rows(package_roots, ext_roots), output)
    print(f"Inventory details written to {csv_path} and {md_path.name}")

    if package_roots:
        _log_missing_dependencies(output)

    inventory["issues"] = issues.summary()
    inv_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"Inventory written to {inv_path}")
    return bool(failures)


def _log_missing_dependencies(output: Path) -> None:
    from os2native.dependencies import write_check

    try:
        report = write_check(output)
    except Exception as exc:
        log.warning("[dependencies] dependency check failed: %s", exc)
        return
    for dep in report["missing_dependencies"]:
        if dep["kind"] == "unknown":
            log.warning(
                "[dependencies] %s is used by %s but is not in the package and is not a known "
                "platform/Forge component; add its .oml/.xif to the run or to known-external.json",
                dep["module"], ", ".join(dep["used_by"]),
            )


def run_report(output: Path) -> int:
    """Rebuild ``inventory.json`` counts and details from an existing ``package`` output."""
    with issue_log(output, "report", fresh=False) as issues:
        roots = sorted(p for p in (output / "extraction").iterdir() if p.is_dir())
        ext_roots = extension_roots(output)
        previous = _load(output / "inventory.json") or {}
        inventory = build_inventory(roots, [], previous.get("failed_modules", {}), ext_roots)
        inventory["extensions_not_extracted"] = previous.get("extensions_not_extracted", [])
        csv_path, md_path = write_report(build_rows(roots, ext_roots), output)
        prev_issues = previous.get("issues") or {}
        inventory["issues"] = {
            "errors": prev_issues.get("errors", 0) + issues.errors,
            "warnings": prev_issues.get("warnings", 0) + issues.warnings,
            "log": issues.summary()["log"],
        }
        (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        print(f"Inventory written to {output / 'inventory.json'}, {csv_path.name}, {md_path.name}")
    return 0
