"""Process domain worker — extracts BPT process definitions from OML XML.

An OutSystems BPT process is split across several ``eSpaceFragment`` wrappers:

* ``FragmentName="Processes"`` holds the ``<Flows.Process>`` element(s): the
  process metadata, input/output parameters, and the ``ProcessActions`` (launch
  action) definition. Its ``NodesShownInESpaceTree`` / ``NodesNotShownInESpaceTree``
  children are empty placeholders.
* ``FragmentName="NodesShownInESpaceTree#<ProcessKey>"`` holds the visible graph
  nodes (``ProcessNodes.Activity``, ``ProcessNodes.Decision``).
* ``FragmentName="NodesNotShownInESpaceTree#<ProcessKey>"`` holds the hidden graph
  nodes (``ProcessNodes.Start``, ``ProcessNodes.End``).

To reconstruct a process graph we join the ``Processes`` fragment with the two
node fragments keyed by the process ``Key``. Flow edges are ``Links.Sequence``
(unlabeled next-step) and ``Links.Named`` (labeled branch off a Decision); their
``TargetNode`` attribute is a reference of the form
``ProcessNodes.Activity:^/^/NodesShownInESpaceTree.<NodeKey>``.

Activity node "kind" (automatic vs human) is derived from the inner
``<Flow>`` child tag (``Flows.Activity`` = automatic, ``Flows.HumanActivity`` =
human), NOT from a distinct XML tag — OutSystems represents both as
``ProcessNodes.Activity``.

Timers live in the separate ``FragmentName="Timers"`` fragment and are not part
of a process graph.

R3: fields the IDE surfaces but the OML does not encode (``Schedule`` on timers;
process ``Description`` / ``Detail`` / ``Is_Public`` when absent) are carried as
cited gaps with a machine-readable cause, never fabricated.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.xml_loader import OmlDocument

# Node tags OutSystems uses inside the process node fragments.
_START_TAG = "ProcessNodes.Start"
_END_TAG = "ProcessNodes.End"
_ACTIVITY_TAG = "ProcessNodes.Activity"
_DECISION_TAG = "ProcessNodes.Decision"

# Inner <Flow> child tag -> activity kind.  Automatic and Human activities are
# both ``ProcessNodes.Activity``; the inner flow element distinguishes them.
_FLOW_KIND = {
    "Flows.Activity": "automatic",
    "Flows.HumanActivity": "human",
    "Flows.Wait": "wait",
    "Flows.Decision": "decision",
}

# Cause enums (R3) for fields the Processes tab shows but the OML does not encode.
_CAUSE_NOT_IN_OML = "not_encoded_in_oml"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(doc: OmlDocument, pkg_root: Path) -> None:
    """Extract all process definitions and write to ``{pkg_root}/processes/``."""
    out = pkg_root / "processes"
    tracker = CoverageTracker(domain="process")

    processes: list[ET.Element] = []

    # Processes live in the "Processes" fragment (fragment-splitting pattern),
    # NOT inline inside the ESpace element.
    proc_frag = doc.fragment("Processes")
    if proc_frag is not None:
        for child in proc_frag:
            if "Process" in child.tag:
                processes.append(child)

    # Also check Processes#<key> fragments if they exist.
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name.startswith("Processes#"):
            for child in frag_elem:
                if "Process" in child.tag:
                    processes.append(child)

    # --- Timers (separate fragment, not under Processes) ---
    timers = _extract_timers(doc, tracker)

    transitions: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    partitions: list[dict[str, Any]] = []

    for proc in processes:
        name = proc.attrib.get("Name", "")
        key = proc.attrib.get("Key", "")
        tracker.discover(name or key, f"Flows.Process/{name}")

        proc_data = _extract_process(proc, doc, tracker)
        transitions.append(proc_data)

        inventory.append({
            "name": name,
            "key": key,
            "label": proc.attrib.get("Label", ""),
            "description": proc.attrib.get("Description", ""),
            "parameter_count": len(proc_data.get("input_parameters", []))
                + len(proc_data.get("output_parameters", [])),
            "activity_count": len(proc_data.get("activities", [])),
            "edge_count": len(proc_data.get("edges", [])),
            "callback_count": len(proc_data.get("callbacks", [])),
        })

        partitions.append({
            "partition_id": f"process:{name}",
            "kind": "process",
            "name": name,
            "key": key,
            "status": "ready",
        })

        tracker.capture(name or key, f"processes/{name}")

    write_json(out / "transitions.json", transitions)
    write_json(out / "inventory.json", inventory)
    write_json(out / "partitions.json", partitions)
    write_json(out / "timers.json", timers)
    write_json(out / "traversal-log.json", tracker.to_traversal_dict())
    write_json(out / "coverage.json", tracker.to_coverage_dict())


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------

def _extract_timers(doc: OmlDocument, tracker: CoverageTracker) -> list[dict[str, Any]]:
    """Extract Timer elements from the ``Timers`` fragment.

    ``Schedule`` is a Processes-tab property but is stored runtime/DB-side, not in
    the OML; when absent it is carried as a cited gap (R3), never fabricated.
    """
    timers: list[dict[str, Any]] = []
    action_names = {
        el.attrib["Key"]: el.attrib.get("Name", "")
        for el in doc.root.iter("Flows.UserAction")
        if "Key" in el.attrib
    }
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "Timers" or frag_name.startswith("Timers#"):
            for child in frag_elem:
                if child.tag != "Timer":
                    continue
                key = child.attrib.get("Key", "")
                action_ref = child.attrib.get("Action", "")
                action_name = action_names.get(action_ref.rsplit(".", 1)[-1], "")
                # Timers usually have no Name of their own (CustomNameIsSuffix):
                # Service Studio names them after the action they run.
                own_name = child.attrib.get("Name", "")
                tracker.discover(key, "Timer")
                schedule = child.attrib.get("Schedule", "")
                gaps: list[dict[str, Any]] = []
                if not schedule:
                    tracker.unresolve(key, "Timer/Schedule")
                    gaps.append({
                        "field": "schedule",
                        "cause": _CAUSE_NOT_IN_OML,
                        "source": "process_worker",
                        "detail": "Timer has no schedule in the module. It only runs "
                                  "when woken by logic (Wake<Timer>) or on a schedule "
                                  "set in Service Center, which is not in the OML.",
                    })
                timers.append({
                    "key": key,
                    "name": own_name or action_name,
                    "name_from_action": not own_name and bool(action_name),
                    "description": child.attrib.get("Description", ""),
                    "action": action_ref,
                    "action_name": action_name,
                    "schedule": schedule,
                    "timeout": child.attrib.get("Timeout", ""),
                    "priority": child.attrib.get("Priority", "Normal"),
                    "gaps": gaps,
                })
                tracker.capture(key, "timers")
    return timers


# ---------------------------------------------------------------------------
# Process extraction
# ---------------------------------------------------------------------------

def _extract_process(
    proc: ET.Element,
    doc: OmlDocument,
    tracker: CoverageTracker,
) -> dict[str, Any]:
    """Build a full process descriptor from a ``Flows.Process`` element."""
    name = proc.attrib.get("Name", "")
    key = proc.attrib.get("Key", "")

    input_params = _extract_parameters(proc, "InputParameters")
    output_params = _extract_parameters(proc, "OutputParameters")
    callbacks = _extract_callbacks(proc)
    actions = _extract_process_actions(proc)
    launch_action = _extract_launch_action(proc)
    event_provider = _extract_event_provider(proc)
    activities = _extract_activities(proc, doc, key, tracker)
    edges = _extract_edges(proc, doc, key)
    launch_on = _extract_launch_on(proc)
    detail, detail_gap = _extract_detail(proc)

    metadata_gaps: list[dict[str, Any]] = []
    # ``Description`` and the human-facing ``Detail`` are IDE Processes-tab fields.
    # Description is not present on the serialized process element in practice.
    if not proc.attrib.get("Description", ""):
        tracker.unresolve(name or key, f"Flows.Process/{name}/Description")
        metadata_gaps.append({
            "field": "description",
            "cause": _CAUSE_NOT_IN_OML,
            "source": "process_worker",
            "detail": "Process Description is not serialized on the "
                      "Flows.Process element.",
        })
    if detail_gap is not None:
        tracker.unresolve(name or key, f"Flows.Process/{name}/Detail")
        metadata_gaps.append(detail_gap)

    return {
        "name": name,
        "key": key,
        "label": proc.attrib.get("Label", ""),
        "description": proc.attrib.get("Description", ""),
        "detail": detail,
        "is_public": proc.attrib.get("Public", "No") == "Yes",
        "launch_on": launch_on,
        "event_provider": event_provider,
        "input_parameters": input_params,
        "output_parameters": output_params,
        "launch_action": launch_action,
        "callbacks": callbacks,
        "actions": actions,
        "activities": activities,
        "edges": edges,
        "metadata_gaps": metadata_gaps,
    }


# ---------------------------------------------------------------------------
# Launch metadata (Launch On)
# ---------------------------------------------------------------------------

def _extract_launch_on(proc: ET.Element) -> dict[str, Any]:
    """Derive the process launch trigger.

    OutSystems expresses launch behavior as either ``EventTrigger="None"``
    (manual launch) or ``EventProvider="Entity:/Entities.<Key>"`` (launched on an
    entity create/update event). The IDE's "Launch On" is derived from these.
    """
    event_provider = proc.attrib.get("EventProvider", "")
    event_trigger = proc.attrib.get("EventTrigger", "")
    if event_provider:
        entity_ref = event_provider
        if event_provider.startswith("Entity:"):
            entity_ref = event_provider[len("Entity:"):]
        return {
            "trigger_type": "entity_event",
            "entity_ref": entity_ref,
            "raw": event_provider,
        }
    return {
        "trigger_type": "manual" if event_trigger == "None" else (event_trigger or "unknown"),
        "entity_ref": "",
        "raw": event_trigger,
    }


def _extract_detail(proc: ET.Element) -> tuple[str, dict[str, Any] | None]:
    """Extract the human-facing "Detail" from ``InboxDetail/ParsedExpression``.

    Returns (detail_text, gap-or-None).
    """
    inbox = proc.find("InboxDetail")
    if inbox is not None:
        text = inbox.find("./ParsedExpression/Text")
        if text is not None and text.attrib.get("Value"):
            return text.attrib["Value"], None
    return "", {
        "field": "detail",
        "cause": _CAUSE_NOT_IN_OML,
        "source": "process_worker",
        "detail": "No InboxDetail expression on the process element.",
    }


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------

def _extract_parameters(parent: ET.Element, container_tag: str) -> list[dict[str, Any]]:
    """Extract input or output parameters from a container child element."""
    container = parent.find(container_tag)
    if container is None:
        return []
    params: list[dict[str, Any]] = []
    for child in container:
        params.append({
            "tag": child.tag,
            "name": child.attrib.get("Name", ""),
            "key": child.attrib.get("Key", ""),
            "type": child.attrib.get("Type", ""),
            "is_mandatory": child.attrib.get("IsMandatory", ""),
            "description": child.attrib.get("Description", ""),
        })
    return params


# ---------------------------------------------------------------------------
# Launch action
# ---------------------------------------------------------------------------

def _extract_launch_action(proc: ET.Element) -> dict[str, Any] | None:
    """Extract the ``ProcessActions.LaunchProcess`` action and its input params.

    The launch action's inputs are held both as ``InputParameters`` and as
    ``SyntheticInputParameters`` (the concrete bindings). Both are captured.
    """
    container = proc.find("ProcessActions")
    if container is None:
        return None
    launch = container.find("ProcessActions.LaunchProcess")
    if launch is None:
        return None
    return {
        "input_parameters": _extract_parameters(launch, "InputParameters"),
        "synthetic_input_parameters": _extract_parameters(
            launch, "SyntheticInputParameters"
        ),
        "output_parameters": _extract_parameters(launch, "OutputParameters"),
    }


# ---------------------------------------------------------------------------
# Event provider
# ---------------------------------------------------------------------------

def _extract_event_provider(proc: ET.Element) -> dict[str, Any] | None:
    """Extract the EventProvider element from a process (if present as element)."""
    ep = proc.find("EventProvider")
    if ep is None:
        return None
    return {
        "tag": ep.tag,
        "name": ep.attrib.get("Name", ""),
        "key": ep.attrib.get("Key", ""),
        "attributes": dict(ep.attrib),
    }


# ---------------------------------------------------------------------------
# Process callbacks / actions
# ---------------------------------------------------------------------------

def _extract_callbacks(proc: ET.Element) -> list[dict[str, Any]]:
    """Extract ProcessCallbacks children."""
    container = proc.find("ProcessCallbacks")
    if container is None:
        return []
    return [
        {
            "tag": child.tag,
            "name": child.attrib.get("Name", ""),
            "key": child.attrib.get("Key", ""),
            "attributes": dict(child.attrib),
        }
        for child in container
    ]


def _extract_process_actions(proc: ET.Element) -> list[dict[str, Any]]:
    """Extract ProcessActions children."""
    container = proc.find("ProcessActions")
    if container is None:
        return []
    return [
        {
            "tag": child.tag,
            "name": child.attrib.get("Name", ""),
            "key": child.attrib.get("Key", ""),
            "attributes": dict(child.attrib),
        }
        for child in container
    ]


# ---------------------------------------------------------------------------
# Activity nodes
# ---------------------------------------------------------------------------

def _process_node_elements(
    proc: ET.Element,
    doc: OmlDocument,
    proc_key: str,
) -> list[ET.Element]:
    """All graph-node elements for a process, joined from its keyed fragments.

    Nodes live in ``NodesShownInESpaceTree#<Key>`` (Activity/Decision) and
    ``NodesNotShownInESpaceTree#<Key>`` (Start/End). Also tolerate nodes nested
    directly under the process element (older/inline layout).
    """
    nodes: list[ET.Element] = []
    seen: set[int] = set()

    def _add(elem: ET.Element) -> None:
        if id(elem) not in seen:
            seen.add(id(elem))
            nodes.append(elem)

    if proc_key:
        for prefix in ("NodesShownInESpaceTree#", "NodesNotShownInESpaceTree#"):
            frag = doc.fragment(prefix + proc_key)
            if frag is None:
                continue
            for child in frag:
                if child.tag.startswith("ProcessNodes."):
                    _add(child)

    # Inline fallback: nodes as descendants of the process element itself.
    for child in proc.iter():
        if child is proc:
            continue
        if child.tag.startswith("ProcessNodes."):
            _add(child)

    return nodes


def _node_type(tag: str) -> str:
    """Map a ProcessNodes tag to a normalized node type."""
    return {
        _START_TAG: "start",
        _END_TAG: "end",
        _ACTIVITY_TAG: "activity",
        _DECISION_TAG: "decision",
    }.get(tag, tag)


def _activity_kind(node: ET.Element) -> str:
    """Derive automatic/human/wait kind from the inner ``Flow`` child tag."""
    flow = node.find("Flow")
    if flow is not None:
        for child in flow:
            kind = _FLOW_KIND.get(child.tag)
            if kind is not None:
                return kind
    return ""


def _extract_activities(
    proc: ET.Element,
    doc: OmlDocument,
    proc_key: str,
    tracker: CoverageTracker,
) -> list[dict[str, Any]]:
    """Extract all process graph nodes with types and properties."""
    activities: list[dict[str, Any]] = []
    for node in _process_node_elements(proc, doc, proc_key):
        node_type = _node_type(node.tag)
        kind = _activity_kind(node) if node_type in ("activity", "decision") else ""
        name = node.attrib.get("Name", "")
        tracker.expand(name or node.attrib.get("Key", ""), f"{node.tag}/{name}")
        activities.append({
            "tag": node.tag,
            "node_type": node_type,
            "activity_kind": kind,
            "name": name,
            "key": node.attrib.get("Key", ""),
            "label": node.attrib.get("Label", ""),
            "activation_instant": node.attrib.get("ActivationInstant", "")
                or (node.findtext("ActivationInstant", "") or ""),
            "output_parameters": _extract_parameters(node, "ActivityOutputParameters"),
        })
    return activities


# ---------------------------------------------------------------------------
# Flow edges
# ---------------------------------------------------------------------------

def _parse_target_ref(ref: str) -> tuple[str, str]:
    """Parse a ``TargetNode`` ref into (target_type, target_key).

    Example: ``ProcessNodes.Activity:^/^/NodesShownInESpaceTree.<Key>`` ->
    ("activity", "<Key>").
    """
    if not ref:
        return "", ""
    target_type = ""
    rest = ref
    if ":" in ref:
        tag, rest = ref.split(":", 1)
        target_type = _node_type(tag)
    # The node key is the final dotted segment of the path.
    key = rest.rsplit(".", 1)[-1] if rest else ""
    return target_type, key


def _extract_edges(
    proc: ET.Element,
    doc: OmlDocument,
    proc_key: str,
) -> list[dict[str, Any]]:
    """Extract flow edges (source node -> target node) for a process.

    Edges are ``Links.Sequence`` / ``Links.Named`` elements nested under each
    source node's ``Links`` container. We scope collection to this process's
    node fragments so we never pick up unrelated screen/action flow links.
    """
    edges: list[dict[str, Any]] = []
    for node in _process_node_elements(proc, doc, proc_key):
        source_key = node.attrib.get("Key", "")
        source_type = _node_type(node.tag)
        links_container = node.find("Links")
        if links_container is None:
            continue
        for link in links_container:
            if not link.tag.startswith("Links."):
                continue
            target_ref = link.attrib.get("TargetNode", "")
            target_type, target_key = _parse_target_ref(target_ref)
            edges.append({
                "tag": link.tag,
                "key": link.attrib.get("Key", ""),
                "label": link.attrib.get("Name", ""),
                "source_key": source_key,
                "source_type": source_type,
                "target_key": target_key,
                "target_type": target_type,
                "target_ref": target_ref,
            })
    return edges
