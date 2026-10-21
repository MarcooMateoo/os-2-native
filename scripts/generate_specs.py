"""Generate backend (Go) and frontend (Flutter) engineering specifications.

Reads extraction packages from a pass and produces per-module specs:

- ``<module>/backend-spec.md``  — Go service specification
- ``<module>/frontend-spec.md`` — Flutter component specification
- ``<module>/state-flows.md``   — Mermaid flowcharts for server actions
- ``index.md``                  — Module index / summary
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from generator_utils import (
    classify_module,
    dart_type,
    decode_http_method,
    deliverables_dir,
    go_type,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    normalize_widgets,
    slugify,
    write_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snake_case(name: str) -> str:
    """Convert PascalCase or camelCase to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _count_widgets(widgets: list[dict[str, Any]] | dict[str, Any]) -> int:
    """Recursively count widgets in a tree."""
    widgets = normalize_widgets(widgets)
    total = 0
    for w in widgets:
        total += 1
        total += _count_widgets(w.get("children", []))
    return total


def _collect_widget_types(widgets: list[dict[str, Any]] | dict[str, Any], types: set[str]) -> None:
    """Recursively collect widget type names."""
    widgets = normalize_widgets(widgets)
    for w in widgets:
        wtype = w.get("type", "")
        if wtype:
            types.add(wtype)
        _collect_widget_types(w.get("children", []), types)


def _collect_nav_destinations(widgets: list[dict[str, Any]] | dict[str, Any], destinations: set[str]) -> None:
    """Recursively find navigation destinations in widget trees."""
    widgets = normalize_widgets(widgets)
    for w in widgets:
        # Link / navigation widgets often have a "destination" or "on_click" ref
        dest = w.get("destination", "") or w.get("screen_ref", "")
        if dest:
            destinations.add(dest)
        for prop in w.get("properties", []):
            if prop.get("name", "") in ("Navigate", "Destination", "OnClick") and prop.get("value", ""):
                destinations.add(prop["value"])
        _collect_nav_destinations(w.get("children", []), destinations)


def _collect_event_handlers(
    widgets: list[dict[str, Any]] | dict[str, Any],
    handlers: list[dict[str, str]],
) -> None:
    """Recursively collect event handler bindings from widget trees."""
    widgets = normalize_widgets(widgets)
    for w in widgets:
        wname = w.get("name", "")
        wtype = w.get("type", "")
        for eh in w.get("event_handlers", []):
            event = eh.get("event", "")
            dest = eh.get("destination", "") or eh.get("action", "")
            if event and dest:
                handlers.append({
                    "widget": wname or wtype,
                    "event": event,
                    "target": dest.rsplit(".", 1)[-1] if "." in dest else dest,
                })
        _collect_event_handlers(w.get("children", []), handlers)


def _collect_visibility_conditions(
    widgets: list[dict[str, Any]] | dict[str, Any],
    conditions: list[str],
    *,
    max_conditions: int = 8,
) -> None:
    """Recursively collect If widget conditions."""
    if len(conditions) >= max_conditions:
        return
    widgets = normalize_widgets(widgets)
    for w in widgets:
        if w.get("type") == "If" and w.get("condition"):
            cond = w["condition"]
            if len(cond) < 120:  # Skip very long expressions
                conditions.append(cond)
        if len(conditions) >= max_conditions:
            return
        _collect_visibility_conditions(w.get("children", []), conditions, max_conditions=max_conditions)


def _param_go_sig(params: list[dict[str, Any]], direction: str) -> str:
    """Build a Go-style parameter list from action parameters."""
    parts = []
    for p in params:
        name = p.get("name", "param")
        tr = p.get("type", "string")
        parts.append(f"{name} {go_type(tr)}")
    return ", ".join(parts)


def _sanitize_mermaid_id(key: str) -> str:
    """Produce a short alphanumeric Mermaid node ID from a key."""
    clean = re.sub(r"[^a-zA-Z0-9]", "", key)
    return clean[:12] if clean else "node"


