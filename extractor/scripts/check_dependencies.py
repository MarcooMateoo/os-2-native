r"""Report modules that extracted modules depend on but the package did not include.

Reads ``<out>/extraction/<Module>/dependencies/graph.json`` for every module
of the last ``os2native package`` run and classifies each referenced module
that is not itself in the package:

- ``platform``:     OutSystems built-ins and mobile plugins (expected to be absent)
- ``forge``:        known Forge components (absent, but need a native replacement)
- ``accepted``:     recorded as not needed in the project's ``project.json``
- ``indirect``:     only used by modules outside the scope (e.g. by a dependency
                    module added to INPUTS); listed, not asked about
- ``unknown``:      used directly by an in-scope module; probably missing from the export

The scope is ``scope`` in the project's ``project.json``, or else every
application in the package manifest (loose files in INPUTS are dependencies).

Only ``unknown`` needs an answer from the user. Writes
``<out>/dependency-check.json`` and exits 1 when any dependency is
``unknown``. ``os2native package`` runs the same check automatically (logic in
``src/os2native/dependencies.py``, which also documents ``project.json``).

Usage::

    python scripts/check_dependencies.py ..\projects\<Project>\OUTPUT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from os2native.dependencies import check, classify, write_check  # noqa: E402,F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="Output directory of `os2native package`")
    parser.add_argument(
        "--project-config", type=Path, default=None,
        help="Project decisions (scope, accepted_missing). Default: project.json next to OUTPUT",
    )
    args = parser.parse_args(argv)

    report = write_check(args.out, project_config=args.project_config)

    print(
        f"Modules in package: {len(report['modules_in_package'])}, "
        f"extensions: {len(report['extensions_in_package'])}"
    )
    scope = report["scope"]
    print(f"Scope ({scope['source']}): {', '.join(scope['applications']) or '(modules)'} "
          f"-> {len(scope['modules'])} module(s)")
    if scope["unmatched"]:
        print(f"  not in this package: {', '.join(scope['unmatched'])}")
    if report["stale_extraction_folders"]:
        print(f"Ignored (left from an earlier run): {', '.join(report['stale_extraction_folders'])}")

    for dep in report["missing_dependencies"]:
        if dep["kind"] == "indirect":
            continue
        users = ", ".join(dep["in_scope_users"] or dep["used_by"])
        line = f"  {dep['kind']:8} {dep['module']:28} used by: {users}"
        if dep["kind"] == "accepted":
            line += f"  ({dep['reason']})"
        print(line)
        if dep["kind"] == "unknown":
            for element in dep.get("used_elements", []):
                print(f"             uses {element}")
    for xif in report["extensions_not_extracted"]:
        print(f"  xif      {xif:28} (extension in package, could not be parsed)")
    print(
        f"{report['unknown_count']} unknown, {report['forge_count']} forge, "
        f"{report['accepted_count']} accepted, {report['indirect_count']} indirect (not asked) "
        f"-> {args.out / 'dependency-check.json'}"
    )
    return 1 if report["unknown_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
