"""Resolve ParsedExpression ASTs to human-readable expression strings."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from os2native.shared.key_index import KeyIndex


# Operator symbols for BinaryOperation nodes.
_BINARY_OPS: dict[str, str] = {
    "Eq": "=",
    "NEq": "<>",
    "GT": ">",
    "GTE": ">=",
    "LT": "<",
    "LTE": "<=",
    "Or": "or",
    "And": "and",
    "Add": "+",
    "Sub": "-",
    "Mul": "*",
    "Div": "/",
    "Mod": "mod",
    "Like": "like",
}


def resolve_expression(node: ET.Element, key_index: KeyIndex) -> str:
    """Walk a ParsedExpression AST and return a human-readable string.

    *node* should be the <ParsedExpression> element itself.  If it has no
    children the empty string is returned.
    """
    children = list(node)
    if not children:
        return ""
    return _resolve_node(children[0], key_index)


def _resolve_node(elem: ET.Element, ki: KeyIndex) -> str:
    tag = _local_name(elem.tag)

    # Literals
    if tag in ("Integer", "Decimal", "Text", "Boolean"):
        val = elem.attrib.get("Value", "")
        if tag == "Text":
            return f'"{val}"'
        return val

    # Identifier — single variable reference
    if tag == "Identifier":
        ref = elem.attrib.get("Ref", "")
        return ki.resolve_ref(ref) if ref else elem.attrib.get("NameRef", elem.attrib.get("Name", "?"))

    # CompoundIdentifier — dotted path reference
    if tag == "CompoundIdentifier":
        ref = elem.attrib.get("Ref", "")
        children = list(elem)
        if ref:
            resolved = ki.resolve_ref(ref)
            base = _compact_ref(resolved)
        else:
            base = elem.attrib.get("NameRef", elem.attrib.get("Name", "?"))
        suffixes = [_relative_suffix(base, _resolve_node(child, ki)) for child in children]
        suffixes = [suffix for suffix in suffixes if suffix and suffix != "?"]
        if suffixes:
            return ".".join([base, *suffixes])
        return base

    # BinaryOperation
    if tag == "BinaryOperation":
        op = elem.attrib.get("Operator", "?")
        op_str = _BINARY_OPS.get(op, op)
        children = list(elem)
        if len(children) >= 2:
            left = _resolve_node(children[0], ki)
            right = _resolve_node(children[1], ki)
            return f"{left} {op_str} {right}"
        return f"({op_str} ?)"

    # UnaryOperation
    if tag == "UnaryOperation":
        op = elem.attrib.get("Operator", "?")
        children = list(elem)
        if children:
            operand = _resolve_node(children[0], ki)
            if op == "Not":
                return f"not {operand}"
            return f"{op}({operand})"
        return f"{op}(?)"

    # Call — function invocation
    if tag == "Call":
        ref = elem.attrib.get("Ref", "")
        func_name = ki.resolve_ref(ref) if ref else elem.attrib.get("NameRef", elem.attrib.get("Name", "?"))
        # Take last segment for readability
        parts = func_name.split(".")
        func_name = parts[-1] if parts else func_name
        children = list(elem)
        args = ", ".join(_resolve_node(c, ki) for c in children)
        return f"{func_name}({args})"

    # Concat
    if tag == "Concat":
        children = list(elem)
        parts = [_resolve_node(c, ki) for c in children]
        return " + ".join(parts)

    # IfExpression (ternary)
    if tag == "IfExpression":
        children = list(elem)
        if len(children) >= 3:
            cond = _resolve_node(children[0], ki)
            then = _resolve_node(children[1], ki)
            else_ = _resolve_node(children[2], ki)
            return f"If({cond}, {then}, {else_})"
        return "If(?)"

    # NullLiteral
    if tag == "NullLiteral":
        return "NullIdentifier()"

    # Fallback: try to recurse into children
    children = list(elem)
    if children:
        return _resolve_node(children[0], ki)

    return f"<{tag}>"


def _local_name(tag: str) -> str:
    """Strip namespace URI from an element tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _compact_ref(resolved: str) -> str:
    """Return the short readable form used in generated expression strings."""
    parts = resolved.split(".")
    if len(parts) > 2:
        return ".".join(parts[-2:])
    return resolved


def _relative_suffix(base: str, suffix: str) -> str:
    """Trim an already-resolved child path relative to its compound base."""
    marker = f"{base}."
    if marker in suffix:
        return suffix.rsplit(marker, 1)[-1]
    return suffix
