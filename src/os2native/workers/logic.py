"""Logic domain worker — extracts server actions, client actions, and contracts."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.shared.expression_resolver import resolve_expression
from os2native.shared.folders import annotate_folders, element_folders
from os2native.shared.key_index import KeyIndex, build_key_index
from os2native.shared.type_resolver import SYSTEM_EXCEPTION_NAMES, TypeResolver, expression_value
from os2native.xml_loader import OmlDocument


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(doc: OmlDocument, pkg_root: Path) -> None:
    """Extract the logic domain from *doc* and write to *pkg_root*/logic/."""
    ki = build_key_index(doc)
    tr = TypeResolver.from_doc(doc)
    cov = CoverageTracker(domain="logic")

    out = pkg_root / "logic"

    server_actions = _extract_server_actions(doc, ki, tr, cov)
    service_actions = _extract_service_actions(doc, ki, tr, cov)
    client_actions = _extract_client_actions(doc, ki, tr, cov)
    contracts = _extract_contracts(doc, ki, tr, cov)

    folders = element_folders(doc)
    for actions in (server_actions, service_actions, client_actions):
        annotate_folders(actions, folders)

    actions_inventory = _build_actions_inventory(server_actions, client_actions, contracts)
    inventory = _build_inventory(server_actions, client_actions, contracts, cov)
    partitions = _build_partitions(server_actions, client_actions)
    advanced_sql = _build_advanced_sql(server_actions, client_actions)

    write_json(out / "server-actions.json", server_actions)
    write_json(out / "service-actions.json", service_actions)
    write_json(out / "client-actions.json", client_actions)
    write_json(out / "contracts.json", contracts)
    write_json(out / "actions.json", actions_inventory)
    write_json(out / "inventory.json", inventory)
    write_json(out / "partitions.json", partitions)
    write_json(out / "advanced-sql.json", advanced_sql)
    write_json(out / "traversal-log.json", cov.to_traversal_dict())
    write_json(out / "coverage.json", cov.to_coverage_dict())


# ---------------------------------------------------------------------------
# Server actions  (Flows.UserAction in ESpace)
# ---------------------------------------------------------------------------

def _extract_server_actions(
    doc: OmlDocument,
    ki: KeyIndex,
    tr: TypeResolver,
    cov: CoverageTracker,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    # Server actions live in UserActions and UserActions#<key> fragments,
    # NOT inside the ESpace element directly (HasChildren="Yes" but 0 inline children).
    # An action placed in a Service Studio folder appears twice: the full
    # Flows.UserAction in "UserActions", plus a Flows.UserActionInsideFolder
    # stub (same Key, partial parameters) in "UserActions#<folder key>".
    # Keep one element per Key, preferring the full definition.
    by_key: dict[str, ET.Element] = {}
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "UserActions" or frag_name.startswith("UserActions#"):
            for child in frag_elem:
                if "UserAction" not in child.tag:
                    continue
                key = child.attrib.get("Key", "") or f"#{id(child)}"
                current = by_key.get(key)
                if current is None or (
                    current.tag.endswith("InsideFolder") and not child.tag.endswith("InsideFolder")
                ):
                    by_key[key] = child
    ua_elements = list(by_key.values())

    for ua in ua_elements:
        key = ua.attrib.get("Key", "")
        name = ua.attrib.get("Name", "")
        cov.discover(name, path=f"UserActions//[@Key='{key}']")

        params = _extract_parameters(ua, tr)
        local_vars = _extract_local_variables(ua, tr)
        flow = _extract_flow_graph(doc, key, ki, tr)

        actions.append({
            "key": key,
            "name": name,
            "description": ua.attrib.get("Description", ""),
            "is_public": ua.attrib.get("Public", "No") == "Yes",
            "is_function": ua.attrib.get("IsFunction", "No") == "Yes",
            "cache_in_minutes": int(ua.attrib.get("CacheInMinutes", "0")),
            "input_parameters": params["inputs"],
            "output_parameters": params["outputs"],
            "local_variables": local_vars,
            "flow": flow,
        })
        cov.capture(name, result="ok")

    return actions


# ---------------------------------------------------------------------------
# Service actions  (Flows.ServiceAPIMethod in ServiceAPIMethods fragments)
# ---------------------------------------------------------------------------

def _extract_service_actions(
    doc: OmlDocument,
    ki: KeyIndex,
    tr: TypeResolver,
    cov: CoverageTracker,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name != "ServiceAPIMethods" and not frag_name.startswith("ServiceAPIMethods#"):
            continue
        for sa in frag_elem:
            if not sa.tag.endswith("ServiceAPIMethod"):
                continue
            key = sa.attrib.get("Key", "")
            if key and key in seen_keys:
                continue
            seen_keys.add(key)
            name = sa.attrib.get("Name", "")
            cov.discover(name, path=f"ServiceAPIMethods//[@Key='{key}']")
            params = _extract_parameters(sa, tr)
            actions.append({
                "key": key,
                "name": name,
                "description": sa.attrib.get("Description", ""),
                "input_parameters": params["inputs"],
                "output_parameters": params["outputs"],
                "local_variables": _extract_local_variables(sa, tr),
                "flow": _extract_flow_graph(doc, key, ki, tr),
            })
            cov.capture(name, result="ok")
    return actions


# ---------------------------------------------------------------------------
# Client actions  (NRFlows.ClientScreenActionFlow / DataScreenActionFlow)
# ---------------------------------------------------------------------------

def _extract_client_actions(
    doc: OmlDocument,
    ki: KeyIndex,
    tr: TypeResolver,
    cov: CoverageTracker,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    # Collect client action elements from multiple possible locations.
    client_tags = (
        "NRFlows.ClientScreenActionFlow",
        "NRFlows.DataScreenActionFlow",
        "NRFlows.ClientActionFlowInsideFolder",
        "Flows.WebScreenAction",
    )

    seen_keys: set[str] = set()

    # 1) Look inside NRWebFlows / WebFlows fragment — WebFlow children may contain them.
    for frag_name in ("NRWebFlows", "WebFlows"):
        frag = doc.fragment(frag_name)
        if frag is not None:
            for tag in client_tags:
                for elem in frag.iter(tag):
                    key = elem.attrib.get("Key", "")
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        actions.append(
                            _parse_client_action(elem, tag, doc, ki, tr, cov)
                        )

    # 2) Look in ClientActionFlows#<parent_key> fragments.
    for frag_name, frag_elem in doc.fragments.items():
        if not frag_name.startswith("ClientActionFlows#"):
            continue
        for tag in client_tags:
            for elem in frag_elem.iter(tag):
                key = elem.attrib.get("Key", "")
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    actions.append(
                        _parse_client_action(elem, tag, doc, ki, tr, cov)
                    )

    # 3) Promote screen/block-embedded client actions to the logic inventory.
    #    These live in ClientActions/DataActions/ScreenActions containers on
    #    NRNodes.WebBlock / NRNodes.WebScreen / Nodes.WebScreen elements inside
    #    NodesShownInESpaceTree fragments — unreachable from paths 1 & 2.
    _screen_block_tags = ("WebBlock", "WebScreen", "EmailScreen")
    _action_container_tags = ("ClientActions", "DataActions", "ScreenActions")

    for frag_name, frag_elem in doc.fragments.items():
        if not (frag_name == "NodesShownInESpaceTree"
                or frag_name.startswith("NodesShownInESpaceTree#")):
            continue
        for node in frag_elem:
            # Match NRNodes.WebBlock, NRNodes.WebScreen, Nodes.WebScreen, etc.
            node_tag = node.tag
            if not any(node_tag.endswith(suffix) for suffix in _screen_block_tags):
                continue

            node_name = node.attrib.get("Name", "")
            node_key = node.attrib.get("Key", "")
            # Determine source kind from tag
            if "WebScreen" in node_tag or "EmailScreen" in node_tag:
                source_kind = "screen"
            else:
                source_kind = "block"

            for container_tag in _action_container_tags:
                container = node.find(container_tag)
                if container is None:
                    continue
                for elem in container:
                    if elem.tag not in client_tags:
                        continue
                    key = elem.attrib.get("Key", "")
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        source = {
                            "source": source_kind,
                            f"{source_kind}_name": node_name,
                            f"{source_kind}_key": node_key,
                        }
                        actions.append(
                            _parse_client_action(
                                elem, elem.tag, doc, ki, tr, cov, source=source,
                            )
                        )
            preparation = node.find("Preparation")
            if preparation is not None:
                for elem in preparation:
                    if elem.tag != "Flows.WebPreparation":
                        continue
                    key = elem.attrib.get("Key", "") or f"{node_key}.#Preparation"
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        source = {
                            "source": source_kind,
                            f"{source_kind}_name": node_name,
                            f"{source_kind}_key": node_key,
                        }
                        actions.append(
                            _parse_client_action(
                                elem,
                                elem.tag,
                                doc,
                                ki,
                                tr,
                                cov,
                                source=source,
                                key_override=key,
                                name_override="Preparation",
                                action_type_override="preparation",
                            )
                        )

    return actions


def _parse_client_action(
    elem: ET.Element,
    tag: str,
    doc: OmlDocument,
    ki: KeyIndex,
    tr: TypeResolver,
    cov: CoverageTracker,
    source: dict[str, str] | None = None,
    key_override: str | None = None,
    name_override: str | None = None,
    action_type_override: str | None = None,
) -> dict[str, Any]:
    key = key_override if key_override is not None else elem.attrib.get("Key", "")
    name = name_override if name_override is not None else elem.attrib.get("Name", "")
    action_type = (
        action_type_override
        if action_type_override is not None
        else "client" if "Client" in tag else "data_fetch"
    )
    cov.discover(name, path=f"{tag}[@Key='{key}']")

    params = _extract_parameters(elem, tr)
    local_vars = _extract_local_variables(elem, tr)
    flow = _extract_flow_graph(doc, key, ki, tr)

    cov.capture(name, result="ok")
    result = {
        "key": key,
        "name": name,
        "action_type": action_type,
        "description": elem.attrib.get("Description", ""),
        "input_parameters": params["inputs"],
        "output_parameters": params["outputs"],
        "local_variables": local_vars,
        "flow": flow,
    }
    if source is not None:
        result["source"] = source
    return result


# ---------------------------------------------------------------------------
# Reference action contracts
# ---------------------------------------------------------------------------

def _extract_contracts(
    doc: OmlDocument,
    ki: KeyIndex,
    tr: TypeResolver,
    cov: CoverageTracker,
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []

    refs_frag = doc.fragment("References")
    if refs_frag is None:
        return contracts

    for ref in refs_frag.findall("Reference"):
        ref_key = ref.attrib.get("Key", "")
        ref_name = ref.attrib.get("Name", "")

        for ra in ref.findall(".//ReferenceAction"):
            ra_key = ra.attrib.get("Key", "")
            ra_name = ra.attrib.get("Name", "")
            item_id = f"{ref_name}.{ra_name}"
            cov.discover(item_id, path=f"References/Reference[@Key='{ref_key}']/ReferenceAction[@Key='{ra_key}']")

            params = _extract_parameters(ra, tr)

            contracts.append({
                "key": ra_key,
                "name": ra_name,
                "source_module": ref_name,
                "source_module_key": ref_key,
                "description": ra.attrib.get("Description", ""),
                "input_parameters": params["inputs"],
                "output_parameters": params["outputs"],
            })
            cov.capture(item_id, result="ok")

    return contracts


# ---------------------------------------------------------------------------
# Parameter & variable extraction helpers
# ---------------------------------------------------------------------------

def _extract_parameters(
    parent: ET.Element, tr: TypeResolver
) -> dict[str, list[dict[str, Any]]]:
    """Extract input and output parameters from a parent element."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    # Input parameters come in several tag variations.
    input_tags = (
        "Variables.GenericInputParameter",
        "Variables.SerializableInputParameter",
        "Variables.InputParameter",
    )
    for container_tag in ("InputParameters", "Parameters"):
        container = parent.find(container_tag)
        if container is None:
            continue
        for child in container:
            tag = child.tag
            if any(tag == it or tag.endswith(f".{it.split('.')[-1]}") for it in input_tags):
                inputs.append(_param_dict(child, "input", tr))
            elif "Output" in tag:
                outputs.append(_param_dict(child, "output", tr))
            else:
                # Fallback — classify by tag content
                inputs.append(_param_dict(child, "input", tr))

    # Explicit OutputParameters container
    out_container = parent.find("OutputParameters")
    if out_container is not None:
        for child in out_container:
            outputs.append(_param_dict(child, "output", tr))

    return {"inputs": inputs, "outputs": outputs}


