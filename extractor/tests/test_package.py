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


def test_applications_are_read_from_the_manifest(tmp_path):
    import io
    import zipfile as zf_mod

    from os2native.package import read_applications

    manifest = (
        '<Pack><Solutions><Solution Name="S"><Applications>'
        '<Application Name="Mobile" Description="the app"><Modules>'
        '<Module Name="App" /><Module Name="App_CS" /></Modules></Application>'
        '<Application Name="Web"><Modules><Module Name="Web" /></Modules></Application>'
        '</Applications></Solution></Solutions></Pack>'
    )
    inner = io.BytesIO()
    with zf_mod.ZipFile(inner, "w") as z:
        z.writestr("Manifest.xml", '<Pack><Application Name="Nested"><Module Name="N" /></Application></Pack>')
    osp = tmp_path / "S.osp"
    with zf_mod.ZipFile(osp, "w") as z:
        z.writestr("Manifest.xml", manifest)
        z.writestr("S/App.oml", b"x")
        z.writestr("S/Inner.oap", inner.getvalue())

    apps = read_applications(osp)
    assert [(a["name"], a["modules"]) for a in apps] == [
        ("Mobile", ["App", "App_CS"]),
        ("Web", ["Web"]),
        ("Nested", ["N"]),
    ]
    assert apps[0]["description"] == "the app"


_MINI_XML = """<OML>
<eSpaceFragment FragmentName="ESpace" Count="1"><ESpace Name="{name}" Key="K{name}"/></eSpaceFragment>
<eSpaceFragment FragmentName="UserActions" Count="1"><Flows.UserAction Key="A{name}" Name="DoIt"/></eSpaceFragment>
</OML>"""


def test_package_run_has_one_run_id(tmp_path, monkeypatch):
    """Every module of a package run, inventory.json and the log share one run id."""
    import os2native.pipeline as pipeline
    from os2native.package import run_package, run_report

    def fake_oml_to_xml(oml_path: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        xml_path = output_dir / oml_path.with_suffix(".xml").name
        xml_path.write_text(_MINI_XML.format(name=oml_path.stem), encoding="utf-8")
        return xml_path

    monkeypatch.setattr(pipeline, "_run_oml_to_xml", fake_oml_to_xml)
    inputs = []
    for name in ("ModA", "ModB"):
        oml = tmp_path / "in" / f"{name}.oml"
        oml.parent.mkdir(exist_ok=True)
        oml.write_bytes(b"not decoded in this test")
        inputs.append(oml)
    out = tmp_path / "OUTPUT"
    run_package(inputs, out)

    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    run_id = inventory["run_id"]
    assert run_id.startswith("run-") and inventory["extracted_at"]
    module_ids = {
        json.loads((out / "extraction" / m / "package.json").read_text(encoding="utf-8"))["run_id"]
        for m in ("ModA", "ModB")
    }
    assert module_ids == {run_id}
    assert f"[{run_id}]" in (out / "extraction-issues.log").read_text(encoding="utf-8").splitlines()[0]

    # `report` recounts without re-extracting: same run.
    run_report(out)
    again = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    assert (again["run_id"], again["extracted_at"]) == (run_id, inventory["extracted_at"])