# Node-type → human-readable verb for flow descriptions
_NODE_VERBS: dict[str, str] = {
    "Nodes.Start": "",
    "Nodes.End": "",
    "Nodes.If": "Check",
    "Nodes.Switch": "Switch on",
    "Nodes.Assign": "Set",
    "Nodes.ExecuteAction": "Call",
    "Nodes.ExecuteServerAction": "Call server action",
    "Nodes.RunClientAction": "Run client action",
    "Nodes.RunServerAction": "Run server action",
    "Nodes.CreateOrUpdateRecord": "Upsert",
    "Nodes.CreateRecord": "Create",
    "Nodes.DeleteRecord": "Delete",
    "Nodes.AggregateQuery": "Query",
    "Nodes.SQLQuery": "Query (SQL)",
    "Nodes.RaiseException": "Raise error",
    "Nodes.ErrorHandler": "On error",
    "Nodes.ForEach": "For each",
    "NRNodes.Destination": "Navigate to",
    "Nodes.WebDestination": "Navigate to",
    "Nodes.Download": "Download",
    "Nodes.JSONSerialize": "Serialize to JSON",
    "Nodes.JSONDeserialize": "Deserialize JSON",
    "Nodes.Comment": "",
}


def _describe_flow_steps(flow: dict[str, Any]) -> list[str]:
    """Walk a flow graph and produce a step-by-step business logic summary.

    Returns a list of description lines (without leading bullet/number).
    Skips Start/End/Comment nodes.  Limits output to 12 steps to stay concise.
    """
    nodes = flow.get("nodes", []) if isinstance(flow, dict) else []
    edges = flow.get("edges", []) if isinstance(flow, dict) else []

    if not nodes:
        return []

    node_by_key: dict[str, dict[str, Any]] = {n["key"]: n for n in nodes if "key" in n}
    # Build adjacency from edges
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["from_key"], []).append(e["to_key"])

    # Find start node
    start_keys = [n["key"] for n in nodes if n.get("type") == "Nodes.Start"]
    if not start_keys:
        # No explicit start — use first node
        start_keys = [nodes[0]["key"]] if nodes else []

    # BFS walk
    visited: set[str] = set()
    queue = list(start_keys)
    steps: list[str] = []
    max_steps = 12

    while queue and len(steps) < max_steps:
        key = queue.pop(0)
        if key in visited:
            continue
        visited.add(key)

        node = node_by_key.get(key)
        if not node:
            continue

        ntype = node.get("type", "")
        name = node.get("name", "")
        verb = _NODE_VERBS.get(ntype, "")

        # Skip infrastructure nodes
        if ntype in ("Nodes.Start", "Nodes.End", "Nodes.Comment", ""):
            for nxt in adj.get(key, []):
                queue.append(nxt)
            continue

        if verb and name:
            steps.append(f"{verb} **{name}**")
        elif verb:
            steps.append(verb)
        elif name:
            steps.append(f"**{name}** ({ntype.split('.')[-1]})")
        else:
            steps.append(ntype.split(".")[-1])

        for nxt in adj.get(key, []):
            queue.append(nxt)

    return steps


def _mermaid_node_shape(node_type: str, label: str, node_id: str) -> str:
    """Return a Mermaid node definition with appropriate shape."""
    safe_label = label.replace('"', "'")
    if node_type == "Nodes.Start":
        return f'{node_id}(("{safe_label or "Start"}"))'
    if node_type == "Nodes.End":
        return f'{node_id}(("{safe_label or "End"}"))'
    if node_type == "Nodes.If":
        return f'{node_id}{{{{{safe_label or "Condition"}}}}}'
    if node_type == "Nodes.ErrorHandler":
        return f'{node_id}{{{{{{{safe_label or "Error Handler"}}}}}}}'
    # Assign, ExecuteAction, and anything else → rectangle
    return f'{node_id}["{safe_label or node_type.split(".")[-1]}"]'


# ---------------------------------------------------------------------------
# Backend spec generation
# ---------------------------------------------------------------------------