def _param_dict(
    elem: ET.Element, direction: str, tr: TypeResolver
) -> dict[str, Any]:
    return {
        "key": elem.attrib.get("Key", ""),
        "name": elem.attrib.get("Name", ""),
        "direction": direction,
        "type": tr.resolve(elem.attrib.get("Type")),
        "is_mandatory": elem.attrib.get("IsMandatory", "No") == "Yes",
        "default": expression_value(elem.find("DefaultValue")),
        "description": elem.attrib.get("Description", ""),
    }


def _extract_local_variables(
    parent: ET.Element, tr: TypeResolver
) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    container = parent.find("LocalVariables")
    if container is None:
        return variables
    for lv in container:
        variables.append({
            "key": lv.attrib.get("Key", ""),
            "name": lv.attrib.get("Name", ""),
            "type": tr.resolve(lv.attrib.get("Type")),
            "default": expression_value(lv.find("DefaultValue")),
        })
    return variables


# ---------------------------------------------------------------------------
# Flow graph extraction
# ---------------------------------------------------------------------------

_KNOWN_NODE_PREFIXES = (
    "Nodes.If",
    "Nodes.ExecuteAction",
    "Nodes.Assign",
    "Nodes.Start",
    "Nodes.End",
    "Nodes.Switch",
    "Nodes.RaiseError",
    "Nodes.ErrorHandler",
    "Nodes.Comment",
    "Nodes.JSONSerialize",
    "Nodes.RefreshQuery",
    "Nodes.WebDestination",
    "Nodes.ForEach",
    "Nodes.DataSet",
    "Nodes.AdvancedQuery",
    "Nodes.Download",
    "Nodes.ExcelToRecordList",
    "Nodes.RecordListToExcel",
    "Nodes.RunClientAction",
    "Nodes.RunServerAction",
    "Nodes.JavaScript",
    "Nodes.AggregateStart",
    "NRNodes.Destination",
    "Nodes.",  # catch-all: any remaining Nodes.* tag
)

