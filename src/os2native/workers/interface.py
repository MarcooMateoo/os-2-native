"""Interface domain worker — extracts UI flows, screens, blocks, widgets, themes, and CSS."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.shared.expression_resolver import resolve_expression
from os2native.shared.key_index import KeyIndex, build_key_index
from os2native.shared.type_resolver import TypeResolver
from os2native.workers.logic import (
    _extract_flow_graph,
    _extract_parameters as _extract_logic_parameters,
    _extract_local_variables as _extract_logic_local_variables,
)
from os2native.xml_loader import OmlDocument


# ---------------------------------------------------------------------------
# Widget type simplification
# ---------------------------------------------------------------------------

def _widget_type_name(tag: str) -> str:
    """Extract a short readable widget type from a long qualified XML tag.

    Examples:
        NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Container -> Container
        NRWebWidgets.Text -> Text
        NRWebWidgets.If -> If
        NRWebWidgets.ReferenceCustomWidget-OutSystems.Plugin.NRWidgets.ReferenceInput -> ReferenceInput
        NRWebWidgets.WebBlockInstance -> WebBlockInstance
        NRWebWidgets.Placeholder -> Placeholder
        NRWebWidgets.CustomPlaceholderWidget -> CustomPlaceholderWidget
    """
    # If it contains "NRWidgets.", take the part after the last "NRWidgets."
    marker = "NRWidgets."
    idx = tag.rfind(marker)
    if idx != -1:
        return tag[idx + len(marker):]

    # Otherwise take the part after the last dot
    dot = tag.rfind(".")
    if dot != -1:
        return tag[dot + 1:]

    return tag


# ---------------------------------------------------------------------------
# Parameter / variable extraction
# ---------------------------------------------------------------------------

def _extract_parameters(
    parent: ET.Element, child_tag: str, type_resolver: TypeResolver | None = None,
) -> list[dict[str, Any]]:
    """Extract input/output parameters or local variables from a container element."""
    container = parent.find(child_tag)
    if container is None:
        return []

    params: list[dict[str, Any]] = []
    for elem in container:
        raw_type = elem.attrib.get("Type", "")
        resolved_type = raw_type
        if type_resolver is not None and raw_type:
            type_info = type_resolver.resolve(raw_type)
            resolved_type = type_info.get("name", type_info.get("raw", raw_type))
        entry = {
            "name": elem.attrib.get("Name", ""),
            "key": elem.attrib.get("Key", ""),
            "type": resolved_type,
            "is_mandatory": elem.attrib.get("IsMandatory", "No"),
            "tag": elem.tag,
        }
        # Default value — attribute, child text, or ParsedExpression
        default_val = elem.attrib.get("DefaultValue", "")
        if not default_val:
            dv_elem = elem.find("DefaultValue")
            if dv_elem is not None:
                if dv_elem.text and dv_elem.text.strip():
                    default_val = dv_elem.text.strip()
                else:
                    # Structured: <ParsedExpression><Boolean Value="True"/></ParsedExpression>
                    parsed = dv_elem.find("ParsedExpression")
                    if parsed is not None and len(parsed) > 0:
                        val_child = parsed[0]
                        default_val = val_child.attrib.get("Value", "")
        if default_val:
            entry["default_value"] = default_val
        params.append(entry)
    return params


# ---------------------------------------------------------------------------
# Event handler extraction
# ---------------------------------------------------------------------------

_EVENT_NAMES = ("OnInitialize", "OnReady", "OnDestroy")


def _extract_events(node: ET.Element) -> list[dict[str, str]]:
    """Extract event handler references from a screen or block element."""
    events: list[dict[str, str]] = []
    for name in _EVENT_NAMES:
        child = node.find(name)
        if child is not None:
            # The event element may contain a Ref/Key or child flow reference
            ref = child.attrib.get("Ref", child.attrib.get("Key", ""))
            events.append({"event": name, "ref": ref})
        else:
            # Also check as an attribute on the node itself
            val = node.attrib.get(name, "")
            if val:
                events.append({"event": name, "ref": val})
    return events


# ---------------------------------------------------------------------------
# Screen data sets extraction
# ---------------------------------------------------------------------------

def _extract_screen_data_sets(node: ET.Element) -> list[dict[str, Any]]:
    """Extract ScreenDataSets (data fetch bindings) from a screen element."""
    container = node.find("ScreenDataSets")
    if container is None:
        return []

    datasets: list[dict[str, Any]] = []
    for elem in container:
        datasets.append({
            "name": elem.attrib.get("Name", ""),
            "key": elem.attrib.get("Key", ""),
            "tag": elem.tag,
        })
    return datasets


# ---------------------------------------------------------------------------
# Widget tree extraction (recursive)
# ---------------------------------------------------------------------------

_WIDGET_CHILD_CONTAINERS = (
    "ChildWidgets",
    "ChildPlaceholders",
    "PlaceholderArguments",
    "Branches",
    "Rows",
    "Cells",
)


def _extract_event_handlers(
    widget: ET.Element, key_index: KeyIndex,
) -> list[dict[str, str]]:
    """Extract widget-level event handlers (OnClick, OnChange, etc.).

    Two patterns exist in OutSystems XML:

    1. **Reactive Web** (NRWebWidgets): handlers in an ``<EventHandlers>``
       container with ``NRWebWidgetEvents.EventHandler`` children that carry
       ``EventName`` and ``Destination`` attributes.

    2. **Traditional Web** (WebWidgets): handlers as direct child elements
       ``<OnClick>``, ``<OnChange>``, ``<OnNotify>`` on the widget, each
       containing a ``WebWidgetEvents.*`` child with a ``Destination``
       attribute.
    """
    handlers: list[dict[str, str]] = []

    def handler_record(
        *,
        event: str,
        destination: str,
        source: ET.Element,
    ) -> dict[str, str]:
        record = {
            "event": event,
            "destination_ref": destination,
            "destination": key_index.resolve_ref(destination) if destination else "",
        }
        for attr_name, result_name in (
            ("Method", "method"),
            ("Validation", "validation"),
        ):
            value = source.attrib.get(attr_name, "")
            if value:
                record[result_name] = value
        confirmation = source.find("ConfirmationMessage")
        if confirmation is not None:
            pe = confirmation.find("ParsedExpression")
            if pe is not None:
                message = resolve_expression(pe, key_index)
            else:
                message = (confirmation.text or "").strip()
            if len(message) >= 2 and message[0] == '"' and message[-1] == '"':
                message = message[1:-1]
            if message:
                record["confirmation_message"] = message
        return record

    # --- Pattern 1: Reactive Web (NRWebWidgets) ---
    container = widget.find("EventHandlers")
    if container is not None:
        for handler in container:
            event_name = handler.attrib.get("EventName", "")
            destination = handler.attrib.get("Destination", "")
            handlers.append(
                handler_record(
                    event=event_name,
                    destination=destination,
                    source=handler,
                )
            )

    # --- Pattern 2: Traditional Web (WebWidgets) ---
    for event_tag in ("OnClick", "OnChange", "OnNotify"):
        event_elem = widget.find(event_tag)
        if event_elem is None:
            continue
        for child in event_elem:
            destination = child.attrib.get("Destination", "")
            if not destination:
                continue
            handlers.append(
                handler_record(
                    event=event_tag,
                    destination=destination,
                    source=child,
                )
            )

    return handlers


def _extract_custom_properties(
    widget: ET.Element, key_index: KeyIndex,
) -> list[dict[str, Any]]:
    """Extract CustomProperty bindings with resolved expressions.

    Custom properties store expressions in ``ValueExpression/ParsedExpression``
    (not ``Value/ParsedExpression``).
    """
    container = widget.find("CustomProperties")
    if container is None:
        return []
    props: list[dict[str, Any]] = []
    for prop in container:
        prop_name = prop.attrib.get("PropertyName", "")
        value_expr_elem = prop.find("ValueExpression/ParsedExpression")
        value = ""
        if value_expr_elem is not None:
            value = resolve_expression(value_expr_elem, key_index)
        if not value:
            value = prop.attrib.get("Value", "")
        if value:  # Only include properties that have a bound value
            props.append({"name": prop_name, "value": value})
    return props


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", value).lower()


def _extract_widget_standard_properties(
    widget: ET.Element,
    key_index: KeyIndex,
) -> dict[str, Any]:
    """Extract non-CustomProperty widget bindings such as ComboBox options."""
    details: dict[str, Any] = {}
    for child_name in (
        "Variable",
        "SourceRecordList",
        "SpecialVariable",
        "Mandatory",
        "NullValue",
    ):
        pe = widget.find(f"{child_name}/ParsedExpression")
        if pe is not None:
            details[_camel_to_snake(child_name)] = resolve_expression(pe, key_index)

    source_attrs: dict[str, str] = {}
    for attr_name in (
        "SourceAttributeParent",
        "SourceAttribute",
        "SourceAttributeRecord",
        "SourceIdentifierAttributeRecord",
    ):
        value = widget.attrib.get(attr_name, "")
        if value:
            source_attrs[_camel_to_snake(attr_name)] = key_index.resolve_ref(value)
            source_attrs[f"raw_{_camel_to_snake(attr_name)}"] = value
    if source_attrs:
        details["source_attributes"] = source_attrs

    special_list = widget.find("SpecialList")
    if special_list is not None:
        entries: list[dict[str, str]] = []
        for entry in special_list:
            if entry.tag.endswith("SpecialListEntry"):
                entries.append({
                    "key": entry.attrib.get("Key", ""),
                    "value": entry.attrib.get("Value", ""),
                    "label": entry.attrib.get("Label", ""),
                })
        if entries:
            details["special_list"] = entries
    return details


def _extract_custom_property_value(
    widget: ET.Element,
    key_index: KeyIndex,
    property_name: str,
) -> str:
    container = widget.find("CustomProperties")
    if container is None:
        return ""
    expected = property_name.strip().lower()
    for prop in container:
        if str(prop.attrib.get("PropertyName", "")).strip().lower() != expected:
            continue
        value_expr_elem = prop.find("ValueExpression/ParsedExpression")
        if value_expr_elem is not None:
            return resolve_expression(value_expr_elem, key_index)
        value = prop.attrib.get("Value", "")
        if value:
            return value
    return ""


_WIDGET_LAYOUT_ATTRIBUTE_MAP = {
    "Width": "width",
    "Height": "height",
    "MarginLeft": "margin_left",
    "MarginTop": "margin_top",
    "Align": "align",
    "EffectiveWidth": "effective_width",
    "EffectiveMarginLeft": "effective_margin_left",
    "IsDefault": "is_default",
    "CustomStyle": "custom_style",
    "MinWidth": "min_width",
    "MaxWidth": "max_width",
}


def _extract_widget_layout_properties(widget: ET.Element) -> dict[str, str]:
    props: dict[str, str] = {}
    for source_name, target_name in _WIDGET_LAYOUT_ATTRIBUTE_MAP.items():
        value = widget.attrib.get(source_name, "")
        if value:
            props[target_name] = value
    return props


def _extract_visible_condition(
    widget: ET.Element, key_index: KeyIndex,
) -> str:
    """Extract the Visible condition expression from a widget."""
    pe = widget.find("Visible/ParsedExpression")
    if pe is not None:
        return resolve_expression(pe, key_index)
    return ""


def _extract_display_condition(
    widget: ET.Element, key_index: KeyIndex,
) -> str:
    """Extract the Display condition expression from a widget.

    OutSystems containers and some widgets expose ``Display`` separately from
    ``Visible``. A static ``Display=False`` hides the whole subtree at runtime,
    so descendants must inherit it as effective visibility evidence.
    """
    pe = widget.find("Display/ParsedExpression")
    if pe is not None:
        return resolve_expression(pe, key_index)
    value = widget.attrib.get("Display", "")
    if value:
        return value
    return ""


def _extract_enabled_condition(
    widget: ET.Element, key_index: KeyIndex,
) -> str:
    """Extract the Enabled condition expression from a widget."""
    pe = widget.find("Enabled/ParsedExpression")
    if pe is not None:
        return resolve_expression(pe, key_index)
    value = _extract_custom_property_value(widget, key_index, "Enabled")
    if value:
        return value
    return ""


def _role_gates_from_expression(expression: str) -> list[dict[str, str]]:
    """Extract explicit role/access gates from a resolved expression or action ref."""
    expression = expression.strip()
    if not expression:
        return []

    gates: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def append(gate: dict[str, str]) -> None:
        key = (
            gate.get("kind", ""),
            gate.get("role", ""),
            gate.get("source", ""),
        )
        if key not in seen:
            seen.add(key)
            gates.append(gate)

    if "AllowAdminOnly" in expression:
        append({"kind": "admin_only", "source": expression})

    if "RestrictAccess" in expression:
        append({"kind": "restrict_access", "source": expression})

    for match in re.finditer(r"#?CheckRole\(([^)]*)\)", expression):
        role = match.group(1).strip().strip('"')
        gate = {"kind": "check_role", "source": expression}
        if role and role != "<EmptyArgument>":
            gate["role"] = role
        append(gate)

    for match in re.finditer(
        r"Roles\.([A-Za-z_][A-Za-z0-9_]*)\.RoleActions\.#?CheckRole",
        expression,
    ):
        append({
            "kind": "check_role",
            "role": match.group(1),
            "source": expression,
        })

    return gates


def _merge_role_gates(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for gate in group:
            key = (
                str(gate.get("kind", "")),
                str(gate.get("role", "")),
                str(gate.get("source", "")),
            )
            if key not in seen:
                seen.add(key)
                merged.append(gate)
    return merged


def _role_gates_from_visibility_path(
    visibility_path: list[dict[str, Any]],
) -> list[dict[str, str]]:
    gates: list[dict[str, str]] = []
    for entry in visibility_path:
        if not isinstance(entry, dict):
            continue
        entry_gates = entry.get("role_gates")
        if isinstance(entry_gates, list):
            gates = _merge_role_gates(gates, entry_gates)
        gates = _merge_role_gates(
            gates,
            _role_gates_from_expression(str(entry.get("condition", ""))),
        )
    return gates


def _if_branch_label(widget: ET.Element, index: int) -> str:
    """Infer an OutSystems IfBranch label from attributes or sibling order."""
    for attr_name in ("Name", "Type", "Branch", "Condition", "IsDefault"):
        value = widget.attrib.get(attr_name, "").strip().lower()
        if value in ("true", "yes", "then"):
            return "true"
        if value in ("false", "no", "else"):
            return "false"
        if value in ("otherwise", "default"):
            return "default"
    return "true" if index == 0 else "false" if index == 1 else f"branch_{index}"


def _visibility_entry_with_role_gates(entry: dict[str, Any]) -> dict[str, Any]:
    gates = _role_gates_from_expression(str(entry.get("condition", "")))
    if gates:
        entry = dict(entry)
        entry["role_gates"] = gates
    return entry


_CSS_VISIBILITY_CLASS_HINTS = {
    "display-none",
    "hidden",
    "hide",
    "invisible",
    "visibility-hidden",
    "is-hidden",
    "d-none",
}

_CSS_INTERACTION_CLASS_HINTS = {
    "disabled",
    "is-disabled",
    "not-allowed",
    "no-click",
    "no-pointer",
    "pointer-events-none",
}


def _style_class_tokens(value: str) -> list[str]:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    if not value:
        return []
    if any(token in value for token in ("(", ")", "+", '"', "'")):
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"\s+", value):
        token = token.strip()
        if not token:
            continue
        if not re.match(r"^-?[A-Za-z_][A-Za-z0-9_-]*$", token):
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _css_effect_hints_from_classes(classes: list[str]) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    for class_name in classes:
        normalized = class_name.strip().lower()
        if normalized in _CSS_VISIBILITY_CLASS_HINTS:
            hints.append({
                "kind": "css_class_visibility_hint",
                "class_name": class_name,
                "effect": "hidden",
            })
        if normalized in _CSS_INTERACTION_CLASS_HINTS:
            hints.append({
                "kind": "css_class_interaction_hint",
                "class_name": class_name,
                "effect": "disabled_or_non_interactive",
            })
    return hints


def _css_effect_hints_from_inline_style(style: str) -> list[dict[str, str]]:
    normalized = style.strip().lower()
    hints: list[dict[str, str]] = []
    if re.search(r"(?:^|[;{])\s*(?:-[a-z]+-)?display\s*:\s*none\b", normalized):
        hints.append({"kind": "inline_style_visibility_hint", "effect": "display_none"})
    if re.search(r"(?:^|[;{])\s*(?:-[a-z]+-)?visibility\s*:\s*hidden\b", normalized):
        hints.append({"kind": "inline_style_visibility_hint", "effect": "visibility_hidden"})
    if re.search(r"(?:^|[;{])\s*(?:-[a-z]+-)?pointer-events\s*:\s*none\b", normalized):
        hints.append({"kind": "inline_style_interaction_hint", "effect": "pointer_events_none"})
    if re.search(r"(?:^|[;{])\s*(?:-[a-z]+-)?opacity\s*:\s*0(?:\.0+)?\s*(?:[;!}]|$)", normalized):
        hints.append({"kind": "inline_style_visibility_hint", "effect": "opacity_zero"})
    return hints


def _style_evidence_for_widget(widget: dict[str, Any]) -> dict[str, Any]:
    style_attr = str(widget.get("style", "")).strip()
    extended_class = str(widget.get("extended_class", "")).strip()
    custom_style_values: list[str] = []
    for prop in widget.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        if str(prop.get("name", "")).strip().lower() == "style":
            value = str(prop.get("value", "")).strip()
            if value:
                custom_style_values.append(value)
    style_classes = [
        *_style_class_tokens(extended_class),
        *_style_class_tokens(style_attr),
        *[
            class_name
            for value in custom_style_values
            for class_name in _style_class_tokens(value)
        ],
    ]
    seen_classes: set[str] = set()
    style_classes = [
        class_name for class_name in style_classes
        if not (class_name in seen_classes or seen_classes.add(class_name))
    ]

    class_expressions: list[str] = []
    if extended_class and not _style_class_tokens(extended_class):
        class_expressions.append(extended_class)
    if style_attr and not _style_class_tokens(style_attr):
        class_expressions.append(style_attr)
    for value in custom_style_values:
        if not _style_class_tokens(value):
            class_expressions.append(value)

    inline_styles: list[str] = []
    for prop in widget.get("extended_properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        value = str(prop.get("value", "")).strip()
        if name == "class":
            prop_classes = _style_class_tokens(value)
            for class_name in prop_classes:
                if class_name not in seen_classes:
                    seen_classes.add(class_name)
                    style_classes.append(class_name)
            if value and not prop_classes:
                class_expressions.append(value)
        elif name == "style" and value:
            inline_styles.append(value)

    effect_hints = _css_effect_hints_from_classes(style_classes)
    for inline_style in inline_styles:
        effect_hints.extend(_css_effect_hints_from_inline_style(inline_style))

    evidence: dict[str, Any] = {}
    if style_classes:
        evidence["style_classes"] = style_classes
    if class_expressions:
        evidence["style_class_expressions"] = class_expressions
    if custom_style_values:
        evidence["custom_style_properties"] = custom_style_values
    if inline_styles:
        evidence["inline_styles"] = inline_styles
    if effect_hints:
        evidence["css_effect_hints"] = effect_hints
    return evidence


def _widget_style_path_entry(widget: dict[str, Any]) -> dict[str, Any] | None:
    evidence = _style_evidence_for_widget(widget)
    if not evidence:
        return None
    entry: dict[str, Any] = {
        "widget_key": widget.get("key", ""),
        "widget_name": widget.get("name", ""),
        "widget_type": widget.get("type", ""),
    }
    entry.update(evidence)
    return entry


def _widget_layout_path_entry(widget: dict[str, Any], *, depth: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "widget_key": widget.get("key", ""),
        "widget_name": widget.get("name", ""),
        "widget_type": widget.get("type", ""),
        "depth": depth,
    }
    style_classes = widget.get("style_classes")
    if isinstance(style_classes, list) and style_classes:
        entry["style_classes"] = style_classes
    extended_class = widget.get("extended_class")
    if extended_class:
        entry["extended_class"] = extended_class
    layout_props = widget.get("layout_props")
    if isinstance(layout_props, dict) and layout_props:
        entry["layout_props"] = layout_props
    return entry


def _layout_type_matches(widget_type: str, suffix: str) -> bool:
    normalized = widget_type.lower()
    return normalized == suffix or normalized.endswith(suffix)


def _layout_group_from_path(
    layout_path: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    group: dict[str, Any] = {}
    if not layout_path:
        return group

    def nearest(suffix: str) -> dict[str, Any] | None:
        for entry in reversed(layout_path):
            widget_type = str(entry.get("widget_type", ""))
            if _layout_type_matches(widget_type, suffix):
                return entry
        return None

    for label, suffix in (
        ("row", "row"),
        ("cell", "cell"),
        ("container", "container"),
    ):
        entry = nearest(suffix)
        if entry:
            group[f"{label}_widget_key"] = entry.get("widget_key", "")
            group[f"{label}_widget_name"] = entry.get("widget_name", "")
            group[f"{label}_widget_type"] = entry.get("widget_type", "")

    return group


def _extract_extended_properties(
    widget: ET.Element, key_index: KeyIndex,
) -> list[dict[str, str]]:
    """Extract ExtendedProperties (CSS/HTML attributes) from a widget.

    Each child element has an ``AttributeName`` attribute and a ``Value``
    child that may contain a ``ParsedExpression``.
    """
    container = widget.find("ExtendedProperties")
    if container is None:
        return []
    props: list[dict[str, str]] = []
    for ep in container:
        name_val = ep.attrib.get("AttributeName", "") or ep.attrib.get("Name", "")
        value_val = ""
        value_elem = ep.find("Value")
        if value_elem is not None:
            pe = value_elem.find("ParsedExpression")
            if pe is not None:
                value_val = resolve_expression(pe, key_index)
            elif value_elem.text:
                value_val = value_elem.text
        # Fallback to Value attribute for older formats
        if not value_val:
            value_val = ep.attrib.get("Value", "")
        if name_val or value_val:
            props.append({"name": name_val, "value": value_val})
    return props


def _known_webblock_semantics(source_block: str) -> dict[str, str]:
    """Classify common Traditional Web/RichWidgets/OutSystemsUIWeb blocks."""
    if not source_block:
        return {}
    leaf = source_block.rsplit(".", 1)[-1]
    normalized = leaf.lower()
    full = source_block.lower()

    semantic_type = ""
    role = ""
    if normalized in {"layout", "layoutlogin", "layoutpopup"} or "layout" in normalized:
        semantic_type = "shell_layout"
        role = "shell/layout"
    elif normalized in {"menu", "applicationtitle", "logininfo"} or normalized.endswith("menu"):
        semantic_type = "shell_navigation"
        role = "shell/navigation"
    elif "modal" in normalized or "popup" in normalized:
        semantic_type = "modal_dialog"
        role = "modal/dialog"
    elif "tab" in normalized:
        semantic_type = "tabs"
        role = "tabs/layout"
    elif "pagination" in normalized or "list_navigation" in normalized or "listnavigation" in normalized:
        semantic_type = "pagination"
        role = "table/paging"
    elif "sort" in normalized or "sortable" in normalized:
        semantic_type = "table_sort"
        role = "table/header-action"
    elif "upload" in normalized:
        semantic_type = "upload_control"
        role = "form/upload"
    elif "outsystemsuiweb" in full or "richwidgets" in full:
        semantic_type = "referenced_ui_primitive"
        role = "ui/primitive"

    if not semantic_type:
        return {}
    return {
        "semantic_type": semantic_type,
        "semantic_role": role,
        "semantic_source": "known_outsystems_webblock_name",
    }


def _extract_webblock_details(
    widget: ET.Element, key_index: KeyIndex,
) -> dict[str, Any]:
    """Extract WebBlockInstance details: source block ref and arguments."""
    details: dict[str, Any] = {}

    source_ref = widget.attrib.get("SourceWebBlock", "")
    if source_ref:
        details["source_ref"] = source_ref
        details["source_block"] = key_index.resolve_ref(source_ref)
        details.update(_known_webblock_semantics(details["source_block"]))

    args_container = widget.find("Arguments")
    if args_container is not None:
        args: list[dict[str, str]] = []
        for arg in args_container:
            param_ref = arg.attrib.get("Parameter", "")
            param_name = key_index.resolve_ref(param_ref) if param_ref else ""
            # Argument values are in Value/ParsedExpression
            value_pe = arg.find("Value/ParsedExpression")
            value = resolve_expression(value_pe, key_index) if value_pe is not None else ""
            if param_name or value:
                args.append({"parameter": param_name, "value": value})
        if args:
            details["arguments"] = args

    # Custom event handlers (event delegation to parent)
    ceh = widget.find("CustomEventHandlers")
    if ceh is not None and len(list(ceh)) > 0:
        custom_events: list[dict[str, str]] = []
        for handler in ceh:
            event_name = handler.attrib.get("EventName", "")
            destination = handler.attrib.get("Destination", "")
            if event_name:
                custom_events.append({
                    "event": event_name,
                    "destination": key_index.resolve_ref(destination) if destination else "",
                })
        if custom_events:
            details["custom_event_handlers"] = custom_events

    return details


def _extract_widget_tree(
    widget: ET.Element,
    doc: OmlDocument,
    key_index: KeyIndex,
    tracker: CoverageTracker,
    depth: int = 0,
    visibility_path: list[dict[str, Any]] | None = None,
    style_path: list[dict[str, Any]] | None = None,
    layout_path: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recursively extract a widget and its children into a dict."""
    tag = widget.tag
    short_type = _widget_type_name(tag)
    key = widget.attrib.get("Key", "")
    name = widget.attrib.get("Name", "")

    tracker.expand(f"widget:{key or name or short_type}", path=f"depth={depth}")

    result: dict[str, Any] = {
        "type": short_type,
        "tag": tag,
        "name": name,
        "key": key,
    }

    # Common properties
    extended_class = widget.attrib.get("ExtendedClass", "")
    if extended_class:
        result["extended_class"] = extended_class

    style = widget.attrib.get("Style", "")
    if style:
        result["style"] = style

    layout_props = _extract_widget_layout_properties(widget)
    if layout_props:
        result["layout_props"] = layout_props
        result.update(layout_props)

    # Text value — check for a <Value> child or Value attribute
    value_elem = widget.find("Value")
    if value_elem is not None and value_elem.text and value_elem.text.strip():
        result["value"] = value_elem.text.strip()
    elif widget.attrib.get("Value"):
        result["value"] = widget.attrib["Value"]

    # <Value> may contain a ParsedExpression instead of plain text
    # (common for InlineExpression and Expression widgets)
    if value_elem is not None and not result.get("value"):
        pe = value_elem.find("ParsedExpression")
        if pe is not None:
            resolved = resolve_expression(pe, key_index)
            if resolved:
                result["value"] = resolved

    # Label — Button widgets store display text in <Label>, not <Value>
    label_elem = widget.find("Label")
    if label_elem is not None:
        pe = label_elem.find("ParsedExpression")
        if pe is not None:
            resolved = resolve_expression(pe, key_index)
            if resolved:
                result["label"] = resolved
        elif label_elem.text:
            result["label"] = label_elem.text

    # Title — Link widgets have a <Title> child for tooltip/accessibility text
    title_elem = widget.find("Title")
    if title_elem is not None:
        pe = title_elem.find("ParsedExpression")
        if pe is not None:
            resolved = resolve_expression(pe, key_index)
            if resolved:
                result["title"] = resolved
        elif title_elem.text:
            result["title"] = title_elem.text

    # Image source — WebWidgets.Image stores source in <URL>, <Filename>,
    # <EntityIdentifier> child elements with ParsedExpression bindings
    if short_type == "Image":
        for img_tag, result_key in (
            ("URL", "image_url"),
            ("Filename", "image_filename"),
            ("EntityIdentifier", "image_entity"),
        ):
            img_elem = widget.find(img_tag)
            if img_elem is not None:
                pe = img_elem.find("ParsedExpression")
                if pe is not None:
                    resolved = resolve_expression(pe, key_index)
                    if resolved:
                        result[result_key] = resolved
                elif img_elem.text:
                    result[result_key] = img_elem.text

        # ActualImage attribute — static image reference by GUID.
        # Formats: "Image:/Images.<GUID>" (local) or
        #          "ReferenceImage:/References.<REF>/ReferenceImages.<GUID>" (imported)
        actual_image = widget.attrib.get("ActualImage", "")
        if actual_image:
            result["actual_image"] = actual_image
            # Extract the image GUID for manifest lookup
            if "/Images." in actual_image:
                guid = actual_image.split("/Images.")[-1]
                result["actual_image_guid"] = guid
                # Local image — owning module is the current module (Gap 9)
                result["actual_image_module"] = doc.module_name
            elif "/ReferenceImages." in actual_image:
                guid = actual_image.split("/ReferenceImages.")[-1]
                result["actual_image_guid"] = guid
                # Resolve the reference-set key to a module name (Gap 9)
                # Format: /References.<REF_KEY>/ReferenceImages.<GUID>
                ref_part = actual_image.split("/References.")[-1].split("/")[0]
                ref_module = key_index.name(ref_part) or ""
                if ref_module:
                    result["actual_image_module"] = ref_module

        # NR Image widgets store image source in a CustomProperty named
        # "Image" with a Value attribute (not ActualImage).  Fall back to
        # CustomProperties only when the Traditional Web path above found
        # nothing, so Traditional Web extraction is never disturbed.
        if not result.get("actual_image"):
            cp_container = widget.find("CustomProperties")
            if cp_container is not None:
                for cp in cp_container.findall("CustomProperty"):
                    prop_name = cp.attrib.get("PropertyName", "")
                    if prop_name == "Image":
                        val = cp.attrib.get("Value", "")
                        if val:
                            result["actual_image"] = val
                            if "/Images." in val:
                                result["actual_image_guid"] = val.split("/Images.")[-1]
                                result["actual_image_module"] = doc.module_name
                            elif "/ReferenceImages." in val:
                                result["actual_image_guid"] = val.split("/ReferenceImages.")[-1]
                                ref_part = val.split("/References.")[-1].split("/")[0]
                                ref_module = key_index.name(ref_part) or ""
                                if ref_module:
                                    result["actual_image_module"] = ref_module
                    elif prop_name == "Url" and not result.get("image_url"):
                        ve = cp.find("ValueExpression")
                        if ve is not None:
                            pe = ve.find("ParsedExpression")
                            if pe is not None:
                                resolved = resolve_expression(pe, key_index)
                                if resolved:
                                    result["image_url"] = resolved
                            elif ve.text:
                                result["image_url"] = ve.text

    # Condition for If / IfBranch widgets
    if short_type in ("If", "IfBranch"):
        condition_elem = widget.find("Condition")
        if condition_elem is not None:
            pe = condition_elem.find("ParsedExpression")
            if pe is not None:
                result["condition"] = resolve_expression(pe, key_index)

    # --- Behavioral data (event handlers, properties, visibility) ---

    # EventHandlers (OnClick, OnChange, etc.)
    event_handlers = _extract_event_handlers(widget, key_index)
    if event_handlers:
        result["event_handlers"] = event_handlers

    # CustomProperties (Variable binding, Prompt, Enabled, etc.)
    custom_properties = _extract_custom_properties(widget, key_index)
    if custom_properties:
        result["properties"] = custom_properties

    standard_properties = _extract_widget_standard_properties(widget, key_index)
    if standard_properties:
        result.update(standard_properties)

    # Visible / Display conditions. Display is separate in OutSystems but has
    # the same runtime effect for descendants when it evaluates to False.
    display = _extract_display_condition(widget, key_index)
    visible = _extract_visible_condition(widget, key_index)
    effective_visibility_path = list(visibility_path or [])
    if display:
        result["display"] = display
        effective_visibility_path.append(
            _visibility_entry_with_role_gates({
                "kind": "display",
                "widget_key": key,
                "condition": display,
            })
        )
    if visible:
        result["visible"] = visible
        effective_visibility_path.append(
            _visibility_entry_with_role_gates({
                "kind": "visible",
                "widget_key": key,
                "condition": visible,
            })
        )
    if effective_visibility_path:
        result["visibility_path"] = effective_visibility_path
        role_gates = _role_gates_from_visibility_path(effective_visibility_path)
        if role_gates:
            result["role_gates"] = role_gates

    # Enabled condition. This is separate from visibility: it controls whether
    # the rendered widget can be activated, but it does not hide child widgets.
    enabled = _extract_enabled_condition(widget, key_index)
    if enabled:
        result["enabled"] = enabled
        enabled_path = [
            *effective_visibility_path,
            _visibility_entry_with_role_gates({
                "kind": "enabled",
                "widget_key": key,
                "condition": enabled,
            }),
        ]
        result["enabled_path"] = enabled_path
        role_gates = _merge_role_gates(
            result.get("role_gates", []) if isinstance(result.get("role_gates"), list) else [],
            _role_gates_from_visibility_path(enabled_path),
        )
        if role_gates:
            result["role_gates"] = role_gates

    # ExtendedProperties (CSS/HTML attributes)
    ext_props = _extract_extended_properties(widget, key_index)
    if ext_props:
        result["extended_properties"] = ext_props

    own_style_evidence = _style_evidence_for_widget(result)
    if own_style_evidence:
        result["style_evidence"] = own_style_evidence
        if own_style_evidence.get("style_classes"):
            result["style_classes"] = own_style_evidence["style_classes"]
        if own_style_evidence.get("css_effect_hints"):
            result["css_effect_hints"] = own_style_evidence["css_effect_hints"]

    effective_style_path = list(style_path or [])
    style_path_entry = _widget_style_path_entry(result)
    if style_path_entry:
        effective_style_path.append(style_path_entry)
    if effective_style_path:
        result["style_path"] = effective_style_path

    effective_layout_path = list(layout_path or [])
    layout_path_entry = _widget_layout_path_entry(result, depth=depth)
    if layout_path_entry:
        effective_layout_path.append(layout_path_entry)
    if effective_layout_path:
        result["layout_path"] = effective_layout_path

    # WebBlockInstance — capture source block, arguments, custom events
    if short_type == "WebBlockInstance":
        wb_details = _extract_webblock_details(widget, key_index)
        result.update(wb_details)

    # Placeholder — capture placeholder name/id
    if short_type in ("Placeholder", "CustomPlaceholderWidget"):
        pid = widget.attrib.get("PlaceholderId", widget.attrib.get("Id", ""))
        if pid:
            result["placeholder_id"] = pid
    if short_type == "PlaceholderArgument":
        placeholder_ref = widget.attrib.get("Placeholder", "")
        if placeholder_ref:
            result["placeholder_ref"] = placeholder_ref
            result["placeholder"] = key_index.resolve_ref(placeholder_ref)

    # Recursively extract children from known child containers
    children: list[dict[str, Any]] = []
    for container_tag in _WIDGET_CHILD_CONTAINERS:
        container = widget.find(container_tag)
        if container is not None:
            for index, child in enumerate(container):
                child_visibility_path = list(effective_visibility_path)
                child_type = _widget_type_name(child.tag)
                if short_type == "If" and child_type == "IfBranch" and result.get("condition"):
                    child_visibility_path.append(
                        _visibility_entry_with_role_gates({
                            "kind": "if_branch",
                            "node_key": key,
                            "condition": result["condition"],
                            "branch": _if_branch_label(child, index),
                        })
                    )
                children.append(
                    _extract_widget_tree(
                        child,
                        doc,
                        key_index,
                        tracker,
                        depth + 1,
                        child_visibility_path,
                        effective_style_path,
                        effective_layout_path,
                    )
                )

    # Also check for direct child widgets (some widgets nest directly)
    # without a wrapper container — iterate direct children that look like widgets
    for child in widget:
        if (child.tag.startswith("NRWebWidgets.") or child.tag.startswith("WebWidgets.")) and child.tag not in {
            c.tag for container_tag in _WIDGET_CHILD_CONTAINERS
            for c in (widget.find(container_tag) or [])
        }:
            # Avoid duplicates: only add if not already captured via containers
            child_key = child.attrib.get("Key", "")
            if not any(c.get("key") == child_key and child_key for c in children):
                children.append(
                    _extract_widget_tree(
                        child,
                        doc,
                        key_index,
                        tracker,
                        depth + 1,
                        effective_visibility_path,
                        effective_style_path,
                        effective_layout_path,
                    )
                )

    if short_type == "TableRecords":
        table_details = _extract_table_records_details(
            table_widget=widget,
            doc=doc,
            key_index=key_index,
            tracker=tracker,
            depth=depth + 1,
            visibility_path=effective_visibility_path,
            style_path=effective_style_path,
            layout_path=effective_layout_path,
        )
        if table_details:
            result["table_records"] = table_details

    if children:
        result["children"] = children

    return result


