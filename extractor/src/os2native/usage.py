"""What the in-scope modules actually use from the other extracted modules.

A project's ``OUTPUT`` also holds modules that were added only to satisfy
dependencies (see ``dependencies.py``). The deliverables should describe the
apps being migrated, plus the parts of other modules those apps really use,
not every element of every dependency.

``trace_usage`` walks the extraction output element by element:

1. Every JSON file of an in-scope module (logic, interfaces, processes, data)
   is scanned for references into other modules.
2. Each referenced element (server/service/client action, entity, structure,
   block, REST integration) is looked up in its module's output, and its own
   definition is scanned the same way, including references to elements of
   its own module (``UserActions.X``, ``Entities.X``, local types, ...).

The result is, per out-of-scope module, the names of the elements reached.
In-scope modules are always included whole.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Element categories tracked in out-of-scope modules.
ACTION = "action"            # server action (logic/server-actions.json)
SERVICE = "service_action"   # logic/service-actions.json
CLIENT = "client_action"     # module-level client action (logic/client-actions.json)
ENTITY = "entity"            # data/entities.json
STRUCTURE = "structure"      # data/structures.json
BLOCK = "block"              # interfaces/blocks.json
EXTERNAL = "external_system" # integrations/external-systems.json (consumed REST/SOAP)
THEME = "theme"              # interfaces/themes.json (own themes; a base theme used by the app)

# References.<Module>.<Kind>.<Name>...
_CROSS_REF = re.compile(
    r"References\.(?P<module>[^.\"]+)\.(?P<kind>Reference[A-Za-z]+)\.(?P<rest>[^\"]+)"
)
_CROSS_KIND = {
    "ReferenceActions": ACTION,
    "ReferenceServiceAPIMethods": SERVICE,
    "ReferenceClientActions": CLIENT,
    "ReferenceEntities": ENTITY,
    "ReferenceStructures": STRUCTURE,
}
# Blocks: References.<Module>.Reference(NR)WebFlows.<Flow>.ReferenceWebBlocks.<Block>
_CROSS_BLOCK = re.compile(r"ReferenceWebBlocks\.(?P<name>[^.\"]+)")

# Local references inside one module's own output.
_LOCAL_REF = re.compile(
    r"^(?P<kind>UserActions|ServiceAPIMethods|ClientActionFlows|Entities|Structures|CustomClients)"
    r"\.(?P<name>[^.]+)"
)
_LOCAL_KIND = {
    "UserActions": ACTION,
    "ServiceAPIMethods": SERVICE,
    "ClientActionFlows": CLIENT,
    "Entities": ENTITY,
    "Structures": STRUCTURE,
    "CustomClients": EXTERNAL,
}
# Blocks (and screens) of the same module: (NR)WebFlows.<Flow>.NodesShownInESpaceTree.<Node>.
# Only names that are blocks resolve to a definition; screens are never reached.
_LOCAL_NODE = re.compile(r"^(?:NR)?WebFlows\.[^.]+\.NodesShownInESpaceTree\.(?P<name>[^.]+)")

_SCANNED_DOMAINS = ("logic", "interfaces", "processes", "data")

# Where each category's definitions live: (relative file, name field).
_DEFINITIONS = {
    ACTION: ("logic/server-actions.json", "name"),
    SERVICE: ("logic/service-actions.json", "name"),
    CLIENT: ("logic/client-actions.json", "name"),
    ENTITY: ("data/entities.json", "name"),
    STRUCTURE: ("data/structures.json", "name"),
    BLOCK: ("interfaces/blocks.json", "name"),
    EXTERNAL: ("integrations/external-systems.json", "name"),
    THEME: ("interfaces/themes.json", "name"),
}


@dataclass
class ModuleUsage:
    """Elements of one out-of-scope module that the scope reaches."""

    elements: dict[str, set[str]] = field(default_factory=dict)
    # (target module, element name) this module's reached elements reference.
    outbound: set[tuple[str, str]] = field(default_factory=set)

    def names(self, category: str) -> set[str]:
        return self.elements.get(category, set())

    def add(self, category: str, name: str) -> bool:
        bucket = self.elements.setdefault(category, set())
        if name in bucket:
            return False
        bucket.add(name)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": {k: sorted(v) for k, v in sorted(self.elements.items()) if v},
            "outbound": sorted(f"{m}.{n}" for m, n in self.outbound),
        }


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _walk(obj: Any):
    """Yield every dict and string in a JSON value."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            yield cur


