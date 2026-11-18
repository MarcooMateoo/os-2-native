"""Tests for scripts/check_dependencies.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_dependencies import check, main  # noqa: E402

KNOWN = {
    "platform_builtins": ["(System)", "OutSystemsUI"],
    "mobile_plugins": ["CameraPlugin"],
    "forge_extensions": ["CryptoAPI"],
}


def _module(out: Path, name: str, targets: list[str]) -> None:
    deps = out / "extraction" / name / "dependencies"
    deps.mkdir(parents=True)
    edges = [{"source": name, "target": t, "direction": "outbound"} for t in targets]
    (deps / "graph.json").write_text(json.dumps({"module": name, "edges": edges}))


def test_classifies_missing_dependencies(tmp_path):
    _module(tmp_path, "App", ["(System)", "App_CS", "CryptoAPI", "HiQRCoder", "CameraPlugin"])
    _module(tmp_path, "App_CS", ["(System)", "OutSystemsUI", "HiQRCoder"])

    report = check(tmp_path, KNOWN)

    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    assert "App_CS" not in by_name  # present in the package
    assert by_name["(System)"]["kind"] == "platform"
    assert by_name["CameraPlugin"]["kind"] == "platform"
    assert by_name["CryptoAPI"]["kind"] == "forge"
    assert by_name["HiQRCoder"]["kind"] == "unknown"
    assert by_name["HiQRCoder"]["used_by"] == ["App", "App_CS"]
    assert by_name["HiQRCoder"]["in_scope_users"] == ["App", "App_CS"]
    assert report["unknown_count"] == 1
    assert report["forge_count"] == 1


def test_main_writes_report_and_fails_on_unknown(tmp_path):
    _module(tmp_path, "App", ["(System)", "Mystery"])
    (tmp_path / "inventory.json").write_text(json.dumps({"extensions_not_extracted": ["X.xif"]}))

    assert main([str(tmp_path)]) == 1
    report = json.loads((tmp_path / "dependency-check.json").read_text(encoding="utf-8"))
    assert report["extensions_not_extracted"] == ["X.xif"]
    assert [d["module"] for d in report["missing_dependencies"]] == ["(System)", "Mystery"]


def test_main_passes_when_only_platform_missing(tmp_path):
    _module(tmp_path, "App", ["(System)"])
    assert main([str(tmp_path)]) == 0


# --- scope, accepted decisions, stale folders --------------------------------

APPS = [
    {"name": "Mobile", "modules": ["App", "App_CS"]},
    {"name": "Backoffice", "modules": ["Web"]},
]


def _estate(out: Path) -> None:
    # Mobile: App -> App_CS -> Lib -> HiQRCoder;  Backoffice: Web -> SVGUtilities
    _module(out, "App", ["(System)", "App_CS"])
    _module(out, "App_CS", ["Lib"])
    _module(out, "Lib", ["HiQRCoder"])
    _module(out, "Web", ["SVGUtilities", "Lib"])
    (out / "applications.json").write_text(json.dumps(APPS))


def test_only_direct_use_by_the_scope_is_asked(tmp_path):
    _estate(tmp_path)
    _module(tmp_path, "Extra", ["Deeper"])
    report = check(tmp_path, KNOWN, {"scope": {"applications": ["mobile"]}})

    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    # Lib is in the package but not in the scope: what it needs is indirect
    assert by_name["HiQRCoder"]["kind"] == "indirect"
    assert by_name["HiQRCoder"]["in_scope_users"] == []
    assert by_name["SVGUtilities"]["kind"] == "indirect"
    assert by_name["Deeper"]["kind"] == "indirect"
    assert report["unknown_count"] == 0
    assert report["scope"]["modules"] == ["App", "App_CS"]
    assert report["scope"]["source"] == "project.json"


def test_default_scope_is_the_manifest_apps_not_extra_inputs(tmp_path):
    _module(tmp_path, "App", ["Mystery"])
    _module(tmp_path, "AddedDep", ["ItsOwnDep"])  # loose .oml the user added
    (tmp_path / "applications.json").write_text(json.dumps([
        {"name": "Mobile", "modules": ["App"], "package": "S.osp"},
        {"name": "(extra inputs)", "modules": ["AddedDep"], "package": None},
    ]))
    report = check(tmp_path, KNOWN)
    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    assert by_name["Mystery"]["kind"] == "unknown"
    assert by_name["ItsOwnDep"]["kind"] == "indirect"
    assert report["scope"] == {
        "source": "package manifest", "applications": ["Mobile"],
        "modules": ["App"], "unmatched": [],
    }


def test_accepted_missing_is_not_asked_again(tmp_path):
    _estate(tmp_path)
    project = {"accepted_missing": {"hiqrcoder": "QR codes rebuilt natively"}}
    report = check(tmp_path, KNOWN, project)
    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    assert by_name["HiQRCoder"]["kind"] == "accepted"
    assert by_name["HiQRCoder"]["reason"] == "QR codes rebuilt natively"
    assert by_name["SVGUtilities"]["kind"] == "unknown"  # no scope: everything counts


def test_unmatched_scope_names_are_reported(tmp_path):
    _estate(tmp_path)
    report = check(tmp_path, KNOWN, {"scope": {"applications": ["Mobile", "Nope"], "modules": ["Ghost"]}})
    assert report["scope"]["unmatched"] == ["Nope", "Ghost"]


def test_folders_from_an_earlier_run_are_ignored(tmp_path):
    _module(tmp_path, "App", ["(System)", "Old"])
    _module(tmp_path, "Old", ["Mystery"])  # left over, not in this run
    (tmp_path / "inventory.json").write_text(json.dumps({"modules": [{"module": "App"}]}))

    report = check(tmp_path, KNOWN)
    by_name = {d["module"]: d for d in report["missing_dependencies"]}
    assert report["stale_extraction_folders"] == ["Old"]
    assert "Mystery" not in by_name
    assert by_name["Old"]["kind"] == "unknown"  # a stale copy doesn't satisfy it


def test_unknown_lists_what_is_used(tmp_path):
    _module(tmp_path, "App", ["PaymentAPI"])
    refs = [
        {"reference_id": "PaymentAPI.Receipt", "type": "structure", "source_module": "PaymentAPI"},
        {"reference_id": "(System).User", "type": "entity", "source_module": "(System)"},
    ]
    (tmp_path / "extraction" / "App" / "dependencies" / "references.json").write_text(json.dumps(refs))
    report = check(tmp_path, KNOWN)
    assert report["missing_dependencies"][0]["used_elements"] == ["structure PaymentAPI.Receipt"]


def test_main_reads_project_json_next_to_output(tmp_path):
    out = tmp_path / "OUTPUT"
    out.mkdir()
    _estate(out)
    (tmp_path / "project.json").write_text(json.dumps({
        "scope": {"applications": ["Mobile"]},
        "accepted_missing": {"HiQRCoder": "not needed"},
    }))
    assert main([str(out)]) == 0
    report = json.loads((out / "dependency-check.json").read_text(encoding="utf-8"))
    assert report["project_config"].endswith("project.json")
