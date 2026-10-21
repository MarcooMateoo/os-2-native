"""Tests for .xif extension parsing and extra package inputs."""

from __future__ import annotations

import base64
import json
import sys
import zlib
from pathlib import Path

from os2native.extension import extract_extension
from os2native.inventory_report import build_rows
from os2native.package import build_inventory, collect_inputs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_dependencies import check  # noqa: E402

CS = "public class CssvUtil { }"


def _xif(tmp_path: Path, name: str = "CSVUtil") -> Path:
    data = base64.b64encode(CS.encode()).decode()
    xml = f"""﻿<?xml version="1.0" encoding="utf-8"?>
<Extension Key="X1" Name="{name}" Description="CSV utility" ApplicationServerType="NET">
  <Resources>
    <Resource Key="R1" Filename="{name}.cs" Path="\\Source\\NET\\" CompileAction="Ignore"><Data>{data}</Data></Resource>
    <Resource Key="R2" Filename="Lib.dll" Path="\\Source\\NET\\Bin\\" CompileAction="Binaries"><Data>{data}</Data></Resource>
    <Resource Key="R3" Filename="evil.cs" Path="\\..\\..\\" CompileAction="Ignore"><Data>{data}</Data></Resource>
    <Resource Key="R1" />
  </Resources>
  <Actions>
    <Action Key="A1" Name="LoadCSV" Description="Load CSV" IsFunction="No">
      <Variables>
        <InputParameter Key="P1" Name="CSV" IsMandatory="Yes" Type="rtBinaryData" Default="" RecordType=""/>
        <InputParameter Key="P2" Name="Config" IsMandatory="No" Type="rtRecord" Default="" RecordType="S1,"/>
        <OutputParameter Key="P3" Name="Count" Type="rtInteger" RecordType=""/>
      </Variables>
    </Action>
  </Actions>
  <Structures>
    <Structure Key="S1" Name="LoadConfig">
      <Attribute Key="T1" Name="Encode" Type="rtText" IsMandatory="No" DefaultValue="&quot;utf-8&quot;" Length="50"/>
    </Structure>
  </Structures>
</Extension>"""
    path = tmp_path / f"{name}_v1.xif"
    path.write_bytes(zlib.compress(xml.encode("utf-8")))
    return path


def test_extract_extension_metadata_and_sources(tmp_path):
    ext_dir = extract_extension(_xif(tmp_path), tmp_path / "out" / "extensions")
    info = json.loads((ext_dir / "extension.json").read_text(encoding="utf-8"))

    assert ext_dir.name == "CSVUtil"
    assert info["server_type"] == "NET"
    action = info["actions"][0]
    assert action["name"] == "LoadCSV"
    assert [(p["name"], p["type"], p["is_mandatory"]) for p in action["input_parameters"]] == [
        ("CSV", "Binary Data", True), ("Config", "Record", False)]
    assert action["input_parameters"][1]["record_type"] == ["LoadConfig"]
    assert action["output_parameters"] == [{"name": "Count", "type": "Integer", "description": ""}]
    assert info["structures"][0]["attributes"][0]["name"] == "Encode"
    assert len(info["resources"]) == 3  # bare back-reference skipped

    # source code written; binaries and path-escaping entries are not
    assert info["source_files_written"] == ["Source/NET/CSVUtil.cs"]
    assert (ext_dir / "source" / "Source" / "NET" / "CSVUtil.cs").read_text() == CS
    assert not (tmp_path / "out" / "evil.cs").exists()


def test_extensions_in_inventory_report_and_dependency_check(tmp_path):
    out = tmp_path / "out"
    ext_dir = extract_extension(_xif(tmp_path), out / "extensions")
    deps = out / "extraction" / "App" / "dependencies"
    deps.mkdir(parents=True)
    (deps / "graph.json").write_text(json.dumps({"edges": [
        {"source": "App", "target": "CSVUtil", "direction": "outbound"},
        {"source": "App", "target": "Mystery", "direction": "outbound"},
    ]}))

    inv = build_inventory([out / "extraction" / "App"], [], {}, [ext_dir])
    assert inv["extension_count"] == 1
    assert inv["totals"]["extensions"] == 1 and inv["totals"]["extension_actions"] == 1
    rows = {r["category"]: r for r in build_rows([], [ext_dir])}
    assert rows["extension_actions"]["location"] == "Integration Studio > CSVUtil > Actions > LoadCSV"

    report = check(out, {"platform_builtins": [], "forge_extensions": [], "mobile_plugins": []})
    assert report["extensions_in_package"] == ["CSVUtil"]
    assert [d["module"] for d in report["missing_dependencies"]] == ["Mystery"]


def test_collect_inputs_takes_extra_files(tmp_path):
    import zipfile

    osp = tmp_path / "App.osp"
    with zipfile.ZipFile(osp, "w") as zf:
        zf.writestr("App.oml", b"oml")
    extra = tmp_path / "HiQRCoder_v2.oml"
    extra.write_bytes(b"oml")
    xif = _xif(tmp_path)

    source = tmp_path / "out" / "_source"
    found = collect_inputs([osp, extra, xif], source)
    assert [p.name for p in found["modules"]] == ["App.oml", "HiQRCoder_v2.oml"]
    assert [p.name for p in found["extensions"]] == ["CSVUtil_v1.xif"]

    # inputs already inside _source are used in place
    found_again = collect_inputs([source / "HiQRCoder_v2.oml"], source)
    assert found_again["modules"] == [source / "HiQRCoder_v2.oml"]