_KNOWN_LINK_TAGS = ("Links.Sequence", "Links.True", "Links.False")


def _extract_flow_graph(
    doc: OmlDocument, action_key: str, ki: KeyIndex, tr: TypeResolver | None = None
) -> dict[str, Any]:
    """Build a flow graph (nodes + edges) for the given action key.

    In OML XML, links are **nested inside** each node as ``<Links>`` child
    elements with a ``@TargetNode`` attribute, rather than being top-level
    siblings of the node elements.
    """
    frag_name = f"NodesNotShownInESpaceTree#{action_key}"
    frag = doc.fragment(frag_name)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    if frag is None:
        return {"nodes": nodes, "edges": edges}

    for child in frag:
        tag = child.tag
        if tag.startswith("Nodes.") or tag.startswith("NRNodes."):
            node_dict = _parse_flow_node(child, ki, tr)
            nodes.append(node_dict)
            # Extract outgoing edges from the <Links> child element
            links_elem = child.find("Links")
            if links_elem is not None:
                from_key = child.attrib.get("Key", "")
                for link in links_elem:
                    edges.append(_parse_link(link, from_key))

    return {"nodes": nodes, "edges": edges}


def _parse_flow_node(
    elem: ET.Element, ki: KeyIndex, tr: TypeResolver | None = None
) -> dict[str, Any]:
    """Extract a single flow node with type-specific data."""
    tag = elem.tag
    key = elem.attrib.get("Key", "")
    name = elem.attrib.get("Name", "")

    node: dict[str, Any] = {
        "key": key,
        "type": tag,
        "name": name,
        "position": {
            "x": _int_attr(elem, "X"),
            "y": _int_attr(elem, "Y"),
        },
    }

    # --- Type-specific enrichment ---

    if tag == "Nodes.If":
        condition_elem = elem.find("Condition/ParsedExpression")
        node["condition"] = (
            resolve_expression(condition_elem, ki)
            if condition_elem is not None
            else ""
        )

    elif tag == "Nodes.ExecuteAction":
        action_ref = elem.attrib.get("Action", "")
        node["action_ref"] = ki.resolve_ref(action_ref) if action_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag in ("Nodes.RunClientAction", "Nodes.RunServerAction"):
        action_ref = elem.attrib.get("Action", "")
        node["action_ref"] = ki.resolve_ref(action_ref) if action_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "Nodes.Assign":
        node["assignments"] = _extract_assignments(elem, ki)

    elif tag == "Nodes.Switch":
        node["condition"] = _extract_condition(elem, ki)
        node["switch_conditions"] = _extract_switch_conditions(elem, ki)

    elif tag == "Nodes.RaiseError":
        message_elem = elem.find("Message/ParsedExpression")
        node["message"] = (
            resolve_expression(message_elem, ki)
            if message_elem is not None
            else ""
        )
        # Capture the raised exception type — previously dropped, leaving error
        # flows without which exception is thrown. Resolve like ErrorHandler.
        exception_key = (
            elem.attrib.get("Exception", "")
            or elem.attrib.get("UserException", "")
            or elem.attrib.get("ExceptionType", "")
        )
        if exception_key:
            resolved = ki.resolve_ref(exception_key)
            if resolved == exception_key:
                resolved = SYSTEM_EXCEPTION_NAMES.get(exception_key, resolved)
            node["exception"] = resolved
        else:
            node["exception"] = ""

    elif tag == "Nodes.ErrorHandler":
        exception_key = elem.attrib.get("Exception", "")
        if exception_key:
            resolved = ki.resolve_ref(exception_key)
            # Fall back to system exception names for platform-level keys
            if resolved == exception_key:
                resolved = SYSTEM_EXCEPTION_NAMES.get(exception_key, resolved)
            node["exception"] = resolved
        else:
            node["exception"] = ""
        node["abort_transaction"] = elem.attrib.get("AbortTransaction", "")
        node["log_error"] = elem.attrib.get("LogError", "")

    elif tag == "Nodes.Comment":
        node["text"] = elem.attrib.get("Text", "")

    elif tag == "Nodes.JSONSerialize":
        data_ref = elem.attrib.get("Data", "")
        node["data_ref"] = ki.resolve_ref(data_ref) if data_ref else ""

    elif tag == "Nodes.RefreshQuery":
        target_ref = elem.attrib.get("Target", "")
        node["query_ref"] = ki.resolve_ref(target_ref) if target_ref else ""

    elif tag == "Nodes.WebDestination":
        # Navigation destination — resolve target screen reference.
        # OML stores the target in a Destination attribute or child element.
        dest_ref = (
            elem.attrib.get("Destination", "")
            or elem.attrib.get("Screen", "")
            or elem.attrib.get("Target", "")
        )
        node["target_ref"] = ki.resolve_ref(dest_ref) if dest_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "Nodes.ForEach":
        record_list_elem = elem.find("RecordList/ParsedExpression")
        node["record_list"] = (
            resolve_expression(record_list_elem, ki)
            if record_list_elem is not None
            else ""
        )

    elif tag == "Nodes.DataSet":
        node["dataset_name"] = elem.attrib.get("Name", "")
        type_ref = elem.attrib.get("Type", "")
        node["output_type"] = ki.resolve_ref(type_ref) if type_ref else ""
        max_rec_expr = elem.find("MaxRecords/ParsedExpression")
        node["max_records"] = (
            resolve_expression(max_rec_expr, ki)
            if max_rec_expr is not None
            else ""
        )
        node["query"] = _extract_dataset_query(elem, ki)

    elif tag == "Nodes.AdvancedQuery":
        node.update(_extract_advanced_query(elem, ki, tr))

    # --- NRNodes enrichment ---

    elif tag == "NRNodes.JavascriptNode":
        node["javascript_code"] = _reconstruct_javascript(
            elem.find("JavascriptSource/JavascriptExpression"), ki,
        )
        node["js_input_parameters"] = _extract_js_parameters(elem, "JSInputParameters")
        node["js_output_parameters"] = _extract_js_parameters(elem, "JSOutputParameters")
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "NRNodes.ExecuteClientAction":
        action_ref = elem.attrib.get("Action", "")
        node["action_ref"] = ki.resolve_ref(action_ref) if action_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "NRNodes.TriggerEvent":
        event_ref = elem.attrib.get("Event", "")
        node["event_ref"] = ki.resolve_ref(event_ref) if event_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "NRNodes.FeedbackMessage":
        node["message_type"] = elem.attrib.get("Type", "")
        message_expr = elem.find("Message/ParsedExpression")
        node["message"] = (
            resolve_expression(message_expr, ki)
            if message_expr is not None
            else ""
        )

    elif tag == "NRNodes.Download":
        content_expr = elem.find("Content/ParsedExpression")
        node["content"] = (
            resolve_expression(content_expr, ki)
            if content_expr is not None
            else ""
        )
        filename_expr = elem.find("Filename/ParsedExpression")
        node["filename"] = (
            resolve_expression(filename_expr, ki)
            if filename_expr is not None
            else ""
        )

    elif tag == "NRNodes.SendEmail":
        email_screen_ref = elem.attrib.get("EmailScreen", "")
        node["email_screen"] = ki.resolve_ref(email_screen_ref) if email_screen_ref else ""
        for field in ("From", "To", "CC", "BCC"):
            expr = elem.find(f"{field}/ParsedExpression")
            node[f"{field.lower()}_expr"] = (
                resolve_expression(expr, ki)
                if expr is not None
                else ""
            )
        node["arguments"] = _extract_arguments(elem, ki)

    elif tag == "NRNodes.Destination":
        # Reactive Web navigation destination — resolve target screen reference.
        dest_ref = (
            elem.attrib.get("Target", "")
            or elem.attrib.get("Destination", "")
            or elem.attrib.get("Screen", "")
        )
        node["target_ref"] = ki.resolve_ref(dest_ref) if dest_ref else ""
        node["arguments"] = _extract_arguments(elem, ki)

    if (
        tag not in ("Nodes.WebDestination", "NRNodes.Destination", "Nodes.RefreshQuery")
        and ("Refresh" in tag or "UpdateScreen" in tag)
    ):
        target_ref = (
            elem.attrib.get("Target", "")
            or elem.attrib.get("Widget", "")
            or elem.attrib.get("Destination", "")
            or elem.attrib.get("Screen", "")
        )
        if target_ref:
            node["target_ref"] = ki.resolve_ref(target_ref)

    return node


