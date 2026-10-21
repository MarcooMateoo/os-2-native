"""Errors and warnings from extraction runs, written to ``<output>/extraction-issues.log``.

Code anywhere in os2native reports problems through the ``os2native`` logger::

    log.error("[Module] stage: what failed", exc_info=True)
    log.warning("[Module] domain: what could not be resolved")

``issue_log`` attaches the file for the duration of a command and returns the
counts so they can go into ``inventory.json``.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

LOG_FILE = "extraction-issues.log"
log = logging.getLogger("os2native")


class _Counter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.errors = 0
        self.warnings = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.errors += 1
        else:
            self.warnings += 1

    def summary(self) -> dict[str, int | str]:
        return {"errors": self.errors, "warnings": self.warnings, "log": LOG_FILE}


class _ConsoleFormatter(logging.Formatter):
    """One line per issue on the console; tracebacks only go to the file."""

    def format(self, record: logging.LogRecord) -> str:
        return f"{record.levelname}: {record.getMessage()}"


def _ensure_console() -> None:
    if not any(getattr(h, "_os2native_console", False) for h in log.handlers):
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        console.setFormatter(_ConsoleFormatter())
        console._os2native_console = True  # type: ignore[attr-defined]
        log.addHandler(console)
    log.setLevel(logging.INFO)
    log.propagate = False


@contextmanager
def issue_log(output: Path, command: str, *, fresh: bool) -> Iterator[_Counter]:
    """Record WARNING/ERROR messages to ``output/extraction-issues.log``.

    ``fresh=True`` (a full ``package`` run) starts a new file; other commands
    append to it, so earlier issues are kept.
    """
    _ensure_console()
    output.mkdir(parents=True, exist_ok=True)
    path = output / LOG_FILE
    started = datetime.now()
    with open(path, "w" if fresh else "a", encoding="utf-8") as fh:
        fh.write(f"=== {started:%Y-%m-%d %H:%M:%S}  os2native {command}\n")

    file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    counter = _Counter()
    log.addHandler(file_handler)
    log.addHandler(counter)
    try:
        yield counter
    finally:
        log.removeHandler(file_handler)
        log.removeHandler(counter)
        file_handler.close()
        with open(path, "a", encoding="utf-8") as fh:
            if counter.errors or counter.warnings:
                fh.write(f"=== finished: {counter.errors} error(s), {counter.warnings} warning(s)\n\n")
            else:
                fh.write("=== finished: no errors or warnings\n\n")


def log_coverage_issues(pkg_root: Path) -> None:
    """Log items each worker recorded as unresolved or blocked for one module."""
    names, explanations = _key_names(pkg_root)
    module = pkg_root.name
    for traversal in sorted(pkg_root.glob("*/traversal-log.json")):
        try:
            events = json.loads(traversal.read_text(encoding="utf-8")).get("events", [])
        except (OSError, ValueError, AttributeError):
            continue
        for event in events:
            action = event.get("action")
            if action not in ("unresolved", "blocked"):
                continue
            item = event.get("item_id", "")
            label = names.get(item, item)
            detail = event.get("path", "")
            result = event.get("result", action)
            where = f" ({detail}; {result})" if detail and result != action else f" ({detail})" if detail else ""
            why = explanations.get((item, detail.rsplit("/", 1)[-1].lower()), "")
            log.warning(
                "[%s] %s: %s %s%s%s",
                module, traversal.parent.name, action, label, where, f": {why}" if why else "",
            )


def _key_names(pkg_root: Path) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Element Key -> readable name, and (Key, field) -> gap explanation.

    Both come from the module's extracted JSON lists; explanations are the
    ``gaps[].detail`` texts workers attach to items (e.g. timer schedule).
    """
    names: dict[str, str] = {}
    explanations: dict[tuple[str, str], str] = {}
    for path in pkg_root.glob("*/*.json"):
        if path.name in ("traversal-log.json", "coverage.json", "partitions.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        kind = _singular(path.stem.replace("-", " "))
        for item in data:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            if item.get("name"):
                names.setdefault(item["key"], f"{kind} '{item['name']}'")
            for gap in item.get("gaps") or []:
                if isinstance(gap, dict) and gap.get("field") and gap.get("detail"):
                    explanations[(item["key"], str(gap["field"]).lower())] = gap["detail"]
    return names, explanations


def _singular(words: str) -> str:
    if words.endswith("sses"):
        return words[:-2]
    if words.endswith("ies"):
        return words[:-3] + "y"
    return words[:-1] if words.endswith("s") else words
