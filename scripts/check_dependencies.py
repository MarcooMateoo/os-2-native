"""Report modules that extracted modules depend on but the package did not include.

Reads ``<out>/extraction/<Module>/dependencies/graph.json`` for every module
written by ``os2native package`` and classifies each referenced module that is
not itself in the package:

- ``platform``: OutSystems built-ins and mobile plugins (expected to be absent)
- ``forge``:    known Forge components (absent, but need a native replacement)
- ``unknown``:  not in scripts/known-external.json; probably a module or
                extension that should have been in the .osp

Writes ``<out>/dependency-check.json`` and exits 1 when any dependency is
``unknown``. ``os2native package`` runs the same check automatically and logs
unknown dependencies to ``<out>/extraction-issues.log``; this script re-runs it
on its own (logic in ``src/os2native/dependencies.py``).

Usage::

    python scripts/check_dependencies.py out
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
    args = parser.parse_args(argv)

    report = write_check(args.out)

    print(
        f"Modules in package: {len(report['modules_in_package'])}, "
        f"extensions: {len(report['extensions_in_package'])}"
    )
    for dep in report["missing_dependencies"]:
        users = ", ".join(dep["used_by"])
        print(f"  {dep['kind']:8} {dep['module']:28} used by: {users}")
    for xif in report["extensions_not_extracted"]:
        print(f"  xif      {xif:28} (extension in package, could not be parsed)")
    print(
        f"{report['unknown_count']} unknown, {report['forge_count']} forge "
        f"-> {args.out / 'dependency-check.json'}"
    )
    return 1 if report["unknown_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
