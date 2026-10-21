"""Integration Studio extensions (``.xif``).

A ``.xif`` is a zlib-compressed XML document::

    <Extension Name=... Description=... ApplicationServerType="NET" ...>
      <Resources><Resource Filename=... Path=... CompileAction=...><Data>base64</Data>...
      <Actions><Action Name=... IsFunction=...><Variables><InputParameter .../>
      <Structures><Structure Name=...><Attribute .../>
      <Entities>...

``extract_extension`` writes ``<output>/<Name>/extension.json`` plus the
embedded source files (C#/Java projects, configs) under ``source/``.
"""

from __future__ import annotations

import base64
import binascii
import json
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

# Embedded files written to source/: code and project files, not binaries.
SOURCE_SUFFIXES = {
    ".cs", ".csproj", ".sln", ".vb", ".vbproj", ".java", ".config", ".resx",
    ".xml", ".json", ".js", ".sql", ".txt", ".md", ".properties", ".settings",
}

# Integration Studio type names (rtText, rtInteger, ...) -> OutSystems names
_RT_TYPES = {
    "rtText": "Text", "rtInteger": "Integer", "rtLongInteger": "Long Integer",
    "rtDecimal": "Decimal", "rtBoolean": "Boolean", "rtDateTime": "Date Time",
    "rtDate": "Date", "rtTime": "Time", "rtBinaryData": "Binary Data",
    "rtEmail": "Email", "rtPhoneNumber": "Phone Number", "rtCurrency": "Currency",
    "rtObject": "Object", "rtRecord": "Record", "rtRecordList": "Record List",
    "rtEntityReference": "Entity Identifier",
}


def read_xif(path: Path) -> ET.Element:
    """Decompress and parse a ``.xif`` file, returning the ``<Extension>`` root."""
    raw = path.read_bytes()
    try:
        xml_bytes = zlib.decompress(raw)
    except zlib.error:
        xml_bytes = raw  # tolerate an uncompressed export
    root = ET.fromstring(xml_bytes.decode("utf-8-sig"))
    if root.tag != "Extension":
        raise ValueError(f"{path.name}: expected <Extension>, found <{root.tag}>")
    return root


def _params(action: ET.Element, structures: dict[str, str]) -> tuple[list[dict], list[dict]]:
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for var in action.iter():
        if var.tag not in ("InputParameter", "OutputParameter"):
            continue
        record_keys = [k for k in var.attrib.get("RecordType", "").split(",") if k]
        param = {
            "name": var.attrib.get("Name", ""),
            "type": _RT_TYPES.get(var.attrib.get("Type", ""), var.attrib.get("Type", "")),
            "description": var.attrib.get("Description", ""),
        }
        if record_keys:
            param["record_type"] = [structures.get(k, k) for k in record_keys]
        if var.tag == "InputParameter":
            param["is_mandatory"] = var.attrib.get("IsMandatory") == "Yes"
            param["default"] = var.attrib.get("Default", "")
            inputs.append(param)
        else:
            outputs.append(param)
    return inputs, outputs


def parse_extension(root: ET.Element) -> dict[str, Any]:
    """Describe an extension: metadata, actions, structures, entities, resources."""
    structures_el = root.find("Structures")
    structure_names = {
        s.attrib.get("Key", ""): s.attrib.get("Name", "")
        for s in (structures_el if structures_el is not None else [])
    }

    actions = []
    for action in root.iterfind("Actions/Action"):
        inputs, outputs = _params(action, structure_names)
        actions.append({
            "key": action.attrib.get("Key", ""),
            "name": action.attrib.get("Name", ""),
            "description": action.attrib.get("Description", ""),
            "is_function": action.attrib.get("IsFunction") == "Yes",
            "input_parameters": inputs,
            "output_parameters": outputs,
        })

    structures = []
    for s in root.iterfind("Structures/Structure"):
        structures.append({
            "key": s.attrib.get("Key", ""),
            "name": s.attrib.get("Name", ""),
            "description": s.attrib.get("Description", ""),
            "attributes": [
                {
                    "name": a.attrib.get("Name", ""),
                    "type": _RT_TYPES.get(a.attrib.get("Type", ""), a.attrib.get("Type", "")),
                    "is_mandatory": a.attrib.get("IsMandatory") == "Yes",
                    "default": a.attrib.get("DefaultValue", ""),
                    "length": a.attrib.get("Length", ""),
                    "description": a.attrib.get("Description", ""),
                }
                for a in s.iterfind("Attribute")
            ],
        })

    entities = [
        {"key": e.attrib.get("Key", ""), "name": e.attrib.get("Name", ""),
         "description": e.attrib.get("Description", "")}
        for e in root.iterfind("Entities/Entity")
    ]

    resources = []
    for r in root.iterfind("Resources/Resource"):
        if not r.attrib.get("Filename"):
            continue  # bare <Resource Key=.../> back-references
        data = r.find("Data")
        resources.append({
            "filename": r.attrib.get("Filename", ""),
            "path": r.attrib.get("Path", "").replace("\\", "/"),
            "compile_action": r.attrib.get("CompileAction", ""),
            "has_data": data is not None and bool((data.text or "").strip()),
        })

    references = sorted({
        ref.attrib.get("Name", "") for ref in root.iterfind("References/Reference")
        if ref.attrib.get("Name")
    })

    return {
        "name": root.attrib.get("Name", ""),
        "key": root.attrib.get("Key", ""),
        "description": root.attrib.get("Description", ""),
        "server_type": root.attrib.get("ApplicationServerType", ""),
        "database": root.attrib.get("Database", ""),
        "platform_version": root.attrib.get("Version", ""),
        "actions": actions,
        "structures": structures,
        "entities": entities,
        "resources": resources,
        "assembly_references": references,
    }


def _safe_relpath(path: str, filename: str) -> PurePosixPath | None:
    """Join an embedded Path + Filename without allowing escape from source/."""
    parts = [p for p in f"{path}/{filename}".replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in parts[0]:
        return None
    return PurePosixPath(*parts)


def extract_extension(xif_path: Path, output: Path) -> Path:
    """Write ``<output>/<Name>/extension.json`` and ``source/`` files; return the folder."""
    root = read_xif(xif_path)
    info = parse_extension(root)
    ext_dir = output / (info["name"] or xif_path.stem)
    ext_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for r in root.iterfind("Resources/Resource"):
        filename = r.attrib.get("Filename", "")
        data = r.find("Data")
        if not filename or data is None or not (data.text or "").strip():
            continue
        if PurePosixPath(filename).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        rel = _safe_relpath(r.attrib.get("Path", ""), filename)
        if rel is None:
            continue
        try:
            content = base64.b64decode(data.text, validate=False)
        except (binascii.Error, ValueError):
            continue
        target = ext_dir / "source" / Path(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(rel.as_posix())

    info["source_file"] = xif_path.name
    info["source_files_written"] = sorted(written)
    (ext_dir / "extension.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return ext_dir
