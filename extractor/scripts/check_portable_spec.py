"""Check a portable product specification before it is shared.

The portable specification (``OUTPUT/SPECS/<App>/portable-spec/``) is a
technology-neutral description of the app for any team or platform. It must
not reveal how the current app is built. This script checks every Markdown
file in it for:

- **platform vocabulary**: OutSystems terms (Aggregate, Server Action, Site
  Property, Static Entity, Web Block, ``NullIdentifier()``, ...);
- **implementation names**: module names of the extraction, module-style
  suffixes (``_CS``, ``_IS``, ``_CW``...), Forge components, plugins and
  libraries (``scripts/known-external.json``), theme names, and technology
  names (languages, frameworks, databases);
- **branding**: colour values (``#1a73e8``, ``rgb(...)``, ``hsl(...)``) and
  font names (common fonts plus those in the app's stylesheets);
- **sensitive values**: email addresses and secret-looking assignments;
- **coverage**: every feature, journey, business rule and open question ID of
  ``product-specification.md`` appears in the pack, and every action of the
  action catalogue is mapped in ``portable-spec-trace.md`` to a behaviour ID
  (``B1``...) that exists in ``behaviour.md``.

Terms that are allowed on purpose (e.g. an external system the new product
must keep integrating with) go in ``SPECS/<App>/portable-spec-allow.txt``,
one per line, ``term  # reason``. That file and the trace file sit next to
the pack, not in it, so they are not shared.

Usage::

    python scripts/check_portable_spec.py ..\\projects\\<Project>\\OUTPUT --app <App>

Exits with 1 when anything is found, 0 when the pack is clean.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PACK_DIR = "portable-spec"
TRACE_FILE = "portable-spec-trace.md"
ALLOW_FILE = "portable-spec-allow.txt"
REQUIRED_FILES = (
    "README.md", "PRD.md", "domain-model.md", "screens-and-navigation.md", "behaviour.md",
    "integrations.md", "configuration-and-jobs.md", "glossary.md",
)
KNOWN_EXTERNAL = Path(__file__).resolve().parent / "known-external.json"

# OutSystems vocabulary and built-in functions
PLATFORM_TERMS = [
    r"OutSystems", r"Service\s?Studio", r"Integration\s?Studio", r"Service\s?Center", r"LifeTime",
    r"TrueChange", r"eSpace", r"Aggregates?", r"Server\s+Actions?", r"Client\s+Actions?", r"Screen\s+Actions?",
    r"Service\s+Actions?", r"Data\s+Actions?", r"Site\s+Propert(?:y|ies)", r"Static\s+Entit(?:y|ies)",
    r"Web\s?Blocks?", r"Entity\s+Identifier", r"Advanced\s+SQL", r"Expand\s+Inline", r"Forge", r"BPT",
    r"Reactive\s+Web", r"Traditional\s+Web", r"MABS", r"Cordova", r"Light\s+Process(?:es)?",
    r"NullIdentifier", r"NullDate", r"NullTextIdentifier", r"CurrDateTime", r"CurrDate", r"CurrTime",
    r"GetUserId", r"Check\w+Role", r"OnInitialize", r"OnReady", r"OnRender", r"OnAfterFetch",
    r"\.oml\b", r"\.osp\b", r"\.oap\b", r"\.xif\b", r"Extension\s+\(Integration",
]
# Languages, frameworks and products that make a spec technology-specific
TECH_TERMS = [
    r"React(?:\s+Native)?", r"Angular", r"Vue(?:\.js)?", r"Next\.js", r"Svelte", r"jQuery", r"Bootstrap",
    r"Tailwind", r"shadcn", r"MUI", r"Material\s+UI", r"Expo", r"Flutter", r"Xamarin", r"Ionic",
    r"\.NET", r"ASP\.NET", r"C#", r"Java(?!Script)", r"Kotlin", r"Swift", r"Node(?:\.js)?", r"TypeScript",
    r"JavaScript", r"Python", r"Golang", r"Spring\s+Boot", r"Django", r"Fastify", r"NestJS", r"Express\.js",
    r"EF\s+Core", r"Entity\s+Framework", r"Dapper", r"Drizzle", r"Prisma", r"Hibernate",
    r"PostgreSQL", r"Postgres", r"SQL\s+Server", r"MySQL", r"Oracle", r"MongoDB", r"Redis",
    r"AWS", r"Azure", r"GCP", r"Kubernetes", r"Docker", r"Lambda", r"S3",
]
COMMON_FONTS = [
    "Roboto", "Open Sans", "Lato", "Montserrat", "Inter", "Arial", "Helvetica", "Poppins", "Nunito",
    "Source Sans Pro", "Source Sans", "Segoe UI", "Raleway", "Ubuntu", "Noto Sans", "Work Sans",
    "Rubik", "Mulish", "Barlow", "Oswald", "Merriweather", "Playfair Display", "Times New Roman",
    "Verdana", "Tahoma", "Calibri", "Georgia", "Font Awesome", "FontAwesome", "Material Icons",
]
GENERIC_FONTS = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "inherit", "initial",
                 "unset", "-apple-system", "blinkmacsystemfont", "ui-sans-serif", "ui-serif", "ui-monospace",
                 "emoji", "math", "fangsong"}
MODULE_SUFFIX = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*_(?:CS|IS|CW|BL|Lib|API|TH|Th|Ops|Timer|MUI|MCW|MCS|MDB|MTH|MPat|Ext|UTIL|Svc)\b")
COLOUR = re.compile(r"(?<![\w(&/])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"
                    r"|\b(?:rgba?|hsla?)\s*\(")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
SECRET = re.compile(r"(?i)\b(password|passwd|secret|api[_ -]?key|token|client[_ ]?secret)\b\s*[:=]\s*[`\"']?[^\s`\"']{6,}")
PLACEHOLDER_EMAIL = re.compile(r"@(example\.(com|org|net)|example)$", re.I)
SPEC_ID = re.compile(r"\b((?:F|A|J|BR|Q)\d+)\b")
B_ID = re.compile(r"\bB\d+\b")


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{where}: [{self.rule}] {self.text}"


def _camel_or_snake(name: str) -> bool:
    """A name that can't be an ordinary word: has an underscore, digit or inner capital."""
    return "_" in name or any(c.isdigit() for c in name) or any(c.isupper() for c in name[1:])