def _extract_widgets_from_node(
    node: ET.Element,
    doc: OmlDocument,
    key_index: KeyIndex,
    tracker: CoverageTracker,
) -> list[dict[str, Any]]:
    """Extract the top-level widget tree from a screen or block node.

    Widgets may be inline children of a <Widgets> element on the node,
    OR in a separate ``Widgets#<node_key>`` fragment (the OML XML splits
    large child collections into keyed fragments).
    """
    node_key = node.attrib.get("Key", "")
    tree: list[dict[str, Any]] = []

    # Strategy A: inline <Widgets> child
    widgets_elem = node.find("Widgets")
    if widgets_elem is not None and len(list(widgets_elem)) > 0:
        for child in widgets_elem:
            tree.append(_extract_widget_tree(child, doc, key_index, tracker))
        return tree

    # Strategy B: fragment "Widgets#<node_key>"
    if node_key:
        frag = doc.fragment(f"Widgets#{node_key}")
        if frag is not None:
            for child in frag:
                tree.append(_extract_widget_tree(child, doc, key_index, tracker))

    return tree


def _extract_table_expression(
    widget: ET.Element,
    child_name: str,
    key_index: KeyIndex,
) -> str:
    child = widget.find(child_name)
    if child is None:
        return ""
    pe = child.find("ParsedExpression")
    if pe is not None:
        return resolve_expression(pe, key_index)
    return (child.text or "").strip()