def _extract_condition(elem: ET.Element, ki: KeyIndex) -> str:
    """Extract a condition expression from a node."""
    cond = elem.find("Condition/ParsedExpression")
    if cond is not None:
        return resolve_expression(cond, ki)
    return ""


def _extract_arguments(elem: ET.Element, ki: KeyIndex) -> list[dict[str, str]]:
    """Extract argument bindings from an ExecuteAction or similar node."""
    args: list[dict[str, str]] = []
    container = elem.find("Arguments")
    if container is None:
        return args
    for arg in container:
        param_name = arg.attrib.get("Name", "")
        param_key = arg.attrib.get("Key", "")
        # The value is in a child Value/ParsedExpression
        value_expr = arg.find("Value/ParsedExpression")
        value = (
            resolve_expression(value_expr, ki)
            if value_expr is not None
            else ""
        )
        args.append({
            "key": param_key,
            "name": param_name,
            "value": value,
        })
    return args


def _extract_assignments(
    elem: ET.Element, ki: KeyIndex
) -> list[dict[str, str]]:
    """Extract assignment pairs from a Nodes.Assign element."""
    assignments: list[dict[str, str]] = []
    container = elem.find("Assignments")
    if container is None:
        return assignments
    for assign in container:
        var_expr = assign.find("Variable/ParsedExpression")
        val_expr = assign.find("Value/ParsedExpression")
        variable = (
            resolve_expression(var_expr, ki)
            if var_expr is not None
            else ""
        )
        value = (
            resolve_expression(val_expr, ki)
            if val_expr is not None
            else ""
        )
        assignments.append({
            "variable": variable,
            "value": value,
        })
    return assignments


