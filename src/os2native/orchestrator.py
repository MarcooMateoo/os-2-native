"""Stage 3: Orchestrator — assemble package-level artifacts from domain outputs."""

from __future__ import annotations

import datetime
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from os2native.shared.coverage import CoverageTracker, write_json
from os2native.shared.type_resolver import RefModule, TypeResolver, expression_value
from os2native.xml_loader import OmlDocument


_ACCOUNTED_SKIP_RESULTS: dict[str, set[str]] = {
    "assets": {"no_css_value", "no_css_dir", "no_root_variables", "no_data"},
    "interfaces": {"dynamic/runtime image (no static guid) — see runtime/aem-assets.json"},
}


def _resolve_contract_status(
    ref: dict[str, Any],
    platform_deps: set[str],
    all_module_names: set[str],
) -> str:
    """Determine the contract status for a referenced module.

    Returns:
        "platform"   — known platform/Forge dependency
        "resolved"   — co-extracted module with meaningful content
        "build_only" — co-extracted but has 0 entities, structures, and actions
        "partial"    — not co-extracted and not platform (blocking)
    """
    name = ref.get("module_name", "")
    if name in platform_deps:
        return "platform"
    if name in all_module_names:
        # Check if the co-extracted module has meaningful content
        entities = ref.get("entities", [])
        structures = ref.get("structures", [])
        actions = ref.get("actions", ref.get("server_actions", []))
        if not entities and not structures and not actions:
            return "build_only"
        return "resolved"
    return "partial"


def _confidence_for_status(status: str) -> str:
    """Map a coverage status to an evidence-confidence label.

    Previously hardcoded to "high" regardless of status, which overstated
    confidence for partial/blocked domains.
    """
    if status == "complete":
        return "high"
    if status == "blocked":
        return "low"
    return "medium"  # partial / unknown


def _aggregate_domain_gaps(domain_completeness: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll per-domain blocked/unresolved/skipped counts up into a flat gap
    list for ``package.json`` and the coverage report.

    Replaces the hardcoded ``"gaps": []`` / "None detected." so the
    package-level honesty claim reflects the per-domain coverage counters
    instead of contradicting them.
    """
    gaps: list[dict[str, Any]] = []
    for domain, info in domain_completeness.items():
        for kind, key in (
            ("blocked", "blocked_count"),
            ("unresolved", "unresolved_count"),
            ("skipped", "skipped_count"),
        ):
            count = info.get(key, 0)
            if kind == "skipped":
                count = max(0, count - info.get("accounted_skipped_count", 0))
            if count:
                gap = {
                    "domain": domain,
                    "kind": kind,
                    "count": count,
                    "coverage_path": info.get("coverage_path", ""),
                }
                if kind == "skipped":
                    reasons = _unaccounted_skip_reasons(info)
                    if reasons:
                        gap["reasons"] = reasons
                gaps.append(gap)
    return gaps


def _unaccounted_skip_reasons(info: dict[str, Any]) -> dict[str, int]:
    reasons = dict(info.get("skip_reasons", {}))
    for reason, count in info.get("accounted_skip_reasons", {}).items():
        remaining = reasons.get(reason, 0) - count
        if remaining > 0:
            reasons[reason] = remaining
        else:
            reasons.pop(reason, None)
    return reasons


def _aggregate_domain_accounted_gaps(domain_completeness: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose accounted skips separately from explicit extraction gaps."""
    accounted: list[dict[str, Any]] = []
    for domain, info in domain_completeness.items():
        count = info.get("accounted_skipped_count", 0)
        if count:
            accounted.append({
                "domain": domain,
                "kind": "skipped",
                "count": count,
                "coverage_path": info.get("coverage_path", ""),
                "reasons": info.get("accounted_skip_reasons", {}),
            })
    return accounted


def _skip_reasons(pkg_root: Path, coverage_rel: str) -> dict[str, int]:
    """Count skip reasons from a coverage file's matching traversal log."""
    traversal_path = pkg_root / str(Path(coverage_rel).with_name("traversal-log.json"))
    if not traversal_path.exists():
        return {}

    try:
        traversal = json.loads(traversal_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    reasons: dict[str, int] = {}
    for event in traversal.get("events", []):
        if event.get("action") != "skip":
            continue
        result = event.get("result", "")
        reasons[result] = reasons.get(result, 0) + 1
    return reasons


def _accounted_skip_reasons(skip_reasons: dict[str, int], domain: str) -> dict[str, int]:
    """Filter skip reasons that are runtime-backed or intentionally empty.

    Coverage counters are aggregate-only, so the reason split comes from the
    matching traversal log. Only allow-listed reasons are moved out of explicit
    gaps; every other skip remains a normal evidence gap.
    """
    accounted_results = _ACCOUNTED_SKIP_RESULTS.get(domain, set())
    if not accounted_results:
        return {}
    return {
        result: count
        for result, count in skip_reasons.items()
        if result in accounted_results
    }


def _source_evidence_confidence(domain_completeness: dict[str, dict[str, Any]]) -> str:
    """Confidence in the extraction evidence itself.

    Domain gaps describe completeness and are already rolled up separately.
    They should not automatically make source provenance medium-confidence:
    a package can be confidently extracted from source XML while still carrying
    explicit unresolved/skipped evidence gaps.
    """
    statuses = [info.get("status", "unknown") for info in domain_completeness.values()]
    if any(status == "blocked" for status in statuses):
        return "low"
    if any(status == "unknown" for status in statuses):
        return "medium"
    return "high"


_URL_SITE_PROPERTY_KEYWORDS = {"baseurl", "endpointurl", "endpoint", "url", "host"}
_AUTH_SITE_PROPERTY_KEYWORDS = {
    "key",
    "token",
    "secret",
    "auth",
    "clientid",
    "clientsecret",
    "grant",
    "password",
    "credential",
}


def _normalise_property_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _site_property_value(prop: dict[str, Any]) -> str:
    for key in ("effective_value", "default"):
        value = prop.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _looks_like_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _find_endpoint_site_property(
    client_name: str,
    site_props: list[dict[str, Any]],
) -> tuple[str, str] | None:
    exact_candidates = [
        f"{client_name}_BaseURL",
        f"BaseURL_{client_name}",
        f"{client_name}_Base_URL",
        f"{client_name}BaseURL",
        f"{client_name}_URL",
        f"{client_name}_EndpointURL",
        f"{client_name}_Endpoint",
        f"{client_name}_Host",
    ]
    by_name = {
        str(sp.get("name") or "").lower(): sp
        for sp in site_props
        if isinstance(sp, dict)
    }
    for candidate in exact_candidates:
        prop = by_name.get(candidate.lower())
        if not prop:
            continue
        value = _site_property_value(prop)
        if _looks_like_url(value):
            return str(prop.get("name") or candidate), value

    client_token = _normalise_property_name(client_name)
    if not client_token:
        return None
    for prop in site_props:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "")
        name_token = _normalise_property_name(name)
        if client_token not in name_token:
            continue
        if not any(keyword in name_token for keyword in _URL_SITE_PROPERTY_KEYWORDS):
            continue
        value = _site_property_value(prop)
        if _looks_like_url(value):
            return name, value
    return None


def _auth_site_property_names(
    client_name: str,
    site_props: list[dict[str, Any]],
    *,
    include_global: bool,
) -> list[str]:
    client_token = _normalise_property_name(client_name)
    names: set[str] = set()
    for prop in site_props:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "")
        lowered = name.lower()
        if lowered.startswith("temp_") or lowered.startswith("istest"):
            continue
        name_token = _normalise_property_name(name)
        if not any(keyword in name_token for keyword in _AUTH_SITE_PROPERTY_KEYWORDS):
            continue
        if include_global or (client_token and client_token in name_token):
            names.add(name)
    return sorted(names)


