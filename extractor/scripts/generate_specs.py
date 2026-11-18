"""Generate back-end and front-end engineering specifications for the target stack.

The stack comes from ``"stack"`` in ``projects/<Project>/project.json`` (see
``target_stack.py`` and ``TECH_STACK.md``):

- back end: ASP.NET Core on .NET 10 (C#) or Node.js + TypeScript
- web front end: React + TypeScript; mobile apps: React Native (Expo)
- database: PostgreSQL, with the table names ``generate_db_schema.py`` writes

Per module it writes:

- ``<module>/backend-spec.md``  — data model, server/service actions, integrations
- ``<module>/frontend-spec.md`` — screens, blocks, data hooks, navigation
- ``<module>/state-flows.md``   — Mermaid flowcharts for server actions
- ``index.md``                  — target stack and module summary
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from generate_db_schema import _table_name
from generator_utils import (
    classify_module,
    decode_http_method,
    deliverables_dir,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    normalize_widgets,
    write_text,
)
from target_stack import (
    BACKENDS,
    CATALOGUE,
    OPEN,
    StackError,
    backend_type,
    camel,
    describe,
    is_identifier,
    is_mandatory,
    kebab,
    load_stack,
    module_is_mobile,
    pascal,
    ts_note,
    ts_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# Back end
# ---------------------------------------------------------------------------

_MAX_BOUNDED_TEXT = 2000  # same limit as generate_db_schema.py (VARCHAR(n) up to here)


def _choice(stack: dict[str, Any], group: str, key: str) -> str | None:
    """Description of a stack layer, or ``None`` when it's open or unknown."""
    value = (stack.get("layers") or {}).get(group, {}).get(key)
    if value is OPEN:
        return None
    for layer in CATALOGUE[group][1]:
        if layer.key == key:
            return layer.options.get(value, str(value))
    return str(value)


def _target(stack: dict[str, Any], group: str, keys: list[str], base: str) -> str:
    parts = [base] + [c for c in (_choice(stack, group, k) for k in keys) if c]
    return ", ".join(parts)


def _code_lang(stack: dict[str, Any]) -> str:
    return "csharp" if stack["backend"] == "dotnet" else "ts"


def _entity_field_dotnet(entity: dict[str, Any], attr: dict[str, Any]) -> str:
    name = attr.get("name", "Field")
    tr = attr.get("type", "Text")
    target = is_identifier(tr)
    is_key = name == (entity.get("identifier_attribute") or "Id") or attr.get("auto_number")
    ctype = backend_type({"backend": "dotnet"}, tr)
    if is_key:
        ctype = "long"
    comment = ""
    if target is not None and not is_key:
        if not attr.get("mandatory"):
            ctype += "?"
        rule = attr.get("delete_rule")
        comment = f" // FK → {target}" + (f" ({rule})" if rule else "")
    prefix = ""
    length = attr.get("length")
    if ctype == "string":
        if isinstance(length, int) and 0 < length <= _MAX_BOUNDED_TEXT:
            prefix = f"[MaxLength({length})] "
        init = ' = "";'
    elif ctype == "byte[]":
        init = " = [];"
        comment = comment or " // BYTEA; consider an S3 key instead"
    else:
        init = ""
    return f"    {prefix}public {ctype} {pascal(name)} {{ get; set; }}{init}{comment}"


def _entity_field_node(entity: dict[str, Any], attr: dict[str, Any]) -> str:
    name = attr.get("name", "field")
    tr = attr.get("type", "Text")
    target = is_identifier(tr)
    is_key = name == (entity.get("identifier_attribute") or "Id") or attr.get("auto_number")
    ttype = "number" if is_key else ts_type(tr)
    notes = []
    if target is not None and not is_key:
        if not attr.get("mandatory"):
            ttype += " | null"
        rule = attr.get("delete_rule")
        notes.append(f"FK → {target}" + (f" ({rule})" if rule else ""))
    length = attr.get("length")
    if ttype == "string" and isinstance(length, int) and 0 < length <= _MAX_BOUNDED_TEXT:
        notes.append(f"max {length}")
    note = ts_note(tr)
    if note:
        notes.append(note)
    comment = f" // {'; '.join(notes)}" if notes else ""
    return f"  {camel(name)}: {ttype};{comment}"