def _reconstruct_javascript(
    expr_elem: ET.Element | None, ki: KeyIndex
) -> str:
    """Reconstruct JavaScript source from a ``JavascriptExpression`` element.

    The expression contains interleaved ``<Text JSText="..."/>`` literals and
    ``<Reference Ref="..."/>`` elements.  We concatenate them, resolving refs
    to human-readable names.
    """
    if expr_elem is None:
        return ""
    parts: list[str] = []
    for child in expr_elem:
        if child.tag == "Text":
            parts.append(child.attrib.get("JSText", ""))
        elif child.tag == "Reference":
            ref = child.attrib.get("Ref", "")
            resolved = ki.resolve_ref(ref) if ref else "?"
            # Use last segment for readability
            segments = resolved.split(".")
            readable = segments[-1] if segments else resolved
            parts.append(f"${{{readable}}}")
        # Skip unknown children
    return "".join(parts)


def _extract_js_parameters(
    elem: ET.Element, container_tag: str
) -> list[dict[str, str]]:
    """Extract JS input or output parameter definitions from a JavascriptNode."""
    params: list[dict[str, str]] = []
    container = elem.find(container_tag)
    if container is None:
        return params
    for child in container:
        params.append({
            "key": child.attrib.get("Key", ""),
            "name": child.attrib.get("Name", ""),
            "is_mandatory": child.attrib.get("IsMandatory", "No") == "Yes",
        })
    return params


