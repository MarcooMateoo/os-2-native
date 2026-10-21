"""Asset extractor — decodes images, CSS, and other binary assets from OML XML."""

from __future__ import annotations

import base64
import json as _json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.xml_loader import OmlDocument


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(doc: OmlDocument, pkg_root: Path) -> None:
    """Extract all assets and write to ``{pkg_root}/evidence/``."""
    out = pkg_root / "evidence"
    assets_dir = out / "assets"
    css_dir = assets_dir / "css"
    tracker = CoverageTracker(domain="assets")

    manifest: list[dict[str, Any]] = []

    # 1. Images from the Images fragment.
    _extract_images(doc, assets_dir, manifest, tracker)

    # 2. ESpace favicon.
    _extract_favicon(doc, assets_dir, manifest, tracker)

    # 3. CSS stylesheets.
    _extract_stylesheets(doc, css_dir, manifest, tracker)

    # 4. ReferenceStyleSheetImage binary assets (includes fonts & images).
    _extract_reference_stylesheet_images(doc, assets_dir, manifest, tracker)

    # 5. StyleSheetImage binary assets (local theme fonts & images).
    _extract_stylesheet_images(doc, assets_dir, manifest, tracker)

    # 6. ReferenceImage binary assets (referenced images from other modules).
    _extract_reference_images(doc, assets_dir, manifest, tracker)

    # 7. Resource and ReferenceResource binary assets (app deploy artifacts).
    _extract_resources(doc, assets_dir, manifest, tracker)

    # 8. Scripts (JavaScript files associated with themes).
    _extract_scripts(doc, out, tracker)

    # 9. Design tokens — parse CSS custom properties from extracted stylesheets.
    _extract_design_tokens(css_dir, out, tracker)

    # 10. Write dedicated animations.json summarising Lottie / animated-SVG assets.
    _write_animations_summary(manifest, out)

    write_json(out / "index.json", manifest)
    write_json(out / "traversal-log.json", tracker.to_traversal_dict())
    write_json(out / "coverage.json", tracker.to_coverage_dict())


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _extract_images(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Decode base64 image binaries from ``Images.Image`` elements."""
    images_frag = doc.fragment("Images")
    if images_frag is None:
        return

    for img in images_frag.findall("Image"):
        name = img.attrib.get("Name", "")
        key = img.attrib.get("Key", "")
        tracker.discover(name, f"Images.Image/{name}")

        binary_elem = img.find("Binary")
        if binary_elem is None or not binary_elem.text:
            tracker.skip(name, result="no_binary_data")
            continue

        original_filename = img.attrib.get("OriginalFilename", "")
        filename = original_filename if original_filename else f"{name}.png"
        dest = assets_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = base64.b64decode(binary_elem.text)
            dest.write_bytes(data)
        except Exception:
            tracker.block(name, result="decode_error")
            continue

        tracker.capture(name, f"evidence/assets/{filename}")
        manifest.append({
            "name": name,
            "key": key,
            "source": "Images.Image",
            "package_path": f"evidence/assets/{filename}",
            "width": img.attrib.get("Width", ""),
            "height": img.attrib.get("Height", ""),
            "mime_type": img.attrib.get("MimeType", ""),
            "runtime_path": img.attrib.get("RuntimePath", ""),
            "original_filename": original_filename,
            "original_format": img.attrib.get("OriginalFormat", ""),
            "folder": img.attrib.get("Folder", ""),
        })


# ---------------------------------------------------------------------------
# Favicon extraction
# ---------------------------------------------------------------------------

def _extract_favicon(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract the module favicon from the ESpace root ``<Image>`` element."""
    tracker.discover("favicon", "ESpace/Image")

    image_elem = doc.espace.find("Image")
    b64_text = image_elem.text if image_elem is not None else None

    # Also try the text directly on the ESpace <Image> attribute fallback.
    if not b64_text:
        b64_text = doc.espace.findtext("Image")

    if not b64_text or not b64_text.strip():
        tracker.skip("favicon", result="no_data")
        return

    dest = assets_dir / "favicon.png"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = base64.b64decode(b64_text.strip())
        dest.write_bytes(data)
    except Exception:
        tracker.block("favicon", result="decode_error")
        return

    tracker.capture("favicon", "evidence/assets/favicon.png")
    manifest.append({
        "name": "favicon",
        "key": "",
        "source": "ESpace/Image",
        "package_path": "evidence/assets/favicon.png",
        "width": "",
        "height": "",
        "mime_type": "image/png",
        "runtime_path": "",
        "original_filename": "favicon.png",
    })


# ---------------------------------------------------------------------------
# Stylesheet extraction
# ---------------------------------------------------------------------------

def _extract_stylesheets(
    doc: OmlDocument,
    css_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract CSS text from WebStyleSheet and ReferenceWebStyleSheet elements."""
    seen_ids: set[str] = set()
    # Counter for generating unique IDs when Key is empty (common for
    # ReferenceWebStyleSheet inside Reference elements).
    auto_id = 0

    # Search both the ESpace and all fragments for stylesheet elements.
    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for tag in ("WebStyleSheet", "ReferenceWebStyleSheet"):
            for ss in root.iter(tag):
                key = ss.attrib.get("Key", "")
                dedup_id = key if key else f"_auto_{auto_id}"
                auto_id += 1
                if dedup_id in seen_ids:
                    continue
                seen_ids.add(dedup_id)

                name = ss.attrib.get("Name", "") or key
                tracker.discover(name or dedup_id, f"{source_label}/{tag}/{name or dedup_id}")

                value_elem = ss.find("Value")
                css_text = value_elem.text if value_elem is not None else None

                if not css_text:
                    tracker.skip(name or dedup_id, result="no_css_value")
                    continue

                # Build a useful filename: prefer Name, fall back to parent
                # Reference Name (for nested RWSS), then use auto_id.
                if name:
                    safe_name = _safe_filename(name)
                else:
                    parent_ref = _find_parent_ref_name(root, ss)
                    safe_name = _safe_filename(parent_ref) if parent_ref else f"stylesheet_{auto_id}"
                filename = f"{safe_name}.css"
                # Avoid overwriting — append dedup suffix if file exists.
                dest = css_dir / filename
                if dest.exists():
                    filename = f"{safe_name}_{auto_id}.css"
                    dest = css_dir / filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(css_text, encoding="utf-8")

                tracker.capture(name or dedup_id, f"evidence/assets/css/{filename}")
                manifest.append({
                    "name": name,
                    "key": key,
                    "source": f"{source_label}/{tag}",
                    "package_path": f"evidence/assets/css/{filename}",
                    "width": "",
                    "height": "",
                    "mime_type": "text/css",
                    "runtime_path": "",
                    "original_filename": filename,
                })


# ---------------------------------------------------------------------------
# ReferenceStyleSheetImage extraction
# ---------------------------------------------------------------------------

def _extract_reference_stylesheet_images(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract binary data from ReferenceStyleSheetImage elements."""
    seen_keys: set[str] = set()

    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for elem in root.iter("ReferenceStyleSheetImage"):
            key = elem.attrib.get("Key", "")
            if key in seen_keys:
                continue
            seen_keys.add(key)

            name = elem.attrib.get("Name", "") or key
            tracker.discover(name, f"{source_label}/ReferenceStyleSheetImage/{name}")

            binary_elem = elem.find("Binary")
            if binary_elem is None or not binary_elem.text:
                tracker.skip(name, result="no_binary_data")
                continue

            original_filename = elem.attrib.get("OriginalFilename", "")
            filename = original_filename if original_filename else f"{name}.png"
            dest = assets_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)

            try:
                data = base64.b64decode(binary_elem.text)
                dest.write_bytes(data)
            except Exception:
                tracker.block(name, result="decode_error")
                continue

            tracker.capture(name, f"evidence/assets/{filename}")
            manifest.append({
                "name": name,
                "key": key,
                "source": f"{source_label}/ReferenceStyleSheetImage",
                "package_path": f"evidence/assets/{filename}",
                "width": elem.attrib.get("Width", ""),
                "height": elem.attrib.get("Height", ""),
                "mime_type": elem.attrib.get("MimeType", ""),
                "runtime_path": elem.attrib.get("RuntimePath", ""),
                "original_filename": original_filename,
            })


# ---------------------------------------------------------------------------
# StyleSheetImage extraction (local theme assets — fonts, images)
# ---------------------------------------------------------------------------

_FONT_EXTENSIONS = frozenset((".woff", ".woff2", ".ttf", ".eot", ".otf"))


def _extract_stylesheet_images(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract binary data from StyleSheetImage elements (local to this module)."""
    seen_keys: set[str] = set()

    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for elem in root.iter("StyleSheetImage"):
            # .iter() returns all descendants — skip if tag doesn't match exactly
            if elem.tag != "StyleSheetImage":
                continue

            key = elem.attrib.get("Key", "")
            if key in seen_keys:
                continue
            seen_keys.add(key)

            name = elem.attrib.get("Name", "") or key
            tracker.discover(name, f"{source_label}/StyleSheetImage/{name}")

            binary_elem = elem.find("Binary")
            if binary_elem is None or not binary_elem.text:
                tracker.skip(name, result="no_binary_data")
                continue

            original_filename = elem.attrib.get("OriginalFilename", "")
            filename = original_filename if original_filename else f"{name}.png"

            # Route font files to fonts/ subdirectory.
            suffix = Path(filename).suffix.lower()
            if suffix in _FONT_EXTENSIONS:
                dest = assets_dir / "fonts" / filename
            else:
                dest = assets_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)

            try:
                data = base64.b64decode(binary_elem.text)
                dest.write_bytes(data)
            except Exception:
                tracker.block(name, result="decode_error")
                continue

            rel_path = dest.relative_to(assets_dir.parent.parent)  # relative to evidence/
            tracker.capture(name, str(rel_path))
            manifest.append({
                "name": name,
                "key": key,
                "source": f"{source_label}/StyleSheetImage",
                "package_path": str(rel_path),
                "width": elem.attrib.get("Width", ""),
                "height": elem.attrib.get("Height", ""),
                "mime_type": elem.attrib.get("MimeType", ""),
                "runtime_path": elem.attrib.get("RuntimePath", ""),
                "original_filename": original_filename,
            })


# ---------------------------------------------------------------------------
# ReferenceImage extraction (referenced images from other modules)
# ---------------------------------------------------------------------------

def _extract_reference_images(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract binary data from ReferenceImage elements."""
    seen_keys: set[str] = set()

    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for elem in root.iter("ReferenceImage"):
            key = elem.attrib.get("Key", "")
            if key in seen_keys:
                continue
            seen_keys.add(key)

            name = elem.attrib.get("Name", "") or key
            tracker.discover(name, f"{source_label}/ReferenceImage/{name}")

            binary_elem = elem.find("Binary")
            if binary_elem is None or not binary_elem.text:
                tracker.skip(name, result="no_binary_data")
                continue

            original_filename = elem.attrib.get("OriginalFilename", "")
            filename = original_filename if original_filename else f"{name}.png"
            dest = assets_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)

            try:
                data = base64.b64decode(binary_elem.text)
                dest.write_bytes(data)
            except Exception:
                tracker.block(name, result="decode_error")
                continue

            tracker.capture(name, f"evidence/assets/{filename}")
            manifest.append({
                "name": name,
                "key": key,
                "source": f"{source_label}/ReferenceImage",
                "package_path": f"evidence/assets/{filename}",
                "width": elem.attrib.get("Width", ""),
                "height": elem.attrib.get("Height", ""),
                "mime_type": elem.attrib.get("MimeType", ""),
                "runtime_path": elem.attrib.get("RuntimePath", ""),
                "original_filename": original_filename,
            })


# ---------------------------------------------------------------------------
# Resource / ReferenceResource extraction
# ---------------------------------------------------------------------------

def _extract_resources(
    doc: OmlDocument,
    assets_dir: Path,
    manifest: list[dict[str, Any]],
    tracker: CoverageTracker,
) -> None:
    """Extract binary data from Resource and ReferenceResource elements."""
    seen_keys: set[str] = set()
    resources_dir = assets_dir / "resources"

    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for tag in ("Resource", "ReferenceResource"):
            for elem in root.iter(tag):
                if elem.tag != tag:
                    continue

                key = elem.attrib.get("Key", "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                name = elem.attrib.get("Name", "") or key
                tracker.discover(name, f"{source_label}/{tag}/{name}")

                binary_elem = elem.find("Binary")
                if binary_elem is None or not binary_elem.text:
                    tracker.skip(name, result="no_binary_data")
                    continue

                filename = _safe_filename(name)
                dest = resources_dir / filename
                dest.parent.mkdir(parents=True, exist_ok=True)

                try:
                    data = base64.b64decode(binary_elem.text)
                    dest.write_bytes(data)
                except Exception:
                    tracker.block(name, result="decode_error")
                    continue

                rel_path = f"evidence/assets/resources/{filename}"
                tracker.capture(name, rel_path)

                # Check for Lottie / animated SVG and enrich source + metadata.
                anim_meta = _detect_lottie(data)
                if anim_meta is None:
                    anim_meta = _detect_animated_svg(data, filename)

                entry: dict[str, Any] = {
                    "name": name,
                    "key": key,
                    "source": (
                        "Resources.Animation"
                        if anim_meta is not None
                        else f"{source_label}/{tag}"
                    ),
                    "package_path": rel_path,
                    "width": str(anim_meta["width"]) if anim_meta else "",
                    "height": str(anim_meta["height"]) if anim_meta else "",
                    "mime_type": (
                        anim_meta["mime_type"]
                        if anim_meta
                        else elem.attrib.get("ContentType", "")
                    ),
                    "runtime_path": elem.attrib.get("RuntimePath", ""),
                    "original_filename": name,
                    "deploy_action": elem.attrib.get("DeployAction", ""),
                    "target_directory": elem.attrib.get(
                        "TargetDirectory", elem.attrib.get("Path", "")
                    ),
                }
                if anim_meta is not None:
                    entry.update({
                        "animation_format": anim_meta["format"],
                        "animation_name": anim_meta.get("animation_name", ""),
                        "animation_frame_rate": anim_meta.get("frame_rate"),
                        "animation_in_point": anim_meta.get("in_point"),
                        "animation_out_point": anim_meta.get("out_point"),
                        "animation_duration_frames": anim_meta.get("duration_frames"),
                        "animation_lottie_version": anim_meta.get("lottie_version", ""),
                        "animation_layer_count": anim_meta.get("layer_count"),
                        "animation_asset_count": anim_meta.get("asset_count"),
                    })
                manifest.append(entry)


# ---------------------------------------------------------------------------
# Script extraction
# ---------------------------------------------------------------------------

def _extract_scripts(
    doc: OmlDocument,
    evidence_dir: Path,
    tracker: CoverageTracker,
) -> None:
    """Extract Script and ReferenceScript elements into ``evidence/scripts.json``."""
    scripts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    sources: list[tuple[str, ET.Element]] = [("ESpace", doc.espace)]
    for frag_name, frag in doc.fragments.items():
        sources.append((f"fragment:{frag_name}", frag))

    for source_label, root in sources:
        for tag in ("Script", "ReferenceScript"):
            for elem in root.iter(tag):
                if elem.tag != tag:
                    continue

                key = elem.attrib.get("Key", "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                name = elem.attrib.get("Name", "") or key
                tracker.discover(name, f"{source_label}/{tag}/{name}")

                js_elem = elem.find("JavaScript")
                js_text = js_elem.text if js_elem is not None and js_elem.text else ""

                tracker.capture(name, f"scripts/{name}")
                scripts.append({
                    "name": name,
                    "key": key,
                    "source": f"{source_label}/{tag}",
                    "is_reference": "Reference" in tag,
                    "runtime_path": elem.attrib.get("RuntimePath", ""),
                    "description": elem.attrib.get("Description", ""),
                    "folder": elem.attrib.get("Folder", ""),
                    "js_length": len(js_text),
                })

    write_json(evidence_dir / "scripts.json", scripts)


# ---------------------------------------------------------------------------
# Design tokens extraction
# ---------------------------------------------------------------------------

def _extract_design_tokens(
    css_dir: Path,
    evidence_dir: Path,
    tracker: CoverageTracker,
) -> None:
    """Parse CSS custom properties from ``:root`` blocks in extracted CSS files.

    Writes ``evidence/design_tokens.json`` with structured token data.
    """
    tracker.discover("design_tokens", "css_dir/:root")

    if not css_dir.exists():
        tracker.skip("design_tokens", result="no_css_dir")
        write_json(evidence_dir / "design_tokens.json", {"tokens": {}, "source_files": []})
        return

    all_tokens: dict[str, str] = {}
    source_files: list[str] = []

    for css_file in sorted(css_dir.glob("*.css")):
        css_text = css_file.read_text(encoding="utf-8", errors="replace")
        if not css_text:
            continue

        # Find :root { ... } blocks — handle nested braces conservatively.
        for root_match in re.finditer(
            r":root\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}", css_text
        ):
            vars_text = root_match.group(1)
            found_any = False
            for var_match in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", vars_text):
                var_name = var_match.group(1)
                var_value = var_match.group(2).strip()
                all_tokens[var_name] = var_value
                found_any = True
            if found_any and css_file.name not in source_files:
                source_files.append(css_file.name)

    if all_tokens:
        tracker.capture("design_tokens", "evidence/design_tokens.json")
    else:
        tracker.skip("design_tokens", result="no_root_variables")

    write_json(evidence_dir / "design_tokens.json", {
        "tokens": all_tokens,
        "source_files": source_files,
    })


# ---------------------------------------------------------------------------
# Animation summary writer
# ---------------------------------------------------------------------------

def _write_animations_summary(
    manifest: list[dict[str, Any]],
    evidence_dir: Path,
) -> None:
    """Write ``evidence/animations.json`` — a filtered view of animation entries.

    Contains only entries whose ``source`` is ``"Resources.Animation"``, with
    the full animation metadata block.  Downstream consumers (e.g. the
    Storybook lottie renderer and the Maverick implementation agent) can read
    this file directly without filtering ``index.json``.
    """
    animations = [entry for entry in manifest if entry.get("source") == "Resources.Animation"]
    write_json(evidence_dir / "animations.json", animations)


# ---------------------------------------------------------------------------
# Animation detection helpers
# ---------------------------------------------------------------------------

# Minimum required Lottie top-level keys (all must be present).
_LOTTIE_REQUIRED_KEYS = frozenset({"v", "fr", "ip", "op", "w", "h", "layers"})


def _detect_lottie(data: bytes) -> dict[str, Any] | None:
    """Return Lottie animation metadata if *data* is a valid Lottie JSON file.

    A Lottie JSON file must contain all of the keys in ``_LOTTIE_REQUIRED_KEYS``
    at the top level (``v``, ``fr``, ``ip``, ``op``, ``w``, ``h``, ``layers``).

    Returns a metadata dict with animation details, or ``None`` if not Lottie.
    """
    # Quick prefix check: Lottie files start with a JSON object.
    stripped = data[:3].lstrip()
    if not stripped.startswith(b"{"):
        return None

    try:
        obj = _json.loads(data)
    except Exception:
        return None

    if not isinstance(obj, dict):
        return None

    if not _LOTTIE_REQUIRED_KEYS.issubset(obj.keys()):
        return None

    fr = obj.get("fr")
    ip = obj.get("ip")
    op = obj.get("op")
    duration_frames = (op - ip) if (op is not None and ip is not None) else None

    return {
        "format": "lottie",
        "mime_type": "application/json",
        "width": obj.get("w", ""),
        "height": obj.get("h", ""),
        "animation_name": obj.get("nm", ""),
        "frame_rate": fr,
        "in_point": ip,
        "out_point": op,
        "duration_frames": duration_frames,
        "lottie_version": str(obj.get("v", "")),
        "layer_count": len(obj.get("layers", [])),
        "asset_count": len(obj.get("assets", [])),
    }


# SVG animation element tags (namespace-agnostic substring match).
_SVG_ANIM_TAGS = (b"<animate", b"<animateTransform", b"<animateMotion", b"<animateColor")


def _detect_animated_svg(data: bytes, filename: str) -> dict[str, Any] | None:
    """Return animation metadata if *data* is an animated SVG file.

    Detection: file ends with ``.svg`` (case-insensitive) AND the content
    contains at least one SMIL animation element.  Width/height are extracted
    from the root ``<svg>`` element's ``width``/``height`` attributes when
    present; duration is not parsed (SMIL timing is complex).

    Returns a metadata dict, or ``None`` if not an animated SVG.
    """
    if not filename.lower().endswith(".svg"):
        return None

    # Quick byte scan for animation tags before full parse.
    lower_data = data[:65536].lower()  # inspect up to first 64KB
    has_anim = any(tag in lower_data for tag in _SVG_ANIM_TAGS)
    if not has_anim:
        return None

    # Try to extract width/height from the SVG root element.
    width: str = ""
    height: str = ""
    try:
        # Parse the SVG — strip BOM, use errors='replace' for non-UTF8 sources.
        text = data.decode("utf-8", errors="replace")
        # Look for the opening <svg ...> tag.
        svg_open_m = re.search(
            r"<svg\b[^>]*>", text, re.IGNORECASE | re.DOTALL
        )
        if svg_open_m:
            svg_tag = svg_open_m.group(0)
            w_m = re.search(r'\bwidth=["\']([^"\']+)["\']', svg_tag)
            h_m = re.search(r'\bheight=["\']([^"\']+)["\']', svg_tag)
            width = w_m.group(1) if w_m else ""
            height = h_m.group(1) if h_m else ""
    except Exception:
        pass

    return {
        "format": "animated_svg",
        "mime_type": "image/svg+xml",
        "width": width,
        "height": height,
        "animation_name": "",
        "frame_rate": None,
        "in_point": None,
        "out_point": None,
        "duration_frames": None,
        "lottie_version": "",
        "layer_count": None,
        "asset_count": None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Sanitise a name for use as a filename (no path separators or special chars)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _find_parent_ref_name(root: ET.Element, target: ET.Element) -> str | None:
    """Walk *root* to find a ``Reference`` ancestor of *target* and return its Name.

    Uses identity comparison (``is``) so each call is O(refs × children).
    """
    for ref in root.iter("Reference"):
        # Only iterate the specific tag we're looking for, not all descendants.
        for child in ref.iter(target.tag):
            if child is target:
                return ref.attrib.get("Name", "") or ref.attrib.get("Key", "")
    return None
