"""Inventory counts must match what Service Studio shows for a module.

Each test uses a small hand-built OML XML snippet shaped like the real
oml.dll output (container elements on the ESpace, content in fragments).
"""

from __future__ import annotations

import json
from pathlib import Path

from os2native.orchestrator import _build_integration_artifacts
from os2native.package import module_inventory
from os2native.shared.coverage import CoverageTracker
from os2native.workers import data, logic
from os2native.workers.interface import _extract_css, _extract_themes
from os2native.xml_loader import load_oml_xml

_TEXT = "%uROOBXPvQEyU76NWO+1uxQ"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<OML>
  <ESpace Key="esp" Name="Mod_IS" FragmentName="eSpace">
    <Themes /><NRThemes /><Roles /><SystemRoles>
      <SystemRole Key="anon" Name="Anonymous" />
      <SystemRole Key="reg" Name="Registered" />
    </SystemRoles>
    <UserExceptions /><ServiceAPIMethods /><SiteProperties />
  </ESpace>
  <eSpaceFragment FragmentName="NRThemes" Count="1">
    <NewRuntime.Theme Key="th1" Name="ModTheme">
      <StyleSheet><WebStyleSheet><CssSource>.a {{ color: red; }}</CssSource></WebStyleSheet></StyleSheet>
    </NewRuntime.Theme>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="Screens" Count="1">
    <NRNodes.WebScreen Key="scr1" Name="Home">
      <StyleSheet><WebStyleSheet /></StyleSheet>
    </NRNodes.WebScreen>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="References" Count="1">
    <Reference Key="ref1" Name="CryptoAPI">
      <ReferenceActions>
        <ReferenceAction Key="ra1" Name="AES_Encrypt" />
        <ReferenceAction Key="ra2" Name="AES_Decrypt" />
      </ReferenceActions>
      <NRReferenceThemes>
        <NewRuntime.ReferenceTheme Key="rth1" Name="OutSystemsUI" />
      </NRReferenceThemes>
      <ReferenceThemes />
    </Reference>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="SiteProperties" Count="3">
    <Variables.SiteProperty Key="sp1" Name="TenantId" IsReadOnlySiteProperty="Yes" Type="{_TEXT}" />
    <Variables.SiteProperty Key="sp2" Name="TenantName" IsReadOnlySiteProperty="Yes" Type="{_TEXT}" />
    <Variables.SiteProperty Key="sp3" Name="CryptoKey" Type="{_TEXT}" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="Roles" Count="1">
    <Role Key="r1" Name="Manager" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="UserExceptions" Count="1">
    <UserException Key="ex1" Name="GenericException" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="ServiceAPIMethods" Count="1">
    <Flows.ServiceAPIMethod Key="svc1" Name="GetEmployee">
      <InputParameters><Variables.InputParameter Key="in1" Name="Id" Type="{_TEXT}" /></InputParameters>
    </Flows.ServiceAPIMethod>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="UserActions" Count="2">
    <Flows.UserAction Key="ua1" Name="EncryptData" Public="Yes" />
    <Flows.UserAction Key="ua2" Name="Helper" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="CustomClients" Count="1">
    <CustomClient-OutSystems.Plugin.REST.RestClient Key="cc1" Name="Weather">
      <CustomProperties><CustomProperty PropertyName="Base URL" Value="https://api.example.test" /></CustomProperties>
      <CustomActions>
        <CustomAction-OutSystems.Plugin.REST.RestAction Key="op1" Name="GetForecast" />
      </CustomActions>
    </CustomClient-OutSystems.Plugin.REST.RestClient>
  </eSpaceFragment>