def _extract_switch_conditions(
    elem: ET.Element, ki: KeyIndex
) -> list[dict[str, Any]]:
    """Extract per-branch conditions from a Nodes.Switch element.

    Each ``SwitchCondition`` has a ``Value/ParsedExpression`` and a ``Link``
    attribute pointing to the corresponding ``Links.Condition`` (which carries
    a human-readable ``Label``).  We also check for ``Links.Otherwise``.
    """
    # Build a map from Links.Condition key → label/index, and capture the
    # Otherwise (default) link if present.
    link_labels: dict[str, dict[str, str]] = {}
    otherwise_link: dict[str, str] | None = None
    links_elem = elem.find("Links")
    if links_elem is not None:
        for link in links_elem:
            if link.tag == "Links.Condition":
                lk = link.attrib.get("Key", "")
                link_labels[lk] = {
                    "label": link.attrib.get("Label", ""),
                    "index": link.attrib.get("Index", ""),
                }
            elif link.tag == "Links.Otherwise":
                otherwise_link = {
                    "key": link.attrib.get("Key", ""),
                    "label": link.attrib.get("Label", "") or "Otherwise",
                }

    conditions: list[dict[str, Any]] = []
    cond_container = elem.find("Conditions")
    if cond_container is not None:
        for sc in cond_container:
            if sc.tag != "SwitchCondition":
                continue
            # Resolve the link reference to get the label
            link_ref = sc.attrib.get("Link", "")
            # Link ref looks like "Links.Condition:^/Links.<key>"
            link_key = link_ref.rsplit(".", 1)[-1] if "." in link_ref else ""
            label_info = link_labels.get(link_key, {})

            value_expr = sc.find("Value/ParsedExpression")
            conditions.append({
                "key": sc.attrib.get("Key", ""),
                "label": label_info.get("label", ""),
                "index": label_info.get("index", ""),
                "value": (
                    resolve_expression(value_expr, ki)
                    if value_expr is not None
                    else ""
                ),
                "is_default": False,
            })

    # The Switch's Otherwise / default branch was previously dropped, which
    # made the consumer believe a multi-way switch had no fall-through.
    if otherwise_link is not None:
        conditions.append({
            "key": otherwise_link["key"],
            "label": otherwise_link["label"],
            "index": "",
            "value": "",
            "is_default": True,
        })

    return conditions


def _extract_dataset_query(
    elem: ET.Element, ki: KeyIndex
) -> dict[str, Any]:
    """Extract the aggregate/query definition from a Nodes.DataSet element.

    Walks ``Table/DataTable/TableOperations`` to extract sources, joins,
    filters, sorts, and group-by information.
    """
    query: dict[str, Any] = {
        "sources": [],
        "joins": [],
        "filters": [],
        "sorts": [],
        "has_group_by": False,
    }

    table_ops = elem.find("Table/DataTable/TableOperations")
    if table_ops is None:
        return query

    for op in table_ops:
        tag = op.tag

        if tag == "DataSetOperations.AddSource":
            source_ref = op.attrib.get("Source", "")
            query["sources"].append({
                "key": op.attrib.get("Key", ""),
                "source": ki.resolve_ref(source_ref) if source_ref else "",
            })

        elif tag == "DataSetOperations.CombineSources":
            # Additional sources nested in Sources container
            sources_container = op.find("Sources")
            if sources_container is not None:
                for ds in sources_container:
                    if ds.tag == "DataSource":
                        src_op_ref = ds.attrib.get("SourceOperation", "")
                        query["sources"].append({
                            "key": ds.attrib.get("Key", ""),
                            "source_operation": ki.resolve_ref(src_op_ref) if src_op_ref else "",
                        })

            # Joins
            joins_container = op.find("Joins")
            if joins_container is not None:
                for jc in joins_container:
                    if jc.tag != "JoinCondition":
                        continue
                    cond_expr = jc.find("Condition/ParsedExpression")
                    query["joins"].append({
                        "join_type": jc.attrib.get("JoinType", ""),
                        "condition": (
                            resolve_expression(cond_expr, ki)
                            if cond_expr is not None
                            else ""
                        ),
                    })

            # Filters
            filters_container = op.find("Filters")
            if filters_container is not None:
                for filt in filters_container:
                    cond_expr = filt.find("Condition/ParsedExpression")
                    if cond_expr is not None:
                        query["filters"].append(resolve_expression(cond_expr, ki))

            # Sorts
            sorts_container = op.find("Sorts")
            if sorts_container is not None:
                for sort in sorts_container:
                    attr_expr = sort.find("OriginalAttribute/ParsedExpression")
                    if attr_expr is not None:
                        query["sorts"].append(resolve_expression(attr_expr, ki))

        elif tag == "DataSetOperations.GroupBy":
            query["has_group_by"] = True

    return query


