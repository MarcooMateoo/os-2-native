"""Load an OML XML file and provide structured access to its fragments."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex matching XML numeric character references that are illegal in XML 1.0.
# Valid XML 1.0 chars: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
# oml-utilities sometimes emits control-char references (&#x0; &#xB; &#x1F; etc.)
# that the C# serializer doesn't sanitize.
_INVALID_XML_CHAR_REF = re.compile(r"&#x([0-9a-fA-F]+);")


def _is_valid_xml_char(codepoint: int) -> bool:
    """Return True if *codepoint* is allowed in XML 1.0 character data."""
    return (
        codepoint in (0x9, 0xA, 0xD)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _sanitize_xml(raw: str) -> str:
    """Replace invalid XML 1.0 numeric character references with the Unicode replacement char."""

    def _replace(m: re.Match) -> str:
        cp = int(m.group(1), 16)
        if _is_valid_xml_char(cp):
            return m.group(0)  # keep valid references as-is
        return "\uFFFD"  # U+FFFD REPLACEMENT CHARACTER

    return _INVALID_XML_CHAR_REF.sub(_replace, raw)


@dataclass
class OmlDocument:
    """Parsed OML XML document with indexed fragment access."""

    tree: ET.ElementTree
    root: ET.Element
    espace: ET.Element
    module_name: str
    fragments: dict[str, ET.Element] = field(default_factory=dict)

    def fragment(self, name: str) -> ET.Element | None:
        """Return the fragment element for the given FragmentName, or None."""
        return self.fragments.get(name)

    def fragment_children(self, name: str) -> list[ET.Element]:
        """Return direct children of the named fragment, or []."""
        frag = self.fragments.get(name)
        if frag is None:
            return []
        return list(frag)


def load_oml_xml(path: Path | str) -> OmlDocument:
    """Parse an OML XML file and build a fragment index.

    The XML structure is:
        <OML>
          <eSpaceFragment FragmentName="ESpace" Count="1">
            <ESpace Name="ModuleName" ...>...</ESpace>
          </eSpaceFragment>
          <eSpaceFragment FragmentName="Entities" Count="N">
            <Entity .../>
            ...
          </eSpaceFragment>
          ...
        </OML>

    Returns an OmlDocument with:
      - .espace: the <ESpace> element
      - .module_name: from ESpace Name attribute
      - .fragments: dict mapping FragmentName -> eSpaceFragment element
    """
    path = Path(path)

    # Read raw XML and sanitize invalid character references that oml-utilities
    # may emit (C# serializer doesn't strip control-char references).
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"Cannot read XML file {path}: {exc}") from exc

    sanitized = _sanitize_xml(raw)
    if sanitized != raw:
        logger.warning(
            "[%s] XML contained invalid character references; they were removed before parsing",
            path.stem,
        )

    try:
        root = ET.fromstring(sanitized)
    except ET.ParseError as exc:
        raise ValueError(
            f"Failed to parse XML for {path.name}: {exc}"
        ) from exc
    tree = ET.ElementTree(root)

    fragments: dict[str, ET.Element] = {}
    espace: ET.Element | None = None
    module_name = "unknown"

    for child in root:
        tag = child.tag
        if tag == "eSpaceFragment":
            frag_name = child.attrib.get("FragmentName", "")
            fragments[frag_name] = child

            # The ESpace is inside a fragment with FragmentName="ESpace"
            # but can also appear as a direct child in some formats.
            if frag_name == "ESpace":
                es = child.find("ESpace")
                if es is not None:
                    espace = es
                    module_name = es.attrib.get("Name", "unknown")
        elif tag == "ESpace":
            # Alternate layout: ESpace as direct child of OML
            espace = child
            module_name = child.attrib.get("Name", "unknown")

    if espace is None:
        # Fallback: look in all fragments for an ESpace element
        for frag in fragments.values():
            es = frag.find("ESpace")
            if es is not None:
                espace = es
                module_name = es.attrib.get("Name", "unknown")
                break

    if espace is None:
        raise ValueError(f"No <ESpace> element found in {path}")

    return OmlDocument(
        tree=tree,
        root=root,
        espace=espace,
        module_name=module_name,
        fragments=fragments,
    )