def _entity_block(module_name: str, entity: dict[str, Any], stack: dict[str, Any]) -> list[str]:
    ename = entity.get("name", "Entity")
    attrs = entity.get("attributes", [])
    lines = [f"```{_code_lang(stack)}"]
    if stack["backend"] == "dotnet":
        lines.append(f'[Table("{_table_name(module_name, ename)}")]')
        lines.append(f"public class {pascal(ename)}")
        lines.append("{")
        lines.extend(_entity_field_dotnet(entity, a) for a in attrs)
        lines.append("}")
    else:
        lines.append(f"// table: {_table_name(module_name, ename)} (snake_case columns)")
        lines.append(f"export interface {pascal(ename)} {{")
        lines.extend(_entity_field_node(entity, a) for a in attrs)
        lines.append("}")
    lines.append("```")
    return lines


def _record(name: str, params: list[dict[str, Any]], stack: dict[str, Any]) -> list[str]:
    """Request/response type for an action's parameters."""
    if stack["backend"] == "dotnet":
        fields = ", ".join(
            f"{backend_type(stack, p.get('type'))}{'' if is_mandatory(p) or not is_identifier(p.get('type') or '') else '?'} "
            f"{pascal(p.get('name', 'Value'))}"
            for p in params
        )
        return [f"public sealed record {name}({fields});"]
    lines = [f"export interface {name} {{"]
    for p in params:
        optional = "" if is_mandatory(p) else "?"
        note = ts_note(p.get("type"))
        lines.append(f"  {camel(p.get('name', 'value'))}{optional}: {ts_type(p.get('type'))};"
                     + (f" // {note}" if note else ""))
    lines.append("}")
    return lines


def _action_signature(action: dict[str, Any], stack: dict[str, Any], service: str) -> list[str]:
    name = action.get("name", "Action")
    inputs = action.get("input_parameters", [])
    outputs = action.get("output_parameters", [])
    req, res = f"{pascal(name)}Request", f"{pascal(name)}Response"
    lines = [f"```{_code_lang(stack)}"]
    if inputs:
        lines.extend(_record(req, inputs, stack))
    if outputs:
        lines.extend(_record(res, outputs, stack))
    if stack["backend"] == "dotnet":
        ret = f"Task<{res}>" if outputs else "Task"
        args = f"{req} request, CancellationToken ct" if inputs else "CancellationToken ct"
        lines.append(f"// {service}")
        lines.append(f"public {ret} {pascal(name)}Async({args});")
    else:
        ret = f"Promise<{res}>" if outputs else "Promise<void>"
        args = f"req: {req}" if inputs else ""
        lines.append(f"// {service}")
        lines.append(f"export async function {camel(name)}({args}): {ret}")
    lines.append("```")
    return lines


def _param_rows(params: list[dict[str, Any]], stack: dict[str, Any], with_required: bool) -> list[list[str]]:
    rows = []
    for p in params:
        row = [p.get("name", ""), f"`{backend_type(stack, p.get('type'))}`"]
        if with_required:
            row.append("yes" if is_mandatory(p) else "no")
        rows.append(row)
    return rows


def _actions_section(
    title: str,
    actions: list[dict[str, Any]],
    stack: dict[str, Any],
    service: str,
) -> list[str]:
    lines = [f"## {title}", ""]
    for sa in actions:
        flow = sa.get("flow", {})
        nodes = flow.get("nodes", []) if isinstance(flow, dict) else []
        lines.append(f"### {sa.get('name', 'Action')}")
        lines.append("")
        if sa.get("description"):
            lines.append(sa["description"])
            lines.append("")
        lines.extend(_action_signature(sa, stack, service))
        lines.append("")
        inputs = sa.get("input_parameters", [])
        outputs = sa.get("output_parameters", [])
        if inputs:
            lines.append("**Inputs:**")
            lines.append("")
            lines.append(md_table(["Parameter", "Type", "Required"], _param_rows(inputs, stack, True)))
            lines.append("")
        if outputs:
            lines.append("**Outputs:**")
            lines.append("")
            lines.append(md_table(["Parameter", "Type"], _param_rows(outputs, stack, False)))
            lines.append("")
        flow_parts = []
        if nodes:
            flow_parts.append(f"{len(nodes)} nodes")
        if any(n.get("type") == "Nodes.ErrorHandler" for n in nodes):
            flow_parts.append("error handling")
        if any(n.get("type") == "Nodes.If" for n in nodes):
            flow_parts.append("branching")
        if sa.get("is_function"):
            flow_parts.append("function (used in expressions)")
        lines.append(f"**Flow**: {' · '.join(flow_parts) if flow_parts else 'empty'}")
        lines.append("")
        steps = _describe_flow_steps(flow)
        if steps:
            lines.append("**Logic steps:**")
            lines.append("")
            lines.extend(f"{i}. {step}" for i, step in enumerate(steps, 1))
            lines.append("")
    return lines


