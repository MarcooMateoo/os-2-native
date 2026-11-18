"""Show, fill in and record a project's target stack in ``project.json``.

The stack has every layer of ``TECH_STACK.md`` (see ``target_stack.CATALOGUE``).
A ``null`` value is an open decision.

    # Every layer, its value, where it came from, and the open decisions
    python scripts/project_stack.py show ..\\projects\\MyProject

    # Write every layer into project.json: TECH_STACK.md defaults for the ones
    # not set yet, null for open decisions. Values already set are kept.
    python scripts/project_stack.py init ..\\projects\\MyProject

    # Record decisions (true/false/null are JSON; anything else is a string)
    python scripts/project_stack.py set ..\\projects\\MyProject web.ui=shadcn database.case_insensitive_text=false

The path can be the project folder, its ``OUTPUT`` folder or ``project.json``.
``show`` exits with 1 while decisions are open, so it can serve as a check.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from target_stack import CATALOGUE, OPEN, PROJECT_CONFIG, normalize, open_decisions, resolve, validate

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def config_path(path: Path) -> Path:
    path = path.resolve()
    if path.is_file() or path.suffix == ".json":
        return path
    if path.name.upper() == "OUTPUT":
        return path.parent / PROJECT_CONFIG
    return path / PROJECT_CONFIG


def read_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def full_stack(configured: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Every catalogue layer, in catalogue order, with values kept from ``configured``."""
    values, _ = resolve(configured)
    return {group: dict(items) for group, items in values.items()}


def parse_value(text: str) -> Any:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return value if isinstance(value, (bool, list)) or value is None else text


def set_values(configured: dict[str, Any] | None, assignments: list[str]) -> dict[str, dict[str, Any]]:
    stack = normalize(configured)
    for item in assignments:
        name, sep, raw = item.partition("=")
        group, dot, key = name.strip().partition(".")
        if not sep or not dot:
            raise ValueError(f"expected <group>.<key>=<value>, got {item!r}")
        if group not in CATALOGUE or key not in {layer.key for layer in CATALOGUE[group][1]}:
            known = ", ".join(f"{g}.{layer.key}" for g, (_l, layers) in CATALOGUE.items() for layer in layers)
            raise ValueError(f"unknown layer {name!r}; known: {known}")
        stack.setdefault(group, {})[key] = parse_value(raw.strip())
    return stack


def show(configured: dict[str, Any] | None, path: Path) -> int:
    values, sources = resolve(configured)
    print(f"Stack of {path}")
    for group, (glabel, layers) in CATALOGUE.items():
        print(f"\n{glabel} ({group})")
        for layer in layers:
            value = values[group][layer.key]
            source = sources[group][layer.key]
            shown = "OPEN" if value is OPEN else json.dumps(value, ensure_ascii=False)
            options = ""
            if value is OPEN and layer.options:
                options = "  options: " + ", ".join(json.dumps(k) for k in layer.options)
            print(f"  {group + '.' + layer.key:<34} {shown:<28} [{source}]{options}")
    errors = validate(values)
    for error in errors:
        print(f"\nERROR: {error}")
    pending = open_decisions(values)
    print(f"\n{len(pending)} open decision(s): {', '.join(pending) or 'none'}")
    return 1 if pending or errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show, fill in and record a project's target stack")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("show", "print every layer and the open decisions"),
                            ("init", "write every layer into project.json (defaults; null when open)"),
                            ("set", "record decisions: <group>.<key>=<value> ...")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("project", type=Path, help="project folder, its OUTPUT folder, or project.json")
        if name == "set":
            p.add_argument("assignments", nargs="+")
    args = parser.parse_args(argv)

    path = config_path(args.project)
    config = read_config(path)
    if args.command == "show":
        return show(config.get("stack"), path)
    if args.command == "set":
        try:
            config["stack"] = full_stack(set_values(config.get("stack"), args.assignments))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        if not path.parent.is_dir():
            print(f"ERROR: {path.parent} does not exist", file=sys.stderr)
            return 2
        config["stack"] = full_stack(config.get("stack"))
    errors = validate(resolve(config["stack"])[0])
    if errors:
        print("ERROR: " + "; ".join(errors), file=sys.stderr)
        return 2
    write_config(path, config)
    pending = open_decisions(config["stack"])
    print(f"wrote {path}")
    print(f"{len(pending)} open decision(s): {', '.join(pending) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