def _enrich_external_systems_from_site_properties(
    external_systems: list[dict[str, Any]],
    site_props: list[dict[str, Any]],
) -> None:
    """Enrich REST integration metadata from extracted site properties.

    This is intentionally offline-only. It uses OML defaults or already-captured
    effective values when present, and for auth it records property names rather
    than secret values.
    """
    for es in external_systems:
        if not isinstance(es, dict) or es.get("type") != "REST":
            continue
        client_name = str(es.get("name") or "")
        if not es.get("endpoint_url"):
            endpoint_prop = _find_endpoint_site_property(client_name, site_props)
            if endpoint_prop:
                prop_name, endpoint_url = endpoint_prop
                es["endpoint_url"] = endpoint_url
                es["endpoint_url_source"] = "site_property"
                es["endpoint_url_site_property"] = prop_name

        auth = es.get("authentication")
        has_callback = isinstance(auth, dict) and bool(auth.get("on_before_request"))
        auth_props = _auth_site_property_names(
            client_name,
            site_props,
            include_global=has_callback,
        )
        if not auth_props:
            continue
        if isinstance(auth, dict):
            existing = {
                str(name)
                for name in auth.get("site_properties", []) or []
                if name
            }
            auth["site_properties"] = sorted(existing | set(auth_props))
        elif not auth:
            es["authentication"] = {
                "type": "site_properties",
                "site_properties": auth_props,
                "source": "site_property_names",
            }