def _ref_leaf_key(ref: str) -> str:
    """Return the Key of the last segment of a Ref path.

    ``ReferenceEntity:/References.R/ReferenceEntities.K`` -> ``K``.
    """
    last = ref.rsplit("/", 1)[-1]
    return last.rpartition(".")[2]


def _ref_leaf_name(ref: str, ki: KeyIndex) -> str:
    key = _ref_leaf_key(ref)
    return ki.name(key) or key


def _extract_advanced_query(
    elem: ET.Element, ki: KeyIndex, tr: TypeResolver | None
) -> dict[str, Any]:
    """Extract SQL text, parameters, output structure and arguments from a
    ``Nodes.AdvancedQuery`` element.
    """
    parameters: list[dict[str, Any]] = []
    param_names: dict[str, str] = {}
    params_container = elem.find("QueryParameters")
    if params_container is not None:
        for p in params_container:
            p_key = p.attrib.get("Key", "")
            p_name = p.attrib.get("Name", "")
            param_names[p_key] = p_name
            type_str = p.attrib.get("Type")
            parameters.append({
                "key": p_key,
                "name": p_name,
                "type": (
                    tr.resolve(type_str)
                    if tr is not None
                    else {"kind": "unknown", "raw": type_str, "name": type_str or "Unknown"}
                ),
            })

    output_structure: list[str] = []
    out_container = elem.find("OutputStructure")
    if out_container is not None:
        for rec in out_container:
            record_ref = rec.attrib.get("Record", "")
            if record_ref:
                output_structure.append(_ref_leaf_name(record_ref, ki))

    arguments: list[dict[str, str]] = []
    args_container = elem.find("Arguments")
    if args_container is not None:
        for arg in args_container:
            p_key = _ref_leaf_key(arg.attrib.get("Parameter", ""))
            value_expr = arg.find("Value/ParsedExpression")
            arguments.append({
                "key": arg.attrib.get("Key", ""),
                "name": param_names.get(p_key) or ki.name(p_key) or p_key,
                "value": (
                    resolve_expression(value_expr, ki)
                    if value_expr is not None
                    else ""
                ),
            })

    max_rec_expr = elem.find("MaxRecords/ParsedExpression")
    return {
        "sql": _reconstruct_sql(elem.find("SQL/ParsedSQLExpression"), ki, param_names),
        "parameters": parameters,
        "output_structure": output_structure,
        "arguments": arguments,
        "max_records": (
            resolve_expression(max_rec_expr, ki)
            if max_rec_expr is not None
            else ""
        ),
    }


def _reconstruct_sql(
    expr_elem: ET.Element | None, ki: KeyIndex, param_names: dict[str, str]
) -> str:
    """Reconstruct SQL text from a ``ParsedSQLExpression`` element.

    Literal ``Text``/``Unbound`` parts keep their ``SQLText``; entities render
    as ``{Entity}`` (``{Entity}.*`` with ``HasStar``), attributes as
    ``{Entity}.[Attr]`` and query parameters as ``@Param`` — the notation
    used in the OutSystems SQL editor.
    """
    if expr_elem is None:
        return ""
    parts: list[str] = []
    for child in expr_elem:
        tag = child.tag
        if tag == "Entity":
            name = _ref_leaf_name(child.attrib.get("Ref", ""), ki)
            star = ".*" if child.attrib.get("HasStar") == "Yes" else ""
            parts.append(f"{{{name}}}{star}")
        elif tag == "Attribute":
            ref = child.attrib.get("Ref", "")
            entity_elem = child.find("Entity")
            if entity_elem is not None:
                entity = _ref_leaf_name(entity_elem.attrib.get("Ref", ""), ki)
            else:
                # .../Entities.<E>/Attributes.<A> -> owning entity is the
                # second-to-last path segment.
                segments = ref.split("/")
                entity = (
                    _ref_leaf_name(segments[-2], ki) if len(segments) >= 2 else "?"
                )
            attr = _ref_leaf_name(ref, ki)
            if child.attrib.get("HasBrackets", "Yes") == "Yes":
                attr = f"[{attr}]"
            parts.append(f"{{{entity}}}.{attr}")
        elif tag == "Parameter":
            key = _ref_leaf_key(child.attrib.get("Ref", ""))
            parts.append(f"@{param_names.get(key) or ki.name(key) or key}")
        else:
            # Text, Unbound and any future literal parts carry SQLText.
            parts.append(child.attrib.get("SQLText", ""))
    return "".join(parts)


