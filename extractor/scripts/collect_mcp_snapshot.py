"""Copy a module that is open in Service Studio into a project's INPUTS.

When a module is missing from the export (an ``unknown`` dependency, a missing
theme module, a module that failed), the user can open it in Service Studio
instead of exporting it. Service Studio's MCP Server snapshots the open
module to ``%TEMP%\\ServiceStudio.MCPServer\\<runId>-in.oml`` the first time
an agent reads it (``getDataModel``, ``listApps`` + any ``get*`` read). That
snapshot is a normal ``.oml``, so it can go through the regular pipeline.

This script finds the newest snapshot of ``--module`` and copies it to
``<INPUTS>\\mcp\\<Module>.oml`` with a ``<Module>.capture.json`` note. Only
``-in.oml`` snapshots of the live module are used; ``-out.oml`` files are
edits made by an agent and are never copied.

Usage::

    python scripts/collect_mcp_snapshot.py ..\\projects\\<Project>\\INPUTS --module PaymentAPI

Then re-run ``os2native package`` with ``INPUTS\\mcp\\<Module>.oml`` added.
Exit code 0 when copied, 1 when no snapshot of the module was found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from os2native.pipeline import _run_oml_to_xml

_ESPACE_NAME = re.compile(r'<ESpace\b[^>]*?\sName="([^"]*)"')


def default_snapshot_dir() -> Path:
    return Path(os.environ.get("TEMP", tempfile.gettempdir())) / "ServiceStudio.MCPServer"


def find_snapshots(snapshot_dir: Path) -> list[Path]:
    """Live-module snapshots (``*-in.oml``), newest first."""
    if not snapshot_dir.is_dir():
        return []
    return sorted(
        snapshot_dir.glob("*-in.oml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def module_name_of_xml(xml_path: Path) -> str | None:
    """Read the ESpace Name from a decoded module XML without parsing it all."""
    tail = ""
    with xml_path.open(encoding="utf-8", errors="replace") as fh:
        while chunk := fh.read(1 << 20):
            m = _ESPACE_NAME.search(tail + chunk)
            if m:
                return m.group(1)
            tail = chunk[-4096:]
    return None


def module_name_of_oml(oml_path: Path, work_dir: Path) -> str | None:
    try:
        xml_path = _run_oml_to_xml(oml_path, work_dir)
    except (RuntimeError, OSError) as exc:
        print(f"  skipped {oml_path.name}: {exc}", file=sys.stderr)
        return None
    return module_name_of_xml(xml_path)


def collect(
    inputs_dir: Path,
    module: str,
    snapshot_dir: Path,
    max_candidates: int = 10,
) -> Path | None:
    """Copy the newest snapshot of *module* into ``inputs_dir/mcp``."""
    candidates = find_snapshots(snapshot_dir)[:max_candidates]
    with tempfile.TemporaryDirectory(prefix="os2native-mcp-") as tmp:
        for snap in candidates:
            name = module_name_of_oml(snap, Path(tmp))
            if name is None or name.casefold() != module.casefold():
                continue
            dest_dir = inputs_dir / "mcp"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{name}.oml"
            shutil.copyfile(snap, dest)
            note = {
                "module": name,
                "source": "Service Studio MCP snapshot",
                "snapshot": str(snap),
                "snapshot_modified": datetime.fromtimestamp(
                    snap.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "copied": datetime.now(timezone.utc).isoformat(),
                "sha1": hashlib.sha1(dest.read_bytes()).hexdigest(),
            }
            (dest_dir / f"{name}.capture.json").write_text(
                json.dumps(note, indent=2), encoding="utf-8"
            )
            return dest
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy a module open in Service Studio (MCP snapshot) into INPUTS\\mcp\\",
    )
    parser.add_argument("inputs_dir", type=Path, help="The project's INPUTS folder")
    parser.add_argument("--module", required=True, help="Module name as shown in Service Studio")
    parser.add_argument(
        "--snapshot-dir", type=Path, default=default_snapshot_dir(),
        help="Service Studio MCP temp folder (default: %%TEMP%%\\ServiceStudio.MCPServer)",
    )
    parser.add_argument(
        "--max-candidates", type=int, default=10,
        help="How many of the newest snapshots to check (default 10)",
    )
    args = parser.parse_args()

    if not args.inputs_dir.is_dir():
        print(f"ERROR: {args.inputs_dir} is not a folder", file=sys.stderr)
        return 1

    dest = collect(args.inputs_dir, args.module, args.snapshot_dir, args.max_candidates)
    if dest is None:
        print(
            f"No Service Studio MCP snapshot of '{args.module}' in {args.snapshot_dir}.\n"
            "Open the module in Service Studio, start the MCP Server, and have the agent\n"
            "read it once (e.g. getDataModel) so Service Studio takes a snapshot. Note that\n"
            "restarting Service Studio clears that folder.",
            file=sys.stderr,
        )
        return 1
    print(f"Copied {args.module} -> {dest}")
    print("Re-run `os2native package` with this file added (see HOW_TO_EXTRACT.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