def run_orchestrator(doc: OmlDocument, pkg_root: Path) -> None:
    """Build top-level package files from domain worker outputs and XML metadata."""
    run_id = f"run-{datetime.date.today().isoformat()}-{uuid.uuid4().hex[:8]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    espace = doc.espace
    module_name = doc.module_name
    app_name = espace.attrib.get("ApplicationName", "")
    description = espace.attrib.get("Description", "")
    module_kind = espace.attrib.get("ModuleKind", "")

    # --- dependencies/ ---
    deps_dir = pkg_root / "dependencies"
    graph, references, ref_summary = _build_dependency_artifacts(doc)
    write_json(deps_dir / "graph.json", graph)
    write_json(deps_dir / "references.json", references)
    stubs = _build_dependency_stubs(doc)
    write_json(deps_dir / "stubs.json", stubs)

    # Compute readiness status based on whether dependencies are co-extracted
    extraction_root = pkg_root.parent
    prerequisites = []
    for ref in ref_summary:
        ref_name = ref["module_name"]
        co_extracted = (extraction_root / ref_name / "package.json").exists()
        prerequisites.append({
            "reference_id": ref_name,
            "type": "module",
            "status": "complete" if co_extracted else "partial",
            "required_for": "package",
            "resolution_path": f"Extract module {ref_name}",
        })
    all_resolved = all(p["status"] == "complete" for p in prerequisites)
    top_status = "complete" if (not prerequisites or all_resolved) else "partial"

    write_json(deps_dir / "readiness.json", {
        "module_name": module_name,
        "status": top_status,
        "prerequisites": prerequisites,
        "blocking_items": [],
        "ready_partitions": [],
        "sequencing_notes": [
            "All referenced modules should be extracted before synthesis."
        ],
    })

    # --- integrations/ ---
    integ_dir = pkg_root / "integrations"
    integ_tracker = CoverageTracker(domain="integrations")
    external_systems = _build_external_systems(doc)
    site_props_path = pkg_root / "data" / "site-properties.json"
    if site_props_path.exists():
        with open(site_props_path, encoding="utf-8") as f:
            site_props = json.load(f)
        if isinstance(site_props, list):
            _enrich_external_systems_from_site_properties(external_systems, site_props)
    integration_lists = _build_integration_artifacts(doc, external_systems)
    for list_name, items in integration_lists.items():
        file_stem = list_name.replace("_", "-")
        for item in items:
            integ_tracker.discover(item.get("name", ""), file_stem)
            integ_tracker.capture(item.get("name", ""), file_stem)
    for es in external_systems:
        integ_tracker.discover(es.get("name", ""), "external-systems")
        integ_tracker.capture(es.get("name", ""), "external-systems")
    for list_name, items in integration_lists.items():
        write_json(integ_dir / f"{list_name.replace('_', '-')}.json", items)
    write_json(integ_dir / "external-systems.json", external_systems)
    write_json(integ_dir / "coverage.json", integ_tracker.to_coverage_dict())
    write_json(integ_dir / "traversal-log.json", integ_tracker.to_traversal_dict())
    (integ_dir / "compatibility-notes.md").parent.mkdir(parents=True, exist_ok=True)
    (integ_dir / "compatibility-notes.md").write_text(
        f"# Compatibility Notes — {module_name}\n\nNo compatibility issues detected.\n",
        encoding="utf-8",
    )

    # --- scaffold/ ---
    scaffold_dir = pkg_root / "scaffold"
    scaffold_tracker = CoverageTracker(domain="scaffold")
    scaffold_reqs = _build_scaffold_requirements(doc)
    for theme in scaffold_reqs.get("themes", []):
        scaffold_tracker.discover(theme, "scaffold/themes")
        scaffold_tracker.capture(theme, "scaffold/themes")
    for flow in scaffold_reqs.get("navigation_flows", []):
        scaffold_tracker.discover(flow, "scaffold/navigation_flows")
        scaffold_tracker.capture(flow, "scaffold/navigation_flows")
    persistence_reqs = _build_persistence_requirements(doc)
    for req in persistence_reqs:
        scaffold_tracker.discover(req.get("entity", ""), "scaffold/persistence")
        scaffold_tracker.capture(req.get("entity", ""), "scaffold/persistence")
    write_json(scaffold_dir / "requirements.json", scaffold_reqs)
    write_json(scaffold_dir / "shared-components.json", [])
    write_json(scaffold_dir / "backend-adapters.json", [])
    write_json(scaffold_dir / "persistence-requirements.json", persistence_reqs)
    write_json(scaffold_dir / "coverage.json", scaffold_tracker.to_coverage_dict())
    write_json(scaffold_dir / "traversal-log.json", scaffold_tracker.to_traversal_dict())

    # --- domain completeness ---
    # domain -> relative coverage.json path. The assets domain writes its
    # coverage under evidence/ (not assets/) and was previously omitted from
    # this rollup entirely — even though it is the one domain that emits real
    # blocked/skipped signals.
    _domain_cov_paths = {
        "interfaces": "interfaces/coverage.json",
        "logic": "logic/coverage.json",
        "processes": "processes/coverage.json",
        "data": "data/coverage.json",
        "assets": "evidence/coverage.json",
    }
    domain_completeness = {}
    for domain, rel in _domain_cov_paths.items():
        cov_path = pkg_root / rel
        if cov_path.exists():
            with open(cov_path, encoding="utf-8") as f:
                cov = json.load(f)
            status = cov.get("status", "unknown")
            skip_reasons = _skip_reasons(pkg_root, rel)
            accounted_skip_reasons = _accounted_skip_reasons(skip_reasons, domain)
            domain_completeness[domain] = {
                "status": status,
                "evidence_confidence": _confidence_for_status(status),
                "coverage_path": rel,
                "traversal_status": cov.get("traversal_status", "unknown"),
                "skipped_count": cov.get("skipped_count", 0),
                "skip_reasons": skip_reasons,
                "accounted_skipped_count": sum(accounted_skip_reasons.values()),
                "accounted_skip_reasons": accounted_skip_reasons,
                "blocked_count": cov.get("blocked_count", 0),
                "unresolved_count": cov.get("unresolved_count", 0),
            }
        else:
            domain_completeness[domain] = {
                "status": "unknown",
                "evidence_confidence": "low",
                "coverage_path": rel,
                "traversal_status": "unknown",
                "skipped_count": 0,
                "skip_reasons": {},
                "accounted_skipped_count": 0,
                "accounted_skip_reasons": {},
                "blocked_count": 0,
                "unresolved_count": 0,
            }
    _integ_cov = integ_tracker.to_coverage_dict()
    domain_completeness["integrations"] = {
        "status": integ_tracker.status,
        "evidence_confidence": _confidence_for_status(integ_tracker.status),
        "coverage_path": "integrations/coverage.json",
        "traversal_status": _integ_cov["traversal_status"],
        "skipped_count": _integ_cov["skipped_count"],
        "skip_reasons": {},
        "accounted_skipped_count": 0,
        "accounted_skip_reasons": {},
        "blocked_count": _integ_cov["blocked_count"],
        "unresolved_count": _integ_cov["unresolved_count"],
    }
    _scaffold_cov = scaffold_tracker.to_coverage_dict()
    domain_completeness["scaffold"] = {
        "status": scaffold_tracker.status,
        "evidence_confidence": _confidence_for_status(scaffold_tracker.status),
        "coverage_path": "scaffold/coverage.json",
        "traversal_status": _scaffold_cov["traversal_status"],
        "skipped_count": _scaffold_cov["skipped_count"],
        "skip_reasons": {},
        "accounted_skipped_count": 0,
        "accounted_skip_reasons": {},
        "blocked_count": _scaffold_cov["blocked_count"],
        "unresolved_count": _scaffold_cov["unresolved_count"],
    }

    # --- downstream partitions ---
    downstream_partitions = {}
    for domain in ("interfaces", "logic", "processes", "data"):
        part_path = pkg_root / domain / "partitions.json"
        if part_path.exists():
            with open(part_path, encoding="utf-8") as f:
                parts = json.load(f)
            # Workers may emit a dict with "partitions" key or a plain list.
            if isinstance(parts, dict):
                partitions = parts.get("partitions", [])
            elif isinstance(parts, list):
                partitions = parts
            else:
                partitions = []
            ready_count = sum(1 for p in partitions if isinstance(p, dict) and p.get("status") == "ready")
            downstream_partitions[domain] = {
                "count": len(partitions),
                "ready": ready_count,
                "partitions_path": f"{domain}/partitions.json",
            }
        else:
            downstream_partitions[domain] = {"count": 0, "ready": 0, "partitions_path": None}

    # --- implementation readiness ---
    all_statuses = [d.get("status") for d in domain_completeness.values()]
    if all(s == "complete" for s in all_statuses):
        impl_status = "ready"
        impl_reason = "All domains extracted completely."
    elif any(s == "blocked" for s in all_statuses):
        impl_status = "blocked"
        blocked = [d for d, v in domain_completeness.items() if v.get("status") == "blocked"]
        impl_reason = f"Blocked domains: {', '.join(blocked)}"
    else:
        impl_status = "partial"
        impl_reason = "Some domains are partial or unknown."

    # --- package.json ---
    gaps = _aggregate_domain_gaps(domain_completeness)
    accounted_gaps = _aggregate_domain_accounted_gaps(domain_completeness)

    # Load known-external.json to filter platform deps from blocking_dependencies
    _known_external_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "known-external.json"
    platform_deps: set[str] = set()
    if _known_external_path.is_file():
        try:
            _ke_data = json.loads(_known_external_path.read_text(encoding="utf-8"))
            platform_deps = (
                set(_ke_data.get("platform_builtins", []))
                | set(_ke_data.get("forge_extensions", []))
                | set(_ke_data.get("mobile_plugins", []))
            )
        except (json.JSONDecodeError, OSError):
            pass

    # Compute which modules are co-extracted (siblings in the same extraction dir)
    all_module_names: set[str] = set()
    extraction_parent = pkg_root.parent
    if extraction_parent.is_dir():
        for sibling in extraction_parent.iterdir():
            if sibling.is_dir() and (sibling / "package.json").exists():
                all_module_names.add(sibling.name)

    blocking_dependencies = [
        r["module_name"] for r in ref_summary
        if r["module_name"] not in platform_deps
        and r["module_name"] not in all_module_names
    ]
    dep_status = "resolved" if not blocking_dependencies else "partial"

    package_json = {
        "module_name": module_name,
        "application_name": app_name,
        "run_id": run_id,
        "extracted_at": now,
        "package_contract_version": "0.1.0",
        "package_boundary": {
            "status": "valid",
            "owned_scope": f"All Service Studio artifacts for module {module_name}",
            "externalized_dependencies": [r["module_name"] for r in ref_summary],
            "open_questions": [],
        },
        "source_evidence": {
            "summary": f"Full XML extraction from {module_name}.oml via oml-utilities",
            "evidence_index_path": "evidence/index.json",
            "confidence": _source_evidence_confidence(domain_completeness),
            "gaps": gaps,
            "accounted_gaps": accounted_gaps,
        },
        "domain_completeness": domain_completeness,
        "dependency_readiness": {
            "status": dep_status,
            "blocking_dependencies": blocking_dependencies,
            "notes": "Referenced modules must be extracted separately." if blocking_dependencies else "All app-level dependencies resolved.",
            "details_path": "dependencies/readiness.json",
        },
        "implementation_readiness": {
            "status": impl_status,
            "reason": impl_reason,
            "required_followups": [],
        },
        "downstream_partitions": downstream_partitions,
        "cross_module_contracts": [
            {
                "contract_id": ref["module_name"],
                "kind": "module_reference",
                "direction": "inbound",
                "status": _resolve_contract_status(ref, platform_deps, all_module_names),
                "notes": f"References {len(ref.get('entities', []))} entities, {len(ref.get('structures', []))} structures",
            }
            for ref in ref_summary
        ],
    }
    write_json(pkg_root / "package.json", package_json)

    # --- summary.md ---
    summary_lines = [
        f"# {module_name} — Module Summary",
        "",
        f"**Application**: {app_name}",
        f"**Module Kind**: {module_kind}",
        f"**Description**: {description or 'N/A'}",
        "",
        "## Key Dependencies",
        "",
    ]
    for ref in ref_summary[:10]:
        summary_lines.append(f"- {ref['module_name']} ({ref.get('reference_type', 'module')})")
    if len(ref_summary) > 10:
        summary_lines.append(f"- ... and {len(ref_summary) - 10} more")
    summary_lines.extend([
        "",
        "## Implementation Readiness",
        "",
        f"**Status**: {impl_status}",
        f"**Reason**: {impl_reason}",
        "",
        "## Domain Coverage",
        "",
    ])
    for domain, info in domain_completeness.items():
        summary_lines.append(f"- **{domain}**: {info['status']}")
    summary_lines.append("")
    (pkg_root / "summary.md").parent.mkdir(parents=True, exist_ok=True)
    (pkg_root / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    # --- coverage-report.md ---
    report_lines = [
        f"# {module_name} — Coverage Report",
        "",
        "## Domain Status",
        "",
    ]
    for domain, info in domain_completeness.items():
        report_lines.append(f"### {domain.title()}")
        report_lines.append(f"- Status: {info['status']}")
        report_lines.append(f"- Traversal: {info['traversal_status']}")
        report_lines.append(f"- Evidence confidence: {info['evidence_confidence']}")
        report_lines.append("")
    report_lines.extend([
        "## Missing Evidence",
        "",
    ])
    if gaps:
        for g in gaps:
            report_lines.append(
                f"- **{g['domain']}**: {g['count']} {g['kind']} (see `{g['coverage_path']}`)"
            )
    else:
        report_lines.append("None detected.")
    report_lines.extend([
        "",
        "## Unresolved Gaps",
        "",
        "External module dependencies are listed in `dependencies/readiness.json`.",
        "",
    ])
    (pkg_root / "coverage-report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def _build_dependency_artifacts(
    doc: OmlDocument,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build dependency graph and references from the References fragment."""
    refs_frag = doc.fragment("References")
    edges: list[dict[str, Any]] = []
    all_references: list[dict[str, Any]] = []
    ref_summary = extract_dependency_references(doc)

    if refs_frag is None:
        return {"module": doc.module_name, "edges": []}, [], []

    resolver = TypeResolver.from_doc(doc)

    for ref in ref_summary:
        edge_types = []
        if ref.get("entities"):
            edge_types.append("entity")
        if ref.get("actions"):
            edge_types.append("action")
        if ref.get("structures"):
            edge_types.append("structure")
        if ref.get("web_blocks"):
            edge_types.append("ui_block")
        if ref.get("themes"):
            edge_types.append("theme")
        if ref.get("client_actions"):
            edge_types.append("client_action")
        if ref.get("nr_web_flows"):
            edge_types.append("ui_flow")

        edges.append({
            "source": doc.module_name,
            "target": ref["module_name"],
            "direction": "outbound",
            "edge_types": edge_types,
            "status": "partial",
        })

    for ref_node in refs_frag.findall("Reference"):
        ref_name = ref_node.attrib.get("Name", "")
        ref_key = ref_node.attrib.get("Key", "")
        ref_mod = resolver.reference_modules.get(ref_key)

        entity_elems = ref_node.findall(".//ReferenceEntity")
        actions = [a.attrib.get("Name", "") for a in ref_node.findall(".//ReferenceAction")]
        structure_elems = ref_node.findall(".//ReferenceStructure")
        client_actions = [c.attrib.get("Name", "") for c in ref_node.findall(".//ReferenceClientAction")]

        for ent_elem in entity_elems:
            ent_name = ent_elem.attrib.get("Name", "")
            ent_attrs = [
                {
                    "name": a.attrib.get("Name"),
                    "label": a.attrib.get("Label"),
                    "mandatory": a.attrib.get("IsMandatory") == "Yes",
                    "type": resolver.resolve(
                        a.attrib.get("Type"),
                        current_reference=ref_mod,
                    ),
                    "length": _ref_field_length(a),
                    "default": _ref_default_value(a),
                }
                for a in ent_elem.findall("./Attributes/ReferenceEntityAttribute")
            ]
            all_references.append({
                "reference_id": f"{ref_name}.{ent_name}",
                "type": "entity",
                "source_module": ref_name,
                "satisfied_in_package": False,
                "attributes": ent_attrs,
            })
        for act in actions:
            all_references.append({
                "reference_id": f"{ref_name}.{act}",
                "type": "action",
                "source_module": ref_name,
                "satisfied_in_package": False,
            })
        for struct_elem in structure_elems:
            struct_name = struct_elem.attrib.get("Name", "")
            struct_attrs = [
                {
                    "name": a.attrib.get("Name"),
                    "label": a.attrib.get("Label"),
                    "mandatory": a.attrib.get("IsMandatory") == "Yes",
                    "type": resolver.resolve(
                        a.attrib.get("Type"),
                        current_reference=ref_mod,
                    ),
                    "length": _ref_field_length(a),
                    "default": _ref_default_value(a),
                }
                for a in struct_elem.findall("./Attributes/ReferenceStructureAttribute")
            ]
            all_references.append({
                "reference_id": f"{ref_name}.{struct_name}",
                "type": "structure",
                "source_module": ref_name,
                "satisfied_in_package": False,
                "attributes": struct_attrs,
            })
        for ca in client_actions:
            all_references.append({
                "reference_id": f"{ref_name}.{ca}",
                "type": "client_action",
                "source_module": ref_name,
                "satisfied_in_package": False,
            })
        for wf in [f.attrib.get("Name", "") for f in ref_node.findall(".//ReferenceNRWebFlows/*")]:
            all_references.append({
                "reference_id": f"{ref_name}.{wf}",
                "type": "web_flow",
                "source_module": ref_name,
                "satisfied_in_package": False,
            })

    graph = {"module": doc.module_name, "edges": edges}
    return graph, all_references, ref_summary


def extract_dependency_references(doc: OmlDocument) -> list[dict[str, Any]]:
    """Extract outbound module references from an OML document.

    This is the shared dependency-discovery surface used by the package
    orchestrator and by entry-module closure walks.
    """
    refs_frag = doc.fragment("References")
    if refs_frag is None:
        return []

    references: list[dict[str, Any]] = []
    for ref_node in refs_frag.findall("Reference"):
        references.append({
            "module_name": ref_node.attrib.get("Name", ""),
            "reference_type": ref_node.attrib.get("ReferenceType", ""),
            "entities": [
                e.attrib.get("Name", "")
                for e in ref_node.findall(".//ReferenceEntity")
            ],
            "actions": [
                a.attrib.get("Name", "")
                for a in ref_node.findall(".//ReferenceAction")
            ],
            "structures": [
                s.attrib.get("Name", "")
                for s in ref_node.findall(".//ReferenceStructure")
            ],
            "web_blocks": [
                w.attrib.get("Name", "")
                for w in ref_node.findall(".//ReferenceWebBlock")
            ],
            "themes": [
                t.attrib.get("Name", "")
                for t in ref_node.findall(".//ReferenceTheme")
            ],
            "client_actions": [
                c.attrib.get("Name", "")
                for c in ref_node.findall(".//ReferenceClientAction")
            ],
            "nr_web_flows": [
                f.attrib.get("Name", "")
                for f in ref_node.findall(".//ReferenceNRWebFlows/*")
            ],
        })
    return references


def _ref_field_length(node: Any) -> int | None:
    """Extract field length from a reference attribute element."""
    val = expression_value(node.find("Length"))
    if isinstance(val, int) and val != 0:
        return val
    return None


def _ref_default_value(node: Any) -> Any:
    """Extract default value from a reference attribute element."""
    return expression_value(node.find("DefaultValue"))


def _build_dependency_stubs(doc: OmlDocument) -> list[dict[str, Any]]:
    """Build typed dependency stubs grouped by source module."""
    refs_frag = doc.fragment("References")
    if refs_frag is None:
        return []

    resolver = TypeResolver.from_doc(doc)
    stubs: list[dict[str, Any]] = []

    for ref_node in refs_frag.findall("Reference"):
        ref_key = ref_node.attrib.get("Key", "")
        ref_name = ref_node.attrib.get("Name", "")
        ref_mod = resolver.reference_modules.get(ref_key)

        actions = _extract_action_stubs(ref_node, ".//ReferenceAction", resolver, ref_mod)
        client_actions = _extract_action_stubs(
            ref_node, ".//ReferenceClientAction", resolver, ref_mod,
        )
        entities = _extract_entity_stubs(ref_node, resolver, ref_mod)
        structures = _extract_structure_stubs(ref_node, resolver, ref_mod)

        stubs.append({
            "module": ref_name,
            "actions": actions,
            "client_actions": client_actions,
            "entities": entities,
            "structures": structures,
        })

    return stubs


def _resolve_type_name(
    resolver: TypeResolver,
    type_attr: str | None,
    ref_mod: Any,
) -> str:
    """Resolve a type attribute to a human-readable string."""
    if not type_attr:
        return "Unknown"
    resolved = resolver.resolve(type_attr, current_reference=ref_mod)
    kind = resolved.get("kind", "unknown")
    if kind == "primitive":
        return resolved.get("name", type_attr)
    if kind in ("reference_structure", "reference_entity", "local_entity"):
        return resolved.get("name") or resolved.get("key", type_attr)
    if kind == "entity_identifier":
        ename = resolved.get("entity_name", "")
        return f"{ename} Identifier" if ename else "Identifier"
    if kind == "list":
        elem = resolved.get("element_type", {})
        inner = elem.get("name") or elem.get("kind", "Unknown")
        return f"List<{inner}>"
    if kind == "anonymous_structure":
        return "Record"
    return resolved.get("name", type_attr)


def _extract_action_stubs(
    ref_node: Any,
    xpath: str,
    resolver: TypeResolver,
    ref_mod: Any,
) -> list[dict[str, Any]]:
    """Extract action stubs (works for both ReferenceAction and ReferenceClientAction)."""
    actions: list[dict[str, Any]] = []
    for action_node in ref_node.findall(xpath):
        name = action_node.attrib.get("Name", "")

        inputs: list[dict[str, Any]] = []
        for param in action_node.findall(
            "./InputParameters/Variables.ReferenceGenericInputParameter"
        ):
            inputs.append({
                "name": param.attrib.get("Name", ""),
                "type": _resolve_type_name(resolver, param.attrib.get("Type"), ref_mod),
                "mandatory": param.attrib.get("IsMandatory") == "Yes",
            })

        outputs: list[dict[str, Any]] = []
        for param in action_node.findall(
            "./OutputParameters/Variables.ReferenceGenericOutputParameter"
        ):
            outputs.append({
                "name": param.attrib.get("Name", ""),
                "type": _resolve_type_name(resolver, param.attrib.get("Type"), ref_mod),
            })

        actions.append({"name": name, "inputs": inputs, "outputs": outputs})
    return actions


def _extract_entity_stubs(
    ref_node: Any,
    resolver: TypeResolver,
    ref_mod: Any,
) -> list[dict[str, Any]]:
    """Extract entity stubs from a Reference node."""
    entities: list[dict[str, Any]] = []
    for entity_node in ref_node.findall(".//ReferenceEntity"):
        name = entity_node.attrib.get("Name", "")
        attrs: list[dict[str, Any]] = []
        for attr in entity_node.findall("./Attributes/ReferenceEntityAttribute"):
            attrs.append({
                "name": attr.attrib.get("Name", ""),
                "type": _resolve_type_name(resolver, attr.attrib.get("Type"), ref_mod),
                "mandatory": attr.attrib.get("IsMandatory") == "Yes",
            })
        entities.append({"name": name, "attributes": attrs})
    return entities


def _extract_structure_stubs(
    ref_node: "ET.Element",
    resolver: TypeResolver,
    ref_mod: "RefModule | None",
) -> list[dict[str, Any]]:
    """Extract structure stubs from a Reference node."""
    structures: list[dict[str, Any]] = []
    for struct_node in ref_node.findall(".//ReferenceStructure"):
        name = struct_node.attrib.get("Name", "")
        attrs: list[dict[str, Any]] = []
        for attr in struct_node.findall("./Attributes/ReferenceStructureAttribute"):
            attrs.append({
                "name": attr.attrib.get("Name", ""),
                "type": _resolve_type_name(resolver, attr.attrib.get("Type"), ref_mod),
                "mandatory": attr.attrib.get("IsMandatory") == "Yes",
            })
        structures.append({"name": name, "attributes": attrs})
    return structures


def _io_parameters(elem: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inputs = [
        {
            "name": p.attrib.get("Name", ""),
            "type": p.attrib.get("Type", ""),
            "is_mandatory": p.attrib.get("IsMandatory", "No") == "Yes",
        }
        for p in elem.findall("./InputParameters/*")
    ]
    outputs = [
        {"name": p.attrib.get("Name", ""), "type": p.attrib.get("Type", "")}
        for p in elem.findall("./OutputParameters/*")
    ]
    return inputs, outputs


def _fragment_items(doc: OmlDocument, base: str) -> list[Any]:
    """Children of the ``base`` fragment and its ``base#<key>`` splits."""
    items: list[Any] = []
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name == base or frag_name.startswith(base + "#"):
            items.extend(frag_elem)
    return items


def _build_exposed_apis(doc: OmlDocument) -> list[dict[str, Any]]:
    """Exposed REST (``CustomServices``) and SOAP (``WebServices``) integrations."""
    exposed: list[dict[str, Any]] = []
    for base, marker in (("CustomServices", "CustomService"), ("WebServices", "WebService")):
        for svc in _fragment_items(doc, base):
            name = svc.attrib.get("Name", "")
            if marker not in svc.tag or not name:
                continue
            kind = "SOAP" if base == "WebServices" else _classify_custom_client_type(svc.tag)
            operations: list[dict[str, Any]] = []
            for container in svc:
                for op in container:
                    op_name = op.attrib.get("Name", "")
                    if not op_name or not ("Action" in op.tag or "Method" in op.tag):
                        continue
                    inputs, outputs = _io_parameters(op)
                    operations.append({
                        "name": op_name,
                        "input_parameters": inputs,
                        "output_parameters": outputs,
                    })
            exposed.append({
                "name": name,
                "kind": kind,
                "description": svc.attrib.get("Description", ""),
                "operations": operations,
            })
    return exposed


def _build_integration_artifacts(
    doc: OmlDocument, external_systems: list[dict[str, Any]] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Split a module's integration surface into four lists.

    - ``consumed_apis``: consumed REST/SOAP integrations (from external systems)
    - ``exposed_apis``: exposed REST/SOAP integrations
    - ``public_actions``: public server actions other modules can reference
    - ``dependency_actions``: actions this module references from other modules

    Public and dependency actions are module-to-module calls, not REST/SOAP.
    """
    if external_systems is None:
        external_systems = _build_external_systems(doc)
    consumed = [
        {
            "name": es.get("name", ""),
            "kind": es.get("type", ""),
            "description": es.get("description", ""),
            "endpoint_url": es.get("endpoint_url", ""),
            "operations": [
                {k: op[k] for k in ("name", "http_method", "url_path") if k in op}
                for op in es.get("operations", [])
            ],
        }
        for es in external_systems
        if es.get("type") in ("REST", "SOAP")
    ]

    dependency_actions: list[dict[str, Any]] = []
    refs_frag = doc.fragment("References")
    if refs_frag is not None:
        for ref_node in refs_frag.findall("Reference"):
            ref_name = ref_node.attrib.get("Name", "")
            for action in ref_node.findall(".//ReferenceAction"):
                dependency_actions.append({
                    "name": action.attrib.get("Name", ""),
                    "module": ref_name,
                    "kind": "dependency_action",
                })

    public_actions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for ua in _fragment_items(doc, "UserActions"):
        if "UserAction" not in ua.tag or ua.attrib.get("Public") != "Yes":
            continue
        key = ua.attrib.get("Key", "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        inputs, outputs = _io_parameters(ua)
        public_actions.append({
            "name": ua.attrib.get("Name", ""),
            "kind": "public_action",
            "description": ua.attrib.get("Description", ""),
            "is_function": ua.attrib.get("IsFunction", "No") == "Yes",
            "input_parameters": inputs,
            "output_parameters": outputs,
        })

    return {
        "consumed_apis": consumed,
        "exposed_apis": _build_exposed_apis(doc),
        "public_actions": public_actions,
        "dependency_actions": dependency_actions,
    }


def _build_flow_name_index(flow_elem: Any) -> dict[str, str]:
    """Build a GUID-key → human-readable-name mapping from a callback flow element.

    Scans the flow's ``LocalVariables``, ``InputParameters``, and
    ``OutputParameters`` children to map their ``Key`` attribute to their
    ``Name`` attribute.  This lets ``_extract_callback_detail`` resolve
    GUID-based variable references into meaningful names.
    """
    index: dict[str, str] = {}
    for container_tag in ("LocalVariables", "InputParameters", "OutputParameters"):
        container = flow_elem.find(container_tag)
        if container is None:
            continue
        for item in container:
            key = item.attrib.get("Key", "")
            name = item.attrib.get("Name", "")
            if key and name:
                index[key] = name
    return index


def _extract_callback_detail(
    node: Any,
    name_index: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract detail from an OnBeforeRequest/OnAfterResponse callback node.

    Args:
        node: XML element containing callback flow nodes.
        name_index: Optional mapping of GUID keys to human-readable names,
            built from the flow's LocalVariables/InputParameters/OutputParameters.
    """
    name = node.attrib.get("Name", "")
    has_conditional = False
    assigned_headers: list[str] = []
    header_keywords = {"header", "authorization", "token", "apikey", "auth", "x-"}
    uses_rest_callback_structure = False
    calls_actions: list[str] = []

    for desc in node.iter():
        tag = desc.tag.rsplit("}", 1)[-1] if "}" in desc.tag else desc.tag
        # Strip OutSystems node-type prefixes (e.g. Nodes.If -> If)
        for prefix in ("Nodes.", "NRNodes."):
            if tag.startswith(prefix):
                tag = tag[len(prefix):]
                break
        if tag in ("If", "Switch"):
            has_conditional = True
        # Capture action calls to understand callback delegation
        if tag == "ExecuteAction":
            action_ref = desc.attrib.get("Action", "")
            if "UserAction" in action_ref:
                calls_actions.append("user_action")
            elif "ReferenceAction" in action_ref:
                calls_actions.append("reference_action")
        # Look for Assign nodes that set header variables.
        # OutSystems stores assignment targets inside:
        #   Nodes.Assign / Assignments / Assignment / Variable / ParsedExpression
        # with Identifier or CompoundIdentifier children whose Ref or Name
        # attribute holds the variable reference.
        if tag in ("Assign", "AssignStatement"):
            # Walk the proper assignment structure
            assignments_container = desc.find("Assignments")
            if assignments_container is not None:
                for assignment in assignments_container:
                    var_expr = assignment.find("Variable/ParsedExpression")
                    if var_expr is None:
                        continue
                    for ident in var_expr:
                        ident_tag = ident.tag.rsplit("}", 1)[-1] if "}" in ident.tag else ident.tag
                        if ident_tag in ("Identifier", "CompoundIdentifier"):
                            # Extract name from Ref path (last segment after last '.')
                            ref = ident.attrib.get("Ref", "")
                            ident_name = ident.attrib.get("Name", "")
                            if ref:
                                # Ref format: "Variables.LocalVariable:/path/LocalVariables.KEY"
                                # Take the last dotted segment as the variable name hint
                                last_seg = ref.rsplit(".", 1)[-1] if "." in ref else ref
                                # Resolve GUID to human-readable name using index
                                if name_index and last_seg in name_index:
                                    resolved = name_index[last_seg]
                                    ident_name = resolved
                                else:
                                    ident_name = ident_name or last_seg
                            # Check for RestCallbackStructure type references
                            type_ref = ident.attrib.get("Type", "")
                            if "RestCallbackStructure" in type_ref:
                                uses_rest_callback_structure = True
                            if ident_name and any(kw in ident_name.lower() for kw in header_keywords):
                                assigned_headers.append(ident_name)
                            break  # Only need the first identifier per assignment
            else:
                # Fallback: check attributes directly (older format)
                var_name = desc.attrib.get("Variable", "") or desc.attrib.get("Name", "")
                if any(kw in var_name.lower() for kw in header_keywords):
                    assigned_headers.append(var_name)
    result: dict[str, Any] = {"name": name}
    if assigned_headers:
        result["assigned_headers"] = assigned_headers
    if has_conditional:
        result["has_conditional_logic"] = True
    if uses_rest_callback_structure:
        result["modifies_http_request"] = True
    if calls_actions:
        result["delegates_to"] = list(set(calls_actions))
    return result


# CustomClient CustomProperty names that may carry credential VALUES baked into
# the OML design-time config. We never emit their values into evidence — we
# record only the presence of a configured credential (Gap 17 + hard security
# rule; redaction precedent: output/site_properties.py secret handling).
_CREDENTIAL_PROPERTY_NAMES = frozenset(
    {
        "Username",
        "Password",
        "Client ID",
        "Client Secret",
        "Access Token URL",
        "Scopes",
        "ClientAuthentication",
        "API Key",
        "ApiKey",
        "ApiKeyValue",
    }
)

# CustomProperty names that hold non-secret integration config we can emit
# verbatim.
_CONFIG_PROPERTY_NAMES = frozenset(
    {
        "Base URL",
        "AuthenticationType",
        "SwaggerDefinitionURL",
        "DateFormat",
        "Interoperability",
    }
)


def _classify_custom_client_type(tag: str) -> str:
    """Classify an integration from its ``CustomClient-OutSystems.Plugin.<X>`` tag.

    OutSystems stores the integration kind in the element tag suffix, e.g.
    ``CustomClient-OutSystems.Plugin.REST.RestClient``. SOAP consumed web
    services live in the ``WebReferences`` fragment instead (handled
    separately). SAP integrations, when present, surface as a ``.SAP.`` plugin
    variant. A bare ``CustomClient`` (no plugin suffix) defaults to REST, which
    is the historical behavior for this repo's corpus.
    """
    local = tag.rsplit("}", 1)[-1] if "}" in tag else tag
    lowered = local.lower()
    if ".sap." in lowered or lowered.endswith(".sap") or "sapclient" in lowered:
        return "SAP"
    if ".soap." in lowered or "soapclient" in lowered:
        return "SOAP"
    return "REST"


def _build_credential_auth_config(
    props_elem: Any,
) -> tuple[dict[str, Any] | None, int]:
    """Return (auth_config, redacted_count) from a CustomProperties element.

    Reads credential-bearing properties (Username/Password/Client Secret/...)
    and records ONLY whether each is configured — never the value. When a
    credential property carries a value it is redacted and counted (R5: no
    silent drop). Returns ``None`` when no credential properties are configured.
    """
    if props_elem is None:
        return None, 0
    configured: list[str] = []
    redacted = 0
    scheme = ""
    for prop in props_elem:
        prop_name = prop.attrib.get("PropertyName", "")
        if prop_name == "AuthenticationType":
            val = prop.attrib.get("Value", "")
            if not val:
                ve = prop.find("ValueExpression")
                val = ve.text.strip() if ve is not None and ve.text else ""
            if val:
                scheme = val
            continue
        if prop_name not in _CREDENTIAL_PROPERTY_NAMES:
            continue
        val = prop.attrib.get("Value", "")
        if not val:
            ve = prop.find("ValueExpression")
            val = ve.text.strip() if ve is not None and ve.text else ""
        if val and val.strip():
            configured.append(prop_name)
            redacted += 1
    if not configured and not scheme:
        return None, 0
    # Derive a scheme when AuthenticationType is blank (the corpus norm) from
    # which credential fields are configured (R3: cause enum for the unknown).
    if not scheme:
        cfg = set(configured)
        if {"Client ID", "Client Secret"} & cfg or "Access Token URL" in cfg:
            scheme = "oauth"
        elif {"Username", "Password"} & cfg:
            scheme = "basic"
        elif {"API Key", "ApiKey", "ApiKeyValue"} & cfg:
            scheme = "api_key"
        else:
            scheme = "configured_credential"
    auth_config: dict[str, Any] = {
        "auth_scheme": scheme,
        "configured_credentials": sorted(configured),
        "source": "custom_client_properties",
        "values_redacted": redacted > 0,
    }
    if redacted:
        auth_config["redacted_credential_count"] = redacted
        auth_config["redaction_reason"] = "embedded_credential"
    return auth_config, redacted


def _build_external_systems(doc: OmlDocument) -> list[dict[str, Any]]:
    """Extract external system integrations from CustomClients and WebReferences."""
    systems: list[dict[str, Any]] = []

    # REST consumed APIs from CustomClients fragments
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name != "CustomClients" and not frag_name.startswith("CustomClients#"):
            continue
        for client in frag_elem:
            if "CustomClient" not in client.tag:
                continue

            name = client.attrib.get("Name", "")
            description = client.attrib.get("Description", "")

            # Extract properties (Base URL, AuthenticationType, etc.)
            # OutSystems stores property values in two places:
            # 1. The "Value" attribute on the CustomProperty element (design-time default)
            # 2. The <ValueExpression> child text (expression override)
            # We check Value first (most common), then fall back to ValueExpression.
            base_url = ""
            auth_type = ""
            props = client.find("CustomProperties")
            if props is not None:
                for prop in props:
                    prop_name = prop.attrib.get("PropertyName", "")
                    val = prop.attrib.get("Value", "")
                    if not val:
                        ve = prop.find("ValueExpression")
                        val = ve.text.strip() if ve is not None and ve.text else ""
                    if prop_name == "Base URL":
                        base_url = val
                    elif prop_name == "AuthenticationType":
                        auth_type = val

            # Scan for OnBeforeRequest / OnAfterResponse callback elements
            on_before_request = None
            on_after_response = None

            # Strategy 1: Look for callback flows inside <CustomCallbackFlows>
            callback_flows_elem = client.find("CustomCallbackFlows")
            # Build a lookup from callback Key → flow element for Strategy 3
            _flow_by_key: dict[str, Any] = {}
            if callback_flows_elem is not None:
                for flow_elem in callback_flows_elem:
                    fk = flow_elem.attrib.get("Key", "")
                    if fk:
                        _flow_by_key[fk] = flow_elem
                    flow_name = flow_elem.attrib.get("Name", "")
                    if "OnBeforeRequest" in flow_name and on_before_request is None:
                        # Build name index from flow definition
                        ni = _build_flow_name_index(flow_elem)
                        on_before_request = _extract_callback_detail(flow_elem, name_index=ni)
                        # Also try to resolve the flow's nodes from a keyed fragment
                        flow_key = flow_elem.attrib.get("Key", "")
                        if flow_key:
                            frag = doc.fragment(f"NodesNotShownInESpaceTree#{flow_key}")
                            if frag is not None:
                                detail = _extract_callback_detail(frag, name_index=ni)
                                if detail.get("assigned_headers"):
                                    on_before_request["assigned_headers"] = detail["assigned_headers"]
                                if detail.get("has_conditional_logic"):
                                    on_before_request["has_conditional_logic"] = True
                                if detail.get("modifies_http_request"):
                                    on_before_request["modifies_http_request"] = True
                                if detail.get("delegates_to"):
                                    on_before_request["delegates_to"] = detail["delegates_to"]
                    elif "OnAfterResponse" in flow_name and on_after_response is None:
                        ni = _build_flow_name_index(flow_elem)
                        on_after_response = _extract_callback_detail(flow_elem, name_index=ni)
                        flow_key = flow_elem.attrib.get("Key", "")
                        if flow_key:
                            frag = doc.fragment(f"NodesNotShownInESpaceTree#{flow_key}")
                            if frag is not None:
                                detail = _extract_callback_detail(frag, name_index=ni)
                                if detail.get("assigned_headers"):
                                    on_after_response["assigned_headers"] = detail["assigned_headers"]
                                if detail.get("has_conditional_logic"):
                                    on_after_response["has_conditional_logic"] = True

            # Strategy 2: Check direct children (older OML format)
            if on_before_request is None or on_after_response is None:
                for child in client:
                    child_tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
                    child_name = child.attrib.get("Name", "")
                    if on_before_request is None and ("OnBeforeRequest" in child_tag or "OnBeforeRequest" in child_name):
                        on_before_request = _extract_callback_detail(child)
                    elif on_after_response is None and ("OnAfterResponse" in child_tag or "OnAfterResponse" in child_name):
                        on_after_response = _extract_callback_detail(child)

            # Strategy 3: Resolve callback GUIDs from CustomProperties
            if props is not None and (on_before_request is None or on_after_response is None):
                for prop in props:
                    prop_name = prop.attrib.get("PropertyName", "")
                    val = prop.attrib.get("Value", "")
                    if not val:
                        continue
                    # Extract the GUID key from the property value
                    # Format: "Flows.CustomCallbackFlow-...:^/CustomCallbackFlows.<KEY>"
                    cb_key = ""
                    if "/CustomCallbackFlows." in val:
                        cb_key = val.split("/CustomCallbackFlows.")[-1]
                    # Build name index from matching flow element if available
                    ni: dict[str, str] | None = None
                    if cb_key:
                        matched_flow = _flow_by_key.get(cb_key)
                        if matched_flow is not None:
                            ni = _build_flow_name_index(matched_flow)
                    if "OnBeforeRequest" in prop_name and on_before_request is None:
                        detail: dict[str, Any] = {"name": val}
                        if cb_key:
                            frag = doc.fragment(f"NodesNotShownInESpaceTree#{cb_key}")
                            if frag is not None:
                                detail = _extract_callback_detail(frag, name_index=ni)
                                detail.setdefault("name", val)
                        on_before_request = detail
                    elif "OnAfterResponse" in prop_name and on_after_response is None:
                        detail = {"name": val}
                        if cb_key:
                            frag = doc.fragment(f"NodesNotShownInESpaceTree#{cb_key}")
                            if frag is not None:
                                detail = _extract_callback_detail(frag, name_index=ni)
                                detail.setdefault("name", val)
                        on_after_response = detail

            # Build structured auth detail when callbacks are present
            auth_detail: str | dict[str, Any] = auth_type
            if on_before_request or on_after_response:
                auth_obj: dict[str, Any] = {"type": auth_type}
                if on_before_request:
                    auth_obj["on_before_request"] = on_before_request
                if on_after_response:
                    auth_obj["on_after_response"] = on_after_response
                auth_detail = auth_obj

            # Extract operations / methods
            operations: list[dict[str, Any]] = []
            actions_elem = client.find("CustomActions")
            if actions_elem is not None:
                for action in actions_elem:
                    if "CustomAction" not in action.tag:
                        continue
                    op_name = action.attrib.get("Name", "")
                    # Extract URL path from CustomAction properties
                    url_path = ""
                    http_method = ""
                    action_props = action.find("CustomProperties")
                    if action_props is not None:
                        for ap in action_props:
                            ap_name = ap.attrib.get("PropertyName", "")
                            ap_val = ap.attrib.get("Value", "")
                            if not ap_val:
                                ap_ve = ap.find("ValueExpression")
                                ap_val = ap_ve.text.strip() if ap_ve is not None and ap_ve.text else ""
                            if ap_name == "URL Path":
                                url_path = ap_val
                            elif ap_name == "HTTPMethod":
                                http_method = ap_val
                    inputs: list[str | dict[str, str]] = []
                    for p in action.findall("./InputParameters/*"):
                        p_name = p.attrib.get("Name", "")
                        p_props = p.find("CustomProperties")
                        placement = ""
                        orig_name = ""
                        if p_props is not None:
                            for pp in p_props:
                                pp_name = pp.attrib.get("PropertyName", "")
                                if pp_name == "InputPlacement":
                                    placement = pp.attrib.get("Value", "")
                                elif pp_name == "Original Name":
                                    orig_name = pp.attrib.get("Value", "")
                        if placement == "1":  # Sent as HTTP header
                            inp_rec: dict[str, str] = {"name": p_name, "send_in": "header"}
                            if orig_name:
                                inp_rec["original_name"] = orig_name
                            inputs.append(inp_rec)
                        else:
                            inputs.append(p_name)
                    outputs = [
                        p.attrib.get("Name", "")
                        for p in action.findall("./OutputParameters/*")
                    ]
                    op: dict[str, Any] = {
                        "name": op_name,
                        "input_parameters": inputs,
                        "output_parameters": outputs,
                    }
                    if url_path:
                        op["url_path"] = url_path
                    if http_method:
                        op["http_method"] = http_method
                    operations.append(op)

            # ----- Infer auth from operation-level header parameters -----
            _AUTH_HEADER_KEYWORDS = {
                "authorization", "token", "apikey", "api_key", "api-key",
                "x-api-key", "bearer", "keyid", "x-dmm-token", "secret",
            }
            auth_headers_found: set[str] = set()
            for _op in operations:
                for _inp in _op.get("input_parameters", []):
                    if isinstance(_inp, dict) and _inp.get("send_in") == "header":
                        _hdr_name = _inp.get("original_name") or _inp.get("name", "")
                        if any(kw in _hdr_name.lower() for kw in _AUTH_HEADER_KEYWORDS):
                            auth_headers_found.add(_hdr_name)

            # ----- Detect OAuth patterns from operation inputs -----
            _OAUTH_KEYWORDS = {
                "client_id", "client_secret", "access_token",
                "grant_type", "redirect_uri", "refresh_token",
            }
            # Also match camelCase / partial variants (e.g. ClientId, Secret)
            _OAUTH_FUZZY = {
                "clientid": "client_id",
                "secret": "client_secret",
                "accesstoken": "access_token",
                "granttype": "grant_type",
                "redirecturi": "redirect_uri",
                "refreshtoken": "refresh_token",
                "appaccesstoken": "access_token",
            }
            oauth_params_found: set[str] = set()
            for _op in operations:
                for _inp in _op.get("input_parameters", []):
                    _inp_name = _inp if isinstance(_inp, str) else _inp.get("name", "")
                    _norm = _inp_name.lower().replace("_", "")
                    if _inp_name.lower() in _OAUTH_KEYWORDS:
                        oauth_params_found.add(_inp_name.lower())
                    elif _norm in _OAUTH_FUZZY:
                        oauth_params_found.add(_OAUTH_FUZZY[_norm])

            # Apply inferred auth when no explicit auth type is set
            if not auth_detail and auth_headers_found:
                auth_detail = {
                    "type": "header",
                    "headers": sorted(auth_headers_found),
                    "source": "operation_parameters",
                }
            elif isinstance(auth_detail, dict) and not auth_detail.get("type") and auth_headers_found:
                auth_detail["inferred_headers"] = sorted(auth_headers_found)

            if not auth_detail and len(oauth_params_found) >= 2:
                auth_detail = {
                    "type": "oauth",
                    "oauth_params": sorted(oauth_params_found),
                    "source": "operation_parameters",
                }
            elif isinstance(auth_detail, dict) and not auth_detail.get("type") and len(oauth_params_found) >= 2:
                auth_detail["type"] = "oauth"
                auth_detail["oauth_params"] = sorted(oauth_params_found)

            client_type = _classify_custom_client_type(client.tag)

            # Capture configured credentials with their VALUES redacted (Gap 17
            # + hard security rule). This never emits a secret value.
            auth_config, _redacted = _build_credential_auth_config(props)

            system: dict[str, Any] = {
                "name": name,
                "type": client_type,
                "description": description,
                "endpoint_url": base_url,
                "authentication": auth_detail,
                "operations": operations,
            }
            if auth_config is not None:
                system["auth_config"] = auth_config
            systems.append(system)

    # SOAP web services from WebReferences fragments. Each WebReference carries
    # the WSDL URL, a SOAP endpoint name, and a set of WebReferenceMethods
    # (operations). Structures are referenced separately and not inlined here.
    for frag_name, frag_elem in doc.fragments.items():
        if frag_name != "WebReferences" and not frag_name.startswith("WebReferences#"):
            continue
        for ws in frag_elem:
            if "WebReference" not in ws.tag:
                continue
            name = ws.attrib.get("Name", "")
            if not name:
                continue
            wsdl_url = ws.attrib.get("URL", "")
            soap_endpoint = ws.attrib.get("Endpoint", "")

            soap_ops: list[dict[str, Any]] = []
            methods_elem = ws.find("WebReferenceMethods")
            if methods_elem is not None:
                for m in methods_elem:
                    if "Method" not in m.tag:
                        continue
                    op_name = m.attrib.get("Name", "") or m.attrib.get(
                        "OriginalName", ""
                    )
                    if not op_name:
                        continue
                    inputs = [
                        p.attrib.get("Name", "")
                        for p in m.findall("./InputParameters/*")
                    ]
                    outputs = [
                        p.attrib.get("Name", "")
                        for p in m.findall("./OutputParameters/*")
                    ]
                    soap_op: dict[str, Any] = {"name": op_name}
                    if inputs:
                        soap_op["input_parameters"] = inputs
                    if outputs:
                        soap_op["output_parameters"] = outputs
                    soap_ops.append(soap_op)

            soap_system: dict[str, Any] = {
                "name": name,
                "type": "SOAP",
                "description": ws.attrib.get("Description", ""),
                "endpoint_url": wsdl_url,
                "authentication": "",
                "operations": soap_ops,
            }
            if wsdl_url:
                soap_system["wsdl_url"] = wsdl_url
            if soap_endpoint:
                soap_system["soap_endpoint"] = soap_endpoint
            if not soap_ops:
                # R3: record why operations are empty rather than a bare [].
                soap_system["operations_status"] = "no_web_reference_methods_in_oml"
            systems.append(soap_system)

    return systems


def _build_scaffold_requirements(doc: OmlDocument) -> dict[str, Any]:
    """Build scaffold requirements from observed patterns."""
    themes: list[str] = []
    theme_frag = doc.fragment("NRThemes")
    if theme_frag is None:
        theme_frag = doc.fragment("Themes")
    if theme_frag is not None:
        for theme in theme_frag:
            themes.append(theme.attrib.get("Name", ""))

    nav_flows: list[str] = []
    flows_frag = doc.fragment("NRWebFlows")
    if flows_frag is None:
        flows_frag = doc.fragment("WebFlows")
    if flows_frag is not None:
        for flow in flows_frag:
            nav_flows.append(flow.attrib.get("Name", ""))

    return {
        "themes": themes,
        "navigation_flows": nav_flows,
        "shared_blocks": [],
        "services": [],
    }


def _build_persistence_requirements(doc: OmlDocument) -> list[dict[str, Any]]:
    """Extract persistence requirements from entities."""
    entities_frag = doc.fragment("Entities")
    if entities_frag is None:
        return []

    requirements: list[dict[str, Any]] = []
    for entity in entities_frag.findall("Entity"):
        name = entity.attrib.get("Name", "")
        is_static = entity.attrib.get("IsStaticEntity") == "Yes"
        attrs = entity.findall("./Attributes/EntityAttribute")
        requirements.append({
            "entity": name,
            "is_static": is_static,
            "attribute_count": len(attrs),
            "type": "lookup_table" if is_static else "transactional_table",
        })

    return requirements