def _integration_block(ext: dict[str, Any], stack: dict[str, Any]) -> list[str]:
    sys_name = ext.get("name", "ExternalSystem")
    client = pascal(sys_name) + "Client"
    lines = [f"```{_code_lang(stack)}"]
    if stack["backend"] == "dotnet":
        lines.append(f"public interface I{client}  // Refit")
        lines.append("{")
        for op in ext.get("operations", []):
            method = decode_http_method(op.get("http_method")).capitalize()
            path = op.get("url_path", "") or "/"
            params = [
                f"string {camel(i if isinstance(i, str) else i.get('name', 'param'))}"
                for i in op.get("input_parameters", [])
            ]
            params.append("CancellationToken ct = default")
            ret = f"Task<{pascal(op.get('name', 'Operation'))}Response>" if op.get("output_parameters") else "Task"
            lines.append(f'    [{method}("{path}")]')
            lines.append(f"    {ret} {pascal(op.get('name', 'Operation'))}Async({', '.join(params)});")
        lines.append("}")
    else:
        lines.append(f"export interface {client} {{")
        for op in ext.get("operations", []):
            method = decode_http_method(op.get("http_method"))
            params = ", ".join(
                f"{camel(i if isinstance(i, str) else i.get('name', 'param'))}: string"
                for i in op.get("input_parameters", [])
            )
            ret = f"Promise<{pascal(op.get('name', 'Operation'))}Response>" if op.get("output_parameters") else "Promise<void>"
            lines.append(f"  // {method} {op.get('url_path', '')}")
            lines.append(f"  {camel(op.get('name', 'operation'))}({params}): {ret};")
        lines.append("}")
    lines.append("```")
    return lines


