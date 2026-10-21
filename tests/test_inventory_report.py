"""Tests for folders, own/consumed split and the itemised inventory report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from os2native.inventory_report import build_rows, module_rows, write_report
from os2native.package import build_inventory, module_inventory
from os2native.shared.coverage import CoverageTracker
from os2native.shared.folders import element_folders
from os2native.workers.interface import _extract_themes
from os2native.xml_loader import load_oml_xml

XML = """<OML>
<eSpaceFragment FragmentName="ESpace" Count="1"><ESpace Name="App" Key="E1">
  <Folders><ModelFolder Key="F1" Name="Security" ESpaceTreeFolder="ServerActions"/></Folders>
  <UserActions><Flows.UserAction Key="A1" Name="CheckToken" Folder="ModelFolder:/Folders.F1"/>
  <Flows.UserAction Key="A2" Name="NoFolder"/></UserActions>
</ESpace></eSpaceFragment>
<eSpaceFragment FragmentName="NRThemes" Count="1">
  <NewRuntime.Theme Key="T1" Name="App"/>
</eSpaceFragment>
<eSpaceFragment FragmentName="References" Count="1">
  <Reference Key="R1" Name="App_TH"><NRReferenceThemes>
    <NewRuntime.ReferenceTheme Key="T2" Name="AppTheme"/></NRReferenceThemes></Reference>
  <Reference Key="R2" Name="OutSystemsUI"><NRReferenceThemes>
    <NewRuntime.ReferenceTheme Key="T3" Name="OutSystemsUI"/></NRReferenceThemes></Reference>
</eSpaceFragment>
</OML>"""


def _doc(tmp_path):
    path = tmp_path / "App.xml"
    path.write_text(XML, encoding="utf-8")
    return load_oml_xml(path)


def test_element_folders(tmp_path):
    assert element_folders(_doc(tmp_path)) == {"A1": "Security"}


FOLDER_DUP_XML = """<OML>
<eSpaceFragment FragmentName="ESpace" Count="1"><ESpace Name="App" Key="E1"/></eSpaceFragment>
<eSpaceFragment FragmentName="UserActions#F1" Count="1">
  <Flows.UserActionInsideFolder Key="A1" Name="DoLogout"/>
</eSpaceFragment>
<eSpaceFragment FragmentName="UserActions" Count="2">
  <Flows.UserAction Key="A1" Name="DoLogout" Folder="ModelFolder:/Folders.F1">
    <InputParameters><Variables.InputParameter Key="P1" Name="UserToken" Type="%uROOBXPvQEyU76NWO+1uxQ"/></InputParameters>
  </Flows.UserAction>
  <Flows.UserAction Key="A2" Name="DoLogin"/>