def generate_backend_spec(
    module_name: str,
    module_type: str,
    entities: list[dict[str, Any]],
    server_actions: list[dict[str, Any]],
    actions_summary: list[dict[str, Any]],
    externals: list[dict[str, Any]],
    exposed: list[dict[str, Any]],
    graph: dict[str, Any],
) -> str:
    """Generate backend-spec.md content for a module."""
    lines: list[str] = []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    dep_count = len(edges)

    # -- Module overview --
    lines.append(f"# Backend Specification: {module_name}")
    lines.append("")
    lines.append(f"- **Module type**: {module_type}")
    lines.append(f"- **Dependencies**: {dep_count}")
    lines.append(f"- **Entities**: {len(entities)}")
    lines.append(f"- **Server actions**: {len(server_actions)}")
    lines.append(f"- **External integrations**: {len(externals)}")
    lines.append(f"- **Exposed APIs**: {len(exposed)}")
    lines.append("")

    # -- Data model --
    if entities:
        lines.append("## Data Model")
        lines.append("")
        for entity in entities:
            ename = entity.get("name", "Unknown")
            lines.append(f"### {ename}")
            lines.append("")
            desc = entity.get("description", "")
            if desc:
                lines.append(f"{desc}")
                lines.append("")
            lines.append("```go")
            lines.append(f"type {ename} struct {{")
            for attr in entity.get("attributes", []):
                aname = attr.get("name", "field")
                tr = attr.get("type", "string")
                gtype = go_type(tr)
                snake = _snake_case(aname)
                # Check for FK annotation in go_type output
                comment = ""
                if "/* FK" in gtype:
                    parts = gtype.split(" /* ", 1)
                    gtype = parts[0].strip()
                    comment = f" // {parts[1].rstrip(' */')}"
                tag = f'`json:"{snake}" db:"{snake}"`'
                padding = max(1, 14 - len(aname))
                type_padding = max(1, 12 - len(gtype))
                lines.append(f"    {aname}{' ' * padding}{gtype}{' ' * type_padding}{tag}{comment}")
            lines.append("}")
            lines.append("```")
            lines.append("")

    # -- Server actions --
    if server_actions:
        lines.append("## Server Actions")
        lines.append("")
        for sa in server_actions:
            sa_name = sa.get("name", "Action")
            inputs = sa.get("input_parameters", [])
            outputs = sa.get("output_parameters", [])
            flow = sa.get("flow", {})
            nodes = flow.get("nodes", []) if isinstance(flow, dict) else []
            node_count = len(nodes)
            has_error = any(n.get("type") == "Nodes.ErrorHandler" for n in nodes)
            has_branch = any(n.get("type") == "Nodes.If" for n in nodes)

            lines.append(f"### {sa_name}")
            lines.append("")

            # Signature
            if outputs:
                out_name = f"{sa_name}Response"
                lines.append("```go")
                lines.append(f"func {sa_name}(ctx context.Context, req {sa_name}Request) (*{out_name}, error)")
                lines.append("```")
            else:
                lines.append("```go")
                lines.append(f"func {sa_name}(ctx context.Context, req {sa_name}Request) error")
                lines.append("```")
            lines.append("")

            # Input/output tables
            if inputs:
                rows = []
                for p in inputs:
                    rows.append([p.get("name", ""), go_type(p.get("type", "string")),
                                 "yes" if p.get("is_mandatory") else "no"])
                lines.append("**Inputs:**")
                lines.append("")
                lines.append(md_table(["Parameter", "Type", "Required"], rows))
                lines.append("")

            if outputs:
                rows = []
                for p in outputs:
                    rows.append([p.get("name", ""), go_type(p.get("type", "string"))])
                lines.append("**Outputs:**")
                lines.append("")
                lines.append(md_table(["Parameter", "Type"], rows))
                lines.append("")

            # Flow summary with business logic steps
            flow_parts = []
            if node_count:
                flow_parts.append(f"{node_count} nodes")
            if has_error:
                flow_parts.append("error handling")
            if has_branch:
                flow_parts.append("branching")
            lines.append(f"**Flow**: {' · '.join(flow_parts) if flow_parts else 'empty'}")
            lines.append("")

            # Walk the flow graph for a step-by-step description
            flow_steps = _describe_flow_steps(flow)
            if flow_steps:
                lines.append("**Logic steps:**")
                lines.append("")
                for i, step in enumerate(flow_steps, 1):
                    lines.append(f"{i}. {step}")
                lines.append("")

    # -- Integration adapters --
    if externals:
        lines.append("## Integration Adapters")
        lines.append("")
        for ext in externals:
            sys_name = ext.get("name", "ExternalSystem")
            endpoint = ext.get("endpoint_url", "")
            operations = ext.get("operations", [])

            # Build a clean interface name
            iface_name = re.sub(r"[^a-zA-Z0-9]", "", sys_name) + "Client"

            lines.append(f"### {sys_name}")
            if endpoint:
                lines.append(f"- **Base URL**: `{endpoint}`")
            lines.append(f"- **Operations**: {len(operations)}")
            lines.append("")
            lines.append("```go")
            lines.append(f"type {iface_name} interface {{")
            for op in operations:
                op_name = op.get("name", "Operation")
                method = decode_http_method(op.get("http_method"))
                url_path = op.get("url_path", "")
                op_inputs = op.get("input_parameters", [])
                op_outputs = op.get("output_parameters", [])

                # Build parameter string
                params = ["ctx context.Context"]
                for inp in op_inputs:
                    pname = inp if isinstance(inp, str) else inp.get("name", "param")
                    params.append(f"{pname} string")
                param_str = ", ".join(params)

                # Return type
                if op_outputs:
                    ret = f"(*{op_name}Response, error)"
                else:
                    ret = "error"

                lines.append(f"    // {method} {url_path}")
                lines.append(f"    {op_name}({param_str}) {ret}")
            lines.append("}")
            lines.append("```")
            lines.append("")

    # -- Exposed API surface --
    if exposed:
        lines.append("## Exposed API Surface")
        lines.append("")
        rows = []
        for api in exposed:
            api_name = api.get("name", "")
            api_type = api.get("type", "")
            in_count = len(api.get("input_parameters", []))
            out_count = len(api.get("output_parameters", []))
            rows.append([api_name, api_type, str(in_count), str(out_count)])
        lines.append(md_table(["Name", "Type", "Inputs", "Outputs"], rows))
        lines.append("")

    # -- Dependencies --
    if edges:
        lines.append("## Dependencies")
        lines.append("")
        rows = []
        for edge in edges:
            target = edge.get("target", "")
            etypes = ", ".join(edge.get("edge_types", []))
            status = edge.get("status", "")
            rows.append([target, etypes, status])
        lines.append(md_table(["Module", "Edge Types", "Status"], rows))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Frontend spec generation