def generate_backend_spec(
    module_name: str,
    module_type: str,
    entities: list[dict[str, Any]],
    server_actions: list[dict[str, Any]],
    actions_summary: list[dict[str, Any]],
    externals: list[dict[str, Any]],
    exposed: list[dict[str, Any]],
    graph: dict[str, Any],
    stack: dict[str, Any],
    service_actions: list[dict[str, Any]] | None = None,
) -> str:
    """Generate backend-spec.md content for a module."""
    service_actions = service_actions or []
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    dotnet = stack["backend"] == "dotnet"
    service = f"{pascal(module_name)}Service" if dotnet else f"{kebab(module_name)}.service.ts"
    server_entities = [e for e in entities if not e.get("is_client_entity")]
    client_entities = [e for e in entities if e.get("is_client_entity")]

    lines: list[str] = [
        f"# Backend Specification: {module_name}",
        "",
        f"- **Target**: {_target(stack, 'backend', ['data_access', 'consumed_apis', 'jobs'], BACKENDS[stack['backend']])} · PostgreSQL",
        f"- **Module type**: {module_type}",
        f"- **Dependencies**: {len(edges)}",
        f"- **Entities**: {len(server_entities)}"
        + (f" (+ {len(client_entities)} mobile client entities)" if client_entities else ""),
        f"- **Server actions**: {len(server_actions)}",
        f"- **Service actions**: {len(service_actions)}",
        f"- **External integrations**: {len(externals)}",
        f"- **Exposed APIs**: {len(exposed)}",
        "",
    ]

    if server_entities:
        lines.append("## Data Model")
        lines.append("")
        if dotnet:
            lines.append("EF Core entities on the tables in `deliverables/db-schema/schema.sql`. Columns are "
                         "snake_case: use `UseSnakeCaseNamingConvention()` (EFCore.NamingConventions) with Npgsql. "
                         "Identifiers are `BIGINT`; delete rules and indexes are in the DDL.")
        else:
            orm = _choice(stack, "backend", "data_access") or "the ORM or query builder you pick (open decision)"
            lines.append("Row types for the tables in `deliverables/db-schema/schema.sql` (columns are snake_case; "
                         f"map them in {orm}). "
                         "Identifiers are `BIGINT`; delete rules and indexes are in the DDL.")
        lines.append("")
        for entity in server_entities:
            ename = entity.get("name", "Unknown")
            lines.append(f"### {ename}")
            lines.append("")
            if entity.get("description"):
                lines.append(entity["description"])
                lines.append("")
            if entity.get("is_static"):
                lines.append("Static entity: rows are in `db-schema/seed-data.sql`; "
                             + ("an `enum` can mirror them in code." if dotnet else "a const object can mirror them in code."))
                lines.append("")
            lines.extend(_entity_block(module_name, entity, stack))
            lines.append("")

    if client_entities:
        lines.append("## Mobile Client Entities")
        lines.append("")
        lines.append("Stored on the device (expo-sqlite), not in PostgreSQL; see `db-schema/client-entities.md`. "
                     "Plan the sync endpoints that fill them.")
        lines.append("")
        lines.append(md_table(["Entity", "Attributes"],
                              [[e.get("name", ""), str(len(e.get("attributes", [])))] for e in client_entities]))
        lines.append("")

    if server_actions:
        lines.extend(_actions_section("Server Actions", server_actions, stack, service))
    if service_actions:
        lines.extend(_actions_section(
            "Service Actions", service_actions, stack,
            f"{service} (internal module of the monolith; split out only when needed)"))

    if externals:
        lines.append("## Integration Adapters")
        lines.append("")
        for ext in externals:
            lines.append(f"### {ext.get('name', 'ExternalSystem')}")
            if ext.get("endpoint_url"):
                lines.append(f"- **Base URL**: `{ext['endpoint_url']}` (move to configuration)")
            lines.append(f"- **Operations**: {len(ext.get('operations', []))}")
            lines.append("")
            lines.extend(_integration_block(ext, stack))
            lines.append("")

    if exposed:
        lines.append("## Exposed API Surface")
        lines.append("")
        how = ("ASP.NET Core controllers or Minimal APIs with an OpenAPI document"
               if dotnet else "HTTP routes with an OpenAPI document")
        lines.append(f"Rebuild as {how}; keep the paths and payloads so callers don't change. "
                     "Stubs: `deliverables/api-catalog/`.")
        lines.append("")
        rows = [[a.get("name", ""), a.get("type", ""), str(len(a.get("input_parameters", []))),
                 str(len(a.get("output_parameters", [])))] for a in exposed]
        lines.append(md_table(["Name", "Type", "Inputs", "Outputs"], rows))
        lines.append("")

    if edges:
        lines.append("## Dependencies")
        lines.append("")
        rows = [[e.get("target", ""), ", ".join(e.get("edge_types", [])), e.get("status", "")] for e in edges]
        lines.append(md_table(["Module", "Edge Types", "Status"], rows))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Front end
# ---------------------------------------------------------------------------

_LIFECYCLE = {
    "OnInitialize": "before first render (initial state)",
    "OnReady": "useEffect(() => …, [])",
    "OnRender": "useEffect(() => …)",
    "OnParametersChanged": "useEffect(() => …, [params])",
    "OnDestroy": "useEffect cleanup",
}


def _lifecycle(item: dict[str, Any]) -> list[str]:
    used = [e for e in item.get("events", []) if e.get("ref") and e.get("event") in _LIFECYCLE]
    return [f"`{e['event']}` → {_LIFECYCLE[e['event']]}" for e in used]


def _data_hooks(item: dict[str, Any]) -> list[str]:
    return [f"`use{pascal(d.get('name', 'Data'))}`" for d in item.get("screen_data_sets", [])]


