"""Deliverables cover the apps in scope plus what they use from other modules.

The extraction tree is hand-built JSON shaped like the real output:
``App`` is the app in scope (package manifest), ``Lib`` is a dependency added
to INPUTS of which App uses part, ``Other`` is a dependency App never uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from os2native.usage import (
    ACTION, BLOCK, CLIENT, ENTITY, STRUCTURE, THEME, references_in, trace_usage,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generator_utils  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _flow(*refs: str) -> dict:
    return {"nodes": [{"kind": "ExecuteServerAction", "action_ref": r} for r in refs]}


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    out = tmp_path / "OUTPUT"
    ext = out / "extraction"
    _write(out / "applications.json", [
        {"name": "App", "modules": ["App"], "package": "App.osp"},
        {"name": "(extra inputs)", "modules": ["Lib", "Other"], "package": None},
    ])
    _write(out / "inventory.json", {"modules": [{"module": m} for m in ("App", "Lib", "Other", "Theme_Th")]})
    for m in ("App", "Lib", "Other"):
        _write(ext / m / "package.json", {"module_name": m})

    # App calls Lib.DoThing and uses Lib's upload block.
    _write(ext / "App/logic/server-actions.json", [
        {"name": "Save", "flow": _flow("References.Lib.ReferenceActions.DoThing")},
    ])
    _write(ext / "App/interfaces/screens.json", [
        {"name": "Home", "widgets": [
            {"block": "References.Lib.ReferenceNRWebFlows.UploadFlow.ReferenceWebBlocks.Upload"}]},
    ])
    _write(ext / "App/data/entities.json", [{"name": "Order", "attributes": []}])
    # App's theme is based on the Corp theme from the Theme_Th module.
    _write(ext / "App/interfaces/themes.json", [
        {"name": "App", "tag": "NewRuntime.Theme", "is_reference": False},
        {"name": "Corp", "tag": "NewRuntime.ReferenceTheme", "is_reference": True, "source_module": "Theme_Th"},
    ])
    _write(ext / "Theme_Th/package.json", {"module_name": "Theme_Th"})
    _write(ext / "Theme_Th/interfaces/themes.json", [
        {"name": "Corp", "tag": "NewRuntime.Theme", "is_reference": False},
        {"name": "Base", "tag": "NewRuntime.ReferenceTheme", "is_reference": True, "source_module": "OutSystemsUI"},
    ])

    # Lib: DoThing -> local Helper + entity Audit; Audit -> Status (identifier);
    # Upload block -> client action Configure -> structure UploadConfig.
    _write(ext / "Lib/logic/server-actions.json", [
        {"name": "DoThing", "flow": _flow("UserActions.Helper", "Entities.Audit.EntityActions.#CreateEntity")},
        {"name": "Helper", "flow": _flow()},
        {"name": "Unused", "flow": _flow("References.Other.ReferenceActions.Nope")},
    ])
    _write(ext / "Lib/logic/client-actions.json", [
        {"name": "Configure", "action_type": "client", "flow": {},
         "input_parameters": [{"name": "c", "type": {"kind": "local_structure", "name": "UploadConfig"}}]},
        {"name": "NotUsed", "action_type": "client", "flow": {}},
        {"name": "OnUpload", "action_type": "client", "source": {"source": "block", "block_name": "Upload"},
         "flow": _flow("ClientActionFlows.Configure")},
    ])
    _write(ext / "Lib/logic/advanced-sql.json", [
        {"action": "DoThing", "sql": "SELECT 1"}, {"action": "Unused", "sql": "SELECT 2"},
    ])
    _write(ext / "Lib/data/entities.json", [
        {"name": "Audit", "attributes": [
            {"name": "StatusId", "type": {"kind": "entity_identifier", "entity_name": "Status"}}]},
        {"name": "Status", "attributes": []},
        {"name": "Junk", "attributes": []},
    ])
    _write(ext / "Lib/data/static-data.json", [
        {"entity_name": "Status", "records": []}, {"entity_name": "Junk", "records": []},
    ])
    _write(ext / "Lib/data/relationships.json", [
        {"from_entity": "Audit", "to_entity": "Status", "kind": "foreign_key", "reference_module": None},
        {"from_entity": "Junk", "to_entity": "Status", "kind": "foreign_key", "reference_module": None},
    ])
    _write(ext / "Lib/data/structures.json", [{"name": "UploadConfig"}, {"name": "Spare"}])
    _write(ext / "Lib/interfaces/blocks.json", [{"name": "Upload", "widgets": []}, {"name": "Banner"}])
    _write(ext / "Lib/interfaces/screens.json", [{"name": "LibAdmin"}])
    _write(ext / "Lib/integrations/public-actions.json", [{"name": "DoThing"}, {"name": "Unused"}])
    _write(ext / "Lib/integrations/dependency-actions.json", [
        {"module": "Other", "name": "Nope", "action_type": "server"},
    ])
    _write(ext / "Other/data/entities.json", [{"name": "X", "attributes": []}])
    return out


def test_references_in_reads_cross_module_local_and_type_references():
    cross, local = references_in({
        "a": "References.Lib.ReferenceActions.DoThing",
        "b": "References.Lib.ReferenceServiceAPIMethods.GetIt",
        "c": "References.OSUI.ReferenceNRWebFlows.Adaptive.ReferenceWebBlocks.Gallery.InputParameters.X",
        "d": "UserActions.Helper",
        "e": "NRWebFlows.Main.NodesShownInESpaceTree.Banner.InputParameters.Y",
        "f": {"kind": "reference_structure", "reference_module": "Lib", "name": "Cfg"},
        "g": {"kind": "entity_identifier", "entity_name": "Status"},
    })
    assert cross == {("Lib", ACTION, "DoThing"), ("Lib", "service_action", "GetIt"),
                     ("OSUI", BLOCK, "Gallery"), ("Lib", STRUCTURE, "Cfg")}
    assert local == {(ACTION, "Helper"), (BLOCK, "Banner"), (ENTITY, "Status")}


def test_trace_follows_definitions_and_skips_unused_modules(out):
    usage = trace_usage(out, {"App"}, ["App", "Lib", "Other", "Theme_Th"])
    assert set(usage) == {"Lib", "Theme_Th"}  # Other is only used by Lib.Unused, never reached
    assert usage["Theme_Th"].names(THEME) == {"Corp"}
    lib = usage["Lib"]
    assert lib.names(ACTION) == {"DoThing", "Helper"}
    assert lib.names(ENTITY) == {"Audit", "Status"}
    assert lib.names(BLOCK) == {"Upload"}
    assert lib.names(CLIENT) == {"Configure"}
    assert lib.names(STRUCTURE) == {"UploadConfig"}


@pytest.fixture()
def scoped(monkeypatch):
    monkeypatch.setattr(generator_utils, "_SCOPES", {})
    monkeypatch.setattr(generator_utils, "_ALL_MODULES", False)
    return generator_utils


def _names(items, field="name"):
    return sorted(x[field] for x in items)


def test_generators_load_scope_in_full_and_only_used_parts_of_others(out, scoped):
    assert [p["module_name"] for p in scoped.load_pass_packages(out)] == ["App", "Lib", "Theme_Th"]
    load = lambda m, d, f: scoped.load_domain_file(out, m, d, f)  # noqa: E731
    assert _names(load("App", "data", "entities.json")) == ["Order"]
    assert _names(load("Lib", "data", "entities.json")) == ["Audit", "Status"]
    assert _names(load("Lib", "data", "static-data.json"), "entity_name") == ["Status"]
    assert _names(load("Lib", "data", "relationships.json"), "from_entity") == ["Audit"]
    assert _names(load("Lib", "data", "structures.json")) == ["UploadConfig"]
    assert _names(load("Lib", "logic", "server-actions.json")) == ["DoThing", "Helper"]
    assert _names(load("Lib", "logic", "client-actions.json")) == ["Configure", "OnUpload"]
    assert _names(load("Lib", "logic", "advanced-sql.json"), "action") == ["DoThing"]
    assert _names(load("Lib", "interfaces", "blocks.json")) == ["Upload"]
    assert load("Lib", "interfaces", "screens.json") == []
    assert _names(load("Lib", "integrations", "public-actions.json")) == ["DoThing"]
    assert load("Lib", "integrations", "dependency-actions.json") == []
    assert load("Other", "data", "entities.json") == []
    assert _names(load("Theme_Th", "interfaces", "themes.json")) == ["Base", "Corp"]


def test_project_json_scope_and_all_modules(out, scoped, monkeypatch):
    _write(out.parent / "project.json", {"scope": {"applications": ["App"]}})
    assert [p["module_name"] for p in scoped.load_pass_packages(out)] == ["App", "Lib", "Theme_Th"]

    scoped.make_arg_parser("test").parse_args([str(out), "--all-modules"])
    monkeypatch.setattr(generator_utils, "_SCOPES", {})
    assert [p["module_name"] for p in scoped.load_pass_packages(out)] == ["App", "Lib", "Other", "Theme_Th"]
    assert _names(scoped.load_domain_file(out, "Lib", "data", "entities.json")) == ["Audit", "Junk", "Status"]
