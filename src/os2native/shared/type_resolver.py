"""Resolve OutSystems encoded type keys to human-readable type information."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from os2native.xml_loader import OmlDocument


# Primitive type keys from the OutSystems platform metamodel.
PRIMITIVE_TYPE_NAMES: dict[str, str] = {
    "%j2Lc0phK0kuggij4gQYfAw": "Integer",
    "%H6G+eTAigEi7LPUbg3Ey0g": "Long Integer",
    "%uROOBXPvQEyU76NWO+1uxQ": "Text",
    "%oD0fxvc7hUOX_305zIXIlg": "Boolean",
    "%snOQIa2+VEi7MGyYOu7OpA": "Decimal",
    "%AIYAbYIrH0SUg8DUlzCMjQ": "Email",
    "%uCRxZE_fNUyUSGrF0nnRIw": "PhoneNumber",
    "%TI32kQokwEyLhBq6zdg2Tw": "DateTime",
    "%T8i8SBbo_EyGNoo0ssngsw": "Binary Data",
    "%DnfdUFhShE2uIYCwnadVLQ": "Date",
    "%EYiTxdlAx0OG1Vuo4D_l4A": "Currency",
    "%Qs4rEIsOoUmXjigHsikpCg": "Time",
    "%xJyn4vH0KkCfmSAS_CIQBg": "Object",
    "%NRGbx9G_WEG4DdPOY7+u7Q": "Object",
    "%vsr38BCkOUqwSFvFoBzuGw": "Object",
    "%V0DejjDtIU2u1eHsZ158BA": "Object",
    "%tRA5+npp1UKZWp2HHMgLSg": "Integer",
    "%hR+HzvoUvkKth+waVG8Vww": "Long Integer",
    "%fGmKYNVfKUiGw0c2kUmb1Q": "Integer",
}

# System exception keys from the OutSystems platform metamodel.
SYSTEM_EXCEPTION_NAMES: dict[str, str] = {
    "%VI1iiDkgT0Szaid0LqgP_w": "AllExceptions",
    "%TcVUoQU5mE6mvZaG9PwyCg": "UserException",
    "%3_Wys5NKV0WNVOSw76lvpg": "Exception",
    "%7hIDxkv2H0WwEAUKGt6QRg": "Exception",
    "%pI8fOxT7MEKDG3D6ig8H1Q": "Exception",
    "%WavjP7iMmU+Txu85ElIsaA": "Exception",
}

# ---- Type reference regex patterns ----

RELATIVE_REFERENCE_STRUCTURE = re.compile(
    r"^ReferenceStructure:\^/\^/ReferenceStructures\.(.+)$"
)
RELATIVE_ENTITY_IDENTIFIER = re.compile(
    r"^EntityIdentifierType:\^/\^/IdentifierType$"
)
LOCAL_ENTITY = re.compile(r"^Entity:/Entities\.([^/]+)$")
LOCAL_ENTITY_IDENTIFIER = re.compile(
    r"^EntityIdentifierType:/Entities\.([^/]+)/IdentifierType$"
)
ANON_STRUCT = re.compile(r"^AnonymousStructure:/AnonymousStructures\.([^/]+)$")
LIST_TYPE = re.compile(r"^ListType:/ListTypes\.([^/]+)$")
REF_STRUCT = re.compile(
    r"^ReferenceStructure:/References\.([^/]+)/ReferenceStructures\.([^/]+)$"
)
REF_ENTITY = re.compile(
    r"^ReferenceEntity:/References\.([^/]+)/ReferenceEntities\.([^/]+)$"
)
REF_ENTITY_IDENTIFIER = re.compile(
    r"^EntityIdentifierType:/References\.([^/]+)/ReferenceEntities\.([^/]+)/IdentifierType$"
)
LOCAL_STRUCTURE = re.compile(r"^Structure:/Structures\.([^/]+)$")
CUSTOM_STRUCTURE = re.compile(
    r"^CustomStructure-OutSystems\.Plugin\.REST\.RestStructure:/CustomClients\.([^/]+)/CustomStructures\.([^/]+)$"
)
SELF_ENTITY_IDENTIFIER = re.compile(r"^EntityIdentifierType:\^/IdentifierType$")


class RefModule:
    """A referenced external module with its entities and structures."""

    __slots__ = ("key", "name", "entity_nodes", "structure_nodes")

    def __init__(
        self,
        key: str,
        name: str,
        entity_nodes: dict[str, ET.Element],
        structure_nodes: dict[str, ET.Element],
    ) -> None:
        self.key = key
        self.name = name
        self.entity_nodes = entity_nodes
        self.structure_nodes = structure_nodes


class TypeResolver:
    """Resolves OutSystems encoded type strings to structured type info."""

    def __init__(
        self,
        local_entities: dict[str, ET.Element],
        anonymous_structures: dict[str, ET.Element],
        list_types: dict[str, ET.Element],
        reference_modules: dict[str, RefModule],
        local_structures: dict[str, ET.Element] | None = None,
        custom_structures: dict[str, ET.Element] | None = None,
    ) -> None:
        self.local_entities = local_entities
        self.anonymous_structures = anonymous_structures
        self.list_types = list_types
        self.reference_modules = reference_modules
        self.local_structures = local_structures or {}
        self.custom_structures = custom_structures or {}

    @classmethod
    def from_doc(cls, doc: "OmlDocument") -> "TypeResolver":
        """Build a TypeResolver from an OmlDocument."""

        entities_frag = doc.fragment("Entities")
        local_entities: dict[str, ET.Element] = {}
        if entities_frag is not None:
            for node in entities_frag.findall("Entity"):
                key = node.attrib.get("Key", "")
                if key:
                    local_entities[key] = node

        anon_frag = doc.fragment("AnonymousStructures")
        anon_structs: dict[str, ET.Element] = {}
        if anon_frag is not None:
            for node in anon_frag.findall("AnonymousStructure"):
                key = node.attrib.get("Key", "")
                if key:
                    anon_structs[key] = node

        list_frag = doc.fragment("ListTypes")
        list_types: dict[str, ET.Element] = {}
        if list_frag is not None:
            for node in list_frag.findall("ListType"):
                key = node.attrib.get("Key", "")
                if key:
                    list_types[key] = node

        refs_frag = doc.fragment("References")
        ref_modules: dict[str, RefModule] = {}
        if refs_frag is not None:
            for ref in refs_frag.findall("Reference"):
                key = ref.attrib.get("Key", "")
                if not key:
                    continue
                ref_modules[key] = RefModule(
                    key=key,
                    name=ref.attrib.get("Name", key),
                    entity_nodes={
                        n.attrib["Key"]: n
                        for n in ref.findall("./ReferenceEntities/ReferenceEntity")
                        if "Key" in n.attrib
                    },
                    structure_nodes={
                        n.attrib["Key"]: n
                        for n in ref.findall("./ReferenceStructures/ReferenceStructure")
                        if "Key" in n.attrib
                    },
                )

        # Index local structures (Structure:/Structures.<KEY>)
        local_structures: dict[str, ET.Element] = {}
        for frag_name, frag_elem in doc.fragments.items():
            if frag_name == "Structures" or frag_name.startswith("Structures#"):
                for node in frag_elem.findall("Structure"):
                    key = node.attrib.get("Key", "")
                    if key:
                        local_structures[key] = node

        # Index REST custom structures
        # (CustomStructure-OutSystems.Plugin.REST.RestStructure:/CustomClients.<CK>/CustomStructures.<SK>)
        custom_structures: dict[str, ET.Element] = {}
        for frag_name, frag_elem in doc.fragments.items():
            if frag_name == "CustomClients" or frag_name.startswith("CustomClients#"):
                for client in frag_elem:
                    client_key = client.attrib.get("Key", "")
                    structs_elem = client.find("CustomStructures")
                    if structs_elem is not None:
                        for cs in structs_elem:
                            cs_key = cs.attrib.get("Key", "")
                            if client_key and cs_key:
                                # Store with composite key "client_key/cs_key"
                                custom_structures[f"{client_key}/{cs_key}"] = cs
            elif frag_name.startswith("CustomStructures#"):
                # The definitions usually live in a keyed fragment (the
                # client's inline <CustomStructures> is empty): the suffix
                # after '#' is the owning client's key.
                client_key = frag_name.split("#", 1)[1]
                if not client_key:
                    continue
                for cs in frag_elem:
                    cs_key = cs.attrib.get("Key", "")
                    if cs_key:
                        custom_structures[f"{client_key}/{cs_key}"] = cs

        return cls(local_entities, anon_structs, list_types, ref_modules,
                   local_structures, custom_structures)

    def resolve(
        self,
        type_str: str | None,
        *,
        current_entity: ET.Element | None = None,
        current_reference: RefModule | None = None,
        depth: int = 0,
        _visited: set | None = None,
    ) -> dict[str, Any]:
        """Resolve an encoded type string to a structured type descriptor."""
        if type_str is None:
            return {"kind": "unknown", "raw": None, "name": "Unknown"}
        if _visited is None:
            _visited = set()
        if depth > 6:
            return {"kind": "max_depth", "raw": type_str, "name": type_str}

        # Primitive types
        if type_str.startswith("%"):
            return {
                "kind": "primitive",
                "raw": type_str,
                "name": PRIMITIVE_TYPE_NAMES.get(type_str, type_str),
            }

        # Relative entity identifier (^/^/IdentifierType)
        if RELATIVE_ENTITY_IDENTIFIER.match(type_str):
            entity_name = (
                current_entity.attrib.get("Name") if current_entity is not None else None
            )
            return {
                "kind": "entity_identifier",
                "raw": type_str,
                "name": f"{entity_name} Identifier" if entity_name else "Identifier",
                "entity_name": entity_name,
            }

        # Local entity
        m = LOCAL_ENTITY.match(type_str)
        if m:
            key = m.group(1)
            node = self.local_entities.get(key)
            if node is None:
                return {"kind": "local_entity", "raw": type_str, "key": key, "name": key, "missing": True}
            visit_key = ("local_entity", key)
            if visit_key in _visited:
                return {"kind": "local_entity", "raw": type_str, "key": key, "name": node.attrib.get("Name")}
            return {
                "kind": "local_entity",
                "raw": type_str,
                "key": key,
                "name": node.attrib.get("Name"),
            }

        # Local entity identifier
        m = LOCAL_ENTITY_IDENTIFIER.match(type_str)
        if m:
            key = m.group(1)
            node = self.local_entities.get(key)
            ent_name = node.attrib.get("Name") if node is not None else key
            return {
                "kind": "entity_identifier",
                "raw": type_str,
                "name": f"{ent_name} Identifier",
                "entity_name": ent_name,
            }

        # Anonymous structure
        m = ANON_STRUCT.match(type_str)
        if m:
            key = m.group(1)
            node = self.anonymous_structures.get(key)
            if node is None:
                return {"kind": "anonymous_structure", "raw": type_str, "key": key, "name": key, "missing": True}
            visit_key = ("anonymous_structure", key)
            if visit_key in _visited:
                return {"kind": "anonymous_structure", "raw": type_str, "key": key, "name": node.attrib.get("Name", key)}
            next_visited = _visited | {visit_key}
            return {
                "kind": "anonymous_structure",
                "raw": type_str,
                "key": key,
                "name": node.attrib.get("Name", key),
                "attributes": [
                    self._parse_anon_attr(attr, current_reference=current_reference, depth=depth + 1, _visited=next_visited)
                    for attr in node.findall("./Attributes/AnonymousStructureAttribute")
                ],
            }

        # List type
        m = LIST_TYPE.match(type_str)
        if m:
            key = m.group(1)
            node = self.list_types.get(key)
            if node is None:
                return {"kind": "list", "raw": type_str, "key": key, "name": f"List ({key})", "missing": True}
            element_type = self.resolve(
                node.attrib.get("ElementType"),
                current_entity=current_entity,
                current_reference=current_reference,
                depth=depth + 1,
                _visited=set(_visited),
            )
            elem_name = element_type.get("name", key)
            return {
                "kind": "list",
                "raw": type_str,
                "key": key,
                "name": f"{elem_name} List",
                "element_type": element_type,
            }

        # Reference structure
        m = REF_STRUCT.match(type_str)
        if m:
            ref_key, struct_key = m.groups()
            ref = self.reference_modules.get(ref_key)
            node = ref.structure_nodes.get(struct_key) if ref else None
            if node is None:
                return {
                    "kind": "reference_structure",
                    "raw": type_str,
                    "reference_key": ref_key,
                    "key": struct_key,
                    "name": struct_key,
                    "missing": True,
                }
            visit_key = ("reference_structure", ref_key, struct_key)
            if visit_key in _visited:
                return {
                    "kind": "reference_structure",
                    "raw": type_str,
                    "reference_module": ref.name,
                    "key": struct_key,
                    "name": node.attrib.get("Name"),
                }
            next_visited = _visited | {visit_key}
            return {
                "kind": "reference_structure",
                "raw": type_str,
                "reference_module": ref.name,
                "reference_key": ref_key,
                "key": struct_key,
                "name": node.attrib.get("Name"),
                "attributes": [
                    self._parse_ref_struct_attr(attr, current_reference=ref, depth=depth + 1, _visited=next_visited)
                    for attr in node.findall("./Attributes/ReferenceStructureAttribute")
                ],
            }

        # Relative reference structure (^/^/ReferenceStructures.KEY)
        m = RELATIVE_REFERENCE_STRUCTURE.match(type_str)
        if m and current_reference is not None:
            struct_key = m.group(1)
            return self.resolve(
                f"ReferenceStructure:/References.{current_reference.key}/ReferenceStructures.{struct_key}",
                current_reference=current_reference,
                depth=depth + 1,
                _visited=set(_visited),
            )

        # Reference entity
        m = REF_ENTITY.match(type_str)
        if m:
            ref_key, entity_key = m.groups()
            ref = self.reference_modules.get(ref_key)
            node = ref.entity_nodes.get(entity_key) if ref else None
            if node is None:
                return {
                    "kind": "reference_entity",
                    "raw": type_str,
                    "reference_key": ref_key,
                    "key": entity_key,
                    "name": entity_key,
                    "missing": True,
                }
            visit_key = ("reference_entity", ref_key, entity_key)
            if visit_key in _visited:
                return {
                    "kind": "reference_entity",
                    "raw": type_str,
                    "reference_module": ref.name,
                    "name": node.attrib.get("Name"),
                    "key": entity_key,
                }
            return {
                "kind": "reference_entity",
                "raw": type_str,
                "reference_module": ref.name,
                "reference_key": ref_key,
                "key": entity_key,
                "name": node.attrib.get("Name"),
            }

        # Reference entity identifier
        m = REF_ENTITY_IDENTIFIER.match(type_str)
        if m:
            ref_key, entity_key = m.groups()
            ref = self.reference_modules.get(ref_key)
            node = ref.entity_nodes.get(entity_key) if ref else None
            ent_name = node.attrib.get("Name") if node is not None else entity_key
            return {
                "kind": "entity_identifier",
                "raw": type_str,
                "name": f"{ent_name} Identifier",
                "entity_name": ent_name,
                "reference_module": ref.name if ref is not None else ref_key,
            }

        # Local structure (Structure:/Structures.<KEY>)
        m = LOCAL_STRUCTURE.match(type_str)
        if m:
            key = m.group(1)
            node = self.local_structures.get(key)
            return {
                "kind": "local_structure",
                "raw": type_str,
                "key": key,
                "name": node.attrib.get("Name") if node is not None else key,
                "missing": node is None,
            }

        # REST custom structure
        m = CUSTOM_STRUCTURE.match(type_str)
        if m:
            client_key, struct_key = m.groups()
            composite = f"{client_key}/{struct_key}"
            node = self.custom_structures.get(composite)
            if node is None:
                return {
                    "kind": "custom_structure",
                    "raw": type_str,
                    "client_key": client_key,
                    "key": struct_key,
                    "name": struct_key,
                    "missing": True,
                }
            visit_key = ("custom_structure", composite)
            if visit_key in _visited:
                return {
                    "kind": "custom_structure",
                    "raw": type_str,
                    "client_key": client_key,
                    "key": struct_key,
                    "name": node.attrib.get("Name", struct_key),
                }
            next_visited = _visited | {visit_key}
            # Find attribute children — try multiple paths used across OML
            # versions. REST attributes carry a plugin-qualified tag
            # (CustomStructureAttribute-OutSystems.Plugin.REST.RestStructureAttribute),
            # so match on tag substring rather than exact names.
            attrs_parent = node.find("Attributes")
            attrs = (
                [c for c in attrs_parent if "StructureAttribute" in c.tag]
                if attrs_parent is not None
                else [c for c in node if "StructureAttribute" in c.tag]
            )
            result: dict[str, Any] = {
                "kind": "custom_structure",
                "raw": type_str,
                "client_key": client_key,
                "key": struct_key,
                "name": node.attrib.get("Name", struct_key),
            }
            if attrs:
                result["attributes"] = [
                    self._parse_anon_attr(attr, current_reference=current_reference, depth=depth + 1, _visited=next_visited)
                    for attr in attrs
                ]
            return result

        # Self-referential entity identifier (EntityIdentifierType:^/IdentifierType)
        # This is the entity's own identifier type, without the ^/^/ prefix used
        # for relative references.  When current_entity is available we use its name.
        if SELF_ENTITY_IDENTIFIER.match(type_str):
            entity_name = (
                current_entity.attrib.get("Name") if current_entity is not None else None
            )
            return {
                "kind": "entity_identifier",
                "raw": type_str,
                "name": f"{entity_name} Identifier" if entity_name else "Identifier",
                "entity_name": entity_name,
            }

        return {"kind": "unknown", "raw": type_str, "name": type_str}

    # ---- Internal attribute parsers ----

    def _parse_anon_attr(
        self,
        attr: ET.Element,
        *,
        current_reference: RefModule | None = None,
        depth: int = 0,
        _visited: set | None = None,
    ) -> dict[str, Any]:
        # REST structure attributes carry a plain Name (plus NameInJSON);
        # anonymous-structure attributes use CustomName / CustomNameIsSuffix.
        name = attr.attrib.get("CustomName") or attr.attrib.get("Name") or None
        resolved = self.resolve(
            attr.attrib.get("Type"),
            current_reference=current_reference,
            depth=depth,
            _visited=set() if _visited is None else set(_visited),
        )
        if name is None and attr.attrib.get("CustomNameIsSuffix") == "Yes":
            name = resolved.get("name") or resolved.get("entity_name")
        record = {
            "name": name,
            "custom_name_is_suffix": attr.attrib.get("CustomNameIsSuffix") == "Yes",
            "type": resolved,
        }
        json_name = attr.attrib.get("NameInJSON", "")
        if json_name:
            record["json_name"] = json_name
        return record

    def _parse_ref_struct_attr(
        self,
        attr: ET.Element,
        *,
        current_reference: RefModule,
        depth: int = 0,
        _visited: set | None = None,
    ) -> dict[str, Any]:
        return {
            "name": attr.attrib.get("Name"),
            "label": attr.attrib.get("Label"),
            "mandatory": attr.attrib.get("IsMandatory") == "Yes",
            "type": self.resolve(
                attr.attrib.get("Type"),
                current_reference=current_reference,
                depth=depth,
                _visited=set() if _visited is None else set(_visited),
            ),
            "length": expression_value(attr.find("Length")),
            "default": expression_value(attr.find("DefaultValue")),
        }


# ---- Helpers used by attribute parsers and workers ----


def expression_value(node: ET.Element | None) -> Any:
    """Extract a simple scalar value from a node containing a ParsedExpression."""
    if node is None:
        return None
    expr = node.find("ParsedExpression")
    if expr is None or not list(expr):
        return None
    child = list(expr)[0]
    value = child.attrib.get("Value")
    tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
    if value is None:
        return None
    if tag == "Integer":
        return int(value)
    if tag == "Decimal":
        try:
            return float(value)
        except ValueError:
            return value
    if tag == "Boolean":
        return value.lower() == "true"
    return value