def _screen_block(scr: dict[str, Any], mobile: bool) -> list[str]:
    sname = scr.get("name", "Screen")
    inputs = scr.get("input_parameters", [])
    params_type = f"{pascal(sname)}Params"
    lines = ["```tsx"]
    if mobile:
        lines.append(f"// app/{kebab(scr.get('flow') or 'main')}/{kebab(sname)}.tsx (Expo Router)")
        if inputs:
            lines.append(f"type {params_type} = {{")
            for p in inputs:
                lines.append(f"  {camel(p.get('name', 'param'))}{'' if is_mandatory(p) else '?'}: string;"
                             f" // {ts_type(p.get('type'))}; route params arrive as strings")
            lines.append("};")
            lines.append(f"export default function {pascal(sname)}Screen() {{")
            lines.append(f"  const params = useLocalSearchParams<{params_type}>();")
        else:
            lines.append(f"export default function {pascal(sname)}Screen() {{")
    else:
        query = f"?{'&'.join(p.get('name', 'param') + '=' for p in inputs)}" if inputs else ""
        lines.append(f"// route: /{kebab(sname)}{query}  (keep the OutSystems URL shape for bookmarks)")
        if inputs:
            lines.append(f"export interface {params_type} {{")
            for p in inputs:
                lines.append(f"  {camel(p.get('name', 'param'))}{'' if is_mandatory(p) else '?'}: "
                             f"{ts_type(p.get('type'))};")
            lines.append("}")
        lines.append(f"export function {pascal(sname)}Page() {{")
    lines.append("  // …")
    lines.append("}")
    lines.append("```")
    return lines


def _block_component(blk: dict[str, Any]) -> list[str]:
    bname = pascal(blk.get("name", "Block"))
    inputs = blk.get("input_parameters", [])
    outputs = blk.get("output_parameters", [])
    lines = ["```tsx", f"export interface {bname}Props {{"]
    for p in inputs:
        lines.append(f"  {camel(p.get('name', 'param'))}{'' if is_mandatory(p) else '?'}: {ts_type(p.get('type'))};")
    for p in outputs:
        lines.append(f"  on{pascal(p.get('name', 'value'))}Changed?: (value: {ts_type(p.get('type'))}) => void;")
    lines.append("}")
    lines.append(f"export function {bname}(props: {bname}Props) {{ /* … */ }}")
    lines.append("```")
    return lines