# ---------------------------------------------------------------------------

def generate_frontend_spec(
    module_name: str,
    module_type: str,
    screens: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    actions_summary: list[dict[str, Any]],
    graph: dict[str, Any],
) -> str:
    """Generate frontend-spec.md content for a module."""
    lines: list[str] = []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    client_actions = [a for a in actions_summary if a.get("type") == "client"]

    # -- Module overview --
    lines.append(f"# Frontend Specification: {module_name}")
    lines.append("")
    lines.append(f"- **Module type**: {module_type}")
    lines.append(f"- **Screens**: {len(screens)}")
    lines.append(f"- **Blocks**: {len(blocks)}")
    lines.append(f"- **Client actions**: {len(client_actions)}")
    lines.append(f"- **Dependencies**: {len(edges)}")
    lines.append("")

    # -- Screen inventory --
    if screens:
        lines.append("## Screen Inventory")
        lines.append("")
        rows = []
        screen_sizes: list[tuple[int, dict[str, Any]]] = []
        for scr in screens:
            sname = scr.get("name", "")
            widgets = scr.get("widgets", [])
            wcount = _count_widgets(widgets)
            pcount = len(scr.get("input_parameters", [])) + len(scr.get("output_parameters", []))
            flow = scr.get("flow", "")
            rows.append([sname, str(wcount), str(pcount), flow])
            screen_sizes.append((wcount, scr))
        lines.append(md_table(["Screen", "Widgets", "Parameters", "Flow"], rows))
        lines.append("")

        # -- Per-screen component spec (top 10 largest) --
        screen_sizes.sort(key=lambda x: x[0], reverse=True)
        top_screens = screen_sizes[:10]

        if top_screens:
            lines.append("## Component Specifications")
            lines.append("")
            for _wcount, scr in top_screens:
                sname = scr.get("name", "Screen")
                inputs = scr.get("input_parameters", [])
                widgets = scr.get("widgets", [])
                widget_types: set[str] = set()
                _collect_widget_types(widgets, widget_types)
                nav_dests: set[str] = set()
                _collect_nav_destinations(widgets, nav_dests)

                lines.append(f"### {sname}")
                lines.append("")

                # Dart widget class
                lines.append("```dart")
                if inputs:
                    lines.append(f"class {sname}Screen extends StatefulWidget {{")
                    for inp in inputs:
                        pname = inp.get("name", "param")
                        tr = inp.get("type", "String")
                        lines.append(f"  final {dart_type(tr)} {pname};")
                    lines.append("")
                    params = ", ".join(
                        f"required this.{inp.get('name', 'param')}" for inp in inputs
                    )
                    lines.append(f"  const {sname}Screen({{{params}}});")
                else:
                    lines.append(f"class {sname}Screen extends StatelessWidget {{")
                    lines.append(f"  const {sname}Screen();")
                lines.append("}")
                lines.append("```")
                lines.append("")

                # Key widget types
                if widget_types:
                    categorized = _categorize_widgets(widget_types)
                    if categorized:
                        lines.append(f"**Key widgets**: {', '.join(sorted(categorized))}")
                        lines.append("")

                # Navigation destinations
                if nav_dests:
                    lines.append(f"**Navigates to**: {', '.join(sorted(nav_dests))}")
                    lines.append("")

                # Event handlers — what user interactions trigger which actions
                handlers: list[dict[str, str]] = []
                _collect_event_handlers(widgets, handlers)
                if handlers:
                    lines.append("**Event handlers**:")
                    lines.append("")
                    for eh in handlers[:12]:
                        lines.append(f"- `{eh['widget']}` → {eh['event']} → `{eh['target']}`")
                    lines.append("")

                # Visibility conditions — conditional rendering rules
                conditions: list[str] = []
                _collect_visibility_conditions(widgets, conditions)
                if conditions:
                    lines.append("**Conditional visibility**:")
                    lines.append("")
                    for cond in conditions:
                        lines.append(f"- `{cond}`")
                    lines.append("")

                # Client action flow logic — behavioral summary
                screen_actions = scr.get("client_actions", [])
                if screen_actions:
                    lines.append("**Client action logic**:")
                    lines.append("")
                    for ca in screen_actions[:6]:
                        ca_name = ca.get("name", "")
                        flow = ca.get("flow", {})
                        steps = _describe_flow_steps(flow)
                        if ca_name and steps:
                            lines.append(f"*{ca_name}*:")
                            for step in steps[:6]:
                                lines.append(f"  - {step}")
                            lines.append("")

    # -- Block inventory --
    if blocks:
        lines.append("## Block Inventory")
        lines.append("")
        rows = []
        for blk in blocks:
            bname = blk.get("name", "")
            in_count = len(blk.get("input_parameters", []))
            out_count = len(blk.get("output_parameters", []))
            widgets = blk.get("widgets", [])
            wcount = _count_widgets(widgets)
            rows.append([bname, str(wcount), str(in_count), str(out_count)])
        lines.append(md_table(["Block", "Widgets", "Inputs", "Outputs"], rows))
        lines.append("")

        # Dart widget specs for blocks
        lines.append("### Block Widget Specs")
        lines.append("")
        for blk in blocks:
            bname = blk.get("name", "Block")
            inputs = blk.get("input_parameters", [])
            outputs = blk.get("output_parameters", [])

            lines.append(f"**{bname}**")
            lines.append("")
            lines.append("```dart")
            lines.append(f"class {bname}Block extends StatefulWidget {{")
            for inp in inputs:
                pname = inp.get("name", "param")
                tr = inp.get("type", "String")
                lines.append(f"  final {dart_type(tr)} {pname};")
            for out in outputs:
                pname = out.get("name", "param")
                tr = out.get("type", "String")
                lines.append(f"  final ValueChanged<{dart_type(tr)}>? on{pname}Changed;")
            if inputs or outputs:
                lines.append("")
            constructor_params = []
            for inp in inputs:
                constructor_params.append(f"required this.{inp.get('name', 'param')}")
            for out in outputs:
                constructor_params.append(f"this.on{out.get('name', 'param')}Changed")
            params_str = ", ".join(constructor_params)
            lines.append(f"  const {bname}Block({{{params_str}}});")
            lines.append("}")
            lines.append("```")
            lines.append("")

    # -- State management --
    lines.append("## State Management")
    lines.append("")
    server_count = sum(1 for a in actions_summary if a.get("type") == "server")
    client_count = len(client_actions)
    lines.append(f"- **Server actions**: {server_count} (data fetch / mutation)")
    lines.append(f"- **Client actions**: {client_count} (UI logic / state)")
    lines.append("")

    if client_actions:
        rows = []
        for ca in client_actions:
            rows.append([ca.get("name", ""), str(ca.get("node_count", 0)),
                         str(ca.get("input_count", 0)), str(ca.get("output_count", 0))])
        lines.append(md_table(["Action", "Nodes", "Inputs", "Outputs"], rows))
        lines.append("")

    # -- Navigation map --
    if screens:
        all_destinations: dict[str, set[str]] = {}
        for scr in screens:
            sname = scr.get("name", "")
            dests: set[str] = set()
            _collect_nav_destinations(scr.get("widgets", []), dests)
            if dests:
                all_destinations[sname] = dests

        if all_destinations:
            lines.append("## Navigation Map")
            lines.append("")
            rows = []
            for src, dests in sorted(all_destinations.items()):
                rows.append([src, ", ".join(sorted(dests))])
            lines.append(md_table(["Screen", "Destinations"], rows))
            lines.append("")

    return "\n".join(lines)