def _parse_link(elem: ET.Element, from_key: str) -> dict[str, Any]:
    """Extract a flow link (edge) from inside a node's <Links> element.

    The link's ``@TargetNode`` contains a reference like
    ``Nodes.ExecuteAction:^/^/NodesNotShownInESpaceTree.<key_prefix>``.
    We extract the target key from the last dot-separated segment.
    """
    tag = elem.tag
    link_type = tag.split(".")[-1] if "." in tag else tag

    target_ref = elem.attrib.get("TargetNode", "")
    # Extract target key from the path — last segment after the last dot
    # e.g. "Nodes.If:^/^/NodesNotShownInESpaceTree.rmN1BQ3NRkiA3nqtNYASpQ"
    to_key = ""
    if "." in target_ref:
        to_key = target_ref.rsplit(".", 1)[-1]

    return {
        "key": elem.attrib.get("Key", ""),
        "from_key": from_key,
        "to_key": to_key,
        "type": link_type,
        "target_ref": target_ref,
    }


# ---------------------------------------------------------------------------
# Inventory, actions list, and partitions
# ---------------------------------------------------------------------------

def _build_actions_inventory(
    server_actions: list[dict[str, Any]],
    client_actions: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combined action inventory with name, type, and parameter counts."""
    items: list[dict[str, Any]] = []

    for sa in server_actions:
        items.append({
            "name": sa["name"],
            "key": sa["key"],
            "type": "server",
            "input_count": len(sa["input_parameters"]),
            "output_count": len(sa["output_parameters"]),
            "node_count": len(sa["flow"]["nodes"]),
        })

    for ca in client_actions:
        items.append({
            "name": ca["name"],
            "key": ca["key"],
            "type": ca["action_type"],
            "input_count": len(ca["input_parameters"]),
            "output_count": len(ca["output_parameters"]),
            "node_count": len(ca["flow"]["nodes"]),
        })

    for c in contracts:
        items.append({
            "name": c["name"],
            "key": c["key"],
            "type": "reference",
            "source_module": c["source_module"],
            "input_count": len(c["input_parameters"]),
            "output_count": len(c["output_parameters"]),
        })

    return items


def _build_inventory(
    server_actions: list[dict[str, Any]],
    client_actions: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    cov: CoverageTracker,
) -> dict[str, Any]:
    """Complete logic domain inventory."""
    return {
        "domain": "logic",
        "status": cov.status,
        "server_action_count": len(server_actions),
        "client_action_count": len(client_actions),
        "reference_action_count": len(contracts),
        "total_action_count": len(server_actions) + len(client_actions) + len(contracts),
        "server_actions": [
            {"key": a["key"], "name": a["name"]} for a in server_actions
        ],
        "client_actions": [
            {"key": a["key"], "name": a["name"], "action_type": a["action_type"]}
            for a in client_actions
        ],
        "reference_actions": [
            {"key": c["key"], "name": c["name"], "source_module": c["source_module"]}
            for c in contracts
        ],
    }


def _build_partitions(
    server_actions: list[dict[str, Any]],
    client_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One partition per action, each independently implementable."""
    partitions: list[dict[str, Any]] = []

    for sa in server_actions:
        partitions.append({
            "id": f"server-action:{sa['name']}",
            "type": "server_action",
            "name": sa["name"],
            "key": sa["key"],
            "status": "ready",
        })

    for ca in client_actions:
        partitions.append({
            "id": f"{ca['action_type']}-action:{ca['name']}",
            "type": ca["action_type"] + "_action",
            "name": ca["name"],
            "key": ca["key"],
            "status": "ready",
        })

    return partitions


def _build_advanced_sql(
    server_actions: list[dict[str, Any]],
    client_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flat list of every Advanced SQL node in the module, by owning action."""
    queries: list[dict[str, Any]] = []
    for action in [*server_actions, *client_actions]:
        for node in action["flow"]["nodes"]:
            if node["type"] != "Nodes.AdvancedQuery":
                continue
            queries.append({
                "action": action["name"],
                "action_key": action["key"],
                "node": node["name"],
                "node_key": node["key"],
                "sql": node["sql"],
                "parameters": node["parameters"],
                "output_structure": node["output_structure"],
            })
    return queries


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------

def _int_attr(elem: ET.Element, attr: str) -> int:
    """Read an integer attribute, defaulting to 0."""
    val = elem.attrib.get(attr, "0")
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