def generate_frontend_spec(
    module_name: str,
    module_type: str,
    screens: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    actions_summary: list[dict[str, Any]],
    graph: dict[str, Any],
    stack: dict[str, Any],
    mobile: bool = False,
) -> str:
    """Generate frontend-spec.md content for a module."""
    edges = graph.get("edges", []) if isinstance(graph, dict) else []
    client_actions = [a for a in actions_summary if a.get("type") == "client"]
    if mobile:
        target = _target(stack, "mobile", ["offline", "build"], CATALOGUE["mobile"][1][0].options[stack["mobile"]])
    else:
        target = _target(stack, "web", ["ui", "server_state", "client_state", "forms", "i18n"],
                         CATALOGUE["web"][1][0].options[stack["web"]])

    lines: list[str] = [
        f"# Frontend Specification: {module_name}",
        "",
        f"- **Target**: {target}",
        f"- **App kind**: {'mobile app' if mobile else 'web app / library'}",
        f"- **Module type**: {module_type}",
        f"- **Screens**: {len(screens)}",
        f"- **Blocks**: {len(blocks)}",
        f"- **Client actions**: {len(client_actions)}",
        f"- **Dependencies**: {len(edges)}",
        "",
    ]

    if screens:
        lines.append("## Screen Inventory")
        lines.append("")
        rows = []
        screen_sizes: list[tuple[int, dict[str, Any]]] = []
        for scr in screens:
            wcount = _count_widgets(scr.get("widgets", []))
            pcount = len(scr.get("input_parameters", [])) + len(scr.get("output_parameters", []))
            rows.append([scr.get("name", ""), str(wcount), str(pcount),
                         str(len(scr.get("screen_data_sets", []))), scr.get("flow", "")])
            screen_sizes.append((wcount, scr))
        lines.append(md_table(["Screen", "Widgets", "Parameters", "Data sources", "Flow"], rows))
        lines.append("")

        screen_sizes.sort(key=lambda x: x[0], reverse=True)
        lines.append("## Component Specifications")
        lines.append("")
        lines.append("Largest screens first (top 10). "
                     + ("Expo Router screens; " if mobile else "Pages under React Router; ")
                     + "data sources become TanStack Query hooks over the back-end API.")
        lines.append("")
        for _wcount, scr in screen_sizes[:10]:
            widgets = scr.get("widgets", [])
            lines.append(f"### {scr.get('name', 'Screen')}")
            lines.append("")
            if scr.get("description"):
                lines.append(scr["description"])
                lines.append("")
            lines.extend(_screen_block(scr, mobile))
            lines.append("")
            hooks = _data_hooks(scr)
            if hooks:
                lines.append(f"**Data (TanStack Query)**: {', '.join(hooks)}")
                lines.append("")
            widget_types: set[str] = set()
            _collect_widget_types(widgets, widget_types)
            categorized = _categorize_widgets(widget_types, mobile)
            if categorized:
                lines.append(f"**Key components**: {', '.join(sorted(categorized))}")
                lines.append("")
            nav: set[str] = set()
            _collect_nav_destinations(widgets, nav)
            if nav:
                lines.append(f"**Navigates to**: {', '.join(sorted(nav))}")
                lines.append("")
            life = _lifecycle(scr)
            if life:
                lines.append(f"**Lifecycle**: {'; '.join(life)}")
                lines.append("")
            handlers: list[dict[str, str]] = []
            _collect_event_handlers(widgets, handlers)
            if handlers:
                lines.append("**Event handlers**:")
                lines.append("")
                lines.extend(f"- `{h['widget']}` → {h['event']} → `{h['target']}`" for h in handlers[:12])
                lines.append("")
            conditions: list[str] = []
            _collect_visibility_conditions(widgets, conditions)
            if conditions:
                lines.append("**Conditional rendering**:")
                lines.append("")
                lines.extend(f"- `{c}`" for c in conditions)
                lines.append("")
            screen_actions = scr.get("client_actions", [])
            if screen_actions:
                lines.append("**Client action logic**:")
                lines.append("")
                for ca in screen_actions[:6]:
                    steps = _describe_flow_steps(ca.get("flow", {}))
                    if ca.get("name") and steps:
                        lines.append(f"*{ca['name']}*:")
                        lines.extend(f"  - {s}" for s in steps[:6])
                        lines.append("")

    if blocks:
        lines.append("## Block Inventory")
        lines.append("")
        rows = [[b.get("name", ""), str(_count_widgets(b.get("widgets", []))),
                 str(len(b.get("input_parameters", []))), str(len(b.get("output_parameters", [])))]
                for b in blocks]
        lines.append(md_table(["Block", "Widgets", "Inputs", "Outputs"], rows))
        lines.append("")
        lines.append("### Block Components")
        lines.append("")
        for blk in blocks:
            lines.append(f"**{blk.get('name', 'Block')}**")
            lines.append("")
            lines.extend(_block_component(blk))
            lines.append("")
            hooks = _data_hooks(blk)
            if hooks:
                lines.append(f"Data: {', '.join(hooks)}")
                lines.append("")

    lines.append("## State Management")
    lines.append("")
    server_count = sum(1 for a in actions_summary if a.get("type") == "server")
    lines.append(f"- **Server data**: TanStack Query over the back-end API ({server_count} server actions in this module)")
    lines.append(f"- **Client logic**: {len(client_actions)} client actions → TypeScript functions in shared packages")
    lines.append("- **Client variables**: Zustand store" + (" (persisted with expo-sqlite / SecureStore)" if mobile
                                                            else " (localStorage when persisted)"))
    lines.append("- **Forms**: react-hook-form + zod (OutSystems built-in validation messages → zod schemas)")
    lines.append("")
    if client_actions:
        rows = [[ca.get("name", ""), str(ca.get("node_count", 0)), str(ca.get("input_count", 0)),
                 str(ca.get("output_count", 0))] for ca in client_actions]
        lines.append(md_table(["Action", "Nodes", "Inputs", "Outputs"], rows))
        lines.append("")

    if screens:
        all_destinations: dict[str, set[str]] = {}
        for scr in screens:
            dests: set[str] = set()
            _collect_nav_destinations(scr.get("widgets", []), dests)
            if dests:
                all_destinations[scr.get("name", "")] = dests
        if all_destinations:
            lines.append("## Navigation Map")
            lines.append("")
            rows = [[src, ", ".join(sorted(d))] for src, d in sorted(all_destinations.items())]
            lines.append(md_table(["Screen", "Destinations"], rows))
            lines.append("")

    return "\n".join(lines)


