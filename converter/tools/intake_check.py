"""Intake gate: check that an os2native OUTPUT folder is fit to convert from.

The converter only builds from os2native output that is complete, current and
unambiguous. This script checks a project's OUTPUT folder against
``contracts/os2native-intake-contract.md`` and prints a Markdown report.

Usage (Python 3.12+, standard library only; the extractor's venv works)::

    python tools/intake_check.py ../projects/<Project>/OUTPUT [--osp <file.osp>] [--out report.md]

Exit codes: 0 = no blockers, 1 = at least one BLOCKER, 2 = bad arguments.

Severity:
    BLOCKER  the converter must not build from this output until it is fixed
    WARNING  usable, but a person must accept or fix it
    INFO     facts the converter needs (e.g. the application -> module map)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Files every extraction/<Module>/ package must contain for the converter to use it.
REQUIRED_MODULE_FILES = [
    "package.json",
    "summary.md",
    "interfaces/screens.json",
    "interfaces/blocks.json",
    "interfaces/screen-dataflow.json",
    "logic/server-actions.json",
    "logic/client-actions.json",
    "logic/service-actions.json",
    "logic/advanced-sql.json",
    "data/entities.json",
    "data/static-data.json",
    "data/roles-and-permissions.json",
    "data/site-properties.json",
    "processes/timers.json",
    "integrations/consumed-apis.json",
    "integrations/exposed-apis.json",
    "dependencies/graph.json",
    "dependencies/readiness.json",
]

# Deliverables the converter consumes, with the folder each lives in.
DELIVERABLES = {
    "db-schema": ["schema.sql", "seed-data.sql"],
    "api-catalog": ["catalog.json"],
    "specs": [],
}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    items: list[str] = field(default_factory=list)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def check_inventory(out: Path, findings: list[Finding]) -> set[str]:
    inv_path = out / "inventory.json"
    if not inv_path.is_file():
        findings.append(Finding("BLOCKER", "inventory", "inventory.json is missing: run `os2native package` first."))
        return set()
    inv = _load_json(inv_path)
    modules = {m["module"] for m in inv.get("modules", [])}
    issues = inv.get("issues", {})
    if inv.get("failed_modules"):
        findings.append(Finding("BLOCKER", "inventory", "Modules failed to extract.", sorted(inv["failed_modules"])))
    if inv.get("extensions_not_extracted"):
        findings.append(Finding("BLOCKER", "inventory", "Extensions failed to parse.", list(inv["extensions_not_extracted"])))
    if issues.get("errors"):
        findings.append(Finding("BLOCKER", "issues-log", f"{issues['errors']} error(s) in extraction-issues.log."))
    if issues.get("warnings"):
        findings.append(Finding("WARNING", "issues-log",
                                f"{issues['warnings']} warning(s) in extraction-issues.log: each needs an owner or an accepted disposition."))
    return modules


def check_stale_xml(out: Path, findings: list[Finding]) -> None:
    """Stage 1 reuses an existing _xml/<name>.xml. If the .oml is newer, the XML (and
    everything extracted from it) describes an older version of the module."""
    src, xml = out / "_source", out / "_xml"
    if not src.is_dir():
        return
    stale, missing = [], []
    for oml in sorted(src.glob("*.oml")):
        x = xml / (oml.stem + ".xml")
        stamp = x.with_name(x.name + ".sha1")  # written by os2native once it hashes its inputs
        if not x.is_file():
            missing.append(oml.name)
        elif stamp.is_file():
            if stamp.read_text(encoding="utf-8").strip() != hashlib.sha1(oml.read_bytes()).hexdigest():
                stale.append(f"{x.name} was decoded from a different {oml.name} (hash mismatch)")
        elif x.stat().st_mtime + 1 < oml.stat().st_mtime:
            stale.append(f"{x.name} ({_mtime(x):%Y-%m-%d %H:%M}) older than {oml.name} ({_mtime(oml):%Y-%m-%d %H:%M})")
    if stale:
        findings.append(Finding(
            "BLOCKER", "stale-xml",
            "Decoded XML is older than its .oml, so the extraction describes an older version of these modules. "
            "Delete OUTPUT/_xml and re-run `os2native package`.", stale))
    if missing:
        findings.append(Finding("BLOCKER", "stale-xml", "No decoded XML for these inputs.", missing))


def check_orphans(out: Path, modules: set[str], findings: list[Finding]) -> None:
    ext = out / "extraction"
    if ext.is_dir() and modules:
        orphans = sorted(p.name for p in ext.iterdir() if p.is_dir() and p.name not in modules)
        if orphans:
            findings.append(Finding(
                "BLOCKER", "orphan-modules",
                "extraction/ holds modules that are not in the current inventory (left over from an earlier run). "
                "Generators and the dependency check read them as if they were part of the app.", orphans))
    nested = sorted(p.name for p in out.iterdir()
                    if p.is_dir() and p.name not in {"SPECS", "deliverables"} and (p / "inventory.json").is_file())
    if nested:
        findings.append(Finding(
            "WARNING", "nested-output",
            "A second, older OUTPUT tree is nested inside OUTPUT. Two versions of the truth: archive or delete it, "
            "and point SPECS at the top-level output only.", nested))


def check_module_packages(out: Path, modules: set[str], findings: list[Finding]) -> None:
    ext = out / "extraction"
    missing, no_app = [], []
    for m in sorted(modules):
        root = ext / m
        gaps = [f for f in REQUIRED_MODULE_FILES if not (root / f).is_file()]
        if gaps:
            missing.append(f"{m}: {', '.join(gaps)}")
        pkg = root / "package.json"
        if pkg.is_file() and not _load_json(pkg).get("application_name"):
            no_app.append(m)
    if missing:
        findings.append(Finding("BLOCKER", "module-files", "Module packages are missing required files.", missing))
    if no_app:
        findings.append(Finding(
            "WARNING", "app-mapping",
            f"{len(no_app)} module package(s) have an empty application_name/module kind. The converter groups code by "
            "OutSystems application, so it needs the application -> module map (see the INFO section; os2native should "
            "read it from the .osp Manifest.xml).", no_app if len(no_app) <= 5 else no_app[:5] + [f"... and {len(no_app) - 5} more"]))


def check_dependencies(out: Path, findings: list[Finding]) -> None:
    dc = out / "dependency-check.json"
    if not dc.is_file():
        findings.append(Finding("BLOCKER", "dependencies", "dependency-check.json is missing: run scripts/check_dependencies.py."))
        return
    data = _load_json(dc)
    unknown = [f"{d['module']} (used by {', '.join(d.get('used_by', []))})"
               for d in data.get("missing_dependencies", []) if d.get("kind") == "unknown"]
    if unknown:
        findings.append(Finding(
            "BLOCKER", "dependencies",
            "Unknown dependencies: get the .oml/.xif, or have the user confirm them as platform/Forge.", unknown))
    inv = out / "inventory.json"
    if inv.is_file() and dc.stat().st_mtime + 1 < inv.stat().st_mtime:
        findings.append(Finding("WARNING", "dependencies", "dependency-check.json is older than inventory.json: re-run check_dependencies.py."))


def check_deliverables(out: Path, modules: set[str], findings: list[Finding]) -> None:
    root = out / "deliverables"
    inv = out / "inventory.json"
    if not root.is_dir():
        findings.append(Finding("BLOCKER", "deliverables", "deliverables/ is missing: run scripts/generate_*.py."))
        return
    ref = inv.stat().st_mtime if inv.is_file() else 0
    stale, absent = [], []
    for folder, files in DELIVERABLES.items():
        d = root / folder
        if not d.is_dir():
            absent.append(folder)
            continue
        for f in files:
            p = d / f
            if not p.is_file():
                absent.append(f"{folder}/{f}")
            elif p.stat().st_mtime + 1 < ref:
                stale.append(f"{folder}/{f} ({_mtime(p):%Y-%m-%d %H:%M})")
    specs = root / "specs"
    if specs.is_dir() and modules:
        spec_mods = {p.name for p in specs.iterdir() if p.is_dir()}
        extra, lacking = sorted(spec_mods - modules), sorted(modules - spec_mods)
        if extra:
            stale.append("specs/ for modules not in the inventory: " + ", ".join(extra))
        if lacking:
            stale.append("specs/ missing for: " + ", ".join(lacking))
    if absent:
        findings.append(Finding("BLOCKER", "deliverables", "Deliverables are missing.", absent))
    if stale:
        findings.append(Finding("BLOCKER", "deliverables",
                                "Deliverables were generated from a different extraction than the current inventory: re-run the generators.", stale))


def check_specs(out: Path, findings: list[Finding]) -> None:
    specs = out / "SPECS"
    inv = out / "inventory.json"
    apps = [p for p in specs.iterdir() if p.is_dir()] if specs.is_dir() else []
    if not apps:
        findings.append(Finding("BLOCKER", "product-spec", "No SPECS/<App>/product-specification.md: the converter needs a reviewed product spec per app."))
        return
    for app in apps:
        spec = app / "product-specification.md"
        if not spec.is_file():
            findings.append(Finding("BLOCKER", "product-spec", f"SPECS/{app.name}/product-specification.md is missing."))
            continue
        if inv.is_file() and spec.stat().st_mtime < inv.stat().st_mtime:
            findings.append(Finding(
                "WARNING", "product-spec",
                f"SPECS/{app.name}/product-specification.md ({_mtime(spec):%Y-%m-%d %H:%M}) predates the current extraction "
                f"({_mtime(inv):%Y-%m-%d %H:%M}). Re-verify its counts, module list and open questions against inventory.json."))
        text = spec.read_text(encoding="utf-8")
        if "## 13. Open questions" in text or re.search(r"^## .*Open questions", text, re.M):
            n = len(re.findall(r"^\| Q\d+ ", text, re.M))
            if n:
                findings.append(Finding("WARNING", "product-spec",
                                        f"SPECS/{app.name}: {n} open question(s) need answers or accepted defaults before the stories that depend on them are built."))


def app_module_map(osp: Path | None, findings: list[Finding]) -> None:
    if not osp or not osp.is_file():
        return
    try:
        with zipfile.ZipFile(osp) as z:
            root = ET.fromstring(z.read("Manifest.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        findings.append(Finding("WARNING", "app-mapping", f"Could not read Manifest.xml from {osp.name}: {exc}"))
        return
    rows = []
    for a in root.iter("Application"):
        mods = ", ".join(f"{m.get('Name')} [{m.get('ESpaceType') or m.get('Kind')}]" for m in a.iter("Module"))
        front = a.get("FrontOfficeEspaceName") or "-"
        rows.append(f"{a.get('Name')} (front-end: {front}): {mods}")
    findings.append(Finding("INFO", "app-mapping", f"Application -> module map from {osp.name}/Manifest.xml", rows))


def render(out: Path, findings: list[Finding]) -> str:
    order = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}
    findings.sort(key=lambda f: order[f.severity])
    counts = {s: sum(f.severity == s for f in findings) for s in order}
    verdict = "NOT READY: fix the blockers before converting" if counts["BLOCKER"] else "READY (review warnings)"
    lines = [f"# Intake check: `{out}`", "",
             f"- Checked: {datetime.now():%Y-%m-%d %H:%M}",
             f"- Verdict: **{verdict}**",
             f"- Blockers: {counts['BLOCKER']}, warnings: {counts['WARNING']}", ""]
    for f in findings:
        lines.append(f"## {f.severity} · {f.check}")
        lines.append("")
        lines.append(f.message)
        if f.items:
            lines.append("")
            lines.extend(f"- {i}" for i in f.items)
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("output", type=Path, help="projects/<Project>/OUTPUT folder")
    ap.add_argument("--osp", type=Path, help=".osp/.oap to read the application -> module map from")
    ap.add_argument("--out", type=Path, help="write the report here instead of stdout")
    args = ap.parse_args(argv)
    out: Path = args.output
    if not out.is_dir():
        print(f"not a folder: {out}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    modules = check_inventory(out, findings)
    check_stale_xml(out, findings)
    check_orphans(out, modules, findings)
    check_module_packages(out, modules, findings)
    check_dependencies(out, findings)
    check_deliverables(out, modules, findings)
    check_specs(out, findings)
    app_module_map(args.osp, findings)

    report = render(out, findings)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    return 1 if any(f.severity == "BLOCKER" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
