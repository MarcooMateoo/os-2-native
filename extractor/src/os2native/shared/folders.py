"""Service Studio folders (``ModelFolder``) for module elements.

Folders are stored as ``<ModelFolder Key=... Name=... ESpaceTreeFolder=...>``
elements; an element placed in a folder carries
``Folder="ModelFolder:/Folders.<folder key>"``.
"""

from __future__ import annotations

from os2native.xml_loader import OmlDocument

_FOLDER_REF_PREFIX = "ModelFolder:/Folders."


def element_folders(doc: OmlDocument) -> dict[str, str]:
    """Map element ``Key`` -> name of the Service Studio folder it is in."""
    folder_names: dict[str, str] = {}
    for elem in doc.root.iter("ModelFolder"):
        key = elem.attrib.get("Key")
        if key:
            folder_names[key] = elem.attrib.get("Name", "")

    placed: dict[str, str] = {}
    for elem in doc.root.iter():
        ref = elem.attrib.get("Folder", "")
        key = elem.attrib.get("Key")
        if key and ref.startswith(_FOLDER_REF_PREFIX):
            name = folder_names.get(ref[len(_FOLDER_REF_PREFIX):])
            if name:
                placed[key] = name
    return placed


def annotate_folders(items: list[dict], folders: dict[str, str]) -> None:
    """Set ``folder`` on each item dict (``None`` when it is not in a folder)."""
    for item in items:
        item["folder"] = folders.get(item.get("key", ""))
