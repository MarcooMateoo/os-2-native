"""Tests for scripts/check_dependencies.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_dependencies import check, main  # noqa: E402

KNOWN = {
    "platform_builtins": ["(System)", "OutSystemsUI"],
    "mobile_plugins": ["CameraPlugin"],
    "forge_extensions": ["CryptoAPI"],
}


def _module(out: Path, name: str, targets: list[str]) -> None:
    deps = out / "extraction" / name / "dependencies"
    deps.mkdir(parents=True)
    edges = [{"source": name, "target": t, "direction": "outbound"} for t in targets]
    (deps / "graph.json").write_text(json.dumps({"module": name, "edges": edges}))


def test_classifies_missing_dependencies(tmp_path):
    _module(tmp_path, "App", ["(System)", "App_CS", "CryptoAPI", "HiQRCoder", "CameraPlugin"])
    _module(tmp_path, "App_CS", ["(System)", "OutSystemsUI", "HiQRCoder"])

    report = check(tmp_path, KNOWN)

    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    assert "App_CS" not in by_name  # present in the package
    assert by_name["(System)"]["kind"] == "platform"
    assert by_name["CameraPlugin"]["kind"] == "platform"
    assert by_name["CryptoAPI"]["kind"] == "forge"
    assert by_name["HiQRCoder"] == {"module": "HiQRCoder", "kind": "unknown", "used_by": ["App", "App_CS"]}
    assert report["unknown_count"] == 1
    assert report["forge_count"] == 1


def test_main_writes_report_and_fails_on_unknown(tmp_path):
    _module(tmp_path, "App", ["(System)", "Mystery"])
    (tmp_path / "inventory.json").write_text(json.dumps({"extensions_not_extracted": ["X.xif"]}))

    assert main([str(tmp_path)]) == 1
    report = json.loads((tmp_path / "dependency-check.json").read_text(encoding="utf-8"))
    assert report["extensions_not_extracted"] == ["X.xif"]
    assert [d["module"] for d in report["missing_dependencies"]] == ["(System)", "Mystery"]


def test_main_passes_when_only_platform_missing(tmp_path):
    _module(tmp_path, "App", ["(System)"])
    assert main([str(tmp_path)]) == 0
