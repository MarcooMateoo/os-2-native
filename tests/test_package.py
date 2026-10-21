"""Tests for package unpacking, inventory aggregation and the CLI wiring."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from os2native.cli import build_parser, main
from os2native.package import build_inventory, module_inventory, unpack_package
from os2native.pipeline import OML_DLL, WORKER_ORDER


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_unpack_collects_modules_and_extensions(tmp_path):
    pkg = _make_zip(
        tmp_path / "App.oap",
        {
            "modules/App_CW.oml": b"oml",
            "modules/App_CS.oml": b"oml",
            "extensions/Crypto.xif": b"xif",
            "readme.txt": b"ignored",
        },
    )
    found = unpack_package(pkg, tmp_path / "out")
    assert [p.name for p in found["modules"]] == ["App_CS.oml", "App_CW.oml"]
    assert [p.name for p in found["extensions"]] == ["Crypto.xif"]
    assert not (tmp_path / "out" / "readme.txt").exists()


def test_unpack_recurses_into_nested_packages(tmp_path):
    inner = _make_zip(tmp_path / "Inner.oap", {"Inner_CW.oml": b"oml"})
    outer = _make_zip(tmp_path / "Solution.osp", {"Inner.oap": inner.read_bytes(), "Top.oml": b"oml"})
    found = unpack_package(outer, tmp_path / "out")
    assert sorted(p.name for p in found["modules"]) == ["Inner_CW.oml", "Top.oml"]


def test_unpack_blocks_path_traversal(tmp_path):
    pkg = _make_zip(tmp_path / "Evil.oap", {"../../escape.oml": b"oml"})
    found = unpack_package(pkg, tmp_path / "out")
    assert found["modules"] == [tmp_path / "out" / "escape.oml"]
    assert not (tmp_path / "escape.oml").exists()


def test_unpack_rejects_non_zip(tmp_path):
    bogus = tmp_path / "bogus.osp"
    bogus.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        unpack_package(bogus, tmp_path / "out")


def test_inventory_counts_and_totals(tmp_path):
    for name, screens, roles in [("ModA", 3, 1), ("ModB", 2, 0)]:
        root = tmp_path / name
        (root / "interfaces").mkdir(parents=True)
        (root / "data").mkdir()
        (root / "interfaces" / "screens.json").write_text(json.dumps([{}] * screens))
        (root / "data" / "roles-and-permissions.json").write_text(json.dumps([{}] * roles))

    assert module_inventory(tmp_path / "ModA")["counts"]["screens"] == 3
    inv = build_inventory([tmp_path / "ModA", tmp_path / "ModB"], [Path("X.xif")], {})
    assert inv["module_count"] == 2
    assert inv["totals"]["screens"] == 5
    assert inv["totals"]["roles"] == 1
    assert inv["totals"]["entities"] == 0
    assert inv["extensions_not_extracted"] == ["X.xif"]


def test_cli_parses_commands():
    parser = build_parser()
    args = parser.parse_args(["extract", "--xml", "m.xml", "--domain", "data"])
    assert args.xml == "m.xml" and args.domain == "data"
    args = parser.parse_args(["package", "App.osp", "--output", "out"])
    assert args.inputs == ["App.osp"] and args.output == "out"
    args = parser.parse_args(["package", "App.osp", "Extra.oml", "Ext.xif", "--output", "out"])
    assert args.inputs == ["App.osp", "Extra.oml", "Ext.xif"]


def test_cli_xml_only_requires_oml():
    assert main(["extract", "--xml", "m.xml", "--xml-only"]) == 2


def test_all_workers_and_orchestrator_import():
    from os2native.pipeline import _ordered_workers
    from os2native.orchestrator import run_orchestrator  # noqa: F401

    assert [name for name, _ in _ordered_workers()] == WORKER_ORDER


def test_oml_utilities_bundled():
    bundle = OML_DLL.parent
    for name in [
        "oml.dll",
        "oml.deps.json",
        "oml.runtimeconfig.json",
        "CommandDotNet.dll",
        "Serilog.dll",
        "Microsoft.Bcl.AsyncInterfaces.dll",
    ]:
        assert (bundle / name).is_file(), name
