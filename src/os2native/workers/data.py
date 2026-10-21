"""Data domain worker — extracts entities, relationships, static data, and site properties."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.shared.type_resolver import TypeResolver, expression_value
from os2native.xml_loader import OmlDocument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _field_length(node: ET.Element) -> Any:
    return expression_value(node.find("Length"))


def _default_value(node: ET.Element) -> Any:
    return expression_value(node.find("DefaultValue"))


def _attribute_name_from_ref(
    ref: str | None,
    attr_map: dict[str, dict[str, Any]],
    entities_frag: ET.Element | None,
) -> str | None:
    """Resolve an attribute Ref string (e.g. ``^/Attributes.KEY``) to its Name."""
    if ref is None:
        return None
    attr_key = ref.rsplit(".", 1)[-1]
    # Authoritative: match by Key against the entity's attribute elements.
    # (Doing this first avoids the name-suffix heuristic below binding the
    # wrong attribute when one attribute's Name is a suffix of another's Key,
    # e.g. ref ".../AreaCode" wrongly matching an attribute named "Code".)
    if entities_frag is not None:
        for node in entities_frag.iter("EntityAttribute"):
            if node.attrib.get("Key") == attr_key:
                return node.attrib.get("Name")
    # Fallback heuristic for refs that are not key-based: match by name suffix.
    for attr in attr_map.values():
        if attr["name"] and ref.endswith(attr["name"]):
            return attr["name"]
    return attr_key


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_attribute(attr: ET.Element, resolver: TypeResolver) -> dict[str, Any]:
    return {
        "name": attr.attrib.get("Name"),
        "label": attr.attrib.get("Label"),
        "mandatory": attr.attrib.get("IsMandatory") == "Yes",
        "auto_number": attr.attrib.get("IsAutoNumber") == "Yes",
        "type": resolver.resolve(attr.attrib.get("Type")),
        "length": _field_length(attr),
        "default": _default_value(attr),
    }


def _parse_entity_action(
    entity: ET.Element,
    action: ET.Element,
    resolver: TypeResolver,
) -> dict[str, Any]:
    tag = _local_name(action.tag)
    if tag == "EntityActions.GetEntity":
        display_name = f"Get{entity.attrib.get('Name')}"
    else:
        display_name = tag.split(".")[-1]
    return {
        "name": display_name,
        "kind": tag,
        "inputs": [
            {
                "name": param.attrib.get("Name"),
                "mandatory": param.attrib.get("IsMandatory") == "Yes",
                "type": resolver.resolve(
                    param.attrib.get("Type"),
                    current_entity=entity,
                ),
            }
            for param in action.findall("./InputParameters/*")
        ],
        "outputs": [
            {
                "name": param.attrib.get("Name"),
                "type": resolver.resolve(
                    param.attrib.get("Type"),
                    current_entity=entity,
                ),
            }
            for param in action.findall("./OutputParameters/*")
        ],
    }


def _parse_local_entity(
    entity: ET.Element,
    resolver: TypeResolver,
    entities_frag: ET.Element | None,
) -> dict[str, Any]:
    attrs = [
        _parse_attribute(attr, resolver)
        for attr in entity.findall("./Attributes/EntityAttribute")
    ]
    attr_map = {a["name"]: a for a in attrs if a["name"]}

    # Static records
    records: list[dict[str, Any]] = []
    for record in entity.findall("./StaticRecords/StaticRecord"):
        values: dict[str, Any] = {}
        for sv in record.findall("./AttributeValues/StaticRecordAttributeValue"):
            attr_ref = sv.attrib.get("Attribute", "")
            attr_key = attr_ref.rsplit(".", 1)[-1]
            attr_name: str | None = None
            for ea in entity.findall("./Attributes/EntityAttribute"):
                if ea.attrib.get("Key") == attr_key:
                    attr_name = ea.attrib.get("Name")
                    break
            values[attr_name or attr_key] = expression_value(sv.find("Value"))
        records.append({"name": record.attrib.get("Name"), "values": values})

    return {
        "name": entity.attrib.get("Name"),
        "description": entity.attrib.get("Description", ""),
        "pretty_name": entity.attrib.get("PrettyName"),
        "pretty_name_plural": entity.attrib.get("PrettyNamePlural"),
        "is_static": entity.attrib.get("IsStaticEntity") == "Yes",
        "is_public": entity.attrib.get("Public", "No") == "Yes",
        "identifier_attribute": _attribute_name_from_ref(
            entity.attrib.get("Identifier"), attr_map, entities_frag,
        ),
        "label_attribute": _attribute_name_from_ref(
            entity.attrib.get("Label"), attr_map, entities_frag,
        ),
        "order_by_attribute": _attribute_name_from_ref(
            entity.attrib.get("OrderBy"), attr_map, entities_frag,
        ),
        "is_active_attribute": _attribute_name_from_ref(
            entity.attrib.get("IsActive"), attr_map, entities_frag,
        ),
        "attributes": attrs,
        "static_records": records,
        "entity_actions": [
            _parse_entity_action(entity, action, resolver)
            for action in entity.findall("./EntityActions/*")
        ],
    }


def _parse_site_property(prop: ET.Element, resolver: TypeResolver) -> dict[str, Any]:
    read_only = prop.attrib.get("IsReadOnlySiteProperty") == "Yes"
    return {
        "name": prop.attrib.get("Name"),
        "description": _text_or_none(prop.attrib.get("Description")),
        "read_only": read_only,
        # Read-only site properties (TenantId, TenantName) are generated by the
        # platform in every module; developers cannot create read-only ones.
        "is_platform": read_only,
        "is_secret": prop.attrib.get("IsSecret", "No") == "Yes",
        "type": resolver.resolve(prop.attrib.get("Type")),
        "default": _default_value(prop),
    }


def _parse_reference_entity_attribute(
    attr: ET.Element,
    resolver: TypeResolver,
) -> dict[str, Any]:
    return {
        "name": attr.attrib.get("Name"),
        "label": attr.attrib.get("Label"),
        "mandatory": attr.attrib.get("IsMandatory") == "Yes",
        "auto_number": attr.attrib.get("IsAutoNumber") == "Yes",
        "type": resolver.resolve(attr.attrib.get("Type")),
        "length": _field_length(attr),
        "default": _default_value(attr),
    }


def _parse_reference_summary(
    doc: OmlDocument,
    resolver: TypeResolver,
) -> list[dict[str, Any]]:
    refs_frag = doc.fragment("References")
    if refs_frag is None:
        return []

    summaries: list[dict[str, Any]] = []
    for ref_node in refs_frag.findall("Reference"):
        ref_key = ref_node.attrib.get("Key", "")
        ref_mod = resolver.reference_modules.get(ref_key)

        entities = [
            {
                "name": ent.attrib.get("Name"),
                "attributes": [
                    _parse_reference_entity_attribute(a, resolver)
                    for a in ent.findall("./Attributes/ReferenceEntityAttribute")
                ],
                "entity_actions": [
                    _parse_entity_action(ent, act, resolver)
                    for act in ent.findall("./EntityActions/*")
                ],
            }
            for ent in ref_node.findall("./ReferenceEntities/ReferenceEntity")
        ]
        structures = [
            {
                "name": struct.attrib.get("Name"),
                "attributes": [
                    {
                        "name": a.attrib.get("Name"),
                        "label": a.attrib.get("Label"),
                        "mandatory": a.attrib.get("IsMandatory") == "Yes",
                        "type": resolver.resolve(
                            a.attrib.get("Type"),
                            current_reference=ref_mod,
                        ),
                        "length": _field_length(a),
                        "default": _default_value(a),
                    }
                    for a in struct.findall("./Attributes/ReferenceStructureAttribute")
                ],
            }
            for struct in ref_node.findall("./ReferenceStructures/ReferenceStructure")
        ]

        if not entities and not structures:
            continue
        summaries.append({
            "module_name": ref_node.attrib.get("Name"),
            "reference_type": ref_node.attrib.get("ReferenceType"),
            "espace_type": ref_node.attrib.get("EspaceType"),
            "entities": entities,
            "structures": structures,
        })
    return summaries


# ---------------------------------------------------------------------------
# Relationship extraction
# ---------------------------------------------------------------------------


def _build_relationships(
    entities: list[dict[str, Any]],
    resolver: TypeResolver,
) -> list[dict[str, Any]]:
    """Derive foreign-key relationships from entity attribute types."""
    relationships: list[dict[str, Any]] = []
    for entity in entities:
        entity_name = entity["name"]
        for attr in entity.get("attributes", []):
            type_info = attr.get("type", {})
            kind = type_info.get("kind")
            if kind == "entity_identifier":
                target = type_info.get("entity_name")
                if target and target != entity_name:
                    relationships.append({
                        "from_entity": entity_name,
                        "from_attribute": attr["name"],
                        "to_entity": target,
                        "kind": "foreign_key",
                        "reference_module": type_info.get("reference_module"),
                    })
            elif kind in ("local_entity", "reference_entity"):
                target = type_info.get("name")
                if target:
                    relationships.append({
                        "from_entity": entity_name,
                        "from_attribute": attr["name"],
                        "to_entity": target,
                        "kind": "entity_reference",
                        "reference_module": type_info.get("reference_module"),
                    })
    return relationships


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def _build_inventory(
    doc: OmlDocument,
    entities: list[dict[str, Any]],
    site_properties: list[dict[str, Any]],
    reference_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "module_name": doc.module_name,
        "local_entities": [
            {"name": e["name"], "is_static": e["is_static"], "attribute_count": len(e["attributes"])}
            for e in entities
        ],
        "reference_entities": [
            {
                "module": ref["module_name"],
                "entities": [{"name": e["name"]} for e in ref["entities"]],
                "structures": [{"name": s["name"]} for s in ref["structures"]],
            }
            for ref in reference_summaries
        ],
        "site_properties": [{"name": sp["name"]} for sp in site_properties],
        "totals": {
            "local_entities": len(entities),
            "reference_modules_with_data": len(reference_summaries),
            "reference_entities": sum(
                len(r["entities"]) for r in reference_summaries
            ),
            "reference_structures": sum(
                len(r["structures"]) for r in reference_summaries
            ),
            "site_properties": len(site_properties),
        },
    }


# ---------------------------------------------------------------------------
# Partitions
# ---------------------------------------------------------------------------


def _build_partitions(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "partition_id": f"entity:{e['name']}",
            "entity_name": e["name"],
            "is_static": e["is_static"],
            "status": "ready",
        }
        for e in entities
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(doc: OmlDocument, pkg_root: Path) -> None:
    """Extract data domain artifacts from *doc* and write to *pkg_root*/data/."""
    out = pkg_root / "data"
    tracker = CoverageTracker(domain="data")
    resolver = TypeResolver.from_doc(doc)
    entities_frag = doc.fragment("Entities")

    # --- Local entities ---
    entity_nodes = doc.fragment_children("Entities")
    # Filter to actual Entity elements (fragments may contain other children)
    entity_nodes = [n for n in entity_nodes if n.tag == "Entity"]

    entities: list[dict[str, Any]] = []
    for node in entity_nodes:
        name = node.attrib.get("Name", node.attrib.get("Key", "?"))
        tracker.discover(f"entity:{name}", path="Entities")
        parsed = _parse_local_entity(node, resolver, entities_frag)
        entities.append(parsed)
        tracker.capture(f"entity:{name}", path="Entities")

    # --- Static data (collected from entities with static records) ---
    static_data: list[dict[str, Any]] = []
    for entity in entities:
        if entity["static_records"]:
            static_data.append({
                "entity_name": entity["name"],
                "records": entity["static_records"],
            })

    # --- Site properties ---
    sp_nodes = doc.fragment_children("SiteProperties")
    site_properties: list[dict[str, Any]] = []
    for node in sp_nodes:
        name = node.attrib.get("Name", node.attrib.get("Key", "?"))
        tracker.discover(f"site_property:{name}", path="SiteProperties")
        site_properties.append(_parse_site_property(node, resolver))
        tracker.capture(f"site_property:{name}", path="SiteProperties")

    # --- Reference summaries (track in coverage) ---
    reference_summaries = _parse_reference_summary(doc, resolver)
    for ref in reference_summaries:
        for ent in ref["entities"]:
            item_id = f"ref_entity:{ref['module_name']}.{ent['name']}"
            tracker.discover(item_id, path="References")
            tracker.capture(item_id, path="References")
        for struct in ref["structures"]:
            item_id = f"ref_structure:{ref['module_name']}.{struct['name']}"
            tracker.discover(item_id, path="References")
            tracker.capture(item_id, path="References")

    # --- Relationships ---
    relationships = _build_relationships(entities, resolver)

    # --- Roles / permissions ---
    roles_and_permissions: list[Any] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "Roles" or frag_name.startswith("Roles#"):
            for child in frag_elem:
                if child.tag == "Role":
                    name = child.attrib.get("Name", "")
                    tracker.discover(f"role:{name}", path="Roles")
                    roles_and_permissions.append({
                        "name": name,
                        "description": child.attrib.get("Description", ""),
                        "is_persistent": child.attrib.get("IsPersistent", "No") == "Yes",
                        "is_public": child.attrib.get("Public", "No") == "Yes",
                    })
                    tracker.capture(f"role:{name}", path="Roles")

    # --- Structures ---
    structures: list[dict[str, Any]] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "Structures" or frag_name.startswith("Structures#"):
            for child in frag_elem:
                if child.tag == "Structure":
                    s_name = child.attrib.get("Name", "")
                    tracker.discover(f"structure:{s_name}", path="Structures")
                    attrs = []
                    for sa in child.findall("StructureAttribute") + child.findall("./Attributes/StructureAttribute"):
                        attrs.append({
                            "name": sa.attrib.get("Name", ""),
                            "type": resolver.resolve(sa.attrib.get("Type")),
                            "is_mandatory": sa.attrib.get("IsMandatory", "No") == "Yes",
                            "length": _field_length(sa),
                            "default": _default_value(sa),
                            "description": sa.attrib.get("Description", ""),
                        })
                    structures.append({
                        "name": s_name,
                        "description": child.attrib.get("Description", ""),
                        "is_public": child.attrib.get("Public", "No") == "Yes",
                        "attributes": attrs,
                    })
                    tracker.capture(f"structure:{s_name}", path="Structures")

    # --- Client variables ---
    client_variables: list[dict[str, Any]] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "ClientVariables" or frag_name.startswith("ClientVariables#"):
            for child in frag_elem:
                if "ClientVariable" in child.tag:
                    cv_name = child.attrib.get("Name", "")
                    tracker.discover(f"client_var:{cv_name}", path="ClientVariables")
                    client_variables.append({
                        "name": cv_name,
                        "type": resolver.resolve(child.attrib.get("Type")),
                        "description": child.attrib.get("Description", ""),
                        "default": _default_value(child),
                    })
                    tracker.capture(f"client_var:{cv_name}", path="ClientVariables")

    # --- Multilingual locales ---
    locales: list[dict[str, Any]] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name in ("Locales", "MultilingualLocales") or frag_name.startswith("Locales#"):
            for child in frag_elem:
                locales.append({
                    "name": child.attrib.get("Name", ""),
                    "label": child.attrib.get("Label", child.attrib.get("Name", "")),
                    "code": child.attrib.get("Locale", child.attrib.get("Code", "")),
                })

    # --- Entity diagrams ---
    entity_diagrams: list[dict[str, Any]] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "EntityDiagrams" or frag_name.startswith("EntityDiagrams#"):
            for child in frag_elem:
                if child.tag == "EntityDiagram" or child.tag == "Flows.EntityDiagram":
                    diag_name = child.attrib.get("Name", "")
                    diag_key = child.attrib.get("Key", "")
                    # Collect referenced entity keys from EntityDiagramNode children
                    entity_refs: list[str] = []
                    for node in child:
                        entity_ref = node.attrib.get("Entity", node.attrib.get("EntityKey", ""))
                        if entity_ref:
                            entity_refs.append(entity_ref)
                    entity_diagrams.append({
                        "name": diag_name,
                        "key": diag_key,
                        "description": child.attrib.get("Description", ""),
                        "entity_refs": entity_refs,
                    })
    # Also search ESpace direct children
    for child in doc.espace:
        if child.tag == "EntityDiagram":
            diag_name = child.attrib.get("Name", "")
            diag_key = child.attrib.get("Key", "")
            if not any(d["key"] == diag_key for d in entity_diagrams):
                entity_refs = []
                for node in child:
                    entity_ref = node.attrib.get("Entity", node.attrib.get("EntityKey", ""))
                    if entity_ref:
                        entity_refs.append(entity_ref)
                entity_diagrams.append({
                    "name": diag_name,
                    "key": diag_key,
                    "description": child.attrib.get("Description", ""),
                    "entity_refs": entity_refs,
                })

    # --- User exceptions ---
    user_exceptions: list[dict[str, Any]] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == "UserExceptions" or frag_name.startswith("UserExceptions#"):
            for child in frag_elem:
                if _local_name(child.tag) != "UserException":
                    continue
                ex_name = child.attrib.get("Name", "")
                tracker.discover(f"user_exception:{ex_name}", path="UserExceptions")
                user_exceptions.append({
                    "name": ex_name,
                    "key": child.attrib.get("Key", ""),
                    "description": child.attrib.get("Description", ""),
                })
                tracker.capture(f"user_exception:{ex_name}", path="UserExceptions")

    # --- Inventory ---
    inventory = _build_inventory(doc, entities, site_properties, reference_summaries)

    # --- Partitions ---
    partitions = _build_partitions(entities)

    # --- Write outputs ---
    write_json(out / "entities.json", entities)
    write_json(out / "relationships.json", relationships)
    write_json(out / "static-data.json", static_data)
    write_json(out / "site-properties.json", site_properties)
    write_json(out / "roles-and-permissions.json", roles_and_permissions)
    write_json(out / "user-exceptions.json", user_exceptions)
    write_json(out / "structures.json", structures)
    write_json(out / "client-variables.json", client_variables)
    write_json(out / "multilingual-locales.json", locales)
    write_json(out / "entity-diagrams.json", entity_diagrams)
    write_json(out / "inventory.json", inventory)
    write_json(out / "partitions.json", partitions)
    write_json(out / "traversal-log.json", tracker.to_traversal_dict())
    write_json(out / "coverage.json", tracker.to_coverage_dict())