</eSpaceFragment>
</OML>"""


def test_actions_in_folders_are_not_counted_twice(tmp_path):
    from os2native.shared.key_index import build_key_index
    from os2native.shared.type_resolver import TypeResolver
    from os2native.workers.logic import _extract_server_actions

    path = tmp_path / "App.xml"
    path.write_text(FOLDER_DUP_XML, encoding="utf-8")
    doc = load_oml_xml(path)
    actions = _extract_server_actions(
        doc, build_key_index(doc), TypeResolver.from_doc(doc), CoverageTracker(domain="logic")
    )
    assert sorted(a["name"] for a in actions) == ["DoLogin", "DoLogout"]
    logout = next(a for a in actions if a["name"] == "DoLogout")
    assert [p["name"] for p in logout["input_parameters"]] == ["UserToken"]


def test_consumed_themes_record_producer_module(tmp_path):
    themes = {t["name"]: t for t in _extract_themes(_doc(tmp_path), CoverageTracker(domain="i"))}
    assert themes["App"]["is_reference"] is False
    assert themes["AppTheme"]["source_module"] == "App_TH"
    assert themes["OutSystemsUI"]["source_module"] == "OutSystemsUI"


def _write(root: Path, rel: str, payload) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _module(tmp_path: Path) -> Path:
    root = tmp_path / "extraction" / "App"
    _write(root, "interfaces/screens.json", [{
        "name": "Home", "key": "S1", "flow": "MainFlow", "is_public": False,
        "client_actions": [{"name": "OnReady", "key": "C1"}, {"name": "SaveOnClick", "key": "C2"}],
    }])
    _write(root, "interfaces/blocks.json", [{"name": "Header", "key": "B1", "flow": "Common"}])
    _write(root, "interfaces/themes.json", [
        {"name": "App", "key": "T1", "is_reference": False},
        {"name": "AppTheme", "key": "T2", "is_reference": True, "source_module": "App_TH"},
        {"name": "OutSystemsUI", "key": "T3", "is_reference": True, "source_module": "OutSystemsUI"},
    ])
    _write(root, "interfaces/assets.json", [
        {"owner": "App", "owner_kind": "Theme", "is_reference": False, "css_length": 50},
        {"owner": "Home", "owner_kind": "WebScreen", "is_reference": False, "css_length": 0},
        {"owner": "OutSystemsUI", "owner_kind": "ReferenceTheme", "is_reference": True,
         "source_module": "OutSystemsUI", "css_length": 9000},
    ])
    # client-actions.json lists module-level AND screen actions
    _write(root, "logic/client-actions.json", [
        {"name": "OnReady", "key": "C1"}, {"name": "SaveOnClick", "key": "C2"},
        {"name": "SyncData", "key": "C3", "folder": "Offline", "action_type": "client"},
    ])
    _write(root, "logic/server-actions.json", [
        {"name": "PurgeOld", "key": "A1", "folder": "Jobs", "is_public": False},
        {"name": "GetUser", "key": "A2", "folder": None, "is_public": True, "is_function": True},
    ])
    _write(root, "logic/advanced-sql.json", [
        {"action": "PurgeOld", "action_key": "A1", "node": "BulkDelete", "node_key": "N1",
         "sql": "DELETE FROM {Audit}\nWHERE 1=1"},
    ])
    _write(root, "integrations/public-actions.json", [{"name": "GetUser", "is_function": True}])
    _write(root, "integrations/dependency-actions.json", [{"name": "AES_Encrypt", "module": "CryptoAPI"}])
    _write(root, "data/site-properties.json", [
        {"name": "TenantId", "is_platform": True}, {"name": "RetentionDays", "is_platform": False},
    ])
    _write(root, "processes/timers.json", [
        {"name": "Timer_Purge", "key": "TM1", "action": "Flows.UserAction:/UserActions.A1"},
    ])
    return root


def test_rows_give_service_studio_locations(tmp_path):
    rows = {(r["category"], r["name"]): r for r in module_rows(_module(tmp_path))}

    assert rows[("screens", "Home")]["location"] == "Interface > UI Flows > MainFlow > Home"
    assert rows[("screen_actions", "OnReady")]["location"] == "Interface > UI Flows > MainFlow > Home > OnReady"
    assert rows[("client_actions", "SyncData")]["location"] == "Logic > Client Actions > Offline > SyncData"
    assert ("client_actions", "OnReady") not in rows
    assert rows[("server_actions", "PurgeOld")]["location"] == "Logic > Server Actions > Jobs > PurgeOld"
    assert rows[("server_actions", "GetUser")]["details"] == "Public, Function"
    assert rows[("themes", "App")]["location"] == "Interface > Themes > App"
    consumed = rows[("themes_consumed", "AppTheme")]
    assert consumed["location"] == "Interface > Themes > App_TH > AppTheme"
    assert consumed["ownership"] == "Consumed from App_TH"
    assert rows[("stylesheets", "App")]["location"] == "Interface > Themes > App > Style Sheet"
    assert ("stylesheets", "Home") not in rows  # empty stylesheet slot
    assert rows[("advanced_sql", "BulkDelete")]["location"] == "Logic > Server Actions > Jobs > PurgeOld > BulkDelete"
    assert rows[("dependency_actions", "AES_Encrypt")]["location"] == "Manage Dependencies > CryptoAPI > AES_Encrypt"
    assert ("site_properties", "TenantId") not in rows
    assert rows[("timers", "Timer_Purge")]["details"] == "runs PurgeOld"


def test_inventory_counts_equal_report_rows(tmp_path):
    root = _module(tmp_path)
    counts = module_inventory(root)["counts"]
    assert counts["themes"] == 1 and counts["themes_consumed"] == 2
    assert counts["stylesheets"] == 1 and counts["stylesheets_consumed"] == 1
    assert counts["client_actions"] == 1 and counts["screen_actions"] == 2
    rows = module_rows(root)
    for category, n in counts.items():
        assert n == sum(1 for r in rows if r["category"] == category), category
    assert build_inventory([root], [], {})["totals"] == counts


def test_write_report_csv_and_markdown(tmp_path):
    root = _module(tmp_path)
    csv_path, md_path = write_report(build_rows([root]), tmp_path)

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0].keys() >= {"module", "category", "name", "location", "source"}
    home = next(r for r in rows if r["name"] == "Home")
    assert home["category"] == "Screen"
    assert home["source"] == "extraction/App/interfaces/screens.json"

    md = md_path.read_text(encoding="utf-8")
    assert "| Theme (consumed) | 2 |" in md
    assert "### Screen/Block action (2)" in md
