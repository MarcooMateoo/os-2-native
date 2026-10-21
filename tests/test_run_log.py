"""Tests for extraction-issues.log."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from os2native.package import run_package, run_report
from os2native.run_log import LOG_FILE, issue_log, log, log_coverage_issues


def _read(out: Path) -> str:
    return (out / LOG_FILE).read_text(encoding="utf-8")


def test_issue_log_counts_and_writes_errors_and_warnings(tmp_path):
    with issue_log(tmp_path, "package App.osp", fresh=True) as issues:
        log.info("progress is not an issue")
        log.warning("[App] interfaces: unresolved thing")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.error("[App] extraction failed: boom", exc_info=True)

    assert (issues.errors, issues.warnings) == (1, 1)
    text = _read(tmp_path)
    assert "os2native package App.osp" in text
    assert "WARNING  [App] interfaces: unresolved thing" in text
    assert "ERROR    [App] extraction failed: boom" in text
    assert "Traceback" in text and "RuntimeError: boom" in text
    assert "progress is not an issue" not in text
    assert "=== finished: 1 error(s), 1 warning(s)" in text


def test_fresh_run_replaces_log_and_other_commands_append(tmp_path):
    with issue_log(tmp_path, "package A", fresh=True):
        log.warning("first run")
    with issue_log(tmp_path, "package B", fresh=True):
        pass
    with issue_log(tmp_path, "report", fresh=False):
        log.warning("from report")

    text = _read(tmp_path)
    assert "first run" not in text
    assert "package B" in text and "no errors or warnings" in text
    assert "from report" in text


def test_coverage_issues_use_readable_names(tmp_path):
    root = tmp_path / "App_CS"
    (root / "processes").mkdir(parents=True)
    (root / "processes" / "timers.json").write_text(json.dumps([{
        "key": "T1", "name": "Timer_Purge",
        "gaps": [{"field": "schedule", "detail": "Set in Service Center, not in the OML."}],
    }]))
    (root / "processes" / "traversal-log.json").write_text(json.dumps({"events": [
        {"action": "discover", "item_id": "T1", "path": "Timer", "result": "ok"},
        {"action": "unresolved", "item_id": "T1", "path": "Timer/Schedule", "result": "unresolved"},
    ]}))

    with issue_log(tmp_path, "test", fresh=True) as issues:
        log_coverage_issues(root)

    assert issues.warnings == 1
    assert (
        "[App_CS] processes: unresolved timer 'Timer_Purge' (Timer/Schedule): "
        "Set in Service Center, not in the OML." in _read(tmp_path)
    )


def test_package_logs_bad_inputs_and_failed_modules(tmp_path):
    osp = tmp_path / "App.osp"
    with zipfile.ZipFile(osp, "w") as zf:
        zf.writestr("Broken.oml", b"not a real oml")
    bogus = tmp_path / "notes.txt"
    bogus.write_text("x")
    out = tmp_path / "out"

    rc = run_package([osp, bogus, tmp_path / "Missing.oml"], out)

    assert rc == 1
    text = _read(out)
    assert "ERROR    [input] notes.txt: unsupported file type" in text
    assert "ERROR    [input]" in text and "Missing.oml: file not found" in text
    assert "ERROR    [Broken] OML to XML conversion failed" in text
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["issues"]["errors"] == 3
    assert "Broken.oml" in inventory["failed_modules"]


def test_report_appends_to_existing_log(tmp_path):
    out = tmp_path / "out"
    (out / "extraction").mkdir(parents=True)
    with issue_log(out, "package App.osp", fresh=True):
        log.warning("from package")
    run_report(out)
    text = _read(out)
    assert "from package" in text and "os2native report" in text
