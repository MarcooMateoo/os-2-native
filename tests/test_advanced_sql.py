"""Tests for Advanced SQL (Nodes.AdvancedQuery) extraction in the logic worker."""

from __future__ import annotations

import json

from os2native.workers import logic
from os2native.xml_loader import load_oml_xml

# One local entity (AuditLog), one reference entity (User from module Users),
# and a server action whose flow holds a single AdvancedQuery node.
_XML = """<?xml version="1.0" encoding="utf-8"?>
<OML>
  <eSpaceFragment FragmentName="ESpace" Count="1">
    <ESpace Key="esp" Name="AuditMod" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="Entities" Count="1">
    <Entity Key="entAudit" Name="AuditLog">
      <Attributes>
        <EntityAttribute Key="attCreated" Name="CreatedOn" />
        <EntityAttribute Key="attUser" Name="UserId" />
      </Attributes>
    </Entity>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="References" Count="1">
    <Reference Key="refUsers" Name="Users">
      <ReferenceEntities>
        <ReferenceEntity Key="refUser" Name="User">
          <Attributes><ReferenceEntityAttribute Key="attUid" Name="Id" /></Attributes>
        </ReferenceEntity>
      </ReferenceEntities>
    </Reference>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="UserActions" Count="1">
    <Flows.UserAction Key="actPurge" Name="PurgeAudit" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="NodesNotShownInESpaceTree#actPurge" Count="1">
    <Nodes.AdvancedQuery Key="aq1" Name="DeleteOld" X="10" Y="20">
      <Links Count="1">
        <Links.Sequence Key="lnk1" TargetNode="Nodes.End:^/^/NodesNotShownInESpaceTree.end1" />
      </Links>
      <MaxRecords />
      <SQL>
        <ParsedSQLExpression FromElement="-1">
          <Text SQLText="DELETE FROM " />
          <Entity Ref="Entity:/Entities.entAudit" />
          <Text SQLText="&#10;" />
          <Unbound SQLText="WHERE" />
          <Text SQLText=" " />
          <Unbound SQLText="GETDATE" />
          <Text SQLText="() &gt;= (" />
          <Attribute Ref="EntityAttribute:/Entities.entAudit/Attributes.attCreated" HasBrackets="Yes">
            <Entity Ref="Entity:/Entities.entAudit" />
          </Attribute>
          <Text SQLText=" + " />
          <Parameter Ref="Variables.AdvancedQueryParameter:^/QueryParameters.pRet" />
          <Text SQLText=") AND " />
          <Attribute Ref="EntityAttribute:/Entities.entAudit/Attributes.attUser" HasBrackets="Yes">
            <Entity Ref="Entity:/Entities.entAudit" />
          </Attribute>
          <Text SQLText=" IN (SELECT " />
          <Entity Ref="ReferenceEntity:/References.refUsers/ReferenceEntities.refUser" HasStar="Yes" />
          <Text SQLText=" FROM " />
          <Entity Ref="ReferenceEntity:/References.refUsers/ReferenceEntities.refUser" />
          <Text SQLText=")" />
        </ParsedSQLExpression>
      </SQL>
      <QueryParameters Count="1">
        <Variables.AdvancedQueryParameter Key="pRet" Name="RetentionPeriod" Type="%j2Lc0phK0kuggij4gQYfAw" />
      </QueryParameters>
      <OutputStructure Count="1">
        <AdvancedQueryRecord Key="rec1" Record="Entity:/Entities.entAudit" />
      </OutputStructure>
      <Arguments Count="1">
        <Argument Key="arg1" Parameter="Variables.AdvancedQueryParameter:^/QueryParameters.pRet">
          <Value><ParsedExpression><Integer Value="365" /></ParsedExpression></Value>
        </Argument>
      </Arguments>
    </Nodes.AdvancedQuery>
    <Nodes.End Key="end1" Name="" />
  </eSpaceFragment>
</OML>
"""

_EXPECTED_SQL = (
    "DELETE FROM {AuditLog}\n"
    "WHERE GETDATE() >= ({AuditLog}.[CreatedOn] + @RetentionPeriod)"
    " AND {AuditLog}.[UserId] IN (SELECT {User}.* FROM {User})"
)


def _run(tmp_path):
    xml_path = tmp_path / "AuditMod.xml"
    xml_path.write_text(_XML, encoding="utf-8")
    logic.run(load_oml_xml(xml_path), tmp_path / "pkg")
    return tmp_path / "pkg" / "logic"


def test_advanced_query_node_is_enriched(tmp_path):
    out = _run(tmp_path)
    actions = json.loads((out / "server-actions.json").read_text(encoding="utf-8"))
    nodes = {n["key"]: n for n in actions[0]["flow"]["nodes"]}
    aq = nodes["aq1"]

    assert aq["type"] == "Nodes.AdvancedQuery"
    assert aq["sql"] == _EXPECTED_SQL
    assert [p["name"] for p in aq["parameters"]] == ["RetentionPeriod"]
    assert aq["parameters"][0]["type"]["raw"] == "%j2Lc0phK0kuggij4gQYfAw"
    assert aq["output_structure"] == ["AuditLog"]
    assert aq["arguments"] == [{"key": "arg1", "name": "RetentionPeriod", "value": "365"}]
    assert aq["max_records"] == ""
    assert actions[0]["flow"]["edges"][0]["to_key"] == "end1"


def test_advanced_sql_flat_list(tmp_path):
    out = _run(tmp_path)
    queries = json.loads((out / "advanced-sql.json").read_text(encoding="utf-8"))
    assert len(queries) == 1
    q = queries[0]
    assert (q["action"], q["node"]) == ("PurgeAudit", "DeleteOld")
    assert q["sql"] == _EXPECTED_SQL
    assert q["output_structure"] == ["AuditLog"]
    assert q["parameters"][0]["name"] == "RetentionPeriod"


def test_module_without_advanced_sql_writes_empty_list(tmp_path):
    xml = _XML.split('<eSpaceFragment FragmentName="NodesNotShownInESpaceTree#actPurge"')[0] + "</OML>"
    xml_path = tmp_path / "Empty.xml"
    xml_path.write_text(xml, encoding="utf-8")
    logic.run(load_oml_xml(xml_path), tmp_path / "pkg")
    data = (tmp_path / "pkg" / "logic" / "advanced-sql.json").read_text(encoding="utf-8")
    assert json.loads(data) == []