_WEB_COMPONENTS = {
    "Container": "layout <div>",
    "List": "list (array.map)",
    "Table": "DataTable (TanStack Table)",
    "TextArea": "Textarea",
    "Input": "Input",
    "Button": "Button",
    "Link": "Link (React Router)",
    "Image": "<img>",
    "Icon": "Icon",
    "Checkbox": "Checkbox",
    "RadioButton": "RadioGroup",
    "Dropdown": "Select",
    "If": "conditional render",
    "Expression": "text",
    "Label": "Label",
    "WebBlockInstance": "child component",
    "Placeholder": "children / slot",
}

_MOBILE_COMPONENTS = {
    "Container": "View",
    "List": "FlatList",
    "Table": "FlatList",
    "TextArea": "TextInput (multiline)",
    "Input": "TextInput",
    "Button": "Pressable",
    "Link": "Link (Expo Router)",
    "Image": "Image",
    "Icon": "Icon (@expo/vector-icons)",
    "Checkbox": "Checkbox (expo-checkbox)",
    "RadioButton": "radio group",
    "Dropdown": "Picker",
    "If": "conditional render",
    "Expression": "Text",
    "Label": "Text",
    "WebBlockInstance": "child component",
    "Placeholder": "children / slot",
}


def _categorize_widgets(widget_types: set[str], mobile: bool = False) -> set[str]:
    """Map OutSystems widget types to the target's components."""
    mapping = _MOBILE_COMPONENTS if mobile else _WEB_COMPONENTS
    categories: set[str] = set()
    for wt in widget_types:
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

def generate_index(module_rows: list[dict[str, Any]], stack: dict[str, Any]) -> str:
    """Generate index.md with the target stack and module summary."""
    total_entities = sum(r["entities"] for r in module_rows)
    total_actions = sum(r["actions"] for r in module_rows)
    total_screens = sum(r["screens"] for r in module_rows)

    lines: list[str] = ["# Engineering Specifications Index", ""]
    lines.append("## Target stack")
    lines.append("")
    lines.append(md_table(["Layer", "Target"], [list(r) for r in describe(stack)]))
    lines.append("")
    lines.append(f"Source: {stack.get('source', 'defaults')}. Full stack and concept mapping: `TECH_STACK.md`.")
    if stack.get("open"):
        lines.append("")
        lines.append(f"**Open decisions** ({len(stack['open'])}): "
                     + ", ".join(f"`{k}`" for k in stack["open"])
                     + ". Record them with `scripts/project_stack.py set`.")
    lines.append("")
    lines.append("## Modules")
    lines.append("")
    lines.append(f"- **Modules**: {len(module_rows)}")
    lines.append(f"- **Total entities**: {total_entities}")
    lines.append(f"- **Total actions**: {total_actions}")
    lines.append(f"- **Total screens**: {total_screens}")
    lines.append("")
    rows = [[f"[{r['name']}]({r['name']}/)", r["type"], r["front_end"], str(r["entities"]),
             str(r["actions"]), str(r["screens"]), str(r["deps"]), r["readiness"]]
            for r in sorted(module_rows, key=lambda x: x["name"])]
    lines.append(md_table(
        ["Module", "Type", "Front end", "Entities", "Actions", "Screens", "Deps", "Readiness"], rows))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_SPEC_FILES = {"backend-spec.md", "frontend-spec.md", "state-flows.md"}