def _extract_table_cell_widgets(
    *,
    cell: ET.Element,
    doc: OmlDocument,
    key_index: KeyIndex,
    tracker: CoverageTracker,
    depth: int,
    visibility_path: list[dict[str, Any]],
    style_path: list[dict[str, Any]],
    layout_path: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    child_widgets = cell.find("ChildWidgets")
    if child_widgets is None:
        return widgets
    for child in child_widgets:
        widgets.append(
            _extract_widget_tree(
                child,
                doc,
                key_index,
                tracker,
                depth,
                list(visibility_path),
                list(style_path),
                list(layout_path),
            )
        )
    return widgets


def _first_widget_text_evidence(widgets: list[dict[str, Any]]) -> dict[str, str]:
    for widget in widgets:
        evidence = _widget_text_evidence(widget)
        if evidence:
            return evidence
    return {}


def _collect_widget_display_bindings(widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any]) -> None:
        for property_name in ("value", "label", "title", "image_url", "image_filename", "image_entity"):
            expression = _ui_binding_expression(str(widget.get(property_name, "")).strip())
            if expression and _is_dynamic_ui_expression(expression):
                bindings.append({
                    "widget_key": widget.get("key", ""),
                    "widget_name": widget.get("name", ""),
                    "widget_type": widget.get("type", ""),
                    "property": property_name,
                    "target": _primary_ui_binding_expression(expression),
                })
        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return bindings


def _collect_widget_action_summaries(widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any]) -> None:
        for handler in widget.get("event_handlers", []) or []:
            if not isinstance(handler, dict):
                continue
            action: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget.get("type", ""),
                "event": handler.get("event", ""),
                "destination": handler.get("destination", ""),
                "destination_ref": handler.get("destination_ref", ""),
            }
            _copy_label_evidence(action, _widget_text_evidence(widget))
            actions.append(action)
        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return actions


def _extract_table_records_details(
    *,
    table_widget: ET.Element,
    doc: OmlDocument,
    key_index: KeyIndex,
    tracker: CoverageTracker,
    depth: int,
    visibility_path: list[dict[str, Any]],
    style_path: list[dict[str, Any]],
    layout_path: list[dict[str, Any]],
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "source_record_list": _extract_table_expression(table_widget, "SourceRecordList", key_index),
        "empty_message": _extract_table_expression(table_widget, "EmptyMessage", key_index),
        "line_count": _extract_table_expression(table_widget, "LineCount", key_index),
        "start_index": _extract_table_expression(table_widget, "StartIndex", key_index),
        "show_header": table_widget.attrib.get("ShowHeader", ""),
        "header_style": table_widget.attrib.get("HeaderStyle", ""),
        "odd_line_style": table_widget.attrib.get("OddLineStyle", ""),
        "even_line_style": table_widget.attrib.get("EvenLineStyle", ""),
    }
    details = {key: value for key, value in details.items() if value}

    header_cells = table_widget.findall("./HeaderRow/*/Cells/*")
    data_cells = table_widget.findall("./DataRow/*/Cells/*")
    columns: list[dict[str, Any]] = []
    for index, header_cell in enumerate(header_cells):
        data_cell = data_cells[index] if index < len(data_cells) else None
        header_layout_path = [
            *layout_path,
            {
                "widget_key": header_cell.attrib.get("Key", ""),
                "widget_name": "",
                "widget_type": "TableRecordsHeaderCell",
                "depth": depth,
                "column_index": index,
            },
        ]
        header_widgets = _extract_table_cell_widgets(
            cell=header_cell,
            doc=doc,
            key_index=key_index,
            tracker=tracker,
            depth=depth + 1,
            visibility_path=visibility_path,
            style_path=style_path,
            layout_path=header_layout_path,
        )
        column: dict[str, Any] = {
            "index": index,
            "header_cell_key": header_cell.attrib.get("Key", ""),
        }
        _copy_label_evidence(column, _first_widget_text_evidence(header_widgets))
        header_actions = _collect_widget_action_summaries(header_widgets)
        if header_actions:
            column["header_actions"] = header_actions
        if data_cell is not None:
            data_layout_path = [
                *layout_path,
                {
                    "widget_key": data_cell.attrib.get("Key", ""),
                    "widget_name": "",
                    "widget_type": "TableRecordsDataCell",
                    "depth": depth,
                    "column_index": index,
                },
            ]
            data_widgets = _extract_table_cell_widgets(
                cell=data_cell,
                doc=doc,
                key_index=key_index,
                tracker=tracker,
                depth=depth + 1,
                visibility_path=visibility_path,
                style_path=style_path,
                layout_path=data_layout_path,
            )
            column["data_cell_key"] = data_cell.attrib.get("Key", "")
            display_bindings = _collect_widget_display_bindings(data_widgets)
            if display_bindings:
                column["display_bindings"] = display_bindings
            data_actions = _collect_widget_action_summaries(data_widgets)
            if data_actions:
                column["data_actions"] = data_actions
        columns.append(column)
    if columns:
        details["columns"] = columns
    return details


# ---------------------------------------------------------------------------
# Screen-level client action extraction
# ---------------------------------------------------------------------------

_SCREEN_ACTION_TAGS = (
    "NRFlows.ClientScreenActionFlow",
    "NRFlows.DataScreenActionFlow",
    "Flows.WebScreenAction",
)


def _extract_screen_client_actions(
    node: ET.Element,
    doc: OmlDocument,
    key_index: KeyIndex,
    type_resolver: TypeResolver,
) -> list[dict[str, Any]]:
    """Extract client actions defined on a screen or block element.

    Screen client actions live in a ``ClientActions`` container directly
    under the screen ``NRNodes.WebBlock`` element.  Data fetch actions
    live in a ``DataActions`` container.  Each is a
    ``NRFlows.ClientScreenActionFlow`` or ``NRFlows.DataScreenActionFlow``
    whose flow graph is stored in a
    ``NodesNotShownInESpaceTree#<action_key>`` fragment — the same pattern
    used by server actions.
    """
    actions: list[dict[str, Any]] = []
    node_key = node.attrib.get("Key", "")

    preparation = node.find("Preparation")
    if preparation is not None:
        for elem in preparation:
            if elem.tag != "Flows.WebPreparation":
                continue
            key = elem.attrib.get("Key", "") or f"{node_key}.#Preparation"
            params = _extract_logic_parameters(elem, type_resolver)
            local_vars = _extract_logic_local_variables(elem, type_resolver)
            flow = _extract_flow_graph(doc, key, key_index)

            actions.append({
                "key": key,
                "name": "Preparation",
                "container": "Preparation",
                "action_type": "preparation",
                "is_async": False,
                "input_parameters": params["inputs"],
                "output_parameters": params["outputs"],
                "local_variables": local_vars,
                "flow": flow,
            })

    for container_tag in ("ClientActions", "DataActions", "ScreenActions"):
        container = node.find(container_tag)
        if container is None:
            continue

        for elem in container:
            if elem.tag not in _SCREEN_ACTION_TAGS:
                continue

            key = elem.attrib.get("Key", "")
            name = elem.attrib.get("Name", "")
            if container_tag == "DataActions":
                action_type = "data_fetch"
            else:
                action_type = "client"

            params = _extract_logic_parameters(elem, type_resolver)
            local_vars = _extract_logic_local_variables(elem, type_resolver)
            flow = _extract_flow_graph(doc, key, key_index)

            actions.append({
                "key": key,
                "name": name,
                "container": container_tag,
                "action_type": action_type,
                "is_async": elem.attrib.get("IsAsync", "No") == "Yes",
                "input_parameters": params["inputs"],
                "output_parameters": params["outputs"],
                "local_variables": local_vars,
                "flow": flow,
            })

    return actions


# ---------------------------------------------------------------------------
# Screen / Block extraction
# ---------------------------------------------------------------------------

def _extract_custom_events(node: ET.Element) -> list[dict[str, str]]:
    """Extract WebBlockCustomEvent definitions from a block's CustomEvents container.

    These represent the parent-child communication contract — events like
    ``IsDismiss``, ``OTPPassed``, ``LoginSuccessful`` that a block can trigger
    and a parent screen can handle.
    """
    events: list[dict[str, str]] = []
    container = node.find("CustomEvents")
    if container is None:
        return events
    for child in container:
        if child.tag.endswith("WebBlockCustomEvent"):
            events.append({
                "key": child.attrib.get("Key", ""),
                "name": child.attrib.get("Name", ""),
            })
    return events


def _has_webblock_custom_event(node: ET.Element) -> bool:
    """Return True when a WebBlock defines parent-facing custom events."""
    container = node.find("CustomEvents")
    if container is None:
        return False
    return any(child.tag.endswith("WebBlockCustomEvent") for child in container)


def _fragment_node_keys(
    doc: OmlDocument,
    fragment_names: tuple[str, ...],
    tag_suffix: str,
) -> set[str]:
    """Collect local node keys from known screen/block origin fragments."""
    keys: set[str] = set()
    for fragment_name in fragment_names:
        frag = doc.fragment(fragment_name)
        if frag is None:
            continue
        for child in frag.iter():
            key = child.attrib.get("Key", "")
            if key and child.tag.endswith(tag_suffix) and "Reference" not in child.tag:
                keys.add(key)
    return keys


def _origin_matches(origin: str, fragment_names: tuple[str, ...]) -> bool:
    """Match an origin label or keyed fragment label such as WebBlocks#flow."""
    return origin in fragment_names or origin.partition("#")[0] in fragment_names


def _classify_screen_or_block(
    node: ET.Element,
    origin: str,
    block_fragment_keys: set[str],
    screen_fragment_keys: set[str],
) -> str | None:
    """Classify an interface node as screen, block, or email_screen."""
    tag = node.tag

    if tag.endswith("WebScreen"):
        return "screen"
    if tag.endswith("EmailScreen"):
        return "email_screen"
    if not tag.endswith("WebBlock"):
        return None

    name = node.attrib.get("Name", "")
    key = node.attrib.get("Key", "")

    if _has_webblock_custom_event(node):
        return "block"
    if name.endswith("_WB"):
        return "block"
    if _origin_matches(origin, ("WebBlocks", "NRWebBlocks")):
        return "block"
    if key and key in block_fragment_keys:
        return "block"
    if _origin_matches(origin, ("WebScreens", "NRWebScreens")):
        return "screen"
    if key and key in screen_fragment_keys:
        return "screen"

    # The XML generated for current Traditional Web and Reactive modules stores
    # local reusable blocks as Nodes.WebBlock under NodesShownInESpaceTree.
    return "block"


def _extract_screen_or_block(
    node: ET.Element,
    kind: str,
    flow_name: str,
    doc: OmlDocument,
    key_index: KeyIndex,
    tracker: CoverageTracker,
    type_resolver: TypeResolver | None = None,
) -> dict[str, Any]:
    """Extract a screen or block node into a spec dict."""
    name = node.attrib.get("Name", "")
    key = node.attrib.get("Key", "")

    tracker.capture(f"{kind}:{name}", path=f"flow={flow_name}")

    entry: dict[str, Any] = {
        "name": name,
        "key": key,
        "kind": kind,
        "flow": flow_name,
        "tag": node.tag,
        "is_public": node.attrib.get("Public", "No") == "Yes",
    }

    # Description
    desc = node.attrib.get("Description", "")
    if desc:
        entry["description"] = desc

    # Input / Output / Local
    entry["input_parameters"] = _extract_parameters(node, "InputParameters", type_resolver)
    entry["output_parameters"] = _extract_parameters(node, "OutputParameters", type_resolver)
    entry["local_variables"] = _extract_parameters(node, "LocalVariables", type_resolver)

    # Events
    entry["events"] = _extract_events(node)

    # Screen data sets
    entry["screen_data_sets"] = _extract_screen_data_sets(node)

    # Widget tree
    entry["widgets"] = _extract_widgets_from_node(node, doc, key_index, tracker)

    # Screen-level client actions (the behavior behind button clicks)
    if type_resolver is not None:
        client_actions = _extract_screen_client_actions(
            node, doc, key_index, type_resolver,
        )
        if client_actions:
            entry["client_actions"] = client_actions

    # Custom events defined on this block (parent-child communication contract)
    custom_events = _extract_custom_events(node)
    if custom_events:
        entry["custom_events"] = custom_events

    return entry


def _screen_action_kind(action: dict[str, Any]) -> str:
    """Classify a screen action in UI dataflow terms."""
    name = str(action.get("name", "")).lower()
    container = str(action.get("container", ""))
    if name == "preparation":
        return "preparation"
    if container == "DataActions":
        return "data_fetch"
    if container == "ScreenActions":
        return "screen_action"
    return str(action.get("action_type") or "client")


def _expression_leaf(expression: str) -> str:
    """Return a readable field/action leaf from a resolved expression."""
    expression = expression.strip()
    if not expression:
        return ""
    segment = expression.split(".")[-1]
    # Keep the useful identifier when the leaf is wrapped in calls/indexers.
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", segment)
    return match.group(1) if match else segment


def _expression_root(expression: str) -> str:
    expression = expression.strip()
    if not expression:
        return ""
    segment = expression.split(".", 1)[0]
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", segment)
    return match.group(1) if match else segment


def _action_ref_leaf(action_ref: str) -> str:
    action_ref = action_ref.strip()
    if not action_ref:
        return ""
    return action_ref.split(".")[-1]