def references_in(obj: Any) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    """References found in a JSON value.

    Returns ``(cross, local)``: cross-module references as
    ``(module, category, name)`` and same-module references as
    ``(category, name)``.
    """
    cross: set[tuple[str, str, str]] = set()
    local: set[tuple[str, str]] = set()
    for item in _walk(obj):
        if isinstance(item, str):
            for m in _CROSS_REF.finditer(item):
                module, kind, rest = m.group("module"), m.group("kind"), m.group("rest")
                if kind in _CROSS_KIND:
                    cross.add((module, _CROSS_KIND[kind], rest.split(".", 1)[0]))
                elif kind in ("ReferenceNRWebFlows", "ReferenceWebFlows"):
                    b = _CROSS_BLOCK.search(rest)
                    if b:
                        cross.add((module, BLOCK, b.group("name")))
            lm = _LOCAL_REF.match(item)
            if lm:
                local.add((_LOCAL_KIND[lm.group("kind")], lm.group("name")))
            nm = _LOCAL_NODE.match(item)
            if nm:
                local.add((BLOCK, nm.group("name")))
            continue
        # A theme from another module (interfaces/themes.json reference entry):
        # the app's base theme, e.g. a *_Th theme module.
        if item.get("is_reference") and str(item.get("tag", "")).endswith("ReferenceTheme"):
            if item.get("source_module") and item.get("name"):
                cross.add((item["source_module"], THEME, item["name"]))
            continue
        # Resolved type objects (see shared/type_resolver.py).
        kind = item.get("kind")
        ref_module = item.get("reference_module")
        if kind in ("reference_entity", "reference_structure") and ref_module:
            category = ENTITY if kind == "reference_entity" else STRUCTURE
            cross.add((ref_module, category, item.get("name", "")))
        elif kind == "entity_identifier":
            name = item.get("entity_name", "")
            if ref_module:
                cross.add((ref_module, ENTITY, name))
            elif name:
                local.add((ENTITY, name))
        elif kind in ("local_entity", "local_structure"):
            local.add((ENTITY if kind == "local_entity" else STRUCTURE, item.get("name", "")))
        elif kind == "foreign_key":
            target = item.get("to_entity", "")
            if ref_module:
                cross.add((ref_module, ENTITY, target))
            elif target:
                local.add((ENTITY, target))
    cross.discard(("", "", ""))
    return {c for c in cross if c[2]}, {l for l in local if l[1]}


class _Definitions:
    """Lazy per-module index: (category, name) -> list of JSON definitions."""

    def __init__(self, extraction: Path) -> None:
        self.extraction = extraction
        self._index: dict[str, dict[tuple[str, str], list[Any]]] = {}

    def get(self, module: str, category: str, name: str) -> list[Any]:
        if module not in self._index:
            index: dict[tuple[str, str], list[Any]] = {}
            for cat, (rel, key) in _DEFINITIONS.items():
                items = _load(self.extraction / module / rel)
                for item in items if isinstance(items, list) else []:
                    if not (isinstance(item, dict) and item.get(key)):
                        continue
                    if cat == THEME and item.get("is_reference"):
                        continue  # a theme this module uses, not one it defines
                    index.setdefault((cat, item[key]), []).append(item)
            # Relationships and static records belong to their entity.
            for rel, key in (("data/relationships.json", "from_entity"),
                             ("data/static-data.json", "entity_name")):
                items = _load(self.extraction / module / rel)
                for item in items if isinstance(items, list) else []:
                    if isinstance(item, dict) and item.get(key):
                        index.setdefault((ENTITY, item[key]), []).append(item)
            # Screen/block actions belong to their block.
            items = _load(self.extraction / module / "logic/client-actions.json")
            for item in items if isinstance(items, list) else []:
                source = item.get("source") if isinstance(item, dict) else None
                if isinstance(source, dict) and source.get("block_name"):
                    index.setdefault((BLOCK, source["block_name"]), []).append(item)
            self._index[module] = index
        return self._index[module].get((category, name), [])


def trace_usage(out: Path, scope: set[str], present: list[str]) -> dict[str, ModuleUsage]:
    """Elements of out-of-scope modules that the in-scope modules reach.

    ``present`` is the run's modules (``dependencies.run_modules``); references
    to modules not extracted (``(System)``, OutSystemsUI, Forge, ...) are
    ignored. Only modules with at least one reached element are returned.
    """
    extraction = out / "extraction"
    present_set = set(present)
    defs = _Definitions(extraction)
    usage: dict[str, ModuleUsage] = {}
    queue: deque[tuple[str, str, str]] = deque()

    def reach(module: str, category: str, name: str) -> None:
        # Only elements the module defines count (a node path can name a
        # screen; a name can be stale). Generators filter by these names.
        if module in scope or module not in present_set:
            return
        if not defs.get(module, category, name):
            return
        if usage.setdefault(module, ModuleUsage()).add(category, name):
            queue.append((module, category, name))

    for module in sorted(scope & present_set):
        for domain in _SCANNED_DOMAINS:
            for path in sorted((extraction / module / domain).glob("*.json")):
                cross, _ = references_in(_load(path))
                for target, category, name in cross:
                    reach(target, category, name)

    while queue:
        module, category, name = queue.popleft()
        for definition in defs.get(module, category, name):
            cross, local = references_in(definition)
            for target, cat, n in cross:
                if target != module:
                    usage[module].outbound.add((target, n))
                reach(target, cat, n)
            for cat, n in local:
                reach(module, cat, n)

    return {m: u for m, u in usage.items() if u.elements}


def deliverable_usage(out: Path) -> tuple[set[str], str, dict[str, ModuleUsage]]:
    """Scope of a project's ``OUTPUT`` and what it uses from other modules."""
    from os2native.dependencies import project_scope, run_modules

    present = run_modules(out)
    scope, source, _ = project_scope(out, present=present)
    return scope, source, trace_usage(out, scope, present)