def remove_stale_specs(out_dir: Path, current: set[str]) -> list[str]:
    """Delete spec folders of modules this run didn't write (left from earlier runs).

    Only folders that hold nothing but generated spec files are removed.
    """
    removed = []
    for folder in sorted(p for p in out_dir.iterdir() if p.is_dir() and p.name not in current):
        files = list(folder.iterdir())
        if files and all(f.is_file() and f.name in _SPEC_FILES for f in files):
            for f in files:
                f.unlink()
            folder.rmdir()
            removed.append(folder.name)
    return removed


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def main() -> None:
    parser = make_arg_parser("Generate back-end and front-end specifications for the target stack")
    parser.add_argument("--backend", choices=sorted(BACKENDS),
                        help="Back end to write the specs for; overrides stack.backend in project.json")
    parser.add_argument("--project-config", type=Path,
                        help="project.json to read the stack from (default: next to the pass directory)")
    args = parser.parse_args()
    pass_dir: Path = args.pass_dir

    try:
        stack = load_stack(pass_dir, args.backend, args.project_config)
    except StackError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    packages = load_pass_packages(pass_dir)
    if not packages:
        print("ERROR: No packages found.", file=sys.stderr)
        sys.exit(1)

    out_dir = deliverables_dir(pass_dir, "specs")
    total_pkgs = len(packages)
    print(f"Generating specs for {total_pkgs} modules → {out_dir}")
    if stack.get("open"):
        print(f"  open stack decisions: {', '.join(stack['open'])}")
    print(f"  stack: back end {stack['backend']}, web {stack['web']}, mobile {stack['mobile']}, "
          f"database {stack['database']} ({stack['source']})")

    module_rows: list[dict[str, Any]] = []
    files_written = 0
    width = len(str(total_pkgs))

    for idx, pkg in enumerate(packages, 1):
        module_name: str = pkg.get("module_name", "unknown")
        module_type = classify_module(module_name)
        print(f"  [{idx:>{width}}/{total_pkgs}] Generating {module_name}...")

        entities = _as_list(load_domain_file(pass_dir, module_name, "data", "entities.json"))
        server_actions = _as_list(load_domain_file(pass_dir, module_name, "logic", "server-actions.json"))
        service_actions = _as_list(load_domain_file(pass_dir, module_name, "logic", "service-actions.json"))
        actions_summary = _as_list(load_domain_file(pass_dir, module_name, "logic", "actions.json"))
        screens = _as_list(load_domain_file(pass_dir, module_name, "interfaces", "screens.json"))
        blocks = _as_list(load_domain_file(pass_dir, module_name, "interfaces", "blocks.json"))
        externals = _as_list(load_domain_file(pass_dir, module_name, "integrations", "external-systems.json"))
        exposed = _as_list(load_domain_file(pass_dir, module_name, "integrations", "exposed-apis.json"))
        graph = load_domain_file(pass_dir, module_name, "dependencies", "graph.json")
        edges = graph.get("edges", []) if isinstance(graph, dict) else []

        mobile = module_is_mobile(pass_dir, module_name, stack)
        has_ui = bool(screens or blocks)
        module_rows.append({
            "name": module_name,
            "type": module_type,
            "front_end": ("mobile" if mobile else "web") if has_ui else "-",
            "entities": len(entities),
            "actions": len(actions_summary),
            "screens": len(screens),
            "deps": len(edges),
            "readiness": pkg.get("implementation_readiness", {}).get("status", "unknown"),
        })

        module_dir = out_dir / module_name
        write_text(module_dir / "backend-spec.md", generate_backend_spec(
            module_name, module_type, entities, server_actions, actions_summary,
            externals, exposed, graph, stack, service_actions,
        ))
        write_text(module_dir / "frontend-spec.md", generate_frontend_spec(
            module_name, module_type, screens, blocks, actions_summary, graph, stack, mobile,
        ))
        files_written += 2

        flows_md = generate_state_flows(module_name, server_actions)
        if flows_md:
            write_text(module_dir / "state-flows.md", flows_md)
            files_written += 1

    write_text(out_dir / "index.md", generate_index(module_rows, stack))
    files_written += 1
    removed = remove_stale_specs(out_dir, {r["name"] for r in module_rows})
    if removed:
        print(f"  removed specs of modules not in this run: {', '.join(removed)}")

    print(f"\n{'=' * 60}")
    print(f"  Back end: {stack['backend']}")
    print(f"  Modules:  {len(module_rows)}")
    print(f"  Entities: {sum(r['entities'] for r in module_rows)}")
    print(f"  Actions:  {sum(r['actions'] for r in module_rows)}")
    print(f"  Screens:  {sum(r['screens'] for r in module_rows)}")
    print(f"  Files:    {files_written}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
