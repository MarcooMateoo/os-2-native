"""Top-level pipeline orchestration: Stage 1 -> Stage 2 -> Stage 3."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from os2native.run_log import log


# Canonical domain-worker execution order. assets MUST precede interface:
# the interface worker resolves image GUIDs against evidence/index.json, which
# the assets worker writes. Running interface first leaves the index missing
# and marks every image unresolved. (REC-002)
WORKER_ORDER = ["assets", "interface", "logic", "process", "data"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OML_DLL = REPO_ROOT / "bin" / "oml-utilities" / "oml.dll"


def _ordered_workers():
    """Return ``[(name, run_fn), ...]`` in ``WORKER_ORDER`` order (lazy imports)."""
    from os2native.workers.assets import run as run_assets
    from os2native.workers.interface import run as run_interface
    from os2native.workers.logic import run as run_logic
    from os2native.workers.process import run as run_process
    from os2native.workers.data import run as run_data

    fns = {
        "assets": run_assets,
        "interface": run_interface,
        "logic": run_logic,
        "process": run_process,
        "data": run_data,
    }
    return [(name, fns[name]) for name in WORKER_ORDER]


def run_pipeline(args: argparse.Namespace) -> int:
    """Execute the extraction pipeline for one module based on CLI arguments."""
    output = Path(args.output)

    # --- Stage 1: OML -> XML ---
    if args.oml:
        xml_path = _run_oml_to_xml(Path(args.oml), output)
        if args.xml_only:
            print(f"XML written to {xml_path}")
            return 0
    else:
        xml_path = Path(args.xml)

    if not xml_path.exists():
        log.error("[%s] XML file not found: %s", xml_path.stem, xml_path)
        return 1

    pkg_root = extract_xml(xml_path, output, domain=args.domain)
    from os2native.run_log import log_coverage_issues

    log_coverage_issues(pkg_root)
    print(f"Package written to {pkg_root}")
    return 0


def extract_xml(xml_path: Path, output: Path, domain: str | None = None) -> Path:
    """Run Stage 2 (+ Stage 3 unless ``domain`` is set) on an XML file.

    Returns the package root directory.
    """
    from os2native.xml_loader import load_oml_xml

    print(f"Loading XML: {xml_path}")
    doc = load_oml_xml(xml_path)

    if domain:
        pkg_root = output / doc.module_name
        dict(_ordered_workers())[domain](doc, pkg_root)
        return pkg_root

    return _run_extraction(doc, output, verbose=True)


def _run_extraction(doc, output: Path, verbose: bool = False) -> Path:
    """Run Stage 2 + Stage 3 on an already-loaded OmlDocument.

    Returns the package root directory.
    """
    from os2native.orchestrator import run_orchestrator

    pkg_root = output / doc.module_name

    for name, worker_fn in _ordered_workers():
        if verbose:
            print(f"  Running {name} worker...")
        worker_fn(doc, pkg_root)

    if verbose:
        print("  Running orchestrator...")
    run_orchestrator(doc, pkg_root)
    return pkg_root


def _dotnet_executable() -> str:
    """Pick the ``dotnet`` host that runs oml-utilities.

    Order: ``OS2NATIVE_DOTNET`` env var, then a per-user install made by
    dotnet-install.ps1 (``%LOCALAPPDATA%\\Microsoft\\dotnet``), then ``dotnet``
    on PATH. This keeps a user-folder .NET 10 private to os2native instead of
    putting it ahead of the system-wide install on PATH.
    """
    override = os.environ.get("OS2NATIVE_DOTNET")
    if override:
        return override
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        user_dotnet = Path(local_appdata) / "Microsoft" / "dotnet" / "dotnet.exe"
        if user_dotnet.is_file():
            return str(user_dotnet)
    return "dotnet"


def _run_oml_to_xml(oml_path: Path, output_dir: Path) -> Path:
    """Stage 1: Convert OML binary to XML using oml-utilities.

    Requires the .NET 10 runtime (see ``_dotnet_executable``). Set ``OML_DLL``
    to use a different oml-utilities build.
    """
    xml_path = output_dir / oml_path.with_suffix(".xml").name

    if xml_path.exists() and xml_path.stat().st_size > 0:
        return xml_path

    xml_path.parent.mkdir(parents=True, exist_ok=True)

    oml_dll = Path(os.environ.get("OML_DLL", OML_DLL))
    if not oml_dll.exists():
        raise FileNotFoundError(f"oml-utilities not found at {oml_dll}")

    result = subprocess.run(
        [_dotnet_executable(), str(oml_dll), "manipulate", str(oml_path), str(xml_path), "--format", "xml"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip() or "(no detail)"
        raise RuntimeError(f"oml-utilities failed (rc={result.returncode}): {detail}")
    if result.stderr.strip():
        log.warning("[%s] oml-utilities: %s", oml_path.stem, " | ".join(result.stderr.strip().splitlines()))
    return xml_path