</OML>
"""


def _doc(tmp_path: Path):
    xml_path = tmp_path / "Mod_IS.xml"
    xml_path.write_text(_XML, encoding="utf-8")
    return load_oml_xml(xml_path)


def test_theme_containers_are_not_themes(tmp_path):
    themes = _extract_themes(_doc(tmp_path), CoverageTracker(domain="interfaces"))
    assert sorted(t["name"] for t in themes) == ["ModTheme", "OutSystemsUI"]
    assert all(t["key"] for t in themes)


def test_stylesheets_named_by_owner_and_flag_css(tmp_path):
    sheets = _extract_css(_doc(tmp_path), CoverageTracker(domain="interfaces"))
    by_owner = {s["owner"]: s for s in sheets}
    assert set(by_owner) == {"ModTheme", "Home"}
    assert by_owner["ModTheme"]["has_css"] and by_owner["ModTheme"]["name"] == "ModTheme"
    assert by_owner["Home"]["owner_kind"] == "WebScreen"
    assert not by_owner["Home"]["has_css"] and by_owner["Home"]["css_length"] == 0


def test_platform_site_properties_flagged(tmp_path):
    data.run(_doc(tmp_path), tmp_path / "pkg")
    props = json.loads((tmp_path / "pkg/data/site-properties.json").read_text(encoding="utf-8"))
    assert {p["name"]: p["is_platform"] for p in props} == {
        "TenantId": True,
        "TenantName": True,
        "CryptoKey": False,
    }


def test_user_exceptions_extracted_and_system_roles_excluded(tmp_path):
    data.run(_doc(tmp_path), tmp_path / "pkg")
    out = tmp_path / "pkg" / "data"
    exceptions = json.loads((out / "user-exceptions.json").read_text(encoding="utf-8"))
    assert [e["name"] for e in exceptions] == ["GenericException"]
    roles = json.loads((out / "roles-and-permissions.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in roles] == ["Manager"]


def test_service_actions_extracted(tmp_path):
    logic.run(_doc(tmp_path), tmp_path / "pkg")
    svc = json.loads((tmp_path / "pkg/logic/service-actions.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in svc] == ["GetEmployee"]
    assert [p["name"] for p in svc[0]["input_parameters"]] == ["Id"]


def test_integrations_split_by_kind(tmp_path):
    lists = _build_integration_artifacts(_doc(tmp_path))
    assert [a["name"] for a in lists["dependency_actions"]] == ["AES_Encrypt", "AES_Decrypt"]
    assert {a["module"] for a in lists["dependency_actions"]} == {"CryptoAPI"}
    assert [a["name"] for a in lists["public_actions"]] == ["EncryptData"]
    assert [(a["name"], a["kind"]) for a in lists["consumed_apis"]] == [("Weather", "REST")]
    assert lists["consumed_apis"][0]["operations"] == [{"name": "GetForecast"}]
    assert lists["exposed_apis"] == []


def test_exposed_rest_and_soap_services(tmp_path):
    xml = _XML.replace(
        "</OML>",
        """<eSpaceFragment FragmentName="CustomServices" Count="1">
    <CustomService-OutSystems.Plugin.REST.RestService Key="cs1" Name="EmployeeAPI">
      <CustomActions><CustomServiceAction Key="m1" Name="ListEmployees" /></CustomActions>
    </CustomService-OutSystems.Plugin.REST.RestService>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="WebServices" Count="1">
    <WebService Key="ws1" Name="LegacyService">
      <WebServiceActions><WebServiceAction Key="w1" Name="Ping" /></WebServiceActions>
    </WebService>
  </eSpaceFragment>
</OML>""",
    )
    xml_path = tmp_path / "Exp.xml"
    xml_path.write_text(xml, encoding="utf-8")
    exposed = _build_integration_artifacts(load_oml_xml(xml_path))["exposed_apis"]
    assert [(a["name"], a["kind"]) for a in exposed] == [
        ("EmployeeAPI", "REST"),
        ("LegacyService", "SOAP"),
    ]
    assert exposed[0]["operations"][0]["name"] == "ListEmployees"


def _write(root: Path, rel: str, payload) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_module_inventory_counts_what_service_studio_shows(tmp_path):
    root = tmp_path / "CryptoAPI_IS"
    _write(root, "interfaces/assets.json", [{"css_length": 0}, {"css_length": 12}])
    _write(root, "data/site-properties.json", [
        {"name": "TenantId", "is_platform": True},
        {"name": "TenantName", "is_platform": True},
        {"name": "CryptoKey", "is_platform": False},
    ])
    _write(root, "data/user-exceptions.json", [{"name": "GenericException"}])
    _write(root, "logic/service-actions.json", [])
    _write(root, "integrations/consumed-apis.json", [{"name": "A", "kind": "REST"}, {"name": "B", "kind": "SOAP"}])
    _write(root, "integrations/exposed-apis.json", [])
    _write(root, "integrations/public-actions.json", [{"name": "EncryptData"}, {"name": "DecryptData"}])
    _write(root, "integrations/dependency-actions.json", [{"name": f"AES_{i}"} for i in range(5)])

    counts = module_inventory(root)["counts"]
    assert counts["stylesheets"] == 1
    assert counts["site_properties"] == 1
    assert counts["user_exceptions"] == 1
    assert counts["service_actions"] == 0
    assert (counts["rest_consumed"], counts["soap_consumed"]) == (1, 1)
    assert (counts["rest_exposed"], counts["soap_exposed"]) == (0, 0)
    assert counts["public_actions"] == 2
    assert counts["dependency_actions"] == 5
    assert "consumed_apis" not in counts and "exposed_apis" not in counts
