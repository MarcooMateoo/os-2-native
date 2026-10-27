# os2native

Extracts OutSystems solution packages (`.osp`), application packages (`.oap`)
and modules (`.oml`) into structured JSON specification packages, so the
application can be rebuilt on a native stack (e.g. Flutter + Go/Node +
PostgreSQL).

os2native does **not** generate application code. It produces a complete,
machine-readable description of the app (screens, logic, data model, APIs,
roles, timers, site properties, dependencies) plus generated deliverables
(PostgreSQL schema, API catalog, per-module specs) that a team or an AI agent
uses to rebuild it.

Built from the reusable core of `catalysts/catalyst-extractor`, with the
Giga/Torpedo-specific layers (dual-output pipeline, Iceman projection,
journeys, Storybook) left out.

## How it works

```
 INPUTS\<App>\                         OUTPUT\<App>\
 ┌──────────────────┐   unpack    ┌──────────────────────────────────────────┐
 │ App.osp / .oap   │ ──────────▶ │ _source\    .oml / .xif files            │
 │ extra .oml/.xif  │             │                                          │
 └──────────────────┘   Stage 1   │ _xml\       one XML per module           │
                        oml.dll ▶ │                                          │
                        Stage 2   │ extraction\<Module>\  JSON spec package  │
                        workers ▶ │                                          │
                        Stage 3   │ summary.md, readiness, integrations      │
                   orchestrator ▶ │                                          │
                                  │ inventory.json, extraction-issues.log    │
                     scripts\  ▶  │ deliverables\  schema, API catalog, specs│
                                  └──────────────────────────────────────────┘
```

1. **Unpack**: `package` opens the `.osp`/`.oap` (a zip) and collects every
   `.oml` module and `.xif` extension, plus any extra files you pass.
2. **Stage 1, decode**: `bin/oml-utilities/oml.dll` (a .NET 10 tool) converts
   each binary `.oml` into XML.
3. **Stage 2, workers**: five workers read the XML, one per domain:
   - `assets`: images, themes, CSS, resources
   - `interface`: UI flows, screens, blocks, widgets, screen data flow
   - `logic`: client, server and service actions, Advanced SQL
   - `process`: timers and BPT processes
   - `data`: entities, static data, roles, site properties, exceptions
4. **Stage 3, orchestrator**: resolves cross-module dependencies and
   integrations (REST/SOAP consumed and exposed), scores rebuild readiness and
   writes each module's `summary.md`.
5. **Report**: counts every item per module into `inventory.json` and lists
   each one with its Service Studio location in `inventory-details.csv`/`.md`,
   so the numbers can be checked against Service Studio.
6. **Extensions**: `.xif` files are parsed into their actions, structures and
   embedded C#/Java source code.
7. **Deliverables** (separate scripts): PostgreSQL DDL, API catalog with
   OpenAPI stubs, and backend/frontend specs per module.

Every error and warning from a run goes to `OUTPUT\<App>\extraction-issues.log`.

## Folders