def _categorize_widgets(widget_types: set[str]) -> set[str]:
    """Map raw widget type names to simplified Flutter-relevant categories."""
    categories: set[str] = set()
    mapping = {
        "Container": "Container",
        "List": "ListView",
        "Table": "DataTable",
        "Input": "TextFormField",
        "TextArea": "TextFormField",
        "Button": "ElevatedButton",
        "Link": "TextButton",
        "Image": "Image",
        "Icon": "Icon",
        "Checkbox": "Checkbox",
        "RadioButton": "Radio",
        "Dropdown": "DropdownButton",
        "If": "Visibility",
        "Expression": "Text",
        "Label": "Text",
        "WebBlockInstance": "CustomWidget",
        "Placeholder": "Slot",
    }
    for wt in widget_types:
        # Strip prefixes like "NRWebWidgets." or "CustomWidget-..."
        short = wt.rsplit(".", 1)[-1] if "." in wt else wt
        for key, category in mapping.items():
            if key.lower() in short.lower():
                categories.add(category)
                break
    return categories


# ---------------------------------------------------------------------------
# State flows generation
# ---------------------------------------------------------------------------

def generate_state_flows(
    module_name: str,
    server_actions: list[dict[str, Any]],
) -> str | None:
    """Generate state-flows.md with Mermaid flowcharts.

    Returns None if there are no qualifying actions.
    """
    # Filter actions with meaningful flows (>=3 nodes)
    qualifying = []
    for sa in server_actions:
        flow = sa.get("flow", {})
        if not isinstance(flow, dict):
            continue
        nodes = flow.get("nodes", [])
        if len(nodes) >= 3:
            qualifying.append(sa)

    if not qualifying:
        return None

    lines: list[str] = []
    lines.append(f"# State Flows: {module_name}")
    lines.append("")
    lines.append(f"{len(qualifying)} action(s) with ≥3 nodes.")
    lines.append("")

    for sa in qualifying:
        sa_name = sa.get("name", "Action")
        flow = sa.get("flow", {})
        nodes = flow.get("nodes", [])
        edges = flow.get("edges", [])

        lines.append(f"## {sa_name}")
        lines.append("")

        # Node type breakdown
        type_counts: dict[str, int] = {}
        for n in nodes:
            ntype = n.get("type", "unknown")
            short = ntype.split(".")[-1] if "." in ntype else ntype
            type_counts[short] = type_counts.get(short, 0) + 1

        parts = [f"{v} {k}" for k, v in sorted(type_counts.items())]
        lines.append(f"**Nodes**: {len(nodes)} ({', '.join(parts)})")

        # Error handling?
        has_error = any(n.get("type") == "Nodes.ErrorHandler" for n in nodes)
        lines.append(f"**Error handling**: {'yes' if has_error else 'no'}")
        lines.append("")

        # Build Mermaid flowchart
        # Create stable IDs for each node
        key_to_id: dict[str, str] = {}
        for i, n in enumerate(nodes):
            key = n.get("key", f"n{i}")
            node_id = f"n{i}"
            key_to_id[key] = node_id

        lines.append("```mermaid")
        lines.append("flowchart TD")

        # Define nodes
        for i, n in enumerate(nodes):
            key = n.get("key", f"n{i}")
            node_id = key_to_id[key]
            ntype = n.get("type", "")
            label = n.get("name", "") or ntype.split(".")[-1]
            node_def = _mermaid_node_shape(ntype, label, node_id)
            lines.append(f"    {node_def}")

        # Draw edges
        if edges:
            for e in edges:
                from_key = e.get("from_key", "")
                to_key = e.get("to_key", "")
                from_id = key_to_id.get(from_key)
                to_id = key_to_id.get(to_key)
                if from_id and to_id:
                    label = e.get("kind", "") or e.get("type", "") or e.get("label", "")
                    if label:
                        safe_label = label.replace('"', "'")
                        lines.append(f"    {from_id} -->|{safe_label}| {to_id}")
                    else:
                        lines.append(f"    {from_id} --> {to_id}")
        else:
            # No explicit edges — infer sequential order from position
            sorted_nodes = sorted(
                enumerate(nodes),
                key=lambda x: (x[1].get("position", {}).get("y", 0),
                               x[1].get("position", {}).get("x", 0)),
            )
            for idx in range(len(sorted_nodes) - 1):
                i_from = sorted_nodes[idx][0]
                i_to = sorted_nodes[idx + 1][0]
                from_key = nodes[i_from].get("key", f"n{i_from}")
                to_key = nodes[i_to].get("key", f"n{i_to}")
                from_id = key_to_id.get(from_key, f"n{i_from}")
                to_id = key_to_id.get(to_key, f"n{i_to}")
                lines.append(f"    {from_id} --> {to_id}")

        lines.append("```")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------

