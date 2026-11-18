"""Build a Key-to-Name index from all named elements in an OML XML document."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from os2native.xml_loader import OmlDocument


@dataclass
class KeyIndex:
    """Maps element Keys to human-readable names and paths."""

    _key_to_name: dict[str, str] = field(default_factory=dict)
    _key_to_path: dict[str, str] = field(default_factory=dict)

    def name(self, key: str) -> str | None:
        """Resolve a Key to its Name attribute value."""
        return self._key_to_name.get(key)

    def path(self, key: str) -> str | None:
        """Resolve a Key to its dotted path (ParentTag.Name)."""
        return self._key_to_path.get(key)

    def resolve_ref(self, ref: str) -> str:
        """Resolve a Key-based Ref path to a human-readable dotted name path.

        Ref paths look like:
            Variables.LocalVariable:/NRWebFlows.KEY1/LocalVariables.KEY2

        The colon-separated prefix is the type hint. After the colon, each
        slash-separated segment is a Tag.Key pair. We resolve each Key to its
        Name using the index.

        Returns the best human-readable form available. Unresolvable keys
        are left as-is.
        """
        if ":" not in ref:
            # Simple key reference — try direct lookup
            return self._key_to_name.get(ref, ref)

        type_hint, _, path_part = ref.partition(":")
        if not path_part:
            return ref

        segments = path_part.strip("/").split("/")
        resolved_parts: list[str] = []

        for segment in segments:
            if "." in segment:
                tag_prefix, dot, key = segment.rpartition(".")
                name = self._key_to_name.get(key)
                if name:
                    resolved_parts.append(f"{tag_prefix}.{name}")
                else:
                    resolved_parts.append(segment)
            else:
                name = self._key_to_name.get(segment)
                resolved_parts.append(name if name else segment)

        return ".".join(resolved_parts)


def build_key_index(doc: OmlDocument) -> KeyIndex:
    """Walk all elements in the XML and index those with Key+Name attributes."""
    index = KeyIndex()

    def _walk(elem: ET.Element, parent_path: str = "") -> None:
        key = elem.attrib.get("Key")
        name = elem.attrib.get("Name")
        tag = elem.tag

        if key:
            if name:
                index._key_to_name[key] = name
            # Build a path: ParentPath/Tag.Name (or Tag.Key if no Name)
            label = name if name else key
            current_path = f"{parent_path}/{tag}.{label}" if parent_path else f"{tag}.{label}"
            index._key_to_path[key] = current_path
        else:
            current_path = parent_path

        for child in elem:
            _walk(child, current_path)

    # Walk the ESpace element
    _walk(doc.espace)

    # Walk all fragments
    for frag in doc.fragments.values():
        for child in frag:
            _walk(child)

    return index