def _normalize_assignment_expression(
    expression: str,
    execute_action_names_by_node: dict[str, str],
) -> str:
    """Collapse resolved OutSystems paths into UI-build-friendly expressions."""
    expression = expression.strip()
    if not expression:
        return ""

    local_match = re.search(
        r"(?:^|\.)LocalVariables\.([A-Za-z_][A-Za-z0-9_]*).*?\.Attributes\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if local_match:
        return f"{local_match.group(1)}.{local_match.group(2)}"

    show_record_attr = re.search(
        r"(?:^|\.)Record\.Attributes\.[^.]+\.References\.[^.]+"
        r"\.ReferenceStructures\.([A-Za-z_][A-Za-z0-9_]*)"
        r"\.Attributes\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if show_record_attr:
        return f"{show_record_attr.group(1)}.{show_record_attr.group(2)}"

    local_direct = re.search(
        r"(?:^|\.)LocalVariables\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)$",
        expression,
    )
    if local_direct:
        return local_direct.group(1)

    output_attr = re.search(
        r"NodesNotShownInESpaceTree\.([^.]+)\.OutputParameters\.([A-Za-z_][A-Za-z0-9_]*).*?\.Attributes\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if output_attr:
        node_key = output_attr.group(1)
        action_name = execute_action_names_by_node.get(node_key) or node_key
        return f"{action_name}.{output_attr.group(2)}.{output_attr.group(3)}"

    reference_action_output_attr = re.search(
        r"NodesNotShownInESpaceTree\.([^.]+)\..*?ReferenceActions\.([A-Za-z_][A-Za-z0-9_]*)"
        r"\.OutputParameters\.([A-Za-z_][A-Za-z0-9_]*).*?\.Attributes\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if reference_action_output_attr:
        node_key = reference_action_output_attr.group(1)
        action_name = (
            execute_action_names_by_node.get(node_key)
            or reference_action_output_attr.group(2)
        )
        return (
            f"{action_name}.{reference_action_output_attr.group(3)}."
            f"{reference_action_output_attr.group(4)}"
        )

    output_direct = re.search(
        r"NodesNotShownInESpaceTree\.([^.]+)\.OutputParameters\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if output_direct:
        node_key = output_direct.group(1)
        action_name = execute_action_names_by_node.get(node_key) or node_key
        return f"{action_name}.{output_direct.group(2)}"

    reference_action_output_direct = re.search(
        r"NodesNotShownInESpaceTree\.([^.]+)\..*?ReferenceActions\.([A-Za-z_][A-Za-z0-9_]*)"
        r"\.OutputParameters\.([A-Za-z_][A-Za-z0-9_]*)$",
        expression,
    )
    if reference_action_output_direct:
        node_key = reference_action_output_direct.group(1)
        action_name = (
            execute_action_names_by_node.get(node_key)
            or reference_action_output_direct.group(2)
        )
        return f"{action_name}.{reference_action_output_direct.group(3)}"

    return expression


def _assignment_field_binding(
    *,
    flow: str,
    screen_name: str,
    action: dict[str, Any],
    node: dict[str, Any],
    assignment: dict[str, str],
    execute_action_names_by_node: dict[str, str],
    control_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_target = str(assignment.get("variable", "")).strip()
    raw_source = str(assignment.get("value", "")).strip()
    target = _normalize_assignment_expression(raw_target, execute_action_names_by_node)
    source = _normalize_assignment_expression(raw_source, execute_action_names_by_node)
    action_kind = _screen_action_kind(action)
    binding_role = "data_preparation" if action_kind in ("preparation", "data_fetch") else "submit_payload"
    binding = {
        "field": _expression_leaf(target),
        "target": target,
        "target_scope": target.rsplit(".", 1)[0] if "." in target else "",
        "source": source,
        "source_action": _expression_root(source),
        "source_field": _expression_leaf(source),
        "action_name": action.get("name", ""),
        "action_key": action.get("key", ""),
        "action_kind": action_kind,
        "binding_role": binding_role,
        "rendering_hint": (
            "data_source_verify_with_ui_binding"
            if binding_role == "data_preparation"
            else "do_not_render_without_widget_or_input_binding"
        ),
        "node_key": node.get("key", ""),
        "evidence_path": ".".join(
            part for part in (flow, screen_name, action.get("name", ""), node.get("key", ""))
            if part
        ),
    }
    if raw_target != target:
        binding["raw_target"] = raw_target
    if raw_source != source:
        binding["raw_source"] = raw_source
    if control_context:
        if control_context.get("control_path"):
            binding["control_path"] = control_context["control_path"]
        if control_context.get("control_paths"):
            binding["control_paths"] = control_context["control_paths"]
        role_gates = control_context.get("role_gates") or []
        if role_gates:
            binding["role_gates"] = role_gates
    return binding


def _is_dynamic_ui_expression(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value.startswith('"') and value.endswith('"'):
        return False
    if re.search(
        r"\b(LocalVariables|InputParameters|OutputParameters|Session|WebFlows|"
        r"NodesShownInESpaceTree|NodesNotShownInESpaceTree|Widgets|ChildWidgets)\.",
        value,
    ):
        return True
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$", value))


def _primary_ui_binding_expression(value: str) -> str:
    """Return the source path rendered by a UI expression, even when wrapped."""
    value = value.strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_+\-]+)+$", value):
        return value

    roots = {
        "LocalVariables",
        "InputParameters",
        "OutputParameters",
        "Session",
        "WebFlows",
        "NodesShownInESpaceTree",
        "NodesNotShownInESpaceTree",
        "Widgets",
        "ChildWidgets",
    }
    for match in re.finditer(
        r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_+\-]+)+)",
        value,
    ):
        path = match.group(1)
        if _expression_root(path) in roots:
            return path
    return value


def _ui_binding_expression(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


def _static_widget_text(value: Any) -> str:
    expression = _ui_binding_expression(str(value or "").strip())
    if not expression or _is_dynamic_ui_expression(expression):
        return ""
    return expression


def _widget_text_evidence_from_value(value: Any) -> dict[str, str]:
    expression = _ui_binding_expression(str(value or "").strip())
    if not expression:
        return {}
    if _is_dynamic_ui_expression(expression) or re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*\s*\(",
        expression,
    ):
        return {"label_expression": expression}
    return {"label": expression}


def _copy_label_evidence(record: dict[str, Any], evidence: dict[str, str] | None) -> None:
    if not evidence:
        return
    label = evidence.get("label", "").strip()
    if label:
        record["label"] = label
    label_expression = evidence.get("label_expression", "").strip()
    if label_expression:
        record["label_expression"] = label_expression


def _copy_text_hint_evidence(record: dict[str, Any], widget: dict[str, Any]) -> None:
    for prop in widget.get("extended_properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        if name not in ("title", "placeholder", "aria-label"):
            continue
        evidence = _widget_text_evidence_from_value(prop.get("value", ""))
        if evidence.get("label"):
            record[name.replace("-", "_")] = evidence["label"]
        elif evidence.get("label_expression"):
            record[f"{name.replace('-', '_')}_expression"] = evidence["label_expression"]

    for prop in widget.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        if name not in ("prompt", "placeholder", "title"):
            continue
        evidence = _widget_text_evidence_from_value(prop.get("value", ""))
        if evidence.get("label"):
            record[name] = evidence["label"]
        elif evidence.get("label_expression"):
            record[f"{name}_expression"] = evidence["label_expression"]


def _widget_own_static_text(widget: dict[str, Any]) -> str:
    for property_name in ("label", "title", "value"):
        text = _static_widget_text(widget.get(property_name, ""))
        if text:
            return text
    for prop in widget.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        if name not in ("label", "title", "text", "value"):
            continue
        text = _static_widget_text(prop.get("value", ""))
        if text:
            return text
    return ""


def _widget_own_text_evidence(widget: dict[str, Any]) -> dict[str, str]:
    for property_name in ("label", "title", "value"):
        evidence = _widget_text_evidence_from_value(widget.get(property_name, ""))
        if evidence:
            return evidence
    for prop in widget.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        if name not in ("label", "title", "text", "value"):
            continue
        evidence = _widget_text_evidence_from_value(prop.get("value", ""))
        if evidence:
            return evidence
    return {}


def _widget_static_text(widget: dict[str, Any]) -> str:
    text = _widget_own_static_text(widget)
    if text:
        return text

    for child in widget.get("children", []) or []:
        if isinstance(child, dict):
            text = _widget_static_text(child)
            if text:
                return text
    for child in widget.get("widgets", []) or []:
        if isinstance(child, dict):
            text = _widget_static_text(child)
            if text:
                return text
    return ""


def _widget_text_evidence(widget: dict[str, Any]) -> dict[str, str]:
    evidence = _widget_own_text_evidence(widget)
    if evidence:
        return evidence

    for child in widget.get("children", []) or []:
        if isinstance(child, dict):
            evidence = _widget_text_evidence(child)
            if evidence:
                return evidence
    for child in widget.get("widgets", []) or []:
        if isinstance(child, dict):
            evidence = _widget_text_evidence(child)
            if evidence:
                return evidence
    return {}


def _widget_sibling_label(widget: dict[str, Any]) -> str:
    if str(widget.get("type", "")) in ("Label", "Text"):
        return _widget_static_text(widget)
    return ""


def _widget_sibling_label_evidence(widget: dict[str, Any]) -> dict[str, str]:
    if str(widget.get("type", "")) in ("Label", "Text"):
        return _widget_text_evidence(widget)
    return {}


def _append_source_field(source_root: str, target_expression: str) -> str:
    if "." not in target_expression:
        return source_root
    return f"{source_root}.{target_expression.split('.', 1)[1]}"


def _resolve_ui_binding_source(
    *,
    expression: str,
    object_copies: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    root = _expression_root(expression)
    via_assignment = object_copies.get(root)
    if not via_assignment:
        return expression, None
    return _append_source_field(str(via_assignment.get("source", "")), expression), via_assignment


def _widget_evidence_path(
    *,
    flow: str,
    screen_name: str,
    widget: dict[str, Any],
    property_name: str,
) -> str:
    return ".".join(
        part for part in (
            flow,
            screen_name,
            "Widgets",
            str(widget.get("key") or widget.get("name") or widget.get("type") or ""),
            property_name,
        )
        if part
    )


def _copy_widget_rendering_context(
    record: dict[str, Any],
    widget: dict[str, Any],
    *,
    include_enabled: bool = True,
) -> None:
    visibility_path = widget.get("visibility_path")
    if isinstance(visibility_path, list) and visibility_path:
        record["visibility_path"] = visibility_path

    if include_enabled:
        enabled = widget.get("enabled")
        if enabled:
            record["enabled"] = enabled
        enabled_path = widget.get("enabled_path")
        if isinstance(enabled_path, list) and enabled_path:
            record["enabled_path"] = enabled_path

    style_path = widget.get("style_path")
    if isinstance(style_path, list) and style_path:
        record["style_path"] = style_path
    style_evidence = widget.get("style_evidence")
    if isinstance(style_evidence, dict) and style_evidence:
        record["style_evidence"] = style_evidence

    layout_path = widget.get("layout_path")
    if isinstance(layout_path, list) and layout_path:
        record["layout_path"] = layout_path
        layout_group = _layout_group_from_path(layout_path)
        if layout_group:
            record["layout_group"] = layout_group
    layout_props = widget.get("layout_props")
    if isinstance(layout_props, dict) and layout_props:
        record["layout_props"] = layout_props

    role_gates = widget.get("role_gates")
    if isinstance(role_gates, list) and role_gates:
        record["role_gates"] = role_gates

    for key in ("semantic_type", "semantic_role", "semantic_source"):
        value = widget.get(key)
        if value:
            record[key] = value


def _extract_widget_ui_bindings(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
    object_copies: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ui_bindings: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], label_hint: dict[str, str] | None = None) -> None:
        candidate_props: list[tuple[str, str]] = []

        for property_name in (
            "value",
            "label",
            "title",
            "image_url",
            "image_filename",
            "image_entity",
        ):
            raw_value = str(widget.get(property_name, "")).strip()
            if raw_value:
                candidate_props.append((property_name, raw_value))

        for prop in widget.get("properties", []) or []:
            if isinstance(prop, dict):
                raw_value = str(prop.get("value", "")).strip()
                if raw_value:
                    candidate_props.append((f"properties.{prop.get('name', '')}", raw_value))

        for property_name, raw_value in candidate_props:
            expression = _ui_binding_expression(raw_value)
            if _is_dynamic_ui_expression(expression):
                binding_expression = _primary_ui_binding_expression(expression)
                normalized_expression = _normalize_assignment_expression(binding_expression, {})
                resolved_source, via_assignment = _resolve_ui_binding_source(
                    expression=normalized_expression,
                    object_copies=object_copies,
                )
                binding: dict[str, Any] = {
                    "field": _expression_leaf(normalized_expression),
                    "target": normalized_expression,
                    "source": resolved_source,
                    "source_action": _expression_root(resolved_source),
                    "source_field": _expression_leaf(resolved_source),
                    "widget_key": widget.get("key", ""),
                    "widget_name": widget.get("name", ""),
                    "widget_type": widget.get("type", ""),
                    "property": property_name,
                    "evidence_path": _widget_evidence_path(
                        flow=flow,
                        screen_name=screen_name,
                        widget=widget,
                        property_name=property_name,
                    ),
                }
                own_label_evidence = (
                    _widget_own_text_evidence(widget)
                    if str(widget.get("type", "")) in ("Label", "Text")
                    else {}
                )
                _copy_label_evidence(binding, label_hint or own_label_evidence)
                if via_assignment:
                    binding["via_assignment"] = {
                        "target": via_assignment.get("target", ""),
                        "source": via_assignment.get("source", ""),
                        "action_name": via_assignment.get("action_name", ""),
                        "action_kind": via_assignment.get("action_kind", ""),
                        "evidence_path": via_assignment.get("evidence_path", ""),
                    }
                if raw_value != normalized_expression:
                    binding["raw_expression"] = raw_value
                _copy_widget_rendering_context(binding, widget)
                ui_bindings.append(binding)

        for children_key in ("children", "widgets"):
            preceding_label: dict[str, str] = {}
            for child in widget.get(children_key, []) or []:
                if not isinstance(child, dict):
                    continue
                visit(child, preceding_label)
                sibling_label = _widget_sibling_label_evidence(child)
                if sibling_label:
                    preceding_label = sibling_label
                elif str(child.get("type", "")) not in ("Label", "Text"):
                    preceding_label = {}

    preceding_label: dict[str, str] = {}
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        visit(widget, preceding_label)
        sibling_label = _widget_sibling_label_evidence(widget)
        if sibling_label:
            preceding_label = sibling_label
        elif str(widget.get("type", "")) not in ("Label", "Text"):
            preceding_label = {}

    return ui_bindings


def _control_path_signature(path: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(entry.get("node_key", "")),
            str(entry.get("branch", "")),
            str(entry.get("condition", "")),
        )
        for entry in path
    )


_INPUT_WIDGET_TYPES = {
    "CheckBox",
    "Checkbox",
    "ComboBox",
    "DatePicker",
    "Dropdown",
    "Input",
    "InputPassword",
    "RadioButton",
    "Select",
    "Switch",
    "TextArea",
    "Toggle",
}

_INPUT_BINDING_PROPERTY_NAMES = {
    "checked",
    "inputvariable",
    "selectedvalue",
    "selection",
    "value",
    "variable",
}


_STANDARD_INPUT_PROPERTIES = {
    "variable": "Variable",
    "source_record_list": "SourceRecordList",
    "special_variable": "SpecialVariable",
    "mandatory": "Mandatory",
    "null_value": "NullValue",
}


def _input_widget_properties(widget: dict[str, Any]) -> list[dict[str, Any]]:
    properties = [
        prop
        for prop in widget.get("properties", []) or []
        if isinstance(prop, dict)
    ]
    seen = {
        str(prop.get("name", "")).strip().lower()
        for prop in properties
        if isinstance(prop, dict)
    }
    for field_name, property_name in _STANDARD_INPUT_PROPERTIES.items():
        value = str(widget.get(field_name, "")).strip()
        if value and property_name.lower() not in seen:
            properties.append({"name": property_name, "value": value})
            seen.add(property_name.lower())
    return properties


def _option_source_for_widget(widget: dict[str, Any]) -> dict[str, Any]:
    option_source: dict[str, Any] = {}
    if widget.get("source_record_list"):
        option_source.update({
            "kind": "source_record_list",
            "source_record_list": widget.get("source_record_list", ""),
            "option_rows_gap": (
                "SourceRecordList identifies the runtime option list, "
                "but concrete option rows were not available in this module package."
            ),
        })
    if isinstance(widget.get("source_attributes"), dict):
        option_source.setdefault("kind", "source_attributes")
        option_source["source_attributes"] = widget["source_attributes"]

    custom_options: dict[str, str] = {}
    for prop in widget.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip()
        value = str(prop.get("value", "")).strip()
        if not value:
            continue
        normalized = name.lower()
        if normalized in {"list", "values", "emptyvalue"}:
            custom_options[normalized] = value
    if custom_options:
        option_source.setdefault("kind", "custom_properties")
        option_source["custom_properties"] = custom_options
        if "list" in custom_options and "source_record_list" not in option_source:
            option_source["source_record_list"] = custom_options["list"]
        if "values" in custom_options:
            option_source["values_expression"] = custom_options["values"]
        if "emptyvalue" in custom_options:
            option_source["empty_value"] = custom_options["emptyvalue"]

    return option_source


def _extract_widget_input_bindings(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    input_bindings: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], label_hint: dict[str, str] | None = None) -> None:
        widget_type = str(widget.get("type", ""))
        if widget_type in _INPUT_WIDGET_TYPES:
            record: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget_type,
                "input_kind": widget_type,
                "evidence_path": _widget_evidence_path(
                    flow=flow,
                    screen_name=screen_name,
                    widget=widget,
                    property_name="input",
                ),
            }
            _copy_label_evidence(record, _widget_own_text_evidence(widget) or label_hint)
            _copy_text_hint_evidence(record, widget)
            option_source = _option_source_for_widget(widget)
            if option_source:
                record["option_source"] = option_source
            if isinstance(widget.get("special_list"), list) and widget.get("special_list"):
                record["special_list"] = widget["special_list"]
            properties = _input_widget_properties(widget)
            if properties:
                record["properties"] = properties
                bindings = []
                for prop in properties:
                    name = str(prop.get("name", "")).strip()
                    value = str(prop.get("value", "")).strip()
                    if name.lower() in _INPUT_BINDING_PROPERTY_NAMES and value:
                        bindings.append({
                            "property": name,
                            "target": _normalize_assignment_expression(value, {}),
                        })
                if bindings:
                    record["bindings"] = bindings
            event_handlers = [
                handler
                for handler in widget.get("event_handlers", []) or []
                if isinstance(handler, dict)
            ]
            if event_handlers:
                record["event_handlers"] = event_handlers
            _copy_widget_rendering_context(record, widget)
            input_bindings.append(record)

        for children_key in ("children", "widgets"):
            preceding_label: dict[str, str] = {}
            for child in widget.get(children_key, []) or []:
                if not isinstance(child, dict):
                    continue
                visit(child, preceding_label)
                sibling_label = _widget_sibling_label_evidence(child)
                if sibling_label:
                    preceding_label = sibling_label
                elif str(child.get("type", "")) not in ("Label", "Text"):
                    preceding_label = {}

    preceding_label: dict[str, str] = {}
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        visit(widget, preceding_label)
        sibling_label = _widget_sibling_label_evidence(widget)
        if sibling_label:
            preceding_label = sibling_label
        elif str(widget.get("type", "")) not in ("Label", "Text"):
            preceding_label = {}

    return input_bindings


def _branch_entry_for_edge(
    *,
    node: dict[str, Any],
    edge: dict[str, Any],
) -> dict[str, Any] | None:
    node_type = str(node.get("type", ""))
    edge_type = str(edge.get("type", "")).strip()
    edge_type_lower = edge_type.lower()

    if node_type == "Nodes.If" and edge_type_lower in ("true", "false"):
        entry = {
            "node_key": node.get("key", ""),
            "node_type": node_type,
            "condition": node.get("condition", ""),
            "branch": edge_type_lower,
        }
        return _visibility_entry_with_role_gates(entry)

    if node_type == "Nodes.Switch":
        entry = {
            "node_key": node.get("key", ""),
            "node_type": node_type,
            "condition": node.get("condition", ""),
            "branch": edge_type_lower or "case",
        }
        if node.get("switch_conditions"):
            entry["switch_conditions"] = node.get("switch_conditions")
        return _visibility_entry_with_role_gates(entry)

    return None


def _build_node_control_contexts(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return branch-control evidence for flow nodes keyed by node key."""
    node_by_key = {str(node.get("key", "")): node for node in nodes if node.get("key")}
    incoming: dict[str, list[dict[str, Any]]] = {key: [] for key in node_by_key}
    outgoing: dict[str, list[dict[str, Any]]] = {key: [] for key in node_by_key}

    for edge in edges:
        from_key = str(edge.get("from_key", ""))
        to_key = str(edge.get("to_key", ""))
        if to_key in incoming:
            incoming[to_key].append(edge)
        if from_key in outgoing:
            outgoing[from_key].append(edge)

    starts = [
        key for key, node in node_by_key.items()
        if str(node.get("type", "")) in ("Nodes.Start", "Nodes.StartIteration")
    ]
    if not starts:
        starts = [key for key in node_by_key if not incoming.get(key)]

    queue: list[tuple[str, list[dict[str, Any]]]] = [(key, []) for key in starts]
    path_by_node: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()

    while queue:
        node_key, path = queue.pop(0)
        if node_key in seen:
            continue
        seen.add(node_key)
        path_by_node[node_key] = path

        node = node_by_key.get(node_key, {})
        for edge in outgoing.get(node_key, []):
            to_key = str(edge.get("to_key", ""))
            if not to_key:
                continue
            next_path = list(path)
            branch_entry = _branch_entry_for_edge(node=node, edge=edge)
            if branch_entry:
                next_path.append(branch_entry)
            elif len(incoming.get(to_key, [])) > 1:
                # Once branches merge, downstream nodes are no longer guarded by
                # a single branch condition. Drop inherited branch context to
                # avoid false-positive control evidence and keep traversal O(N).
                next_path = []
            queue.append((to_key, next_path))

    contexts: dict[str, dict[str, Any]] = {}
    for node_key, path in path_by_node.items():
        if not path:
            continue
        context: dict[str, Any] = {}
        context["control_path"] = path
        role_gates = _role_gates_from_visibility_path(path)
        if role_gates:
            context["role_gates"] = role_gates
        contexts[node_key] = context

    return contexts


def _copy_control_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    copied: dict[str, Any] = {}
    for key in ("control_path", "control_paths", "role_gates"):
        value = context.get(key)
        if value:
            copied[key] = value
    return copied


def _extract_widget_actions(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    widget_actions: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], label_hint: dict[str, str] | None = None) -> None:
        for handler in widget.get("event_handlers", []) or []:
            if not isinstance(handler, dict):
                continue
            event = str(handler.get("event", ""))
            record: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget.get("type", ""),
                "event": event,
                "destination": handler.get("destination", ""),
                "destination_ref": handler.get("destination_ref", ""),
                "evidence_path": _widget_evidence_path(
                    flow=flow,
                    screen_name=screen_name,
                    widget=widget,
                    property_name=event,
                ),
            }
            _copy_label_evidence(record, _widget_text_evidence(widget) or label_hint)
            _copy_widget_rendering_context(record, widget)
            role_gates = _merge_role_gates(
                widget.get("role_gates", []) if isinstance(widget.get("role_gates"), list) else [],
                _role_gates_from_expression(str(handler.get("destination", ""))),
                _role_gates_from_expression(str(handler.get("destination_ref", ""))),
            )
            if role_gates:
                record["role_gates"] = role_gates
            widget_actions.append(record)

        for children_key in ("children", "widgets"):
            preceding_label: dict[str, str] = {}
            for child in widget.get(children_key, []) or []:
                if not isinstance(child, dict):
                    continue
                visit(child, preceding_label)
                sibling_label = _widget_sibling_label_evidence(child)
                if sibling_label:
                    preceding_label = sibling_label
                elif str(child.get("type", "")) not in ("Label", "Text"):
                    preceding_label = {}

    preceding_label: dict[str, str] = {}
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        visit(widget, preceding_label)
        sibling_label = _widget_sibling_label_evidence(widget)
        if sibling_label:
            preceding_label = sibling_label
        elif str(widget.get("type", "")) not in ("Label", "Text"):
            preceding_label = {}

    return widget_actions


def _extract_widget_tables(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []

    def headers_from_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        headers: list[dict[str, Any]] = []
        for column in columns:
            if not isinstance(column, dict):
                continue
            header: dict[str, Any] = {
                "index": column.get("index"),
                "header_cell_key": column.get("header_cell_key", ""),
            }
            _copy_label_evidence(header, {
                key: str(column.get(key, ""))
                for key in ("label", "label_expression")
                if column.get(key)
            })
            header_actions = column.get("header_actions")
            if isinstance(header_actions, list) and header_actions:
                header["header_actions"] = header_actions
            header = {
                key: value
                for key, value in header.items()
                if value not in ("", None, [])
            }
            if header:
                headers.append(header)
        return headers

    def visit(widget: dict[str, Any]) -> None:
        table_records = widget.get("table_records")
        if str(widget.get("type", "")) == "TableRecords" and isinstance(table_records, dict):
            columns = table_records.get("columns", [])
            table: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget.get("type", ""),
                "evidence_path": _widget_evidence_path(
                    flow=flow,
                    screen_name=screen_name,
                    widget=widget,
                    property_name="table_records",
                ),
                "source_record_list": table_records.get("source_record_list", ""),
                "empty_message": table_records.get("empty_message", ""),
                "line_count": table_records.get("line_count", ""),
                "start_index": table_records.get("start_index", ""),
                "show_header": table_records.get("show_header", ""),
                "header_style": table_records.get("header_style", ""),
                "odd_line_style": table_records.get("odd_line_style", ""),
                "even_line_style": table_records.get("even_line_style", ""),
                "headers": headers_from_columns(columns) if isinstance(columns, list) else [],
                "columns": columns,
            }
            table = {
                key: value
                for key, value in table.items()
                if value != "" and value != []
            }
            _copy_widget_rendering_context(table, widget)
            tables.append(table)

        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)

    return tables


def _dialog_kind_for_widget(widget: dict[str, Any]) -> str:
    source_block = str(widget.get("source_block", ""))
    widget_type = str(widget.get("type", ""))
    name = str(widget.get("name", ""))
    style = str(widget.get("style", ""))
    classes = " ".join(str(item) for item in widget.get("style_classes", []) or [])
    evidence = " ".join((source_block, name, style, classes)).lower()

    if widget_type == "WebBlockInstance":
        if "referencewebblocks.modal" in source_block.lower():
            return "modal_webblock"
        if "referencewebblocks.layoutpopup" in source_block.lower():
            return "layout_popup"
        if "referencewebblocks.popup_editor" in source_block.lower():
            return "popup_editor"
    if widget_type == "Container" and re.search(r"\b(modal|popup|dialog)\b", evidence):
        return "modal_container"
    return ""


def _extract_widget_dialogs(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dialogs: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], inside_dialog: bool = False) -> None:
        dialog_kind = _dialog_kind_for_widget(widget)
        is_dialog = bool(dialog_kind)
        if is_dialog and not inside_dialog:
            dialog: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget.get("type", ""),
                "dialog_kind": dialog_kind,
                "source_block": widget.get("source_block", ""),
                "evidence_path": _widget_evidence_path(
                    flow=flow,
                    screen_name=screen_name,
                    widget=widget,
                    property_name="dialog",
                ),
            }
            _copy_label_evidence(dialog, _widget_text_evidence(widget))
            inputs = _extract_widget_input_bindings(
                flow=flow,
                screen_name=screen_name,
                widgets=[widget],
            )
            if inputs:
                dialog["input_bindings"] = inputs
            actions = _extract_widget_actions(
                flow=flow,
                screen_name=screen_name,
                widgets=[widget],
            )
            if actions:
                dialog["widget_actions"] = actions
            tables = _extract_widget_tables(
                flow=flow,
                screen_name=screen_name,
                widgets=[widget],
            )
            if tables:
                dialog["tables"] = tables
            _copy_widget_rendering_context(dialog, widget)
            dialogs.append({
                key: value
                for key, value in dialog.items()
                if value != "" and value != []
            })

        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child, inside_dialog or is_dialog)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child, inside_dialog or is_dialog)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)

    return dialogs


def _widget_descriptor_text(widget: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            widget.get("name", ""),
            widget.get("style", ""),
            " ".join(str(item) for item in widget.get("style_classes", []) or []),
            widget.get("source_block", ""),
        )
        if value
    ).lower()


def _is_filter_container(widget: dict[str, Any]) -> bool:
    if str(widget.get("type", "")) != "Container":
        return False
    text = _widget_descriptor_text(widget)
    return bool(re.search(r"\b(filters?_wrapper|filters?|search_filters?)\b", text))


def _extract_widget_filters(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], inside_filter: bool = False) -> None:
        is_filter = _is_filter_container(widget)
        if is_filter and not inside_filter:
            inputs = _extract_widget_input_bindings(
                flow=flow,
                screen_name=screen_name,
                widgets=[widget],
            )
            actions = _extract_widget_actions(
                flow=flow,
                screen_name=screen_name,
                widgets=[widget],
            )
            filter_record: dict[str, Any] = {
                "widget_key": widget.get("key", ""),
                "widget_name": widget.get("name", ""),
                "widget_type": widget.get("type", ""),
                "evidence_path": _widget_evidence_path(
                    flow=flow,
                    screen_name=screen_name,
                    widget=widget,
                    property_name="filters",
                ),
                "input_bindings": inputs,
                "widget_actions": actions,
            }
            _copy_widget_rendering_context(filter_record, widget)
            filters.append({
                key: value
                for key, value in filter_record.items()
                if value != "" and value != []
            })

        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child, inside_filter or is_filter)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child, inside_filter or is_filter)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return filters


def _has_direct_action_widget(widget: dict[str, Any]) -> bool:
    for child in widget.get("children", []) or []:
        if isinstance(child, dict) and child.get("event_handlers"):
            return True
    for child in widget.get("widgets", []) or []:
        if isinstance(child, dict) and child.get("event_handlers"):
            return True
    return False


def _is_footer_action_container(widget: dict[str, Any]) -> bool:
    if str(widget.get("type", "")) != "Container":
        return False
    text = _widget_descriptor_text(widget)
    if re.search(r"\b(footer|bottomnavigation|buttoncontainer|actions?)\b", text):
        return True
    children = [
        child
        for child in (widget.get("children", []) or []) + (widget.get("widgets", []) or [])
        if isinstance(child, dict)
    ]
    actionable = [
        child for child in children
        if str(child.get("type", "")) in ("Button", "Link") and child.get("event_handlers")
    ]
    non_action_children = [
        child for child in children
        if str(child.get("type", "")) not in ("Button", "Link", "Text")
    ]
    return len(actionable) >= 2 and not non_action_children


def _extract_widget_footer_actions(
    *,
    flow: str,
    screen_name: str,
    widgets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    footers: list[dict[str, Any]] = []

    def visit(widget: dict[str, Any], inside_footer: bool = False) -> None:
        is_footer = _is_footer_action_container(widget)
        if is_footer and not inside_footer and _has_direct_action_widget(widget):
            actions = [
                action for action in _extract_widget_actions(
                    flow=flow,
                    screen_name=screen_name,
                    widgets=[widget],
                )
                if str(action.get("widget_type", "")) in ("Button", "Link")
            ]
            if actions:
                footer: dict[str, Any] = {
                    "widget_key": widget.get("key", ""),
                    "widget_name": widget.get("name", ""),
                    "widget_type": widget.get("type", ""),
                    "evidence_path": _widget_evidence_path(
                        flow=flow,
                        screen_name=screen_name,
                        widget=widget,
                        property_name="footer_actions",
                    ),
                    "widget_actions": actions,
                }
                _copy_widget_rendering_context(footer, widget)
                footers.append({
                    key: value
                    for key, value in footer.items()
                    if value != "" and value != []
                })

        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child, inside_footer or is_footer)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child, inside_footer or is_footer)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return footers


def _condition_required_options_from_field_bindings(
    field_bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    pattern = re.compile(r"\bEntities\.([A-Za-z_][A-Za-z0-9_]*)\.StaticRecords\.([A-Za-z_][A-Za-z0-9_]*)\b")
    for binding in field_bindings:
        source = str(binding.get("source", ""))
        for match in pattern.finditer(source):
            entity = match.group(1)
            static_record = match.group(2)
            key = (
                entity,
                static_record,
                str(binding.get("action_name", "")),
                str(binding.get("evidence_path", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            options.append({
                "entity": entity,
                "static_record": static_record,
                "source": match.group(0),
                "reason": "condition_references_static_record",
                "synthetic_required": True,
                "synthetic_option": {
                    "value": match.group(0),
                    "label": static_record,
                    "value_source": "static_record_expression",
                    "label_source": "static_record_name",
                },
                "action_name": binding.get("action_name", ""),
                "evidence_path": binding.get("evidence_path", ""),
            })
    return options


def _static_record_refs_from_expression(expression: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    pattern = re.compile(r"\bEntities\.([A-Za-z_][A-Za-z0-9_]*)\.StaticRecords\.([A-Za-z_][A-Za-z0-9_]*)\b")
    seen: set[tuple[str, str, str]] = set()
    for match in pattern.finditer(expression):
        key = (match.group(1), match.group(2), match.group(0))
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "entity": match.group(1),
            "static_record": match.group(2),
            "source": match.group(0),
        })
    return refs


def _synthetic_option_for_static_record_ref(ref: dict[str, str]) -> dict[str, str]:
    return {
        "value": ref.get("source", ""),
        "label": ref.get("static_record", ""),
        "value_source": "static_record_expression",
        "label_source": "static_record_name",
    }


def _link_condition_required_options_to_inputs(
    input_bindings: list[dict[str, Any]],
    field_bindings: list[dict[str, Any]],
) -> None:
    """Attach condition-required static options to controls that drive them."""
    for input_record in input_bindings:
        targets = [
            str(binding.get("target", "")).strip()
            for binding in input_record.get("bindings", []) or []
            if isinstance(binding, dict) and str(binding.get("target", "")).strip()
        ]
        if not targets:
            continue
        required_options: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for field_binding in field_bindings:
            source = str(field_binding.get("source", ""))
            if not any(target and target in source for target in targets):
                continue
            for ref in _static_record_refs_from_expression(source):
                key = (
                    ref["entity"],
                    ref["static_record"],
                    str(field_binding.get("action_name", "")),
                    str(field_binding.get("evidence_path", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                required_options.append({
                    **ref,
                    "reason": "input_value_drives_visibility_or_state_condition",
                    "synthetic_required": True,
                    "synthetic_option": _synthetic_option_for_static_record_ref(ref),
                    "action_name": field_binding.get("action_name", ""),
                    "evidence_path": field_binding.get("evidence_path", ""),
                })
        if required_options:
            input_record["condition_required_options"] = required_options


def _screen_state_defaults(screen: dict[str, Any]) -> list[dict[str, Any]]:
    defaults: list[dict[str, Any]] = []
    for scope, collection_name in (
        ("input_parameter", "input_parameters"),
        ("local_variable", "local_variables"),
        ("output_parameter", "output_parameters"),
    ):
        for item in screen.get(collection_name, []) or []:
            if not isinstance(item, dict):
                continue
            default_value = item.get("default_value", "")
            if default_value in ("", None):
                continue
            defaults.append({
                "scope": scope,
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "default_value": default_value,
                "key": item.get("key", ""),
            })
    return defaults


def _initial_state_from_actions(actions_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    initial_state: list[dict[str, Any]] = []
    for action in actions_out:
        if action.get("kind") != "preparation":
            continue
        for assignment_group in action.get("assignments", []) or []:
            if not isinstance(assignment_group, dict):
                continue
            for assignment in assignment_group.get("assignments", []) or []:
                if not isinstance(assignment, dict):
                    continue
                record = {
                    "target": assignment.get("target", ""),
                    "source": assignment.get("source", ""),
                    "action_name": action.get("name", ""),
                    "action_key": action.get("key", ""),
                    "node_key": assignment_group.get("node_key", ""),
                    "reason": "preparation_assignment_before_first_render",
                }
                control_context = _copy_control_context(assignment)
                if control_context:
                    record.update(control_context)
                initial_state.append(record)
    return initial_state


def _collect_widget_style_classes(widgets: list[dict[str, Any]]) -> list[str]:
    classes: list[str] = []

    def visit(widget: dict[str, Any]) -> None:
        style_classes = widget.get("style_classes")
        if isinstance(style_classes, list):
            classes.extend(str(item) for item in style_classes if str(item).strip())
        style_evidence = widget.get("style_evidence")
        if isinstance(style_evidence, dict) and isinstance(style_evidence.get("style_classes"), list):
            classes.extend(str(item) for item in style_evidence["style_classes"] if str(item).strip())
        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return classes


def _css_asset_declares_class(css_text: str, class_name: str) -> bool:
    if not css_text or not class_name:
        return False
    pattern = re.compile(
        r"(^|[,{>\s])\." + re.escape(class_name) + r"(?=[\s\.#:{>\[,]|$)"
    )
    return bool(pattern.search(css_text))


def _screen_css_class_inventory(
    widgets: list[dict[str, Any]],
    css_assets: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for class_name in _collect_widget_style_classes(widgets):
        counts[class_name] = counts.get(class_name, 0) + 1
    inventory: list[dict[str, Any]] = []
    for class_name in sorted(counts):
        declared_in: list[str] = []
        for asset in css_assets or []:
            css_text = str(asset.get("css_text", ""))
            if _css_asset_declares_class(css_text, class_name):
                declared_in.append(str(asset.get("name") or asset.get("key") or ""))
        entry: dict[str, Any] = {
            "class_name": class_name,
            "usage_count": counts[class_name],
            "declared_in": declared_in,
        }
        if not declared_in:
            entry["css_gap"] = "missing_class_declaration"
        inventory.append(entry)
    return inventory


_SOURCE_BLOCK_NAME_RE = re.compile(
    r"(?:WebFlows|ReferenceWebFlows)\.([^.]+)\.NodesShownInESpaceTree\.([A-Za-z_][A-Za-z0-9_]*)"
)


def _embedded_block_dataflow_refs(widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def visit(widget: dict[str, Any]) -> None:
        if str(widget.get("type", "")) == "WebBlockInstance":
            source_block = str(widget.get("source_block", ""))
            match = _SOURCE_BLOCK_NAME_RE.search(source_block)
            if match:
                flow_name = match.group(1)
                block_name = match.group(2)
                key = (str(widget.get("key", "")), flow_name, block_name)
                if key not in seen:
                    seen.add(key)
                    ref: dict[str, Any] = {
                        "widget_key": widget.get("key", ""),
                        "source_block": source_block,
                        "flow": flow_name,
                        "block_name": block_name,
                    }
                    arguments = widget.get("arguments")
                    if isinstance(arguments, list) and arguments:
                        ref["arguments"] = arguments
                    _copy_widget_rendering_context(ref, widget, include_enabled=False)
                    refs.append({
                        key_: value
                        for key_, value in ref.items()
                        if value not in ("", None, [])
                    })
        for child in widget.get("children", []) or []:
            if isinstance(child, dict):
                visit(child)
        for child in widget.get("widgets", []) or []:
            if isinstance(child, dict):
                visit(child)

    for widget in widgets:
        if isinstance(widget, dict):
            visit(widget)
    return refs


def _extract_screen_dataflow_records(
    screens: list[dict[str, Any]],
    css_assets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project embedded screen action graphs into screen-scoped dataflow evidence.

    ``screens.json`` already carries embedded action graphs, but UI rebuild
    consumers need a direct screen -> action -> field mapping. This artifact
    keeps the raw action evidence and adds normalized field bindings without
    inventing values.
    """
    records: list[dict[str, Any]] = []

    for screen in screens:
        if screen.get("kind") not in ("screen", "email_screen", "block"):
            continue

        flow = str(screen.get("flow", ""))
        screen_name = str(screen.get("name", ""))
        actions_out: list[dict[str, Any]] = []
        field_bindings: list[dict[str, Any]] = []
        ui_bindings: list[dict[str, Any]] = []
        input_bindings: list[dict[str, Any]] = []
        widget_actions: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        dialogs: list[dict[str, Any]] = []
        filters: list[dict[str, Any]] = []
        footer_actions: list[dict[str, Any]] = []
        condition_required_options: list[dict[str, Any]] = []

        for action in screen.get("client_actions", []) or []:
            if not isinstance(action, dict):
                continue
            flow_graph = action.get("flow") or {}
            nodes = flow_graph.get("nodes") or []
            edges = flow_graph.get("edges") or []
            control_contexts = _build_node_control_contexts(nodes, edges)
            execute_action_names_by_node = {
                str(node.get("key", "")): _action_ref_leaf(str(node.get("action_ref", "")))
                for node in nodes
                if isinstance(node, dict) and str(node.get("action_ref", "")).strip()
            }
            assignments: list[dict[str, Any]] = []
            execute_action_calls: list[dict[str, Any]] = []
            conditions: list[dict[str, Any]] = []
            ajax_refresh_targets: list[dict[str, Any]] = []
            navigation_targets: list[dict[str, Any]] = []
            action_role_gates: list[dict[str, str]] = []

            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("type", ""))
                node_key = str(node.get("key", ""))
                control_context = control_contexts.get(node_key, {})
                if node_type == "Nodes.Assign":
                    node_assignments = []
                    for assignment in node.get("assignments", []) or []:
                        if not isinstance(assignment, dict):
                            continue
                        binding = _assignment_field_binding(
                            flow=flow,
                            screen_name=screen_name,
                            action=action,
                            node=node,
                            assignment=assignment,
                            execute_action_names_by_node=execute_action_names_by_node,
                            control_context=control_context,
                        )
                        node_assignments.append({
                            "target": binding["target"],
                            "source": binding["source"],
                        })
                        node_assignment_context = _copy_control_context(control_context)
                        if node_assignment_context:
                            node_assignments[-1].update(node_assignment_context)
                        field_bindings.append(binding)
                    assignment_record = {
                        "node_key": node.get("key", ""),
                        "node_name": node.get("name", ""),
                        "assignments": node_assignments,
                    }
                    assignment_record.update(_copy_control_context(control_context))
                    assignments.append(assignment_record)
                if (
                    "ExecuteAction" in node_type
                    or "RunClientAction" in node_type
                    or "RunServerAction" in node_type
                ):
                    action_ref = str(node.get("action_ref", ""))
                    call_role_gates = _merge_role_gates(
                        _role_gates_from_expression(action_ref),
                        control_context.get("role_gates", [])
                        if isinstance(control_context.get("role_gates"), list)
                        else [],
                    )
                    action_role_gates = _merge_role_gates(
                        action_role_gates,
                        call_role_gates,
                    )
                    call_record = {
                        "node_key": node.get("key", ""),
                        "node_type": node_type,
                        "action_ref": action_ref,
                        "arguments": node.get("arguments", []) or [],
                    }
                    call_record.update(_copy_control_context(control_context))
                    if call_role_gates:
                        call_record["role_gates"] = call_role_gates
                    execute_action_calls.append(call_record)
                if (
                    "AjaxRefresh" in node_type
                    or "UpdateScreen" in node_type
                    or node_type == "Nodes.RefreshQuery"
                    or node_type.endswith(".RefreshQuery")
                ):
                    refresh_record = {
                        "node_key": node.get("key", ""),
                        "node_type": node_type,
                        "target_ref": node.get("target_ref", ""),
                        "query_ref": node.get("query_ref", ""),
                    }
                    refresh_record.update(_copy_control_context(control_context))
                    ajax_refresh_targets.append({
                        key: value
                        for key, value in refresh_record.items()
                        if value not in ("", None, [])
                    })
                if (
                    node_type in ("Nodes.WebDestination", "NRNodes.Destination")
                    or "NavigateTo" in node_type
                    or node_type.endswith(".Destination")
                ):
                    navigation_record = {
                        "node_key": node.get("key", ""),
                        "node_type": node_type,
                        "target_ref": node.get("target_ref", ""),
                        "arguments": node.get("arguments", []) or [],
                    }
                    navigation_record.update(_copy_control_context(control_context))
                    navigation_targets.append({
                        key: value
                        for key, value in navigation_record.items()
                        if value not in ("", None, [])
                    })
                if node_type in ("Nodes.If", "Nodes.Switch"):
                    condition = {
                        "node_key": node.get("key", ""),
                        "node_type": node_type,
                        "condition": node.get("condition", ""),
                    }
                    if node.get("switch_conditions"):
                        condition["switch_conditions"] = node.get("switch_conditions")
                    role_gates = _role_gates_from_expression(str(node.get("condition", "")))
                    if role_gates:
                        condition["role_gates"] = role_gates
                        action_role_gates = _merge_role_gates(
                            action_role_gates,
                            role_gates,
                        )
                    conditions.append(condition)

            action_record = {
                "name": action.get("name", ""),
                "key": action.get("key", ""),
                "kind": _screen_action_kind(action),
                "container": action.get("container", ""),
                "action_type": action.get("action_type", ""),
                "assignments": assignments,
                "execute_action_calls": execute_action_calls,
                "conditions": conditions,
                "source_evidence": {
                    "source_path": "interfaces/screens.json",
                    "screen_key": screen.get("key", ""),
                    "action_key": action.get("key", ""),
                },
            }
            if ajax_refresh_targets:
                action_record["ajax_refresh_targets"] = ajax_refresh_targets
            if navigation_targets:
                action_record["navigation_targets"] = navigation_targets
            if action_role_gates:
                action_record["role_gates"] = action_role_gates
            actions_out.append(action_record)

        object_copies = {
            binding["target"]: binding
            for binding in field_bindings
            if "." not in str(binding.get("target", ""))
            and "." in str(binding.get("source", ""))
        }
        ui_bindings = _extract_widget_ui_bindings(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
            object_copies=object_copies,
        )
        input_bindings = _extract_widget_input_bindings(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        _link_condition_required_options_to_inputs(input_bindings, field_bindings)
        widget_actions = _extract_widget_actions(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        tables = _extract_widget_tables(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        dialogs = _extract_widget_dialogs(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        filters = _extract_widget_filters(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        footer_actions = _extract_widget_footer_actions(
            flow=flow,
            screen_name=screen_name,
            widgets=screen.get("widgets", []) or [],
        )
        condition_required_options = _condition_required_options_from_field_bindings(
            field_bindings
        )
        state_defaults = _screen_state_defaults(screen)
        initial_state = _initial_state_from_actions(actions_out)
        ajax_refresh_targets = [
            target
            for action in actions_out
            for target in action.get("ajax_refresh_targets", []) or []
            if isinstance(target, dict)
        ]
        navigation_targets = [
            target
            for action in actions_out
            for target in action.get("navigation_targets", []) or []
            if isinstance(target, dict)
        ]
        embedded_block_refs = _embedded_block_dataflow_refs(screen.get("widgets", []) or [])
        css_class_inventory = _screen_css_class_inventory(
            screen.get("widgets", []) or [],
            css_assets,
        )

        gaps: list[dict[str, str]] = []
        if (
            actions_out
            and not field_bindings
            and not ui_bindings
            and not input_bindings
            and not tables
            and not dialogs
            and not filters
            and not footer_actions
            and not initial_state
            and not state_defaults
        ):
            gaps.append({
                "classification": "no_screen_field_assignments_found",
                "summary": (
                    "Screen actions were extracted, but no Nodes.Assign field "
                    "bindings, UI value bindings, or input bindings were found "
                    "for this screen."
                ),
            })
        elif not actions_out and (
            screen.get("input_parameters")
            or screen.get("local_variables")
            or screen.get("screen_data_sets")
        ):
            gaps.append({
                "classification": "no_screen_actions_for_dataflow",
                "summary": (
                    "Screen declares parameters, local variables, or data sets "
                    "but no embedded screen actions were extracted."
                ),
            })

        records.append({
            "flow": flow,
            "screen": screen_name,
            "screen_key": screen.get("key", ""),
            "kind": screen.get("kind", ""),
            "dataflow_status": (
                "mapped"
                if (
                    field_bindings
                    or ui_bindings
                    or input_bindings
                    or tables
                    or dialogs
                    or filters
                    or footer_actions
                    or initial_state
                    or state_defaults
                )
                else "partial" if gaps else "empty"
            ),
            "actions": actions_out,
            "field_bindings": field_bindings,
            "ui_bindings": ui_bindings,
            "input_bindings": input_bindings,
            "widget_actions": widget_actions,
            "tables": tables,
            "dialogs": dialogs,
            "filters": filters,
            "footer_actions": footer_actions,
            "state_defaults": state_defaults,
            "initial_state": initial_state,
            "ajax_refresh_targets": ajax_refresh_targets,
            "navigation_targets": navigation_targets,
            "condition_required_options": condition_required_options,
            "embedded_block_dataflow_refs": embedded_block_refs,
            "css_class_inventory": css_class_inventory,
            "gaps": gaps,
        })

    return records


# ---------------------------------------------------------------------------
# Theme extraction
# ---------------------------------------------------------------------------

_THEME_TAGS = {"Theme", "ReferenceTheme"}


def _reference_modules(doc: OmlDocument) -> dict[int, str]:
    """Map ``id(element)`` -> producer module name for consumed elements.

    Consumed (referenced) elements live under ``<Reference Name="Module">``.
    """
    owners: dict[int, str] = {}
    for ref in doc.root.iter("Reference"):
        module = ref.attrib.get("Name", "")
        for elem in ref.iter():
            owners[id(elem)] = module
    return owners


def _is_theme_element(elem: ET.Element) -> bool:
    """True for a real Theme / ReferenceTheme (``NewRuntime.`` prefix optional).

    The ``<Themes>`` / ``<NRThemes>`` containers on the ESpace are empty
    placeholders whose content lives in fragments; they are not themes.
    """
    return elem.tag.rsplit(".", 1)[-1] in _THEME_TAGS


def _extract_themes(doc: OmlDocument, tracker: CoverageTracker) -> list[dict[str, Any]]:
    """Extract themes from the NRThemes fragment and ESpace children."""
    themes: list[dict[str, Any]] = []

    # Check NRThemes and Themes fragments
    for child in doc.fragment_children("NRThemes") + doc.fragment_children("Themes"):
        if not _is_theme_element(child):
            continue
        name = child.attrib.get("Name", "")
        key = child.attrib.get("Key", "")
        tag = child.tag

        tracker.discover(f"theme:{name}")
        tracker.capture(f"theme:{name}")

        entry: dict[str, Any] = {
            "name": name,
            "key": key,
            "tag": tag,
            "is_reference": "Reference" in tag,
        }

        # BaseTheme reference
        base = child.attrib.get("BaseTheme", "")
        if base:
            entry["base_theme"] = base

        # Grid layout config (attributes on the theme element itself)
        grid: dict[str, Any] = {}
        layout_type = child.attrib.get("UseGrid", "")
        if layout_type:
            grid["layout_type"] = layout_type
        for attr, field in (
            ("GridColumns", "columns"),
            ("ColumnWidth", "column_width"),
            ("GutterWidth", "gutter_width"),
            ("GutterPercentage", "gutter_percentage"),
        ):
            val = child.attrib.get(attr, "")
            if val:
                try:
                    grid[field] = int(val)
                except ValueError:
                    grid[field] = val
        if grid:
            entry["grid"] = grid

        themes.append(entry)

    # Also check ESpace direct children for Theme elements
    nr_themes_frag = doc.fragment("NRThemes")
    nr_themes_children = list(nr_themes_frag) if nr_themes_frag is not None else []
    for child in doc.espace:
        if _is_theme_element(child) and child not in nr_themes_children:
            name = child.attrib.get("Name", "")
            key = child.attrib.get("Key", "")
            if not any(t["key"] == key for t in themes):
                tracker.discover(f"theme:{name}")
                tracker.capture(f"theme:{name}")
                themes.append({
                    "name": name,
                    "key": key,
                    "tag": child.tag,
                    "is_reference": "Reference" in child.tag,
                })

    # Referenced themes — NRReferenceThemes/ReferenceThemes containers inside Reference fragments
    seen_theme_keys = {t["key"] for t in themes}
    producers = _reference_modules(doc)
    for frag in doc.fragments.values():
        for container_tag in ("NRReferenceThemes", "ReferenceThemes"):
            for container in frag.iter(container_tag):
                for child in container:
                    if not _is_theme_element(child):
                        continue
                    name = child.attrib.get("Name", "")
                    key = child.attrib.get("Key", "")
                    if key in seen_theme_keys:
                        continue
                    seen_theme_keys.add(key)

                    tracker.discover(f"theme:{name}")
                    tracker.capture(f"theme:{name}")

                    entry_ref: dict[str, Any] = {
                        "name": name,
                        "key": key,
                        "tag": child.tag,
                        "is_reference": True,
                        "source_module": producers.get(id(child), ""),
                    }

                    base = child.attrib.get("BaseTheme", "")
                    if base:
                        entry_ref["base_theme"] = base

                    grid: dict[str, Any] = {}
                    layout_type = child.attrib.get("UseGrid", "")
                    if layout_type:
                        grid["layout_type"] = layout_type
                    for attr, field in (
                        ("GridColumns", "columns"),
                        ("ColumnWidth", "column_width"),
                        ("GutterWidth", "gutter_width"),
                        ("GutterPercentage", "gutter_percentage"),
                    ):
                        val = child.attrib.get(attr, "")
                        if val:
                            try:
                                grid[field] = int(val)
                            except ValueError:
                                grid[field] = val
                    if grid:
                        entry_ref["grid"] = grid

                    desc = child.attrib.get("Description", "")
                    if desc:
                        entry_ref["description"] = desc

                    themes.append(entry_ref)

    return themes


# ---------------------------------------------------------------------------
# CSS / stylesheet extraction
# ---------------------------------------------------------------------------

def _parse_css_effect_rules(css_text: str) -> list[dict[str, Any]]:
    """Extract CSS rules that can affect visibility or interaction."""
    rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css_text, flags=re.S):
        selectors = [selector.strip() for selector in match.group(1).split(",")]
        body = match.group(2).strip()
        if not body:
            continue
        effects = _css_effect_hints_from_inline_style(body)
        if not effects:
            continue
        for selector in selectors:
            class_names = re.findall(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)", selector)
            if not class_names:
                continue
            for effect in effects:
                key = (selector, effect.get("kind", ""), effect.get("effect", ""))
                if key in seen:
                    continue
                seen.add(key)
                rules.append({
                    "selector": selector,
                    "class_names": class_names,
                    "effect": effect,
                    "declarations": body[:500],
                })
    return rules


def _extract_css(doc: OmlDocument, tracker: CoverageTracker) -> list[dict[str, Any]]:
    """Extract CSS content from WebStyleSheet and ReferenceWebStyleSheet elements."""
    stylesheets: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_elems: set[int] = set()

    # Stylesheets carry no Name of their own: they sit in a <StyleSheet>
    # slot of the screen / block / theme that owns them. Map each one to
    # its owner so entries are identifiable.
    owners: dict[int, ET.Element] = {}

    def _index_owners(root: ET.Element) -> None:
        for owner in root.iter():
            for slot in owner.findall("StyleSheet"):
                for sheet in slot:
                    owners[id(sheet)] = owner

    _index_owners(doc.espace)
    for frag in doc.fragments.values():
        _index_owners(frag)
    producers = _reference_modules(doc)

    def _collect_from(root: ET.Element) -> None:
        for elem in root.iter():
            tag = elem.tag
            if tag not in ("WebStyleSheet", "ReferenceWebStyleSheet"):
                continue
            if id(elem) in seen_elems:
                continue
            seen_elems.add(id(elem))

            key = elem.attrib.get("Key", "")
            if key in seen_keys:
                continue
            if key:
                seen_keys.add(key)

            owner = owners.get(id(elem))
            owner_name = owner.attrib.get("Name", "") if owner is not None else ""
            name = elem.attrib.get("Name", "") or owner_name
            css_id = f"css:{name or key}"
            tracker.discover(css_id)

            # CSS content can be in <Value> (Reference stylesheets) or
            # <CssSource>/<UserCssSource>/<FinalCssSource> (local stylesheets).
            css_text = ""
            for css_child_tag in ("Value", "CssSource", "UserCssSource", "FinalCssSource"):
                child_elem = elem.find(css_child_tag)
                if child_elem is not None and child_elem.text:
                    css_text = child_elem.text
                    break

            tracker.capture(css_id)
            css_effect_rules = _parse_css_effect_rules(css_text)
            stylesheets.append({
                "name": name,
                "key": key,
                "tag": tag,
                "owner": owner_name,
                "owner_kind": owner.tag.rsplit(".", 1)[-1] if owner is not None else "",
                "is_reference": "Reference" in tag,
                "source_module": producers.get(id(elem), "") if "Reference" in tag else "",
                # Every screen/block/theme has a stylesheet slot; only those
                # with CSS are stylesheets as Service Studio counts them.
                "has_css": bool(css_text.strip()),
                "css_length": len(css_text),
                "css_effect_rule_count": len(css_effect_rules),
                "css_effect_rules": css_effect_rules[:250],
                # css_preview is a 5 KB index-friendly truncation; css_text
                # holds the complete body for downstream token / scaffold work.
                "css_preview": css_text[:5000] if css_text else "",
                "css_text": css_text,
            })

    # Search ESpace and all fragments
    _collect_from(doc.espace)
    for frag in doc.fragments.values():
        _collect_from(frag)

    return stylesheets


# ---------------------------------------------------------------------------
# Design token extraction (Gap 2)
# ---------------------------------------------------------------------------

# Regex patterns for token extraction — compiled once at module load.
_RE_COLOR_PROP = re.compile(
    r"(?:^|[{;])\s*"
    r"(?:background(?:-color)?|color|border(?:-(?:top|right|bottom|left))?-color|fill|stroke)"
    r"\s*:\s*"
    r"(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.,\s%]+\)|hsla?\(\s*[\d.,\s%]+\))",
    re.MULTILINE,
)

_RE_FONT_SIZE = re.compile(r"font-size\s*:\s*([^;}{]+)", re.MULTILINE)
_RE_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}{]+)", re.MULTILINE)
_RE_LINE_HEIGHT = re.compile(r"line-height\s*:\s*([^;}{]+)", re.MULTILINE)
_RE_FONT_WEIGHT = re.compile(r"font-weight\s*:\s*([^;}{]+)", re.MULTILINE)

# Only capture bare numeric / pixel / rem / em / % / vw / vh values — filter
# out CSS var() references and complex expressions so the scale is clean.
_RE_SPACING_BARE = re.compile(
    r"(?:^|[{;])\s*(?:padding|margin)(?:-(?:top|right|bottom|left))?\s*:\s*"
    r"((?:[\d.]+(?:px|rem|em|%|vw|vh)(?:\s+[\d.]+(?:px|rem|em|%|vw|vh))*|0))\s*(?:;|$)",
    re.MULTILINE,
)


def _count_occurrences(items: list[str]) -> list[dict[str, Any]]:
    """Deduplicate a list and return ``{value, count}`` dicts sorted by freq."""
    counts: dict[str, int] = {}
    for item in items:
        v = item.strip()
        if v:
            counts[v] = counts.get(v, 0) + 1
    return sorted(
        [{"value": k, "count": v} for k, v in counts.items()],
        key=lambda x: (-x["count"], x["value"]),
    )


def _extract_design_tokens(
    css_assets: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> dict[str, Any]:
    """Parse design tokens from the full CSS bodies captured in *css_assets*.

    Returns a dict suitable for writing to ``interfaces/design-tokens.json``
    with three top-level sections:

    * ``colors``     — ``{value, count}`` list, deduplicated by frequency
    * ``typography`` — ``{font_size, font_family, line_height, font_weight}``
                       combinations deduplicated by frequency
    * ``spacing``    — numeric padding/margin scale, deduplicated by frequency

    This is a best-effort parse. CSS-in-JS, SCSS variables expanded in build
    tools, and minified selectors are handled as plain text.
    """
    tracker.discover("design_tokens_interface", "css_assets/design_tokens")

    all_colors: list[str] = []
    all_font_sizes: list[str] = []
    all_font_families: list[str] = []
    all_line_heights: list[str] = []
    all_font_weights: list[str] = []
    all_spacing: list[str] = []

    source_names: list[str] = []

    for entry in css_assets:
        css_body = entry.get("css_text", "")
        if not css_body:
            continue
        source_names.append(entry.get("name") or entry.get("key") or "")

        for m in _RE_COLOR_PROP.finditer(css_body):
            all_colors.append(m.group(1).strip())

        for m in _RE_FONT_SIZE.finditer(css_body):
            all_font_sizes.append(m.group(1).strip())

        for m in _RE_FONT_FAMILY.finditer(css_body):
            all_font_families.append(m.group(1).strip())

        for m in _RE_LINE_HEIGHT.finditer(css_body):
            all_line_heights.append(m.group(1).strip())

        for m in _RE_FONT_WEIGHT.finditer(css_body):
            all_font_weights.append(m.group(1).strip())

        for m in _RE_SPACING_BARE.finditer(css_body):
            all_spacing.append(m.group(1).strip())

    # Build typography combinations: emit per-file summary at the module level.
    # Individual lists are more useful than forcing multi-field join here.
    typography = {
        "font_sizes": _count_occurrences(all_font_sizes),
        "font_families": _count_occurrences(all_font_families),
        "line_heights": _count_occurrences(all_line_heights),
        "font_weights": _count_occurrences(all_font_weights),
    }

    colors = _count_occurrences(all_colors)
    spacing = _count_occurrences(all_spacing)

    total = len(colors) + len(all_font_sizes) + len(all_spacing)
    if total > 0:
        tracker.capture("design_tokens_interface", "interfaces/design-tokens.json")
    elif source_names:
        # CSS bodies were present but parsing extracted zero tokens. That is a
        # real extraction miss (CSS we could not turn into tokens), not an
        # absent UI surface. Capture the artefact but mark it unresolved so the
        # interface domain reports 'partial' and the gap is visible — rather
        # than silently reporting 'complete' over missing design data.
        tracker.capture(
            "design_tokens_interface",
            "interfaces/design-tokens.json",
            result="css_present_but_no_tokens_parsed",
        )
        tracker.unresolve(
            "design_tokens_interface",
            "interfaces/design-tokens.json",
            result="css_present_but_no_tokens_parsed",
        )
    else:
        # No CSS bodies at all (backend/plugin module, or only empty inline
        # stylesheets). Legitimately nothing to extract — stay quiet.
        tracker.capture(
            "design_tokens_interface",
            "interfaces/design-tokens.json",
            result="no_css_values_found",
        )

    return {
        "colors": colors,
        "typography": typography,
        "spacing": spacing,
        "source_count": len(source_names),
        "source_names": source_names,
    }


# ---------------------------------------------------------------------------
# Layout inheritance manifest (Gap 8)
# ---------------------------------------------------------------------------

_LAYOUT_NAME_PREFIX = "layout"

# Global blocks that appear across multiple layout variants and carry
# cross-cutting behaviour (routing, dialogs, platform events).
_GLOBAL_BLOCK_PATTERNS: tuple[str, ...] = (
    "Menu",
    "BottomBar",
    "AdjustableTextSizeContent",
    "Firebase",
    "OfflineDataSyncEvents",
    "SwipeEvents",
    "PullToRefresh",
    "PullRefresh",
    "OnResumeApp",
    "DeeplinkHandlerCentral",
    "ProcessingDialog",
    "MessageDialog",
    "ErrorDialog",
    "GeneralDialog",
    "OverlayModal",
)


def _block_short_name(source_block: str) -> str:
    """Extract the terminal block name from a fully-qualified source_block path.

    Example:
        NRWebFlows.Common.NodesShownInESpaceTree.Menu  ->  Menu
        References.OutSystemsUI...SwipeEvents          ->  SwipeEvents
    """
    parts = source_block.rsplit(".", 1)
    return parts[-1] if parts else source_block


def _is_global_block(short_name: str) -> bool:
    """Return True if *short_name* matches any known global block pattern."""
    return any(pat in short_name for pat in _GLOBAL_BLOCK_PATTERNS)


def _collect_layout_items(
    widgets: list[dict[str, Any]],
    placeholders: list[str],
    embedded_blocks: list[str],
    global_widgets: list[str],
    seen_blocks: set[str],
) -> None:
    """Recursively collect placeholders and embedded WebBlock references."""
    for w in widgets:
        wtype = w.get("type", "")

        if wtype in ("CustomPlaceholderWidget", "Placeholder"):
            name = w.get("name", "")
            if name and name not in placeholders:
                placeholders.append(name)

        elif wtype == "WebBlockInstance":
            src = w.get("source_block", "") or ""
            if src and src not in seen_blocks:
                seen_blocks.add(src)
                short = _block_short_name(src)
                if _is_global_block(short):
                    if short not in global_widgets:
                        global_widgets.append(short)
                else:
                    if src not in embedded_blocks:
                        embedded_blocks.append(src)

        # Recurse
        for child in w.get("children", []):
            _collect_layout_items([child], placeholders, embedded_blocks, global_widgets, seen_blocks)
        for child in w.get("widgets", []):
            _collect_layout_items([child], placeholders, embedded_blocks, global_widgets, seen_blocks)


def _infer_lineage(
    layout_name: str,
    all_layouts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Infer family / lineage relationships for a layout block.

    Rules applied in order:
    1. ``_v2`` suffix → parent is the same name without ``_v2``.
    2. ``WithSubscriber`` → shares family with ``Normal``
       (adds subscriber-selector zone).
    3. ``LandingPage`` → derived from Blank (full-page, no nav header).
    4. ``Splash`` / ``Login`` / ``WithoutHeader`` / ``Blank`` → base variants.
    5. ``Layout`` (bare name) → original base; ``LayoutNormal`` extends it.
    """
    name = layout_name
    lineage: dict[str, Any] = {"family": None, "parent": None, "notes": []}

    layout_names = {b["name"] for b in all_layouts}

    # v2 upgrade
    if "_v2" in name:
        base = name.replace("_v2", "")
        if base in layout_names:
            lineage["parent"] = base
            lineage["notes"].append(
                f"{name} is a v2 upgrade of {base}: "
                "adds AdjustableTextSizeContent, DeeplinkHandlerCentral, and class changes."
            )

    # Family grouping
    lower = name.lower()
    if "withsubscriber" in lower:
        lineage["family"] = "subscriber"
        if not lineage["parent"]:
            # v1 WithSubscriber extends LayoutNormal in spirit
            lineage["notes"].append(
                "Adds multi-subscriber selector zone; shares header/content slots with LayoutNormal."
            )
    elif "normal" in lower:
        lineage["family"] = "normal"
        if name == "LayoutNormal":
            lineage["notes"].append("Primary layout: header + pull-to-refresh + content + bottom nav.")
    elif "landingpage" in lower:
        lineage["family"] = "landing"
        lineage["notes"].append(
            "Full-page layout for unauthenticated / onboarding screens. "
            "No sub-header; includes Menu_v2 and AdjustableTextSizeContent."
        )
    elif "blank" in lower:
        lineage["family"] = "minimal"
        lineage["notes"].append("Minimal shell: content slot only, Firebase + OfflineDataSyncEvents.")
    elif "splash" in lower:
        lineage["family"] = "minimal"
        lineage["notes"].append("Splash-screen shell: content slot only, no BottomBar or Menu.")
    elif "login" in lower:
        lineage["family"] = "minimal"
        lineage["notes"].append("Login flow shell.")
    elif "withoutheader" in lower:
        lineage["family"] = "normal"
        lineage["notes"].append(
            "LayoutNormal variant with header hidden; Content and Bottom slots only."
        )
    elif name == "Layout":
        lineage["family"] = "base"
        lineage["notes"].append(
            "Original base layout (pre-NR era). LayoutNormal is the current successor."
        )

    return lineage


def _build_layout_inheritance(
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a layout-graph manifest from already-extracted block records.

    For each layout block, records:
    - ``placeholders``   — named content slots (Content, Bottom, HeaderLeft, …)
    - ``global_widgets`` — cross-cutting embedded blocks (Menu, Firebase, …)
    - ``embedded_blocks``— non-global WebBlock references embedded in the layout
    - ``input_parameters`` — the block's declared input params
    - ``lineage``        — inferred family / parent / notes

    Returns a dict with ``"layouts"`` list and ``"summary"`` counts.
    """
    layout_blocks = [
        b for b in blocks
        if b.get("name", "").lower().startswith(_LAYOUT_NAME_PREFIX)
    ]

    entries: list[dict[str, Any]] = []
    for block in layout_blocks:
        name = block["name"]
        placeholders: list[str] = []
        embedded_blocks: list[str] = []
        global_widgets: list[str] = []
        seen: set[str] = set()

        _collect_layout_items(
            block.get("widgets", []),
            placeholders,
            embedded_blocks,
            global_widgets,
            seen,
        )

        lineage = _infer_lineage(name, layout_blocks)

        entries.append({
            "name": name,
            "key": block.get("key", ""),
            "flow": block.get("flow", ""),
            "placeholders": placeholders,
            "global_widgets": global_widgets,
            "embedded_blocks": embedded_blocks,
            "input_parameters": [p["name"] for p in block.get("input_parameters", [])],
            "lineage": lineage,
        })

    # Sort: base → normal → subscriber → minimal
    _FAMILY_ORDER = {"base": 0, "normal": 1, "landing": 2, "subscriber": 3, "minimal": 4, None: 5}
    entries.sort(key=lambda e: (
        _FAMILY_ORDER.get(e["lineage"]["family"], 5),
        e["name"],
    ))

    return {
        "total_layouts": len(entries),
        "layouts": entries,
    }


# ---------------------------------------------------------------------------
# Assets-resolved manifest (Gap 9)
# ---------------------------------------------------------------------------

def _collect_image_widgets(
    widgets: list[dict[str, Any]],
    result: list[dict[str, Any]],
    context: str,
) -> None:
    """Recursively collect Image widgets from an already-extracted widget tree."""
    for w in widgets:
        if w.get("type") == "Image":
            entry: dict[str, Any] = {
                "widget_key": w.get("key", ""),
                "widget_name": w.get("name", ""),
                "context": context,
                "actual_image": w.get("actual_image", ""),
                "actual_image_guid": w.get("actual_image_guid", ""),
                "actual_image_module": w.get("actual_image_module", ""),
            }
            result.append(entry)
        for child in w.get("children", []):
            _collect_image_widgets([child], result, context)
        for child in w.get("widgets", []):
            _collect_image_widgets([child], result, context)


def _build_assets_resolved(
    screens: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    module_name: str,
    evidence_index: list[dict[str, Any]],
    tracker: CoverageTracker | None = None,
) -> dict[str, Any]:
    """Build a pre-resolved image-widget manifest for the module.

    Collects every Image widget from screens and blocks, then resolves each
    ``actual_image_guid`` against the module's own evidence index.

    Returns a dict with ``"image_widgets"`` list, ``"total"``, ``"resolved"``,
    and ``"unresolved"`` counts. When ``tracker`` is supplied, every image
    widget that cannot be resolved is also recorded via ``tracker.unresolve``
    so the interface ``coverage.json`` reflects it (instead of the count being
    computed here and then dropped).
    """
    # Build a guid → evidence entry lookup from this module's index
    guid_to_evidence: dict[str, dict[str, Any]] = {}
    for item in evidence_index:
        key = item.get("key", "")
        if key:
            guid_to_evidence[key] = item

    image_widgets: list[dict[str, Any]] = []

    for screen in screens:
        ctx = f"screen:{screen.get('name', '')}"
        _collect_image_widgets(screen.get("widgets", []), image_widgets, ctx)

    for block in blocks:
        ctx = f"block:{block.get('name', '')}"
        _collect_image_widgets(block.get("widgets", []), image_widgets, ctx)

    resolved_count = 0
    unresolved_count = 0
    skipped_count = 0

    for entry in image_widgets:
        guid = entry["actual_image_guid"]
        if guid and guid in guid_to_evidence:
            ev = guid_to_evidence[guid]
            entry["resolved"] = True
            entry["asset_name"] = ev.get("name", "")
            entry["package_path"] = ev.get("package_path", "")
            entry["runtime_path"] = ev.get("runtime_path", "")
            resolved_count += 1
        elif guid:
            entry["resolved"] = False
            # If module is known from actual_image_module, note cross-module
            mod = entry.get("actual_image_module", "")
            if mod and mod != module_name:
                entry["resolution_note"] = f"asset defined in module '{mod}'; check that module's evidence/index.json"
            else:
                entry["resolution_note"] = "guid not found in this module's evidence index"
            unresolved_count += 1
            if tracker is not None:
                tracker.unresolve(
                    f"image:{entry.get('context', '')}:{guid}",
                    "interfaces/assets-resolved.json",
                    result=entry["resolution_note"],
                )
        else:
            # No static guid — dynamic/runtime image (e.g. data-bound or AEM
            # Headless URL resolved at runtime). Represent as a skip backed by
            # the runtime asset fixture, not an unresolved gap. (REC-002)
            entry["resolved"] = False
            entry["resolution_note"] = (
                "dynamic/runtime image (no static guid) — see runtime/aem-assets.json"
            )
            entry["runtime_fixture"] = "runtime/aem-assets.json"
            skipped_count += 1
            if tracker is not None:
                tracker.skip(
                    f"image:{entry.get('context', '')}",
                    "interfaces/assets-resolved.json",
                    result=entry["resolution_note"],
                )

    return {
        "module": module_name,
        "total": len(image_widgets),
        "resolved": resolved_count,
        "unresolved": unresolved_count,
        "skipped": skipped_count,
        "image_widgets": image_widgets,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(doc: OmlDocument, pkg_root: Path) -> None:
    """Execute the Interface domain worker.

    Reads the OML XML document, extracts all UI flows, screens, blocks,
    widget trees, themes, and CSS, then writes the interface package files.
    """
    out_dir = pkg_root / "interfaces"
    tracker = CoverageTracker(domain="interface")
    key_index = build_key_index(doc)
    type_resolver = TypeResolver.from_doc(doc)

    # ------------------------------------------------------------------
    # 1. Parse WebFlows from the NRWebFlows fragment
    # ------------------------------------------------------------------
    flows: list[dict[str, Any]] = []
    screens: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []

    flow_elements = doc.fragment_children("NRWebFlows") + doc.fragment_children("WebFlows")
    flow_keys = {flow.attrib.get("Key", "") for flow in flow_elements}
    emitted_node_keys: set[str] = set()
    block_fragment_keys = _fragment_node_keys(
        doc, ("NRWebBlocks", "WebBlocks"), "WebBlock",
    )
    screen_fragment_keys = _fragment_node_keys(
        doc, ("NRWebScreens", "WebScreens"), "WebBlock",
    )

    for flow_elem in flow_elements:
        if not flow_elem.tag.endswith("WebFlow"):
            continue

        flow_name = flow_elem.attrib.get("Name", "")
        flow_key = flow_elem.attrib.get("Key", "")
        flow_desc = flow_elem.attrib.get("Description", "")

        tracker.discover(f"flow:{flow_name}")
        tracker.capture(f"flow:{flow_name}")

        flows.append({
            "name": flow_name,
            "key": flow_key,
            "description": flow_desc,
        })

        # ---------------------------------------------------------------
        # 2. Find screens and blocks within this flow
        # ---------------------------------------------------------------
        # Strategy A: Look in NodesShownInESpaceTree child of the flow element
        nodes_container = flow_elem.find("NodesShownInESpaceTree")
        node_sources: list[tuple[ET.Element, str]] = []

        if nodes_container is not None:
            node_sources.extend(
                (child, "NodesShownInESpaceTree") for child in nodes_container
            )

        # Strategy B: Some XML layouts group flow children by screen/block
        # fragment rather than a combined NodesShownInESpaceTree container.
        for container_name in ("NRWebScreens", "WebScreens", "NRWebBlocks", "WebBlocks"):
            container = flow_elem.find(container_name)
            if container is not None:
                node_sources.extend((child, container_name) for child in container)

        # Strategy C: Look in fragment "NodesShownInESpaceTree#<flow_key>"
        frag_name = f"NodesShownInESpaceTree#{flow_key}"
        frag_children = doc.fragment_children(frag_name)
        if frag_children:
            node_sources.extend((child, frag_name) for child in frag_children)

        # Strategy D: Look in keyed screen/block fragments for this flow.
        for fragment_prefix in ("NRWebScreens", "WebScreens", "NRWebBlocks", "WebBlocks"):
            keyed_frag_name = f"{fragment_prefix}#{flow_key}"
            keyed_frag_children = doc.fragment_children(keyed_frag_name)
            if keyed_frag_children:
                node_sources.extend(
                    (child, keyed_frag_name) for child in keyed_frag_children
                )

        # Deduplicate by key
        seen_keys: set[str] = set()
        for node, origin in node_sources:
            node_key = node.attrib.get("Key", "")
            if node_key in seen_keys:
                continue
            if node_key:
                seen_keys.add(node_key)

            kind = _classify_screen_or_block(
                node, origin, block_fragment_keys, screen_fragment_keys,
            )
            if kind is None:
                continue
            if node_key:
                emitted_node_keys.add(node_key)

            if kind == "screen":
                tracker.discover(f"screen:{node.attrib.get('Name', '')}")
                entry = _extract_screen_or_block(
                    node, kind, flow_name, doc, key_index, tracker, type_resolver,
                )
                screens.append(entry)

            elif kind == "block":
                tracker.discover(f"block:{node.attrib.get('Name', '')}")
                entry = _extract_screen_or_block(
                    node, kind, flow_name, doc, key_index, tracker, type_resolver,
                )
                blocks.append(entry)

            elif kind == "email_screen":
                tracker.discover(f"email_screen:{node.attrib.get('Name', '')}")
                entry = _extract_screen_or_block(
                    node, "email_screen", flow_name, doc, key_index, tracker,
                    type_resolver,
                )
                screens.append(entry)

    for frag_name, frag in doc.fragments.items():
        if not frag_name.startswith("NodesShownInESpaceTree#"):
            continue
        owner_key = frag_name.partition("#")[2]
        if owner_key in flow_keys:
            continue

        flow_name = key_index.name(owner_key) or ""
        for node in frag:
            if not node.tag.endswith("WebBlock") or "Reference" in node.tag:
                continue

            node_key = node.attrib.get("Key", "")
            if node_key and node_key in emitted_node_keys:
                continue

            kind = _classify_screen_or_block(
                node, frag_name, block_fragment_keys, screen_fragment_keys,
            )
            if kind is None:
                continue
            if node_key:
                emitted_node_keys.add(node_key)

            if kind == "block":
                tracker.discover(f"block:{node.attrib.get('Name', '')}")
                entry = _extract_screen_or_block(
                    node, kind, flow_name, doc, key_index, tracker, type_resolver,
                )
                blocks.append(entry)
            elif kind == "screen":
                tracker.discover(f"screen:{node.attrib.get('Name', '')}")
                entry = _extract_screen_or_block(
                    node, kind, flow_name, doc, key_index, tracker, type_resolver,
                )
                screens.append(entry)

    # ------------------------------------------------------------------
    # 3. Themes
    # ------------------------------------------------------------------
    themes = _extract_themes(doc, tracker)

    # ------------------------------------------------------------------
    # 4. CSS / stylesheets
    # ------------------------------------------------------------------
    css_assets = _extract_css(doc, tracker)

    # ------------------------------------------------------------------
    # 4a. Design tokens — derived from full CSS bodies (Gap 2)
    # ------------------------------------------------------------------
    design_tokens = _extract_design_tokens(css_assets, tracker)

    # ------------------------------------------------------------------
    # 5. Build inventory
    # ------------------------------------------------------------------
    inventory_items: list[dict[str, Any]] = []

    for f in flows:
        inventory_items.append({
            "id": f"flow:{f['name']}",
            "kind": "flow",
            "name": f["name"],
            "key": f["key"],
        })

    for s in screens:
        inventory_items.append({
            "id": f"{s['kind']}:{s['name']}",
            "kind": s["kind"],
            "name": s["name"],
            "key": s["key"],
            "parent_flow": s["flow"],
        })

    for b in blocks:
        inventory_items.append({
            "id": f"block:{b['name']}",
            "kind": "block",
            "name": b["name"],
            "key": b["key"],
            "parent_flow": b["flow"],
        })

    for t in themes:
        inventory_items.append({
            "id": f"theme:{t['name']}",
            "kind": "theme",
            "name": t["name"],
            "key": t["key"],
        })

    for c in css_assets:
        inventory_items.append({
            "id": f"css:{c['name'] or c['key']}",
            "kind": "stylesheet",
            "name": c["name"],
            "key": c["key"],
        })

    inventory = {
        "domain": "interface",
        "total_items": len(inventory_items),
        "items": inventory_items,
    }

    # ------------------------------------------------------------------
    # 6. Build partitions (one per screen/block)
    # ------------------------------------------------------------------
    partitions: list[dict[str, Any]] = []

    for s in screens:
        partitions.append({
            "partition_id": f"{s['kind']}:{s['name']}",
            "kind": s["kind"],
            "name": s["name"],
            "key": s["key"],
            "flow": s["flow"],
            "widget_count": _count_widgets(s.get("widgets", [])),
            "parameter_count": (
                len(s.get("input_parameters", []))
                + len(s.get("output_parameters", []))
            ),
            "status": "ready",
        })

    for b in blocks:
        partitions.append({
            "partition_id": f"block:{b['name']}",
            "kind": "block",
            "name": b["name"],
            "key": b["key"],
            "flow": b["flow"],
            "widget_count": _count_widgets(b.get("widgets", [])),
            "parameter_count": (
                len(b.get("input_parameters", []))
                + len(b.get("output_parameters", []))
            ),
            "status": "ready",
        })

    partitions_doc = {
        "domain": "interface",
        "partition_strategy": "one_per_screen_or_block",
        "total_partitions": len(partitions),
        "partitions": partitions,
    }

    # ------------------------------------------------------------------
    # 7. Layout inheritance manifest (Gap 8)
    # ------------------------------------------------------------------
    layout_inheritance = _build_layout_inheritance(blocks)

    # ------------------------------------------------------------------
    # 7a. Screen-scoped dataflow manifest
    # ------------------------------------------------------------------
    screen_dataflow = _extract_screen_dataflow_records([*screens, *blocks], css_assets)

    # ------------------------------------------------------------------
    # 8. Assets-resolved manifest (Gap 9)
    # ------------------------------------------------------------------
    # Load this module's evidence index if it already exists on disk.
    evidence_index_path = pkg_root / "evidence" / "index.json"
    evidence_index: list[dict[str, Any]] = []
    if evidence_index_path.exists():
        import json as _json
        try:
            evidence_index = _json.loads(evidence_index_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    assets_resolved = _build_assets_resolved(
        screens, blocks, doc.module_name, evidence_index, tracker=tracker
    )

    # ------------------------------------------------------------------
    # 9. Write all output files
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 8. Extract deep-link route catalogue
    # ------------------------------------------------------------------
    deep_links = _extract_deep_links(screens, blocks)

    write_json(out_dir / "screens.json", screens)
    write_json(out_dir / "blocks.json", blocks)
    write_json(out_dir / "themes.json", themes)
    write_json(out_dir / "assets.json", css_assets)
    write_json(out_dir / "design-tokens.json", design_tokens)
    write_json(out_dir / "inventory.json", inventory)
    write_json(out_dir / "partitions.json", partitions_doc)
    write_json(out_dir / "deep-links.json", deep_links)
    write_json(out_dir / "screen-dataflow.json", screen_dataflow)
    write_json(out_dir / "layout-inheritance.json", layout_inheritance)
    write_json(out_dir / "assets-resolved.json", assets_resolved)
    write_json(out_dir / "traversal-log.json", tracker.to_traversal_dict())
    write_json(out_dir / "coverage.json", tracker.to_coverage_dict())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches: Index(<expr>, "<pattern>") where <pattern> contains a path segment.
# Captures the quoted string (the URL path pattern being matched).
_DEEPLINK_PATTERN_RE = re.compile(
    r'Index\([^,]+,\s*"([^"]+)"',
    re.IGNORECASE,
)

# Matches the last path component from a WebDestination target_ref.
# e.g. "NRWebFlows.Common.NodesShownInESpaceTree.Splash" -> "Splash"
_DEST_SCREEN_RE = re.compile(r'NodesShownInESpaceTree\.(\w+)')

# Name of the event handler action generated by OutSystems for the
# EventfulDeeplinksPlugin OnDeeplinkOpen custom event.
_DEEPLINK_HANDLER_ACTION = "DeeplinkHandlerCentralOnDeeplinkOpen"

# The plugin block that owns the deep-link event.
_DEEPLINK_PLUGIN_BLOCK = "DeeplinkHandlerCentral"


def _extract_deep_links(
    screens: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract deep-link URL patterns from DeeplinkHandlerCentralOnDeeplinkOpen handlers.

    OutSystems wires the ``EventfulDeeplinksPlugin`` by placing a
    ``DeeplinkHandlerCentral`` ``WebBlockInstance`` on each screen or layout
    block that needs deep-link handling.  When the plugin fires its
    ``OnDeeplinkOpen`` custom event, OutSystems generates a client action named
    ``DeeplinkHandlerCentralOnDeeplinkOpen`` on the host block.  Each such
    action's flow contains ``If`` nodes whose ``condition`` uses
    ``Index(<URL expr>, "<path-pattern>") > Minus(1)`` to route the incoming
    URL to a screen.  ``WebDestination`` / ``Nodes.WebDestination`` nodes in
    the same flow record the target screen name.

    This function scans every block's and screen's ``client_actions`` list,
    collects all unique URL path patterns, resolves their destination screens,
    and returns a deduplicated list of deep-link records.

    Returns a list of dicts with at minimum:
        ``pattern``   – URL path fragment used in the routing condition
                        (e.g. ``"SpitFireApp/Splash?Action=Scan"``)
        ``destinations`` – list of screen names the pattern routes to
        ``parameters``  – query-parameter names parsed from the pattern
        ``handler_block`` – name of the OutSystems block that owns the handler
        ``source``    – always ``"EventfulDeeplinksPlugin.DeeplinkHandlerCentral"``
    """
    # pattern -> { destinations: set, handler_blocks: set }
    seen: dict[str, dict[str, Any]] = {}

    def _process_action(action: dict[str, Any], host_name: str) -> None:
        if action.get("name") != _DEEPLINK_HANDLER_ACTION:
            return

        flow = action.get("flow") or {}
        nodes = flow.get("nodes") or []

        # Collect URL patterns from If-node conditions.
        patterns_in_action: list[str] = []
        for node in nodes:
            cond = node.get("condition") or ""
            for match in _DEEPLINK_PATTERN_RE.finditer(cond):
                raw = match.group(1).strip()
                if raw and ("SpitFireApp" in raw or "Action=" in raw or "/" in raw):
                    patterns_in_action.append(raw)

        # Collect destination screen names from WebDestination nodes.
        dests_in_action: list[str] = []
        for node in nodes:
            node_type = node.get("type") or ""
            if "WebDestination" in node_type or "NavigateTo" in node_type:
                ref = node.get("target_ref") or node.get("destination") or ""
                m = _DEST_SCREEN_RE.search(ref)
                if m:
                    dests_in_action.append(m.group(1))

        # Register each unique pattern.
        for pattern in patterns_in_action:
            if pattern not in seen:
                seen[pattern] = {
                    "destinations": set(),
                    "handler_blocks": set(),
                }
            seen[pattern]["destinations"].update(dests_in_action)
            seen[pattern]["handler_blocks"].add(host_name)

    # Walk all extracted blocks and screens.
    for item in (*blocks, *screens):
        host_name = item.get("name") or ""
        for action in item.get("client_actions") or []:
            _process_action(action, host_name)

    # Build output records — one per unique pattern, sorted for stability.
    result: list[dict[str, Any]] = []
    for pattern in sorted(seen):
        info = seen[pattern]
        # Parse query parameters from the pattern string.
        params: list[str] = []
        if "?" in pattern:
            qs = pattern.split("?", 1)[1]
            for kv in qs.split("&"):
                k = kv.split("=")[0]
                if k:
                    params.append(k)

        result.append({
            "pattern": pattern,
            "destinations": sorted(info["destinations"]),
            "parameters": params,
            "handler_block": _DEEPLINK_PLUGIN_BLOCK,
            "handler_blocks_in_module": sorted(info["handler_blocks"]),
            "source": f"EventfulDeeplinksPlugin.{_DEEPLINK_PLUGIN_BLOCK}",
        })

    return result


def _count_widgets(widgets: list[dict[str, Any]]) -> int:
    """Recursively count all widgets in a tree."""
    count = 0
    for w in widgets:
        count += 1
        count += _count_widgets(w.get("children", []))
    return count