def _words(names: set[str]) -> re.Pattern[str] | None:
    names = {n for n in names if n and len(n) > 2}
    if not names:
        return None
    alternatives = sorted((re.escape(n) for n in names), key=len, reverse=True)
    return re.compile(r"(?<![\w.])(?:" + "|".join(alternatives) + r")(?![\w])")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def implementation_names(out: Path) -> dict[str, set[str]]:
    """Names from the extraction that must not appear in the pack."""
    modules, themes, external, fonts = set(), set(), set(), set()
    extraction = out / "extraction"
    for mod in sorted(p for p in extraction.iterdir() if p.is_dir()) if extraction.is_dir() else []:
        if _camel_or_snake(mod.name):
            modules.add(mod.name)
        for theme in _read_json(mod / "interfaces" / "themes.json") or []:
            name = theme.get("name") if isinstance(theme, dict) else None
            if name and _camel_or_snake(name):
                themes.add(name)
        tokens = (_read_json(mod / "evidence" / "design_tokens.json") or {}).get("tokens") or {}
        values = [v for k, v in tokens.items() if "font" in k.lower()]
        for css in (mod / "evidence" / "assets" / "css").glob("*.css") if (mod / "evidence").is_dir() else []:
            try:
                values += re.findall(r"font-family\s*:\s*([^;}{]+)", css.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        for value in values:
            for part in value.split(","):
                font = part.strip().strip("'\"").strip()
                if font and font.lower() not in GENERIC_FONTS and not font.startswith(("var(", "--")) \
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9 \-]{2,40}", font):
                    fonts.add(font)
    known = _read_json(KNOWN_EXTERNAL) or {}
    for key in ("platform_builtins", "forge_extensions", "mobile_plugins"):
        external |= {n for n in known.get(key, []) if isinstance(n, str) and _camel_or_snake(n)}
    for ext in (out / "extensions").iterdir() if (out / "extensions").is_dir() else []:
        if ext.is_dir() and _camel_or_snake(ext.name):
            external.add(ext.name)
    return {"module name": modules, "theme name": themes, "component or library": external, "font": fonts}


def load_allow(app_dir: Path) -> set[str]:
    path = app_dir / ALLOW_FILE
    if not path.is_file():
        return set()
    allowed = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip()
        if term:
            allowed.add(term.lower())
    return allowed


def _rules(out: Path) -> list[tuple[str, re.Pattern[str]]]:
    rules = [
        ("platform term", re.compile(r"(?<![\w-])(?:" + "|".join(PLATFORM_TERMS) + r")(?![\w])")),
        ("technology", re.compile(r"(?<![\w.#-])(?:" + "|".join(TECH_TERMS) + r")(?![\w])")),
        ("module-style name", MODULE_SUFFIX),
        ("colour", COLOUR),
        ("font", re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(f) for f in COMMON_FONTS) + r")(?![\w-])")),
    ]
    for label, names in implementation_names(out).items():
        pattern = _words(names)
        if pattern:
            rules.append((label, pattern))
    return rules


