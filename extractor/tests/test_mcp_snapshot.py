"""Tests for scripts/collect_mcp_snapshot.py and the Stage 1 XML cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect_mcp_snapshot as cms  # noqa: E402
from os2native import pipeline  # noqa: E402


def _xml_for(name: str) -> str:
    return (
        '<?xml version="1.0"?><OML><eSpaceFragment FragmentName="ESpace">'
        f'<ESpace Key="k" Version="1" Name="{name}" /></eSpaceFragment></OML>'
    )


def test_module_name_of_xml(tmp_path):
    xml = tmp_path / "x.xml"
    xml.write_text(_xml_for("PaymentAPI"), encoding="utf-8")
    assert cms.module_name_of_xml(xml) == "PaymentAPI"


def test_only_live_snapshots_newest_first(tmp_path):
    old = tmp_path / "run1-in.oml"
    new = tmp_path / "run2-in.oml"
    mutated = tmp_path / "run2-out.oml"
    for i, p in enumerate((old, new, mutated)):
        p.write_bytes(b"oml")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    assert cms.find_snapshots(tmp_path) == [new, old]
    assert cms.find_snapshots(tmp_path / "missing") == []


def test_collect_copies_matching_module(tmp_path, monkeypatch):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    (snaps / "a-in.oml").write_bytes(b"other module")
    (snaps / "b-in.oml").write_bytes(b"wanted module")
    names = {"a-in.oml": "Other", "b-in.oml": "PaymentAPI"}
    monkeypatch.setattr(cms, "module_name_of_oml", lambda p, work: names[p.name])

    inputs = tmp_path / "INPUTS"
    inputs.mkdir()
    dest = cms.collect(inputs, "paymentapi", snaps)

    assert dest == inputs / "mcp" / "PaymentAPI.oml"
    assert dest.read_bytes() == b"wanted module"
    note = json.loads((inputs / "mcp" / "PaymentAPI.capture.json").read_text(encoding="utf-8"))
    assert note["module"] == "PaymentAPI"
    assert note["snapshot"].endswith("b-in.oml")
    assert cms.collect(inputs, "NotOpen", snaps) is None


def test_stage1_redecodes_when_oml_changes(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        Path(cmd[4]).write_text(_xml_for("M"), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    dll = tmp_path / "oml.dll"
    dll.write_bytes(b"")
    monkeypatch.setenv("OML_DLL", str(dll))
    monkeypatch.setattr(pipeline, "_dotnet_executable", lambda: "dotnet")
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    oml = tmp_path / "M.oml"
    out = tmp_path / "xml"
    oml.write_bytes(b"v1")
    pipeline._run_oml_to_xml(oml, out)
    pipeline._run_oml_to_xml(oml, out)
    assert len(calls) == 1  # same content: cached

    oml.write_bytes(b"v2")
    pipeline._run_oml_to_xml(oml, out)
    assert len(calls) == 2  # new content: decoded again
