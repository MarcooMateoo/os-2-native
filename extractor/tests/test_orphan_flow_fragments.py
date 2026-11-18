"""Blocks in a flow-node fragment whose flow no longer exists are not items.

Traditional modules keep a flow's nodes in ``NodesShownInESpaceTree#<flow key>``.
A fragment whose flow key matches no element is a leftover that Service Studio
does not show, so its blocks must not be extracted or counted.
"""

from __future__ import annotations

import json
from pathlib import Path

from os2native.inventory_report import module_rows
from os2native.workers import interface, logic
from os2native.xml_loader import load_oml_xml

_XML = """<?xml version="1.0" encoding="utf-8"?>
<OML>
  <ESpace Key="esp" Name="Mod_Email" FragmentName="eSpace">
    <Flows.WebFlow Key="FLOW1" Name="Common" />
  </ESpace>
  <eSpaceFragment FragmentName="NodesShownInESpaceTree#FLOW1" Count="1">
    <Nodes.WebBlock Key="B1" Name="Header" Public="Yes" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="NodesShownInESpaceTree#GONEFLOW" Count="2">
    <Nodes.WebBlock Key="B2" Name="LayoutSideMenu" Public="Yes">
      <Preparation><Flows.WebPreparation Key="P2" /></Preparation>
    </Nodes.WebBlock>
    <Nodes.WebBlock Key="B3" Name="LayoutTopMenu" Public="Yes" />
  </eSpaceFragment>
</OML>
"""


def test_blocks_of_a_missing_flow_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "Mod_Email.xml"
    path.write_text(_XML, encoding="utf-8")
    interface.run(load_oml_xml(path), tmp_path / "pkg")

    blocks = json.loads((tmp_path / "pkg" / "interfaces" / "blocks.json").read_text(encoding="utf-8"))
    names = sorted(b["name"] for b in blocks)
    assert names == ["Header"]
    assert blocks[0]["flow"] == "Common"


def test_actions_of_a_missing_flow_are_not_client_actions(tmp_path: Path) -> None:
    path = tmp_path / "Mod_Email.xml"
    path.write_text(_XML, encoding="utf-8")
    doc = load_oml_xml(path)
    pkg = tmp_path / "Mod_Email"
    interface.run(doc, pkg)
    logic.run(doc, pkg)

    rows = module_rows(pkg)
    assert {r["category"] for r in rows} >= {"blocks"}
    assert [r["name"] for r in rows if r["category"] == "client_actions"] == []
    assert [r["name"] for r in rows if r["category"] == "blocks"] == ["Header"]