| Folder | Purpose | In git? |
|---|---|---|
| `bin/` | The OML → XML decoder in `bin/oml-utilities/`: `oml.dll`, its 3 supporting DLLs and 2 config files. Needs the .NET 10 runtime. Binary, never edit. | Yes |
| `docs/` | Reference documentation (see [Documentation](#documentation) below). | Yes |
| `INPUTS/` | **Your working folder for exports from OutSystems.** One subfolder per app holding the `.osp`/`.oap` plus any extra `.oml`/`.xif` dependencies (and optionally screenshots). os2native only reads it, never changes it. | No: create it yourself |
| `OUTPUT/` | **Default destination of every command.** One subfolder per app. Rebuilt on each run, so don't hand-edit generated files. `OUTPUT/SPECS/<App>/` is the exception: hand-reviewed documents that no command writes or deletes. | No: generated |
| `scripts/` | Tools run on an `OUTPUT\<App>` folder after extraction: deliverable generators, the missing-dependency check, optional ServiceCenter tools, and `known-external.json` (known platform modules, mobile plugins, Forge components). | Yes |
| `src/` | The `os2native` Python package: CLI, pipeline, workers, orchestrator, inventory report. Standard library only. | Yes |
| `tests/` | pytest suite. Uses small hand-built XML snippets, never customer data. | Yes |
| `.venv/` | Python virtual environment. Disposable: recreate with the setup steps. | No |

`INPUTS/` and `OUTPUT/` are git-ignored because they contain customer
packages and data. A fresh clone doesn't have them: create
`INPUTS\<App>\` before your first run.

### `src/os2native/`

| File / folder | What it does |
|---|---|
| `cli.py` | Commands: `package`, `extract`, `report`, `closure` |
| `package.py` | Unpacks `.osp`/`.oap`, runs every module, writes `inventory.json` |
| `pipeline.py` | Stage 1 (OML → XML), Stage 2 (workers), Stage 3 (orchestrator); finds `dotnet` |
| `xml_loader.py` | XML loading and fragment index |
| `workers/` | `assets`, `interface`, `logic`, `process`, `data`: one worker per domain |
| `orchestrator.py` | Dependencies, stubs, integrations, readiness, `summary.md` |
| `extension.py` | Parses `.xif` (zlib-compressed XML): actions, structures, embedded source files |
| `inventory_report.py` | One row per counted item with its Service Studio location; the source of all inventory counts |
| `dependencies.py` | Missing-dependency check |
| `closure.py` | Module dependency closure |
| `run_log.py` | `extraction-issues.log`: run header/footer, errors and warnings |
| `shared/` | Key index, type and expression resolvers, folders, coverage, optional ServiceCenter client |

### `scripts/`

| Script | What it does |
|---|---|
| `check_dependencies.py` | Compares each module's dependencies with what's in the package; writes `dependency-check.json`, exits 1 if anything is unknown |
| `generate_db_schema.py` | PostgreSQL DDL, seed data, ER diagram, type mapping → `deliverables\db-schema\` |
| `generate_api_catalog.py` | API catalog, coverage matrix, OpenAPI stubs → `deliverables\api-catalog\` |
| `generate_specs.py` | Backend/frontend specs per module → `deliverables\specs\` |
| `generate_assessment.py` | Migration assessment (thinner without Catalyst scoring files) |
| `download_omls.py`, `scrape_site_properties.py` | Optional: fetch modules and runtime site property values from ServiceCenter |
| `known-external.json` | Platform modules, mobile plugins and Forge components treated as expected external dependencies |

## Prerequisites

- **Windows** with PowerShell or cmd
- **Python 3.12+**: the pipeline uses only the standard library
- **.NET 10 runtime**: `bin/oml-utilities/oml.dll` targets `net10.0`
  (`dotnet --list-runtimes` must show `Microsoft.NETCore.App 10.x`).
  A per-user install at `%LOCALAPPDATA%\Microsoft\dotnet` (from
  `dotnet-install.ps1`) is picked up automatically without touching PATH, so
  other projects on older .NET versions are unaffected.
  To use a different host, set `OS2NATIVE_DOTNET` to its `dotnet.exe` path.

## Setup

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Quick start

The full step-by-step guide is [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md). In short:

```powershell
# 1. Extract the package (add extra .oml/.xif dependencies after it)
.venv\Scripts\os2native package INPUTS\MyApp\MyApp.osp --output OUTPUT\MyApp

# 2. Find missing modules (exit code 1 if any are unknown)
.venv\Scripts\python scripts\check_dependencies.py OUTPUT\MyApp

# 3. Check OUTPUT\MyApp\inventory.json against Service Studio

# 4. Generate deliverables
.venv\Scripts\python scripts\generate_db_schema.py OUTPUT\MyApp
.venv\Scripts\python scripts\generate_api_catalog.py OUTPUT\MyApp
.venv\Scripts\python scripts\generate_specs.py OUTPUT\MyApp
```

After step 1, read `OUTPUT\MyApp\extraction-issues.log`:

- **ERROR**: a module or extension failed, or an input was missing. Those
  items are missing from the output.
- **WARNING**: extraction finished but something is incomplete (unresolved
  items, unknown dependencies, decoder messages).

Step 2 classifies each dependency that isn't in the package:

| Kind | Meaning | What to do |
|---|---|---|
| `platform` | OutSystems built-in or mobile plugin (`(System)`, `OutSystemsUI`, `Users`, ...) | Expected. Map to native equivalents at rebuild time. |
| `forge` | Known Forge component | Needs a native replacement library. |
| `unknown` | Not in the package and not a known component | Get the missing `.oml`/`.xif` from OutSystems, add it to the `package` command and re-run step 1. |

Don't move on to rebuilding until steps 2 and 3 are clean.

## Other commands

```powershell
# Single module
os2native extract --oml MyModule.oml --output OUTPUT\MyModule
os2native extract --xml OUTPUT\MyApp\_xml\MyModule.xml --output OUTPUT\MyApp --domain data

# Rebuild inventory.json + inventory-details.csv/.md without re-extracting
os2native report OUTPUT\MyApp

# Dependency closure of a set of module XMLs
os2native closure --help
```

## Output layout

```
OUTPUT\<App>\
  _source\                 unpacked .oml / .xif files
  _xml\                    decoded XML per module
  extraction\<Module>\     spec package per module (see below)
  extensions\<Name>\       extension.json + source\ (embedded C#/Java) per .xif
  deliverables\            db-schema\, api-catalog\, specs\ (from scripts\generate_*.py)
  inventory.json           counts per module + totals (screens, blocks, roles,
                           entities, actions, APIs, timers, site properties, ...)
  inventory-details.csv    one row per counted item: module, category, name,
  inventory-details.md     own/consumed, Service Studio location, details, source file
  extraction-issues.log    every error and warning from the run
  dependency-check.json    dependencies not in the package, classified
```

Each `extraction\<Module>\` folder contains:

| Item | Contents |
|---|---|
| `package.json`, `summary.md`, `coverage-report.md` | Module metadata, human-readable summary, extraction coverage |
| `interfaces\` | Screens, blocks, screen data flow, themes, assets, wireframes |
| `logic\` | Client, server and service actions, contracts, Advanced SQL |
| `processes\` | Timers, BPT processes and transitions |
| `data\` | Entities, relationships, static data, roles, site properties, exceptions |
| `integrations\` | Consumed/exposed REST and SOAP, public actions, external systems |
| `dependencies\` | Dependency graph, references, readiness |
| `scaffold\` | Requirements for the native rebuild: shared components, backend adapters, persistence |
| `evidence\` | Raw evidence: assets (images, CSS), flow trees, metadata |

The full format is in
[docs/module-output-package-contract.md](docs/module-output-package-contract.md).

## Documentation

| Document | For |
|---|---|
| [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md) | Step-by-step extraction of an app (start here) |
| [docs/outsystems-context.md](docs/outsystems-context.md) | OutSystems concepts and how they map to native ones |
| [docs/module-output-package-contract.md](docs/module-output-package-contract.md) | Output format of each module package |
| [docs/oml-xml-knowledge-base.md](docs/oml-xml-knowledge-base.md) | XML tag reference, needed when changing a worker |
| [AGENTS.md](AGENTS.md) | Rules for AI coding agents (and a good checklist for people changing the code) |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

For end-to-end tests, convert a module of your own and point
`OS2NATIVE_GOLDEN_XML` at it:

```powershell
os2native extract --oml MyModule.oml --output tmp --xml-only
$env:OS2NATIVE_GOLDEN_XML = "tmp\MyModule.xml"
.venv\Scripts\python -m pytest
```

## Contributing

- Confirm any change against the real XML in `OUTPUT\<App>\_xml\` and
  [docs/oml-xml-knowledge-base.md](docs/oml-xml-knowledge-base.md).
- Add a test with every fix or feature, using hand-built XML snippets. Never
  commit customer XML or output.
- Keep `src/` generic and standard-library only. App-specific knowledge goes
  in config such as `scripts/known-external.json`.
- Pass `encoding="utf-8"` on every file read and write.
- Report problems through `os2native.run_log.log.warning/error(...)`, not `print`.

See [AGENTS.md](AGENTS.md) for the full list.

## Known gaps

- **No code generation yet.** The target stack has not been chosen.
- **Advanced SQL** is extracted as text (`logic/advanced-sql.json` plus `sql` on each
  `Nodes.AdvancedQuery` flow node) in OutSystems notation: `{Entity}`, `{Entity}.[Attr]`,
  `@Param`. It is not parsed or translated. Platform-specific SQL (e.g. `GETDATE()`,
  `OFFSET ... FETCH`) still has to be ported by hand.
- **`.xif` extensions** are parsed (actions, parameters, structures, embedded
  source code) but their logic is not translated; port it from
  `extensions/<Name>/source/`.
- **CSS** usually lives in theme modules (`*_TH`, `OutSystemsUI`). Include those
  modules in the package, or extract them separately.
- **Runtime-only data** (effective site property values, user-role assignments)
  requires ServiceCenter access: `scripts/download_omls.py`,
  `scripts/scrape_site_properties.py`.
