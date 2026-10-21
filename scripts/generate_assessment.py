"""Generate architecture assessment from extraction output.

Produces a structured assessment of extraction quality and architecture
readiness.  On pass 1 (no prior assessment) a full evaluation is performed.
On pass 2+ a mechanical delta is computed and only structurally-triggered
sections are re-assessed.

Output goes to ``<pass_dir>/assessment/``.

Usage::

    python scripts/generate_assessment.py passes/pass-2026-04-24T05-55-45
    python scripts/generate_assessment.py passes/latest --previous passes/pass-001
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import shared utilities from the same scripts/ directory
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from generator_utils import (  # noqa: E402
    classify_module,
    load_domain_file,
    load_pass_packages,
    md_table,
    write_json,
    write_text,
)

# ---------------------------------------------------------------------------
# Platform / forge modules that should be classified as "drop"
# ---------------------------------------------------------------------------

def _load_known_external_manifest() -> dict:
    """Load the shared known-external.json manifest (cached)."""
    global _KNOWN_EXTERNAL_MANIFEST
    if _KNOWN_EXTERNAL_MANIFEST is not None:
        return _KNOWN_EXTERNAL_MANIFEST
    manifest_path = Path(__file__).resolve().parent / "known-external.json"
    with open(manifest_path, encoding="utf-8") as f:
        _KNOWN_EXTERNAL_MANIFEST = json.load(f)
    return _KNOWN_EXTERNAL_MANIFEST


_KNOWN_EXTERNAL_MANIFEST: dict | None = None


def _platform_module_names() -> set[str]:
    return set(_load_known_external_manifest().get("assessment_drop", []))


def _replace_keywords() -> set[str]:
    return set(_load_known_external_manifest().get("replace_keywords", []))


# ---------------------------------------------------------------------------
# Section 1: Pass Health (mechanical)
# ---------------------------------------------------------------------------

def _assess_pass_health(pass_dir: Path) -> dict[str, Any]:
    """Read scorecard.json and extract pass health metrics."""
    scorecard_path = pass_dir / "scorecard.json"
    if not scorecard_path.exists():
        return {
            "total_modules": 0,
            "overall_readiness_pct": 0.0,
            "modules_below_80": [],
            "external_api_count": 0,
            "api_url_coverage_pct": 0.0,
            "total_entities": 0,
            "total_screens": 0,
            "unresolved_dependencies": [],
        }

    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    agg = scorecard.get("aggregate", {})
    modules = scorecard.get("modules", [])

    below_80 = [
        {"module": m["module_name"], "score": m["overall_readiness_pct"]}
        for m in modules
        if m["overall_readiness_pct"] < 80
    ]

    api_info = agg.get("external_apis", {})
    return {
        "total_modules": agg.get("total_modules", 0),
        "overall_readiness_pct": agg.get("overall_avg_readiness_pct", 0.0),
        "modules_below_80": below_80,
        "external_api_count": api_info.get("total", 0),
        "api_url_coverage_pct": api_info.get("url_coverage_pct", 0.0),
        "total_entities": agg.get("data_model", {}).get("total_entities", 0),
        "total_screens": agg.get("ui", {}).get("total_screens", 0),
        "unresolved_dependencies": agg.get("unresolved_dependencies", []),
    }


# ---------------------------------------------------------------------------
# Section 2: Client Action Coverage (mechanical)
# ---------------------------------------------------------------------------

def _assess_client_action_coverage(
    pass_dir: Path, packages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare logic inventory vs interface-embedded client actions."""
    details: list[dict[str, Any]] = []
    modules_with_gap = 0

    for pkg in packages:
        module = pkg.get("module_name", "")

        # Count promoted client actions from logic worker
        ca_data = load_domain_file(pass_dir, module, "logic", "client-actions.json")
        inventory_count = len(ca_data) if isinstance(ca_data, list) else 0

        # Count embedded client actions from interface worker
        embedded_screens = 0
        embedded_blocks = 0
        for kind, field in [("screens", "embedded_in_screens"), ("blocks", "embedded_in_blocks")]:
            items = load_domain_file(pass_dir, module, "interfaces", f"{kind}.json")
            if isinstance(items, list):
                count = sum(len(item.get("client_actions", [])) for item in items)
                if kind == "screens":
                    embedded_screens = count
                else:
                    embedded_blocks = count

        total_embedded = embedded_screens + embedded_blocks
        if total_embedded == 0 and inventory_count == 0:
            continue  # Module has no client actions at all

        coverage = min(round(inventory_count / max(total_embedded, 1) * 100, 1), 100.0)
        if coverage < 80 and total_embedded > 0:
            modules_with_gap += 1

        details.append({
            "module": module,
            "inventory_count": inventory_count,
            "embedded_in_screens": embedded_screens,
            "embedded_in_blocks": embedded_blocks,
            "total_embedded": total_embedded,
            "coverage_pct": coverage,
        })

    # Sort by gap severity (lowest coverage first)
    details.sort(key=lambda d: d["coverage_pct"])

    return {
        "modules_checked": len(details),
        "modules_with_gap": modules_with_gap,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Section 3: Journey Quality (mechanical)
# ---------------------------------------------------------------------------

def _assess_journey_quality(pass_dir: Path) -> dict[str, Any]:
    """Evaluate journey quality from deliverable output."""
    journeys_dir = pass_dir / "deliverables" / "journeys"

    total_journeys = 0
    total_screens = 0
    total_transitions = 0
    cross_module = False

    # Walk per-module journey files
    if journeys_dir.is_dir():
        for jf in journeys_dir.glob("*/journeys.json"):
            try:
                journeys = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for j in journeys:
                total_journeys += 1
                total_screens += j.get("screen_count", 0)
                total_transitions += len(j.get("transitions", []))

        # Check for cross-module journeys
        cross_path = journeys_dir / "cross-module-journeys.md"
        if cross_path.exists():
            content = cross_path.read_text(encoding="utf-8")
            if "cross-module navigation" not in content.lower() or "no cross-module" in content.lower():
                cross_module = False
            else:
                cross_module = True

    avg_transitions = round(total_transitions / max(total_journeys, 1), 2)

    if avg_transitions >= 3:
        verdict = "usable"
    elif avg_transitions >= 1:
        verdict = "weak"
    else:
        verdict = "unusable"

    return {
        "total_journeys": total_journeys,
        "total_screens_in_journeys": total_screens,
        "total_transitions": total_transitions,
        "avg_transitions_per_journey": avg_transitions,
        "cross_module_navigation_detected": cross_module,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Section 4: Spec Depth (mechanical)
# ---------------------------------------------------------------------------

_SUBSTANTIVE_MARKERS_BACKEND = ("func ", "if err != nil", "return ", "ctx context.Context")
_SUBSTANTIVE_MARKERS_FRONTEND = ("StatefulWidget", "BlocProvider", "setState", "Navigator.push")


def _assess_spec_depth(pass_dir: Path) -> dict[str, Any]:
    """Evaluate spec depth from deliverable output."""
    specs_dir = pass_dir / "deliverables" / "specs"

    frontend = {"modules_with_specs": 0, "modules_with_substantive_specs": 0}
    backend = {"modules_with_specs": 0, "modules_with_substantive_specs": 0}
    state_flows = {"modules_with_flows": 0, "flows_with_semantic_labels": 0}

    if not specs_dir.is_dir():
        return {
            "frontend": {**frontend, "verdict": "none"},
            "backend": {**backend, "verdict": "none"},
            "state_flows": {**state_flows, "verdict": "none"},
        }

    for mod_dir in sorted(specs_dir.iterdir()):
        if not mod_dir.is_dir():
            continue

        # Backend spec
        be_path = mod_dir / "backend-spec.md"
        if be_path.exists():
            backend["modules_with_specs"] += 1
            content = be_path.read_text(encoding="utf-8")
            if len(content) > 2048 and any(m in content for m in _SUBSTANTIVE_MARKERS_BACKEND):
                backend["modules_with_substantive_specs"] += 1

        # Frontend spec
        fe_path = mod_dir / "frontend-spec.md"
        if fe_path.exists():
            frontend["modules_with_specs"] += 1
            content = fe_path.read_text(encoding="utf-8")
            if len(content) > 2048 and any(m in content for m in _SUBSTANTIVE_MARKERS_FRONTEND):
                frontend["modules_with_substantive_specs"] += 1

        # State flows
        sf_path = mod_dir / "state-flows.md"
        if sf_path.exists():
            state_flows["modules_with_flows"] += 1
            content = sf_path.read_text(encoding="utf-8")
            # Semantic labels = decision nodes with actual condition text, not just "If"
            if "-->|" in content and content.count("-->|") > 3:
                state_flows["flows_with_semantic_labels"] += 1

    def _verdict(with_specs: int, substantive: int) -> str:
        if with_specs == 0:
            return "none"
        ratio = substantive / with_specs
        if ratio >= 0.5:
            return "substantive"
        if ratio >= 0.1:
            return "signatures_only"
        return "stubs_only"

    return {
        "frontend": {**frontend, "verdict": _verdict(frontend["modules_with_specs"], frontend["modules_with_substantive_specs"])},
        "backend": {**backend, "verdict": _verdict(backend["modules_with_specs"], backend["modules_with_substantive_specs"])},
        "state_flows": {**state_flows, "verdict": _verdict(state_flows["modules_with_flows"], state_flows["flows_with_semantic_labels"])},
    }


# ---------------------------------------------------------------------------
# Section 5: Module Classification (heuristic)
# ---------------------------------------------------------------------------

def _collect_module_classification(
    packages: list[dict[str, Any]], pass_dir: Path,
) -> dict[str, Any]:
    """Classify modules as migrate/replace/drop using heuristics."""
    classifications: dict[str, str] = {}

    for pkg in packages:
        name = pkg.get("module_name", "")
        mod_type = classify_module(name)

        # Platform modules → drop
        if name in _platform_module_names():
            classifications[name] = "drop"
            continue

        # Plugin/analytics modules → replace
        if any(kw in name for kw in _replace_keywords()):
            classifications[name] = "replace"
            continue

        # Theme modules → drop (styling, not logic)
        if mod_type == "theme":
            classifications[name] = "drop"
            continue

        # Everything else → migrate
        classifications[name] = "migrate"

    counts = {"migrate": 0, "replace": 0, "drop": 0}
    migrate_list = []
    replace_list = []
    drop_list = []
    for name, cls in sorted(classifications.items()):
        counts[cls] += 1
        if cls == "migrate":
            migrate_list.append(name)
        elif cls == "replace":
            replace_list.append(name)
        else:
            drop_list.append(name)

    return {
        "migrate": migrate_list,
        "replace": replace_list,
        "drop": drop_list,
        "counts": counts,
        "classifications": classifications,
    }


# ---------------------------------------------------------------------------
# Section 6: Domain Mapping (heuristic — groups by dependency clusters)
# ---------------------------------------------------------------------------

def _collect_domain_mapping(
    packages: list[dict[str, Any]],
    pass_dir: Path,
    classifications: dict[str, str],
) -> list[dict[str, Any]]:
    """Group MIGRATE modules into domains based on dependency clusters."""
    # Collect dependency edges for migrate modules
    migrate_deps: dict[str, list[str]] = {}
    for pkg in packages:
        name = pkg.get("module_name", "")
        if classifications.get(name) != "migrate":
            continue
        deps = pkg.get("package_boundary", {}).get("externalized_dependencies", [])
        app_deps = [d for d in deps if classifications.get(d) == "migrate"]
        migrate_deps[name] = app_deps

    # Return a placeholder structure — proper domain clustering requires
    # LLM judgment or manual input.  For now, just group by module type.
    type_groups: dict[str, list[str]] = {}
    for pkg in packages:
        name = pkg.get("module_name", "")
        if classifications.get(name) != "migrate":
            continue
        mod_type = classify_module(name)
        type_groups.setdefault(mod_type, []).append(name)

    domains: list[dict[str, Any]] = []
    for mod_type, modules in sorted(type_groups.items()):
        domains.append({
            "name": f"Group: {mod_type}",
            "weight": round(len(modules) / max(len(migrate_deps), 1), 2),
            "key_modules": modules[:5],
            "supporting_modules": modules[5:],
            "module_count": len(modules),
        })

    return domains


# ---------------------------------------------------------------------------
# Section 7: Integration Tiers (heuristic)
# ---------------------------------------------------------------------------

def _collect_integration_tiers(
    packages: list[dict[str, Any]], pass_dir: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Classify external integrations by complexity tier."""
    tier_1: list[dict[str, Any]] = []
    tier_2: list[dict[str, Any]] = []
    tier_3: list[dict[str, Any]] = []

    for pkg in packages:
        module = pkg.get("module_name", "")
        ext_systems = load_domain_file(pass_dir, module, "integrations", "external-systems.json")
        if not isinstance(ext_systems, list) or not ext_systems:
            continue

        for sys_info in ext_systems:
            ops = sys_info.get("operations", [])
            ops_count = len(ops)

            # Check max flow complexity from server actions
            sa_data = load_domain_file(pass_dir, module, "logic", "server-actions.json")
            max_nodes = 0
            if isinstance(sa_data, list):
                for sa in sa_data:
                    node_count = len(sa.get("flow", {}).get("nodes", []))
                    max_nodes = max(max_nodes, node_count)

            entry = {
                "module": module,
                "system_name": sys_info.get("name", ""),
                "ops_count": ops_count,
                "max_flow_nodes": max_nodes,
            }

            if ops_count > 50 or max_nodes > 80:
                entry["reason"] = "high operation count or complex flows"
                tier_1.append(entry)
            elif ops_count > 10 or max_nodes > 30:
                entry["reason"] = "moderate complexity"
                tier_2.append(entry)
            else:
                entry["reason"] = "thin wrapper"
                tier_3.append(entry)

    return {
        "tier_1_complex": tier_1,
        "tier_2_moderate": tier_2,
        "tier_3_thin_wrapper": tier_3,
    }


# ---------------------------------------------------------------------------
# Section 8: God Module Analysis
# ---------------------------------------------------------------------------

def _collect_god_modules(
    packages: list[dict[str, Any]], pass_dir: Path,
) -> list[dict[str, Any]]:
    """Identify modules with excessive complexity."""
    god_modules: list[dict[str, Any]] = []

    for pkg in packages:
        module = pkg.get("module_name", "")

        # Count logic partitions
        logic_parts = load_domain_file(pass_dir, module, "logic", "partitions.json")
        logic_count = len(logic_parts) if isinstance(logic_parts, list) else 0

        # Count dependencies
        deps = pkg.get("package_boundary", {}).get("externalized_dependencies", [])
        dep_count = len(deps)

        # Count screens/blocks
        screens = load_domain_file(pass_dir, module, "interfaces", "screens.json")
        screen_count = len(screens) if isinstance(screens, list) else 0
        blocks = load_domain_file(pass_dir, module, "interfaces", "blocks.json")
        block_count = len(blocks) if isinstance(blocks, list) else 0

        # Count server actions
        sa = load_domain_file(pass_dir, module, "logic", "server-actions.json")
        sa_count = len(sa) if isinstance(sa, list) else 0

        if logic_count > 100 or dep_count > 50 or screen_count > 50:
            god_modules.append({
                "module": module,
                "logic_partitions": logic_count,
                "external_deps": dep_count,
                "screens": screen_count,
                "blocks": block_count,
                "server_actions": sa_count,
            })

    god_modules.sort(key=lambda g: g["logic_partitions"], reverse=True)
    return god_modules


# ---------------------------------------------------------------------------
# Section 9: Data Model Summary
# ---------------------------------------------------------------------------

def _collect_data_model_summary(
    packages: list[dict[str, Any]], pass_dir: Path,
    classifications: dict[str, str],
) -> dict[str, Any]:
    """Summarize data model across all modules."""
    raw_count = 0
    platform_count = 0
    entity_names: dict[str, list[str]] = {}  # name → [modules]

    for pkg in packages:
        module = pkg.get("module_name", "")
        entities = load_domain_file(pass_dir, module, "data", "entities.json")
        if not isinstance(entities, list):
            continue
        for ent in entities:
            raw_count += 1
            name = ent.get("name", "")
            entity_names.setdefault(name, []).append(module)
            if classifications.get(module) == "drop":
                platform_count += 1

    # Duplicate detection: same entity name in multiple modules
    duplicates = {name: mods for name, mods in entity_names.items() if len(mods) > 1}

    # Cross-module FK count from db-schema deliverable
    cross_fk_count = 0
    cross_fk_path = pass_dir / "deliverables" / "db-schema" / "cross-module-fks.json"
    if cross_fk_path.exists():
        try:
            fk_data = json.loads(cross_fk_path.read_text(encoding="utf-8"))
            cross_fk_count = len(fk_data) if isinstance(fk_data, list) else 0
        except (json.JSONDecodeError, OSError):
            pass

    migrate_count = raw_count - platform_count - len(duplicates)

    return {
        "raw_entity_count": raw_count,
        "estimated_target_count": max(migrate_count, 0),
        "cross_module_fks": cross_fk_count,
        "duplicate_entities_identified": len(duplicates),
        "platform_entities_to_drop": platform_count,
    }


# ---------------------------------------------------------------------------
# Section 10: Screen Estimate
# ---------------------------------------------------------------------------

def _collect_screen_estimate(
    packages: list[dict[str, Any]], pass_dir: Path,
    classifications: dict[str, str],
) -> dict[str, Any]:
    """Estimate real app screens vs platform/infrastructure screens."""
    raw_count = 0
    platform_screens = 0
    app_screens = 0

    for pkg in packages:
        module = pkg.get("module_name", "")
        screens = load_domain_file(pass_dir, module, "interfaces", "screens.json")
        if not isinstance(screens, list):
            continue
        count = len([s for s in screens if s.get("kind") != "block"])
        raw_count += count

        if classifications.get(module) == "drop":
            platform_screens += count
        else:
            app_screens += count

    return {
        "raw_screen_count": raw_count,
        "platform_screens": platform_screens,
        "real_app_screens": app_screens,
        "estimated_target_screens": app_screens,
    }


# ---------------------------------------------------------------------------
# Summary Scorecard
# ---------------------------------------------------------------------------

def _compute_summary_scorecard(assessment: dict[str, Any]) -> dict[str, str]:
    """Compute green/yellow/red verdicts for each dimension."""
    health = assessment["pass_health"]
    ca = assessment["client_action_coverage"]
    journeys = assessment["journey_quality"]
    specs = assessment["spec_depth"]

    def _health_verdict() -> str:
        if health["overall_readiness_pct"] >= 90 and not health["modules_below_80"]:
            return "green"
        if health["overall_readiness_pct"] >= 70:
            return "yellow"
        return "red"

    def _ca_verdict() -> str:
        if ca["modules_with_gap"] == 0:
            return "green"
        if ca["modules_with_gap"] <= 3:
            return "yellow"
        return "red"

    def _journey_verdict() -> str:
        v = journeys["verdict"]
        if v == "usable":
            return "green"
        if v == "weak":
            return "yellow"
        return "red"

    def _spec_verdict() -> str:
        be = specs["backend"]["verdict"]
        if be == "substantive":
            return "green"
        if be in ("signatures_only", "stubs_only"):
            return "yellow"
        return "red"

    return {
        "pass_health": _health_verdict(),
        "client_actions": _ca_verdict(),
        "user_journeys": _journey_verdict(),
        "spec_depth": _spec_verdict(),
        "domain_alignment": "green",  # Heuristic always produces output
        "integration_catalog": "green",
        "data_model": "yellow",  # Needs manual review
        "visual_catalog": "yellow",
    }


# ---------------------------------------------------------------------------
# Blockers & Gaps
# ---------------------------------------------------------------------------

def _identify_blockers_and_gaps(assessment: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Identify blockers and gaps from assessment data."""
    blockers: list[str] = []
    gaps: list[str] = []

    # Client action gaps
    ca = assessment["client_action_coverage"]
    if ca["modules_with_gap"] > 0:
        total_missing = sum(
            d["total_embedded"] - d["inventory_count"]
            for d in ca["details"]
            if d["coverage_pct"] < 80
        )
        if total_missing > 100:
            blockers.append(
                f"Client actions not fully promoted to logic inventory "
                f"({ca['modules_with_gap']} modules, ~{total_missing} actions missing)"
            )
        else:
            gaps.append(f"Minor client action promotion gaps in {ca['modules_with_gap']} modules")

    # Journey quality
    journeys = assessment["journey_quality"]
    if journeys["verdict"] == "unusable":
        blockers.append(
            f"Journey generator produces unusable output "
            f"({journeys['avg_transitions_per_journey']} avg transitions/journey)"
        )
    elif journeys["verdict"] == "weak":
        gaps.append("Journey quality is weak — some flows are alphabetical, not navigational")

    # Spec depth
    specs = assessment["spec_depth"]
    if specs["backend"]["verdict"] in ("none", "stubs_only"):
        gaps.append("Backend specs are stubs — no function bodies or business logic")
    if specs["frontend"]["verdict"] in ("none", "stubs_only"):
        gaps.append("Frontend specs are stubs — no widget implementations")

    return blockers, gaps


# ---------------------------------------------------------------------------
# App Context (carry-forward)
# ---------------------------------------------------------------------------

def _build_app_context(
    pass_id: str,
    module_classification: dict[str, Any],
    integration_tiers: dict[str, list[dict[str, Any]]],
    god_modules: list[dict[str, Any]],
    data_model: dict[str, Any],
    screen_estimate: dict[str, Any],
    previous_context: dict[str, Any] | None = None,
    packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the carry-forward app-context.json."""
    # Derive app_name from the most common application_name across packages
    app_name = "unknown"
    if packages:
        from collections import Counter
        app_names = [
            p["application_name"]
            for p in packages
            if p.get("application_name") and p["application_name"] != "unknown"
        ]
        if app_names:
            app_name = Counter(app_names).most_common(1)[0][0]

    # Prefer previous context if it was already set to something meaningful
    if previous_context and previous_context.get("app_name", "unknown") != "unknown":
        app_name = previous_context["app_name"]

    ctx: dict[str, Any] = {
        "app_name": app_name,
        "first_assessed_pass": (
            previous_context.get("first_assessed_pass", pass_id)
            if previous_context
            else pass_id
        ),
        "last_updated_pass": pass_id,
        "module_classifications": module_classification.get("classifications", {}),
        "integration_tiers": {},
        "known_god_modules": [g["module"] for g in god_modules],
        "table_estimate": {
            "target_count": data_model["estimated_target_count"],
        },
        "screen_estimate": {
            "real_app_screens": screen_estimate["real_app_screens"],
        },
    }

    # Flatten integration tiers into lookup
    for tier_name, tier_num in [("tier_1_complex", 1), ("tier_2_moderate", 2), ("tier_3_thin_wrapper", 3)]:
        for entry in integration_tiers.get(tier_name, []):
            ctx["integration_tiers"][entry["module"]] = {
                "tier": tier_num,
                "reason": entry.get("reason", ""),
            }

    return ctx


# ---------------------------------------------------------------------------
# Delta Comparison (Pass 2+)
# ---------------------------------------------------------------------------

def _compute_delta(
    current: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Compute mechanical delta between two assessments."""
    prev_health = previous.get("pass_health", {})
    curr_health = current.get("pass_health", {})

    prev_modules = set(
        d["module"] for d in previous.get("client_action_coverage", {}).get("details", [])
    )
    curr_modules = set(
        d["module"] for d in current.get("client_action_coverage", {}).get("details", [])
    )

    # Client action diff
    ca_diff: dict[str, Any] = {"modules_fixed": []}
    prev_ca = {d["module"]: d for d in previous.get("client_action_coverage", {}).get("details", [])}
    curr_ca = {d["module"]: d for d in current.get("client_action_coverage", {}).get("details", [])}
    for mod in sorted(curr_ca):
        old = prev_ca.get(mod, {})
        new = curr_ca[mod]
        if old.get("coverage_pct", 0) < 50 and new["coverage_pct"] >= 80:
            ca_diff["modules_fixed"].append(mod)
        if old.get("inventory_count", 0) != new["inventory_count"]:
            ca_diff[mod] = {
                "old_inventory": old.get("inventory_count", 0),
                "new_inventory": new["inventory_count"],
                "old_coverage_pct": old.get("coverage_pct", 0),
                "new_coverage_pct": new["coverage_pct"],
            }

    prev_journeys = previous.get("journey_quality", {})
    curr_journeys = current.get("journey_quality", {})

    return {
        "pass_id": current.get("pass_id", ""),
        "compared_to": previous.get("pass_id", ""),
        "assessment_type": "delta",
        "scorecard_diff": {
            "overall_readiness_change": round(
                curr_health.get("overall_readiness_pct", 0)
                - prev_health.get("overall_readiness_pct", 0),
                1,
            ),
            "modules_gained": sorted(curr_modules - prev_modules),
            "modules_lost": sorted(prev_modules - curr_modules),
        },
        "client_action_diff": ca_diff,
        "journey_diff": {
            "old_transitions": prev_journeys.get("total_transitions", 0),
            "new_transitions": curr_journeys.get("total_transitions", 0),
            "cross_module_navigation": curr_journeys.get("cross_module_navigation_detected", False),
        },
        "entity_diff": {
            "old_count": prev_health.get("total_entities", 0),
            "new_count": curr_health.get("total_entities", 0),
            "count_change": (
                curr_health.get("total_entities", 0) - prev_health.get("total_entities", 0)
            ),
        },
    }


# ---------------------------------------------------------------------------
# Structural Triggers (Pass 2+)
# ---------------------------------------------------------------------------

def _evaluate_structural_triggers(
    current: dict[str, Any],
    previous: dict[str, Any],
    prev_context: dict[str, Any],
    *,
    all_current_modules: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate structural trigger conditions."""
    triggers: list[dict[str, Any]] = []

    curr_modules = {
        d["module"] for d in current.get("client_action_coverage", {}).get("details", [])
    }
    prev_classified = set(prev_context.get("module_classifications", {}).keys())

    # new_module
    new_modules = curr_modules - prev_classified
    if new_modules:
        triggers.append({
            "trigger": "new_module",
            "detail": f"New modules: {', '.join(sorted(new_modules))}",
            "action": "re_assess_module_classification",
        })

    # removed_module — use the authoritative full module set when provided
    curr_all = all_current_modules if all_current_modules is not None else curr_modules
    removed = prev_classified - curr_all
    if removed:
        triggers.append({
            "trigger": "removed_module",
            "detail": f"Removed: {', '.join(sorted(removed))}",
            "action": "re_assess_module_classification",
        })

    # client_action_count_change_gt_50pct
    prev_ca = {
        d["module"]: d["inventory_count"]
        for d in previous.get("client_action_coverage", {}).get("details", [])
    }
    curr_ca = {
        d["module"]: d["inventory_count"]
        for d in current.get("client_action_coverage", {}).get("details", [])
    }
    for mod in curr_ca:
        old = prev_ca.get(mod, 0)
        new = curr_ca[mod]
        if old == 0 and new > 0:
            triggers.append({
                "trigger": "client_action_count_change_gt_50pct",
                "detail": f"{mod}: {old} → {new} client actions",
                "action": "re_assess_journey_quality",
            })
            break
        elif old > 0 and abs(new - old) / old > 0.5:
            triggers.append({
                "trigger": "client_action_count_change_gt_50pct",
                "detail": f"{mod}: {old} → {new} ({round((new-old)/old*100)}% change)",
                "action": "re_assess_journey_quality",
            })
            break

    # screen_count_change_gt_20pct
    prev_screens = previous.get("pass_health", {}).get("total_screens", 0)
    curr_screens = current.get("pass_health", {}).get("total_screens", 0)
    if prev_screens > 0 and abs(curr_screens - prev_screens) / prev_screens > 0.2:
        triggers.append({
            "trigger": "screen_count_change_gt_20pct",
            "detail": f"Screens: {prev_screens} → {curr_screens}",
            "action": "re_assess_screen_estimate",
        })

    # journey_transition_increase >100%
    prev_t = previous.get("journey_quality", {}).get("total_transitions", 0)
    curr_t = current.get("journey_quality", {}).get("total_transitions", 0)
    if prev_t > 0 and curr_t > prev_t * 2:
        triggers.append({
            "trigger": "journey_transition_increase",
            "detail": f"Transitions: {prev_t} → {curr_t} (+{round((curr_t-prev_t)/prev_t*100)}%)",
            "action": "re_assess_journey_quality",
        })

    return triggers


# ---------------------------------------------------------------------------
# Markdown Rendering
# ---------------------------------------------------------------------------

_VERDICT_BADGE = {"green": "GREEN", "yellow": "YELLOW", "red": "RED"}


def _render_assessment_md(assessment: dict[str, Any]) -> str:
    """Render assessment.json as human-readable Markdown."""
    lines: list[str] = []
    pass_id = assessment.get("pass_id", "unknown")
    lines.append(f"# Architecture Assessment — {pass_id}")
    lines.append("")
    lines.append(f"**Assessed at:** {assessment.get('assessed_at', '')}")
    lines.append(f"**Type:** {assessment.get('assessment_type', 'full')}")
    lines.append("")

    # Summary scorecard table
    sc = assessment.get("summary_scorecard", {})
    lines.append("## Summary Scorecard")
    lines.append("")
    lines.append(md_table(
        ["Dimension", "Status"],
        [[dim.replace("_", " ").title(), f"[{_VERDICT_BADGE.get(status, '?')}]"]
         for dim, status in sc.items()],
    ))
    lines.append("")

    # Section 1: Pass Health
    health = assessment.get("pass_health", {})
    lines.append("## 1. Pass Health")
    lines.append("")
    lines.append(f"- **Modules:** {health.get('total_modules', 0)}")
    lines.append(f"- **Overall readiness:** {health.get('overall_readiness_pct', 0)}%")
    lines.append(f"- **External APIs:** {health.get('external_api_count', 0)} (URL coverage: {health.get('api_url_coverage_pct', 0)}%)")
    lines.append(f"- **Entities:** {health.get('total_entities', 0)}")
    lines.append(f"- **Screens:** {health.get('total_screens', 0)}")
    below = health.get("modules_below_80", [])
    if below:
        lines.append(f"- **Modules below 80%:** {len(below)}")
        for m in below[:10]:
            lines.append(f"  - {m['module']}: {m['score']}%")
    unresolved = health.get("unresolved_dependencies", [])
    if unresolved:
        lines.append(f"- **Unresolved dependencies:** {len(unresolved)}")
    lines.append("")

    # Section 2: Client Action Coverage
    ca = assessment.get("client_action_coverage", {})
    lines.append("## 2. Client Action Coverage")
    lines.append("")
    lines.append(f"- **Modules checked:** {ca.get('modules_checked', 0)}")
    lines.append(f"- **Modules with gap (<80% coverage):** {ca.get('modules_with_gap', 0)}")
    lines.append("")
    details = ca.get("details", [])
    if details:
        lines.append(md_table(
            ["Module", "Inventory", "Embedded", "Coverage"],
            [[d["module"], str(d["inventory_count"]), str(d["total_embedded"]),
              f"{d['coverage_pct']}%"] for d in details[:20]],
        ))
        lines.append("")

    # Section 3: Journey Quality
    jq = assessment.get("journey_quality", {})
    lines.append("## 3. Journey Quality")
    lines.append("")
    lines.append(f"- **Journeys:** {jq.get('total_journeys', 0)}")
    lines.append(f"- **Screens in journeys:** {jq.get('total_screens_in_journeys', 0)}")
    lines.append(f"- **Transitions:** {jq.get('total_transitions', 0)}")
    lines.append(f"- **Avg transitions/journey:** {jq.get('avg_transitions_per_journey', 0)}")
    lines.append(f"- **Cross-module navigation:** {'Yes' if jq.get('cross_module_navigation_detected') else 'No'}")
    lines.append(f"- **Verdict:** {jq.get('verdict', '?')}")
    lines.append("")

    # Section 4: Spec Depth
    specs = assessment.get("spec_depth", {})
    lines.append("## 4. Spec Depth")
    lines.append("")
    for kind in ("backend", "frontend", "state_flows"):
        s = specs.get(kind, {})
        label = kind.replace("_", " ").title()
        lines.append(f"- **{label}:** {s.get('modules_with_specs', 0)} modules, "
                      f"{s.get('modules_with_substantive_specs', s.get('flows_with_semantic_labels', 0))} substantive — "
                      f"verdict: {s.get('verdict', '?')}")
    lines.append("")

    # Section 5: Module Classification
    mc = assessment.get("module_classification", {})
    counts = mc.get("counts", {})
    lines.append("## 5. Module Classification")
    lines.append("")
    lines.append(f"- **Migrate:** {counts.get('migrate', 0)}")
    lines.append(f"- **Replace:** {counts.get('replace', 0)}")
    lines.append(f"- **Drop:** {counts.get('drop', 0)}")
    lines.append("")

    # Section 8: God Modules
    gods = assessment.get("god_modules", [])
    if gods:
        lines.append("## 8. God Modules")
        lines.append("")
        lines.append(md_table(
            ["Module", "Logic Partitions", "Deps", "Screens", "Blocks"],
            [[g["module"], str(g["logic_partitions"]), str(g["external_deps"]),
              str(g["screens"]), str(g["blocks"])] for g in gods],
        ))
        lines.append("")

    # Blockers & Gaps
    blockers = assessment.get("blockers", [])
    gaps_list = assessment.get("gaps", [])
    if blockers:
        lines.append("## Blockers")
        lines.append("")
        for b in blockers:
            lines.append(f"- **BLOCKER:** {b}")
        lines.append("")
    if gaps_list:
        lines.append("## Gaps")
        lines.append("")
        for g in gaps_list:
            lines.append(f"- {g}")
        lines.append("")

    return "\n".join(lines)


def _render_delta_md(delta: dict[str, Any]) -> str:
    """Render delta.json as human-readable Markdown."""
    lines: list[str] = []
    lines.append(f"# Pass Delta: {delta.get('pass_id', '?')} vs {delta.get('compared_to', '?')}")
    lines.append("")

    sd = delta.get("scorecard_diff", {})
    change = sd.get("overall_readiness_change", 0)
    sign = "+" if change >= 0 else ""
    lines.append("## Scorecard")
    lines.append(f"- Overall readiness change: **{sign}{change}%**")
    gained = sd.get("modules_gained", [])
    lost = sd.get("modules_lost", [])
    if gained:
        lines.append(f"- Modules gained: {', '.join(gained)}")
    if lost:
        lines.append(f"- Modules lost: {', '.join(lost)}")
    lines.append("")

    ca_diff = delta.get("client_action_diff", {})
    fixed = ca_diff.get("modules_fixed", [])
    if fixed:
        lines.append("## Client Actions FIXED")
        for mod in fixed:
            info = ca_diff.get(mod, {})
            lines.append(f"- {mod}: {info.get('old_inventory', 0)} → {info.get('new_inventory', 0)} "
                          f"(coverage: {info.get('old_coverage_pct', 0)}% → {info.get('new_coverage_pct', 0)}%)")
        lines.append("")

    jd = delta.get("journey_diff", {})
    lines.append("## Journeys")
    lines.append(f"- Transitions: {jd.get('old_transitions', 0)} → {jd.get('new_transitions', 0)}")
    lines.append(f"- Cross-module navigation: {'Yes' if jd.get('cross_module_navigation') else 'No'}")
    lines.append("")

    triggers = delta.get("structural_triggers_fired", [])
    if triggers:
        lines.append("## Structural Triggers Fired")
        for t in triggers:
            lines.append(f"- **{t['trigger']}**: {t['detail']} → action: {t['action']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Generate the architecture assessment."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate architecture assessment from extraction output",
    )
    parser.add_argument(
        "pass_dir",
        type=Path,
        help="Path to the extraction pass directory",
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="Path to previous pass for delta comparison",
    )
    args = parser.parse_args(argv)

    pass_dir: Path = args.pass_dir.resolve()
    if not (pass_dir / "extraction").is_dir():
        print(f"ERROR: {pass_dir / 'extraction'} does not exist", file=sys.stderr)
        return 1

    print(f"Generating assessment for {pass_dir.name}")

    packages = load_pass_packages(pass_dir)
    if not packages:
        print("No packages found.")
        return 1

    pass_id = pass_dir.name
    out_dir = pass_dir / "assessment"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load previous assessment if available
    previous_assessment: dict[str, Any] | None = None
    previous_context: dict[str, Any] | None = None
    if args.previous:
        prev_assess_path = args.previous / "assessment" / "assessment.json"
        prev_ctx_path = args.previous / "assessment" / "app-context.json"
        if prev_assess_path.exists():
            try:
                previous_assessment = json.loads(prev_assess_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        if prev_ctx_path.exists():
            try:
                previous_context = json.loads(prev_ctx_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    # --- Run all assessment sections ---
    print("  Section 1: Pass health...")
    pass_health = _assess_pass_health(pass_dir)

    print("  Section 2: Client action coverage...")
    client_actions = _assess_client_action_coverage(pass_dir, packages)

    print("  Section 3: Journey quality...")
    journey_quality = _assess_journey_quality(pass_dir)

    print("  Section 4: Spec depth...")
    spec_depth = _assess_spec_depth(pass_dir)

    print("  Section 5: Module classification...")
    module_classification = _collect_module_classification(packages, pass_dir)

    print("  Section 6: Domain mapping...")
    domain_mapping = _collect_domain_mapping(
        packages, pass_dir, module_classification["classifications"],
    )

    print("  Section 7: Integration tiers...")
    integration_tiers = _collect_integration_tiers(packages, pass_dir)

    print("  Section 8: God modules...")
    god_modules = _collect_god_modules(packages, pass_dir)

    print("  Section 9: Data model...")
    data_model = _collect_data_model_summary(
        packages, pass_dir, module_classification["classifications"],
    )

    print("  Section 10: Screen estimate...")
    screen_estimate = _collect_screen_estimate(
        packages, pass_dir, module_classification["classifications"],
    )

    # --- Build assessment ---
    assessment: dict[str, Any] = {
        "pass_id": pass_id,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "previous_pass_id": args.previous.name if args.previous else None,
        "assessment_type": "delta" if previous_assessment else "full",
        "pass_health": pass_health,
        "client_action_coverage": client_actions,
        "journey_quality": journey_quality,
        "spec_depth": spec_depth,
        "module_classification": {
            "migrate": module_classification["migrate"],
            "replace": module_classification["replace"],
            "drop": module_classification["drop"],
            "counts": module_classification["counts"],
        },
        "domain_mapping": domain_mapping,
        "integration_tiers": integration_tiers,
        "god_modules": god_modules,
        "data_model": data_model,
        "screen_estimate": screen_estimate,
    }

    # Summary scorecard
    assessment["summary_scorecard"] = _compute_summary_scorecard(assessment)

    # Blockers & gaps
    blockers, gaps = _identify_blockers_and_gaps(assessment)
    assessment["blockers"] = blockers
    assessment["gaps"] = gaps

    # --- Write outputs ---
    write_json(out_dir / "assessment.json", assessment)
    write_text(out_dir / "assessment.md", _render_assessment_md(assessment))

    # App context (carry-forward)
    app_context = _build_app_context(
        pass_id, module_classification, integration_tiers,
        god_modules, data_model, screen_estimate, previous_context,
        packages=packages,
    )
    write_json(out_dir / "app-context.json", app_context)

    # Delta (pass 2+ only)
    if previous_assessment:
        print("  Computing delta...")
        delta = _compute_delta(assessment, previous_assessment)

        # Structural triggers
        all_module_names = {p.get("module_name", "") for p in packages} - {""}
        triggers = _evaluate_structural_triggers(
            assessment, previous_assessment, previous_context or {},
            all_current_modules=all_module_names,
        )
        delta["structural_triggers_fired"] = triggers
        delta["sections_re_assessed"] = list({t["action"] for t in triggers})
        delta["sections_carried_forward"] = [
            s for s in [
                "module_classification", "domain_mapping", "integration_tiers",
                "god_modules", "data_model", "screen_estimate",
            ]
            if s not in delta["sections_re_assessed"]
        ]

        # Blocker resolution tracking
        prev_blockers = set(previous_assessment.get("blockers", []))
        curr_blockers = set(blockers)
        delta["new_blockers"] = sorted(curr_blockers - prev_blockers)
        delta["resolved_blockers"] = sorted(prev_blockers - curr_blockers)

        write_json(out_dir / "delta.json", delta)
        write_text(out_dir / "delta.md", _render_delta_md(delta))

    # --- Summary ---
    sc = assessment["summary_scorecard"]
    reds = sum(1 for v in sc.values() if v == "red")
    yellows = sum(1 for v in sc.values() if v == "yellow")
    greens = sum(1 for v in sc.values() if v == "green")
    print()
    print(f"Assessment: {pass_id}")
    print(f"  Scorecard: {greens} green, {yellows} yellow, {reds} red")
    print(f"  Blockers: {len(blockers)}")
    print(f"  Gaps: {len(gaps)}")
    if previous_assessment:
        print(f"  Delta computed against {previous_assessment.get('pass_id', '?')}")
    print(f"  Output: {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
