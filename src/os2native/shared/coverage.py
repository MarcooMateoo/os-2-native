"""Coverage tracking helpers for domain workers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CoverageTracker:
    """Tracks extraction coverage for a single domain."""

    domain: str
    discovered: int = 0
    captured: int = 0
    expanded: int = 0
    blocked: int = 0
    skipped: int = 0
    unresolved: int = 0
    _events: list[dict[str, Any]] = field(default_factory=list)
    _sequence: int = 0

    def discover(self, item_id: str, path: str = "") -> None:
        self.discovered += 1
        self._log("discover", item_id, path)

    def capture(self, item_id: str, path: str = "", result: str = "ok") -> None:
        self.captured += 1
        self._log("capture", item_id, path, result=result)

    def expand(self, item_id: str, path: str = "", result: str = "ok") -> None:
        self.expanded += 1
        self._log("expand", item_id, path, result=result)

    def block(self, item_id: str, path: str = "", result: str = "blocked") -> None:
        self.blocked += 1
        self._log("blocked", item_id, path, result=result)

    def skip(self, item_id: str, path: str = "", result: str = "skipped") -> None:
        self.skipped += 1
        self._log("skip", item_id, path, result=result)

    def unresolve(self, item_id: str, path: str = "", result: str = "unresolved") -> None:
        """Record a discovered item that was captured but could NOT be fully
        resolved to a concrete value.

        Use this when the artifact itself was found and captured but a fact it
        depends on was left open — a ref that did not resolve to a name, a
        dynamic binding with no static source, a type that fell back to a raw
        GUID. It is distinct from ``block`` (a hard extraction failure) and
        ``skip`` (intentionally not extracted). An unresolved item keeps its
        ``capture`` but drags the domain ``status`` to ``partial`` so the
        consumer can see that something was left for it to derive.
        """
        self.unresolved += 1
        self._log("unresolved", item_id, path, result=result)

    def _log(self, action: str, item_id: str, path: str = "", result: str = "ok") -> None:
        self._sequence += 1
        self._events.append({
            "sequence": self._sequence,
            "action": action,
            "item_id": item_id,
            "path": path,
            "result": result,
        })

    @property
    def status(self) -> str:
        # A hard extraction failure is the most severe signal.
        if self.blocked > 0:
            return "blocked"
        # Captured-but-unresolved facts mean the traversal ran but left work
        # for the consumer to derive — that is not "complete".
        if self.unresolved > 0:
            return "partial"
        if self.discovered > 0 and self.captured == self.discovered:
            return "complete"
        if self.captured > 0:
            return "partial"
        if self.discovered == 0:
            return "complete"  # nothing to extract = fully extracted
        # Items were discovered but none captured. If some were intentionally
        # skipped, the domain is partially covered — the skips are recorded
        # and accounted for, not a silent gap. Only a discovery with no
        # capture/skip/block/unresolve at all is genuinely "unknown".
        if self.skipped > 0:
            return "partial"
        return "unknown"

    def to_coverage_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "status": self.status,
            "traversal_status": "exhausted" if self.status == "complete" else "partial",
            "discovered_count": self.discovered,
            "captured_count": self.captured,
            "expanded_count": self.expanded,
            "blocked_count": self.blocked,
            "skipped_count": self.skipped,
            "unresolved_count": self.unresolved,
        }

    def to_traversal_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "events": self._events,
        }


def write_json(path: Path, data: Any) -> None:
    """Write a JSON file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