def generate_index(module_rows: list[dict[str, Any]]) -> str:
    """Generate index.md with module summary."""
    total_entities = sum(r["entities"] for r in module_rows)
    total_actions = sum(r["actions"] for r in module_rows)
    total_screens = sum(r["screens"] for r in module_rows)

    lines: list[str] = []
    lines.append("# Engineering Specifications Index")
    lines.append("")
    lines.append(f"- **Modules**: {len(module_rows)}")
    lines.append(f"- **Total entities**: {total_entities}")
    lines.append(f"- **Total actions**: {total_actions}")
    lines.append(f"- **Total screens**: {total_screens}")
    lines.append("")

    rows = []
    for r in sorted(module_rows, key=lambda x: x["name"]):
        rows.append([
            f"[{r['name']}]({r['name']}/)",
            r["type"],
            str(r["entities"]),
            str(r["actions"]),
            str(r["screens"]),
            str(r["deps"]),
            r["readiness"],
        ])
    lines.append(md_table(
        ["Module", "Type", "Entities", "Actions", "Screens", "Deps", "Readiness"],
        rows,
    ))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_arg_parser("Generate backend (Go) and frontend (Flutter) engineering specifications")
    args = parser.parse_args()
    pass_dir: Path = args.pass_dir

    packages = load_pass_packages(pass_dir)
    if not packages:
        print("ERROR: No packages found.", file=sys.stderr)
        sys.exit(1)

    out_dir = deliverables_dir(pass_dir, "specs")
    total_pkgs = len(packages)
    print(f"Generating specs for {total_pkgs} modules → {out_dir}")

    module_rows: list[dict[str, Any]] = []
    files_written = 0
    width = len(str(total_pkgs))

    for idx, pkg in enumerate(packages, 1):
        module_name: str = pkg.get("module_name", "unknown")
        module_type = classify_module(module_name)
        print(f"  [{idx:>{width}}/{total_pkgs}] Generating {module_name}...")

        # Load domain files
        entities = load_domain_file(pass_dir, module_name, "data", "entities.json")
        server_actions = load_domain_file(pass_dir, module_name, "logic", "server-actions.json")
        actions_summary = load_domain_file(pass_dir, module_name, "logic", "actions.json")
        screens = load_domain_file(pass_dir, module_name, "interfaces", "screens.json")
        blocks = load_domain_file(pass_dir, module_name, "interfaces", "blocks.json")
        externals = load_domain_file(pass_dir, module_name, "integrations", "external-systems.json")
        exposed = load_domain_file(pass_dir, module_name, "integrations", "exposed-apis.json")
        graph = load_domain_file(pass_dir, module_name, "dependencies", "graph.json")

        # Normalize lists
        if not isinstance(entities, list):
            entities = []
        if not isinstance(server_actions, list):
            server_actions = []
        if not isinstance(actions_summary, list):
            actions_summary = []
        if not isinstance(screens, list):
            screens = []
        if not isinstance(blocks, list):
            blocks = []
        if not isinstance(externals, list):
            externals = []
        if not isinstance(exposed, list):
            exposed = []

        edges = graph.get("edges", []) if isinstance(graph, dict) else []

        # Readiness from package
        impl = pkg.get("implementation_readiness", {})
        readiness = impl.get("status", "unknown")

        # Build index row
        module_rows.append({
            "name": module_name,
            "type": module_type,
            "entities": len(entities),
            "actions": len(actions_summary),
            "screens": len(screens),
            "deps": len(edges),
            "readiness": readiness,
        })

        module_dir = out_dir / module_name

        # Backend spec
        backend_md = generate_backend_spec(
            module_name, module_type, entities, server_actions,
            actions_summary, externals, exposed, graph,
        )
        write_text(module_dir / "backend-spec.md", backend_md)
        files_written += 1

        # Frontend spec
        frontend_md = generate_frontend_spec(
            module_name, module_type, screens, blocks,
            actions_summary, graph,
        )
        write_text(module_dir / "frontend-spec.md", frontend_md)
        files_written += 1

        # State flows
        flows_md = generate_state_flows(module_name, server_actions)
        if flows_md:
            write_text(module_dir / "state-flows.md", flows_md)
            files_written += 1

    # Index
    index_md = generate_index(module_rows)
    write_text(out_dir / "index.md", index_md)
    files_written += 1

    # Summary
    total_entities = sum(r["entities"] for r in module_rows)
    total_actions = sum(r["actions"] for r in module_rows)
    total_screens = sum(r["screens"] for r in module_rows)
    print(f"\n{'=' * 60}")
    print(f"  Modules:  {len(module_rows)}")
    print(f"  Entities: {total_entities}")
    print(f"  Actions:  {total_actions}")
    print(f"  Screens:  {total_screens}")
    print(f"  Files:    {files_written}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
