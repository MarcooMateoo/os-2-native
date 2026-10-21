"""CLI entry point for the os2native extraction pipeline.

Commands:
  os2native extract --oml Module.oml --output out/      one module (OML or XML)
  os2native package Solution.osp --output out/          whole .osp / .oap package
  os2native report out/                                 rebuild inventory + details report
  os2native closure ...                                 dependency closure of a module set
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from os2native.pipeline import WORKER_ORDER


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="os2native",
        description="Extract OutSystems modules into specification packages for native rebuilds.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="Extract a single module")
    source = extract.add_mutually_exclusive_group(required=True)
    source.add_argument("--oml", type=str, help="Path to a .oml module file")
    source.add_argument("--xml", type=str, help="Path to an already-converted module XML")
    extract.add_argument("--output", type=str, default="OUTPUT", help="Output directory")
    extract.add_argument("--domain", choices=WORKER_ORDER, help="Run only one domain worker")
    extract.add_argument("--xml-only", action="store_true", help="Stop after OML -> XML")

    package = sub.add_parser(
        "package",
        help="Extract every module in a .osp / .oap package, plus any extra .oml / .xif files",
    )
    package.add_argument(
        "inputs", nargs="+", type=str,
        help="The .osp/.oap package, followed by any extra .oml modules or .xif extensions "
             "it depends on (e.g. Forge components not included in the package)",
    )
    package.add_argument("--output", type=str, default="OUTPUT", help="Output directory")
    package.add_argument("--xml-only", action="store_true", help="Stop after OML -> XML")

    report = sub.add_parser(
        "report", help="Rebuild inventory.json + inventory-details.csv/.md from a package output"
    )
    report.add_argument("output", type=str, help="Output directory of `os2native package`")

    sub.add_parser("closure", help="Resolve a module dependency closure (see closure --help)", add_help=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # closure has its own argument parser; forward the remaining args untouched.
    if argv and argv[0] == "closure":
        from os2native.closure import main as closure_main

        return closure_main(argv[1:])

    args = build_parser().parse_args(argv)

    if args.command == "extract":
        if args.xml_only and not args.oml:
            print("Error: --xml-only requires --oml", file=sys.stderr)
            return 2
        from os2native.pipeline import run_pipeline
        from os2native.run_log import issue_log, log

        source = args.oml or args.xml
        with issue_log(Path(args.output), f"extract {Path(source).name}", fresh=False) as issues:
            try:
                rc = run_pipeline(args)
            except Exception as exc:
                log.error("[%s] extraction failed: %s", Path(source).stem, exc, exc_info=True)
                rc = 1
        return 1 if rc or issues.errors else 0

    if args.command == "package":
        from os2native.package import run_package

        return run_package([Path(p) for p in args.inputs], Path(args.output), xml_only=args.xml_only)

    if args.command == "report":
        from os2native.package import run_report

        return run_report(Path(args.output))

    return 2


if __name__ == "__main__":
    sys.exit(main())
