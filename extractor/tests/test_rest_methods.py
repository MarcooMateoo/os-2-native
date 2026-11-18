"""Exposed REST methods: their logic flows are extracted, including Advanced SQL."""

from __future__ import annotations

import json
from pathlib import Path

from os2native.inventory_report import module_rows
from os2native.workers import logic
from os2native.xml_loader import load_oml_xml

_XML = """<?xml version="1.0" encoding="utf-8"?>
<OML>
  <ESpace Key="esp" Name="Mod_API" FragmentName="eSpace" />
  <eSpaceFragment FragmentName="CustomServices" Count="1">
    <CustomService-OutSystems.Plugin.RESTService.RestService Key="api1" Name="v1">
      <CustomActionFlows>
        <Flows.CustomActionFlow-OutSystems.Plugin.RESTService.RestServiceAction Key="m1" Name="GetOngoingEvent" />
      </CustomActionFlows>
      <CustomCallbackFlows>
        <Flows.CustomCallbackFlow-OutSystems.Plugin.RESTService.RestServiceCallbackActionFlow Key="cb1" Name="OnRequest" />
      </CustomCallbackFlows>
    </CustomService-OutSystems.Plugin.RESTService.RestService>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="NodesNotShownInESpaceTree#m1" Count="2">
    <Nodes.AdvancedQuery Key="aq1" Name="GetEvents">
      <Links Count="1">
        <Links.Sequence Key="l1" TargetNode="Nodes.End:^/^/NodesNotShownInESpaceTree.end1" />
      </Links>
      <SQL><ParsedSQLExpression><Text SQLText="SELECT 1" /></ParsedSQLExpression></SQL>
    </Nodes.AdvancedQuery>
    <Nodes.End Key="end1" Name="" />
  </eSpaceFragment>
</OML>
"""


def test_rest_method_flows_and_sql(tmp_path: Path) -> None:
    path = tmp_path / "Mod_API.xml"
    path.write_text(_XML, encoding="utf-8")
    pkg = tmp_path / "Mod_API"
    logic.run(load_oml_xml(path), pkg)

    methods = json.loads((pkg / "logic" / "rest-methods.json").read_text(encoding="utf-8"))
    assert [(m["api"], m["method"], m["kind"]) for m in methods] == [
        ("v1", "GetOngoingEvent", "method"), ("v1", "OnRequest", "callback")]
    assert [n["key"] for n in methods[0]["flow"]["nodes"]] == ["aq1", "end1"]

    queries = json.loads((pkg / "logic" / "advanced-sql.json").read_text(encoding="utf-8"))
    assert [(q["action"], q["node"], q["sql"]) for q in queries] == [
        ("v1.GetOngoingEvent", "GetEvents", "SELECT 1")]

    rows = [r for r in module_rows(pkg) if r["category"] == "advanced_sql"]
    assert rows[0]["location"] == "Logic > Integrations > REST > v1 > GetOngoingEvent > GetEvents"
