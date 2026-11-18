"""Specs follow the project's target stack (project.json "stack", TECH_STACK.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_specs import (  # noqa: E402
    generate_backend_spec,
    generate_frontend_spec,
    generate_index,
    remove_stale_specs,
)
from target_stack import (  # noqa: E402
    StackError,
    csharp_type,
    load_stack,
    module_is_mobile,
    ts_type,
)

TEXT = {"kind": "primitive", "name": "Text"}
INT = {"kind": "primitive", "name": "Integer"}
DATE = {"kind": "primitive", "name": "DateTime"}
USER_ID = {"kind": "entity_identifier", "name": "User Identifier", "entity_name": "User"}

ENTITY = {
    "name": "Order",
    "identifier_attribute": "Id",
    "attributes": [
        {"name": "Id", "type": INT, "mandatory": True, "auto_number": True},
        {"name": "OrderName", "type": TEXT, "mandatory": True, "length": 100},
        {"name": "Owner", "type": USER_ID, "mandatory": False, "delete_rule": "Protect"},
        {"name": "CreatedOn", "type": DATE, "mandatory": False},
    ],
}
ACTION = {
    "name": "Order_ChangeStatus",
    "input_parameters": [{"name": "OrderId", "type": USER_ID, "is_mandatory": True}],
    "output_parameters": [{"name": "IsSuccess", "type": {"kind": "primitive", "name": "Boolean"}}],
    "flow": {"nodes": [], "edges": []},
}
SCREEN = {
    "name": "OrderDetail",
    "flow": "MainFlow",
    "input_parameters": [{"name": "OrderId", "type": "Order Identifier", "is_mandatory": "Yes"}],
    "screen_data_sets": [{"name": "GetOrderById"}],
    "events": [{"event": "OnReady", "ref": "x"}, {"event": "OnDestroy", "ref": ""}],
    "widgets": [{"type": "NRWebWidgets.Input", "children": [{"type": "Button"}]}],
}


def _project(tmp_path: Path, stack: dict | None) -> Path:
    out = tmp_path / "OUTPUT"
    out.mkdir()
    if stack is not None:
        (tmp_path / "project.json").write_text(json.dumps({"stack": stack}), encoding="utf-8")
    return out


def test_backend_must_be_chosen(tmp_path):
    out = _project(tmp_path, None)
    with pytest.raises(StackError, match="No back end chosen"):
        load_stack(out)


def test_stack_from_project_json_and_override(tmp_path):
    out = _project(tmp_path, {"backend": "node"})
    stack = load_stack(out)
    assert stack["backend"] == "node"
    assert (stack["web"], stack["mobile"], stack["database"]) == ("react", "expo", "postgresql")
    assert load_stack(out, backend="dotnet")["backend"] == "dotnet"


def test_unsupported_value_is_rejected(tmp_path):
    out = _project(tmp_path, {"backend": "dotnet", "database": "sqlserver"})
    with pytest.raises(StackError, match="stack.database"):
        load_stack(out)


def test_type_mapping():
    assert csharp_type(TEXT) == "string"
    assert csharp_type({"kind": "primitive", "name": "Date"}) == "DateOnly"
    assert csharp_type(USER_ID) == "long"
    assert csharp_type("Order Identifier") == "long"
    assert csharp_type({"kind": "list", "element_type": USER_ID}) == "List<long>"
    assert csharp_type({"kind": "local_structure", "name": "Question Item"}) == "QuestionItem"
    assert ts_type(DATE) == "string"
    assert ts_type({"kind": "list", "element_type": TEXT}) == "string[]"
    assert ts_type("Order Identifier") == "number"


def test_mobile_detection(tmp_path):
    xml = tmp_path / "_xml"
    xml.mkdir()
    fragments = "<eSpaceFragment>" + "x" * 300_000 + "</eSpaceFragment>"  # ESpace comes after the fragments
    (xml / "MobileApp.xml").write_text(f'<OML>{fragments}<ESpace Name="MobileApp"\n  SplashScreen="x"></ESpace>',
                                       encoding="utf-8")
    (xml / "WebApp_v3.xml").write_text('<Document><ESpace Name="WebApp" DefaultScreen="y"></ESpace>', encoding="utf-8")
    stack = {"mobile_modules": ["Forced"]}
    assert module_is_mobile(tmp_path, "MobileApp", stack)
    assert not module_is_mobile(tmp_path, "WebApp", stack)
    assert module_is_mobile(tmp_path, "Forced", stack)
    assert not module_is_mobile(tmp_path, "Missing", stack)


def test_dotnet_backend_spec():
    stack = {"backend": "dotnet"}
    md = generate_backend_spec("SP_Orders_CS", "core_service", [ENTITY], [ACTION], [], [], [], {}, stack)
    assert "ASP.NET Core on .NET 10" in md
    assert '[Table("sp_orders_cs__order")]' in md
    assert "public long Id { get; set; }" in md
    assert '[MaxLength(100)] public string OrderName { get; set; } = "";' in md
    assert "public long? Owner { get; set; } // FK → User (Protect)" in md
    assert "UseSnakeCaseNamingConvention" in md
    assert "public sealed record OrderChangeStatusRequest(long OrderId);" in md
    assert ("public Task<OrderChangeStatusResponse> OrderChangeStatusAsync("
            "OrderChangeStatusRequest request, CancellationToken ct);") in md
    assert "```go" not in md


def test_node_backend_spec():
    stack = {"backend": "node"}
    ext = {"name": "HR System", "operations": [
        {"name": "GetEmployee", "http_method": "", "url_path": "/employees", "input_parameters": ["Id"],
         "output_parameters": ["Name"]}]}
    md = generate_backend_spec("SP_Orders_CS", "core_service", [ENTITY], [ACTION], [], [ext], [], {}, stack)
    assert "Node.js + TypeScript" in md
    assert "export interface Order {" in md
    assert "  owner: number | null; // FK → User (Protect)" in md
    assert "  createdOn: string; // ISO 8601" in md
    assert "export async function orderChangeStatus(req: OrderChangeStatusRequest): " \
           "Promise<OrderChangeStatusResponse>" in md
    assert "export interface HRSystemClient {" in md
    assert "  getEmployee(id: string): Promise<GetEmployeeResponse>;" in md


def test_client_entities_are_not_in_the_data_model():
    client = {**ENTITY, "name": "LocalOrder", "is_client_entity": True}
    md = generate_backend_spec("App", "application", [client], [], [], [], [], {}, {"backend": "dotnet"})
    assert "## Mobile Client Entities" in md
    assert "public class LocalOrder" not in md


def test_web_frontend_spec():
    md = generate_frontend_spec("App", "application", [SCREEN], [], [], {}, {"web": "react", "mobile": "expo"})
    assert "React + TypeScript" in md
    assert "// route: /order-detail?OrderId=" in md
    assert "  orderId: number;" in md
    assert "export function OrderDetailPage()" in md
    assert "`useGetOrderById`" in md
    assert "`OnReady` → useEffect" in md and "OnDestroy" not in md
    assert "Input" in md and "Button" in md
    assert "StatefulWidget" not in md


def test_mobile_frontend_spec():
    md = generate_frontend_spec("App", "application", [SCREEN], [], [], {}, {"web": "react", "mobile": "expo"},
                                mobile=True)
    assert "React Native (Expo)" in md
    assert "// app/main-flow/order-detail.tsx (Expo Router)" in md
    assert "useLocalSearchParams<OrderDetailParams>()" in md
    assert "TextInput" in md and "Pressable" in md


def test_index_lists_the_stack():
    row = {"name": "App", "type": "application", "front_end": "web", "entities": 1, "actions": 2,
           "screens": 3, "deps": 0, "readiness": "partial"}
    md = generate_index([row], {"backend": "node", "web": "react", "mobile": "expo", "database": "postgresql",
                                "source": "project.json"})
    assert "| Back end: Language | Node.js + TypeScript |" in md
    assert "| Web front end: UI components | **open** (`shadcn`, `mui`) |" in md
    assert "| Platform (AWS): Email | Amazon SES |" in md
    assert "| [App](App/) | application | web |" in md


def test_full_stack_from_project_json(tmp_path):
    out = _project(tmp_path, {"backend": {"language": "dotnet"}, "web": {"ui": "mui"},
                              "database": {"case_insensitive_text": False}})
    stack = load_stack(out)
    assert stack["backend"] == "dotnet" and stack["web"] == "react"
    assert stack["layers"]["backend"]["data_access"] == "efcore+dapper"  # default for dotnet
    assert stack["layers"]["web"]["ui"] == "mui"
    assert stack["layers"]["database"]["case_insensitive_text"] is False
    assert "web.ui" not in stack["open"] and "auth.identity_provider" in stack["open"]
    md = generate_index([], {**stack, "source": "project.json"})
    assert "| Web front end: UI components | MUI |" in md
    assert "**Open decisions**" in md and "`auth.identity_provider`" in md


def test_node_defaults_leave_node_choices_open(tmp_path):
    stack = load_stack(_project(tmp_path, {"backend": {"language": "node"}}))
    assert stack["layers"]["backend"]["data_access"] is None
    assert "backend.data_access" in stack["open"] and "backend.framework" in stack["open"]


def test_project_stack_init_and_set(tmp_path):
    from project_stack import main

    (tmp_path / "project.json").write_text(json.dumps({"scope": {"applications": ["App"]},
                                                       "stack": {"backend": "dotnet"}}), encoding="utf-8")
    assert main(["init", str(tmp_path)]) == 0
    config = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert config["scope"] == {"applications": ["App"]}  # other keys kept
    assert config["stack"]["backend"]["language"] == "dotnet"  # flat form converted
    assert config["stack"]["web"]["ui"] is None and config["stack"]["platform"]["email"] == "ses"

    assert main(["set", str(tmp_path / "OUTPUT"), "web.ui=shadcn", "database.case_insensitive_text=false"]) == 0
    config = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert config["stack"]["web"]["ui"] == "shadcn"
    assert config["stack"]["database"]["case_insensitive_text"] is False
    assert main(["set", str(tmp_path), "web.colour=blue"]) == 2  # unknown layer
    assert main(["set", str(tmp_path), "web.framework=vue"]) == 2  # unsupported by the generators
    assert main(["show", str(tmp_path)]) == 1  # decisions still open


def test_stale_spec_folders_are_removed(tmp_path):
    (tmp_path / "Old").mkdir()
    (tmp_path / "Old" / "backend-spec.md").write_text("x", encoding="utf-8")
    (tmp_path / "Kept").mkdir()
    (tmp_path / "Kept" / "notes.md").write_text("mine", encoding="utf-8")
    (tmp_path / "Current").mkdir()
    (tmp_path / "Current" / "backend-spec.md").write_text("x", encoding="utf-8")
    assert remove_stale_specs(tmp_path, {"Current"}) == ["Old"]
    assert (tmp_path / "Kept" / "notes.md").exists() and (tmp_path / "Current").exists()