def scan_text(rel: str, text: str, rules, allowed: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    in_comment = False
    for no, line in enumerate(text.splitlines(), 1):
        # Template comments (<!-- ... -->) are instructions, not content
        if "<!--" in line:
            in_comment = "-->" not in line.split("<!--", 1)[1]
            line = line.split("<!--", 1)[0]
        elif in_comment:
            if "-->" in line:
                in_comment = False
                line = line.split("-->", 1)[1]
            else:
                continue
        for label, pattern in rules:
            for m in pattern.finditer(line):
                if m.group(0).strip().lower() in allowed:
                    continue
                findings.append(Finding(rel, no, label, m.group(0).strip()))
        for m in EMAIL.finditer(line):
            if not PLACEHOLDER_EMAIL.search(m.group(0)):
                findings.append(Finding(rel, no, "email address", m.group(0)))
        for m in SECRET.finditer(line):
            findings.append(Finding(rel, no, "secret value", m.group(0)[:40]))
    return findings


def _catalogue_actions(app_dir: Path) -> set[str]:
    """``Module › Action`` for every entry of the action catalogue (single file or per module)."""
    files = [app_dir / "action-catalogue.md"] + sorted((app_dir / "actions").glob("*.md"))
    actions: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        module = path.stem if path.parent.name == "actions" else ""
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^##\s+(?!#)(.+?)\s*$", line)
            if m and path.parent.name != "actions" and not m.group(1).lower().startswith(("summary", "<")):
                module = m.group(1).strip("` ")
                continue
            m = re.match(r"^###\s+[^:`]+:\s*`([^`]+)`", line)
            if m and module:
                actions.add(f"{module} › {m.group(1)}")
    return actions


def _trace(app_dir: Path) -> dict[str, str]:
    """Behaviour ID → ``Module › Action`` from the trace file."""
    path = app_dir / TRACE_FILE
    mapping: dict[str, str] = {}
    if not path.is_file():
        return mapping
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(B\d+)\s*\|\s*`?([^|`]+?)`?\s*\|", line)
        if m:
            source = re.sub(r"\s*(?:›|>|\.)\s*", " › ", m.group(2).strip(), count=1)
            mapping.setdefault(source, m.group(1))
    return mapping


def check(out: Path, app: str) -> list[Finding]:
    app_dir = out / "SPECS" / app
    pack = app_dir / PACK_DIR
    findings: list[Finding] = []
    if not pack.is_dir():
        return [Finding(f"SPECS/{app}/{PACK_DIR}", 0, "missing", "the portable specification folder does not exist")]
    for name in REQUIRED_FILES:
        if not (pack / name).is_file():
            findings.append(Finding(f"{PACK_DIR}/{name}", 0, "missing", "required file is missing"))

    rules = _rules(out)
    allowed = load_allow(app_dir)
    texts: dict[str, str] = {}
    for path in sorted(pack.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".md", ".markdown", ".txt"):
            rel = f"{PACK_DIR}/{path.relative_to(pack).as_posix()}"
            texts[rel] = path.read_text(encoding="utf-8")
            findings += scan_text(rel, texts[rel], rules, allowed)
        elif path.is_file():
            findings.append(Finding(f"{PACK_DIR}/{path.relative_to(pack).as_posix()}", 0, "not markdown",
                                    "only Markdown files belong in the pack"))
    all_text = "\n".join(texts.values())

    # Coverage of the product specification's IDs
    spec = app_dir / "product-specification.md"
    if spec.is_file():
        ids = set(SPEC_ID.findall(spec.read_text(encoding="utf-8")))
        present = set(SPEC_ID.findall(all_text))
        for missing in sorted(ids - present, key=lambda x: (re.sub(r"\d", "", x), int(re.sub(r"\D", "", x)))):
            findings.append(Finding("coverage", 0, "missing ID", f"{missing} from product-specification.md"))
    else:
        findings.append(Finding(f"SPECS/{app}/product-specification.md", 0, "missing",
                                "no product specification to check coverage against"))

    # Coverage of the action catalogue, through the trace file
    actions = _catalogue_actions(app_dir)
    trace = _trace(app_dir)
    if actions and not trace:
        findings.append(Finding(f"SPECS/{app}/{TRACE_FILE}", 0, "missing",
                                f"no trace file mapping the {len(actions)} catalogue actions to behaviour IDs"))
    else:
        for action in sorted(actions - set(trace)):
            findings.append(Finding("coverage", 0, "unmapped action", f"{action} has no behaviour ID in {TRACE_FILE}"))
        behaviour_ids = set(B_ID.findall(texts.get(f"{PACK_DIR}/behaviour.md", "")))
        for source, bid in sorted(trace.items(), key=lambda kv: int(kv[1][1:])):
            if bid not in behaviour_ids:
                findings.append(Finding("coverage", 0, "missing behaviour", f"{bid} ({source}) is not in behaviour.md"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a portable product specification before it is shared")
    parser.add_argument("pass_dir", type=Path, help="the project's OUTPUT folder")
    parser.add_argument("--app", help="app folder under SPECS (default: every app with a portable-spec folder)")
    args = parser.parse_args(argv)
    specs = args.pass_dir / "SPECS"
    apps = [args.app] if args.app else sorted(p.name for p in specs.iterdir()
                                               if (p / PACK_DIR).is_dir()) if specs.is_dir() else []
    if not apps:
        print(f"ERROR: no SPECS/<App>/{PACK_DIR}/ folder under {args.pass_dir}", file=sys.stderr)
        return 2
    total = 0
    for app in apps:
        findings = check(args.pass_dir, app)
        total += len(findings)
        print(f"{app}: {len(findings)} finding(s)")
        for f in findings:
            print(f"  {f}")
    print("clean: the pack can be shared" if total == 0 else f"{total} finding(s): fix them before sharing")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
