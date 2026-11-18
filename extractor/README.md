# os2native

Extracts OutSystems solution packages (`.osp`), application packages (`.oap`)
and modules (`.oml`) into structured JSON specification packages, so the
application can be rebuilt on the target stack in
[`../TECH_STACK.md`](../TECH_STACK.md): React (web), React Native/Expo
(mobile), ASP.NET Core (.NET 10) or Node.js + TypeScript (back end) and
PostgreSQL.

os2native does **not** generate application code. It produces a complete,
machine-readable description of the app (screens, logic, data model, APIs,
roles, timers, site properties, dependencies) plus generated deliverables
(PostgreSQL schema, API catalog, per-module specs) that a team or an AI agent
uses to rebuild it.

It covers the first four stages of [`../STAGES.md`](../STAGES.md); the
`converter` repository does the fifth:

| Stage | What os2native (and its agent) produce | User gate |
|---|---|---|
| 1. Extraction | `OUTPUT\` inventory, dependency check, module packages, for the apps in scope | |
| 2. Verification | `OUTPUT\verification\verification-report.md`: the extraction checked against Service Studio through the MCP | Approve the report |
| 3. Prototype | `OUTPUT\deliverables\`, then `OUTPUT\SPECS\<App>\product-specification.md` and `prototype\` | Feedback |
| 4. Conversion plan | `OUTPUT\SPECS\CONVERTER_HANDOVER.md` | Approve the handover |
| 5. Conversion | (in `../converter/`) | |

Built from the reusable core of an earlier internal extractor, with its
product-specific layers left out.

## How it works

```
 projects\<Project>\INPUTS\            projects\<Project>\OUTPUT\
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
7. **Deliverables** (separate scripts): PostgreSQL DDL, an Advanced SQL
   porting checklist, API catalog with OpenAPI stubs, and backend/frontend
   specs per module written for the project's target stack (`"stack"` in
   `project.json`).
8. **Gaps in the export** (optional): when a module is missing from
   `INPUTS`, either add the exported file, or open the module in Service
   Studio and let an AI agent capture it through the Service Studio MCP
   Server (see [Filling gaps with Service Studio](#filling-gaps-with-service-studio)).

Every error and warning from a run goes to `projects\<Project>\OUTPUT\extraction-issues.log`.

## Project workspace

The tool and the project data live side by side. Customer packages and
output stay outside the repo, so they never end up in git:

```
OutSystems_2_Native\
  extractor\              this folder (the os2native tool)
  projects\
    MyProject\            one folder per project
      INPUTS\             what you export from OutSystems
      OUTPUT\             everything os2native generates for this project
        SPECS\            hand-reviewed documents (kept across runs)
      project.json        your decisions: apps in scope, missing modules not needed, complete target stack
```

A **project** is the solution you're migrating (e.g. `MyProject`). Its package
can contain several OutSystems apps (e.g. a mobile app and its back office).

| Folder | Purpose |
|---|---|
| `projects\<Project>\INPUTS\` | The `.osp`/`.oap` plus any extra `.oml`/`.xif` dependencies, and optionally screenshots. os2native only reads it, never changes it. The one exception is `INPUTS\mcp\`, where `collect_mcp_snapshot.py` puts modules captured from Service Studio when you choose that option. |
| `projects\<Project>\OUTPUT\` | Destination for every command run on this project. Rebuilt on each run, so don't hand-edit generated files. Use one `OUTPUT` per project: `extraction\` isn't cleared between runs, so two projects in one folder mix their modules. |
| `projects\<Project>\OUTPUT\verification\` | Stage 2: `verification-report.md` for the current run (written by the agent). |
| `projects\<Project>\OUTPUT\SPECS\` | The exception: hand-reviewed documents that no command writes or deletes: `<App>\product-specification.md` and `<App>\prototype\` (stage 3), `CONVERTER_HANDOVER.md` (stage 4). |

Run every command from the `extractor` folder and point it at the project
with `..\projects\<Project>\...`. Always pass `--output`: without it, the
output goes to an `OUTPUT` folder inside the repo (git-ignored, safe to
delete).

## Repo folders

| Folder | Purpose |
|---|---|
| `bin/` | The OML → XML decoder in `bin/oml-utilities/`: `oml.dll`, its 3 supporting DLLs and 2 config files. Needs the .NET 10 runtime. Binary, never edit. |
| `docs/` | Reference documentation (see [Documentation](#documentation) below). |
| `scripts/` | Tools run on a project's `OUTPUT` folder after extraction: deliverable generators, the missing-dependency check, optional ServiceCenter tools, and `known-external.json` (known platform modules, mobile plugins, Forge components). |
| `src/` | The `os2native` Python package: CLI, pipeline, workers, orchestrator, inventory report. Standard library only. |
| `tests/` | pytest suite. Uses small hand-built XML snippets, never customer data. |
| `.venv/` | Python virtual environment (not in git). Disposable: recreate with the setup steps. |
| `.mcp.json` | Connects AI agents (Claude Code) to the Service Studio MCP Server at `http://127.0.0.1:41820/mcp`. Used only to fill gaps in the export. |

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
| `generate_db_schema.py` | PostgreSQL DDL (delete rules, indexes, Text/Decimal sizes), seed data, ER diagram, type mapping, client entities → `deliverables\db-schema\`. `--citext` makes Text case-insensitive like SQL Server |
| `generate_sql_porting.py` | Checklist of what each Advanced SQL query needs for PostgreSQL → `deliverables\sql-porting\` |
| `collect_mcp_snapshot.py` | Copies a module open in Service Studio (MCP snapshot) to `INPUTS\mcp\<Module>.oml` |
| `generate_api_catalog.py` | API catalog, coverage matrix, OpenAPI stubs → `deliverables\api-catalog\` |
| `project_stack.py` | Shows, fills in (`init`) and records (`set`) a project's complete target stack in `project.json`: every TECH_STACK.md layer, with the open decisions listed |
| `check_portable_spec.py` | Checks the optional portable product specification (`SPECS\<App>\portable-spec\`) before it's shared: no platform terms, module or library names, technologies, colours, fonts, emails or secrets, and every feature, rule and action covered |
| `generate_site_properties.py` | Every site property in one table (type, default, secret, target store, config key, runtime value collected or not) → `deliverables\configuration\`; masks secrets and personal-looking defaults. The handover copies it |
| `generate_specs.py` | Backend/frontend specs per module for the target stack → `deliverables\specs\`. Reads `"stack"` from `project.json`; `--backend dotnet\|node` overrides the language. Web modules get React pages, mobile apps Expo Router screens; `index.md` lists every layer and the open decisions |
| `target_stack.py` | The stack catalogue (every TECH_STACK.md layer, its default and options), and the OutSystems → C#/TypeScript type mapping used by the specs |
| `generate_assessment.py` | Optional migration assessment → `OUTPUT\assessment\` (thinner without Catalyst scoring files) |
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
- **Optional: Service Studio (O11) with its MCP Server**, only to fill gaps
  in the export. Start it from Service Studio's MCP Server dialog (default
  port 41820); the repo's `.mcp.json` points agents at it.

## Setup

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Quick start

The full step-by-step guide is [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md). In short,
for a project in `..\projects\MyProject\`:

```powershell
# 1. Extract the package (add extra .oml/.xif dependencies after it)
.venv\Scripts\os2native package ..\projects\MyProject\INPUTS\MyApp.osp ..\projects\MyProject\INPUTS\Extra.oml ..\projects\MyProject\INPUTS\Extra.xif --output ..\projects\MyProject\OUTPUT

# 2. Find missing modules (exit code 1 if any are unknown)
.venv\Scripts\python scripts\check_dependencies.py ..\projects\MyProject\OUTPUT

# 3. Stage 2: verify inventory-details.csv against Service Studio (the agent does
#    this through the MCP and writes OUTPUT\verification\verification-report.md)

# 4. Stage 3: generate deliverables (then the agent writes the product spec and prototype)
.venv\Scripts\python scripts\generate_db_schema.py ..\projects\MyProject\OUTPUT
.venv\Scripts\python scripts\generate_sql_porting.py ..\projects\MyProject\OUTPUT
.venv\Scripts\python scripts\generate_api_catalog.py ..\projects\MyProject\OUTPUT
.venv\Scripts\python scripts\generate_specs.py ..\projects\MyProject\OUTPUT   # needs "stack": {"backend": ...} in project.json

# 5. Stage 4: the agent writes OUTPUT\SPECS\CONVERTER_HANDOVER.md for your approval
```

After step 1, read `..\projects\MyProject\OUTPUT\extraction-issues.log`:

- **ERROR**: a module or extension failed, or an input was missing. Those
  items are missing from the output.
- **WARNING**: extraction finished but something is incomplete (unresolved
  items, unknown dependencies, decoder messages).

Step 2 classifies each dependency that isn't in the package. It only asks
about modules that the apps in scope use **directly**: by default every app
in the package manifest (`applications.json`), or the `scope` in
`..\projects\MyProject\project.json`. Dependencies of the extra modules you
add are `indirect`, so you don't end up chasing dependencies of dependencies.

| Kind | Meaning | What to do |
|---|---|---|
| `platform` | OutSystems built-in or mobile plugin (`(System)`, `OutSystemsUI`, `Users`, ...) | Expected. Map to native equivalents at rebuild time. |
| `forge` | Known Forge component | Needs a native replacement library. |
| `accepted` | Listed in `accepted_missing` in `project.json` (you said it isn't needed) | Nothing. |
| `indirect` | Only used by modules outside the scope | Nothing. |
| `unknown` | Used directly by an app in scope; not in the package and not a known component | Add the missing `.oml`/`.xif` to `INPUTS`, or open the module in Service Studio (see below), then re-run step 1. If it isn't needed, add it to `accepted_missing` with the reason. Add it to `known-external.json` only if it's a public Forge/platform component. |

Don't move on to rebuilding until the verification report and the handover
are approved and the [readiness checklist](HOW_TO_EXTRACT.md#readiness-checklist)
is ticked off.

## Filling gaps with Service Studio

If `INPUTS` isn't enough (an `unknown` dependency, a missing theme module, a
module that failed to decode, counts that don't match Service Studio), there
are two ways to fill the gap:

1. **Add files to `INPUTS`**: export the `.oml` (Service Studio) or `.xif`
   (Integration Studio), or a fresh `.osp`/`.oap`, and re-run step 1.
2. **Open the module in Service Studio** and let an AI agent use the
   **Service Studio MCP Server** (configured in `.mcp.json`). The agent reads
   the module with the MCP's read-only tools, which makes Service Studio save
   a snapshot of it; `scripts\collect_mcp_snapshot.py` copies that snapshot to
   `INPUTS\mcp\<Module>.oml`, and step 1 is re-run with it. The agent can also
   compare the module's elements against `inventory.json`.

Extensions (`.xif`) can't be opened in Service Studio, so they always need
option 1. The MCP only reads design-time data: runtime values (site property
values, timer schedules set in Service Center, user-role assignments) still
need ServiceCenter. Agents use only the MCP's read tools here and never
change, merge or publish the module. Details: [AGENTS.md](AGENTS.md#when-inputs-are-not-enough-service-studio-mcp).

## Other commands

```powershell
# Single module
.venv\Scripts\os2native extract --oml ..\projects\MyProject\INPUTS\MyModule.oml --output ..\projects\MyProject\OUTPUT
.venv\Scripts\os2native extract --xml ..\projects\MyProject\OUTPUT\_xml\MyModule.xml --output ..\projects\MyProject\OUTPUT --domain data

# Rebuild inventory.json + inventory-details.csv/.md without re-extracting
.venv\Scripts\os2native report ..\projects\MyProject\OUTPUT

# Dependency closure of a set of module XMLs
.venv\Scripts\os2native closure --help
```

## Output layout

```
projects\<Project>\OUTPUT\
  _source\                 unpacked .oml / .xif files
  _xml\                    decoded XML per module
  extraction\<Module>\     spec package per module (see below)
  extensions\<Name>\       extension.json + source\ (embedded C#/Java) per .xif
  deliverables\            db-schema\, sql-porting\, api-catalog\, specs\ (from scripts\generate_*.py)
  inventory.json           run_id and extracted_at of the package run (shared by every
                           module's package.json; approvals refer to it), counts per
                           module + totals (screens, blocks, roles, entities, actions,
                           APIs, timers, site properties, ...)
  inventory-details.csv    one row per counted item: module, category, name,
  inventory-details.md     own/consumed, Service Studio location, details, source file
  extraction-issues.log    every error and warning from the run
  applications.json        apps in the package manifest and their modules
  dependency-check.json    dependencies not in the package, classified
  verification\            stage 2: verification-report.md for this run (written by the agent)
  SPECS\<App>\             stage 3: product-specification.md, prototype\ (hand-reviewed, never written by a command)
  SPECS\CONVERTER_HANDOVER.md   stage 4: conversion plan and handover to the converter
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
.venv\Scripts\os2native extract --oml MyModule.oml --output tmp --xml-only
$env:OS2NATIVE_GOLDEN_XML = "tmp\MyModule.xml"
.venv\Scripts\python -m pytest
```

## Contributing

- Confirm any change against the real XML in `..\projects\<Project>\OUTPUT\_xml\` and
  [docs/oml-xml-knowledge-base.md](docs/oml-xml-knowledge-base.md).
- Add a test with every fix or feature, using hand-built XML snippets. Never
  commit customer XML or output.
- Keep `src/` generic and standard-library only. App-specific knowledge goes
  in config such as `scripts/known-external.json`.
- Pass `encoding="utf-8"` on every file read and write.
- Report problems through `os2native.run_log.log.warning/error(...)`, not `print`.

See [AGENTS.md](AGENTS.md) for the full list.

## Known gaps

- **No code generation.** The specs describe the target code (types,
  signatures, routes, hooks) for the stack in `project.json`, but the code
  itself is written by the team or an AI agent. The back end (`dotnet` or
  `node`) must be chosen before `generate_specs.py` runs.
- **Advanced SQL** is extracted as text (`logic/advanced-sql.json` plus `sql` on each
  `Nodes.AdvancedQuery` flow node) in OutSystems notation: `{Entity}`, `{Entity}.[Attr]`,
  `@Param`. It covers server and client actions and exposed REST methods; queries that
  Service Studio itself can't fully parse (e.g. `FOR XML PATH`) are kept too, with
  `sql_fully_parsed: false`. It is not parsed or translated. Platform-specific SQL (e.g. `GETDATE()`,
  `DATEADD`, `CONVERT`) still has to be ported by hand;
  `scripts/generate_sql_porting.py` lists what each query needs.
- **Database parity decisions** are left to you: SQL Server compares text
  case-insensitively and PostgreSQL doesn't (`--citext` keeps OutSystems
  behaviour), OutSystems uses `1900-01-01`/`0`/`''` instead of NULL, and
  references to `(System)` entities such as `User` get no FK
  (`cross-module-fks.md`).
- **`.xif` extensions** are parsed (actions, parameters, structures, embedded
  source code) but their logic is not translated; port it from
  `extensions/<Name>/source/`.
- **CSS** usually lives in theme modules (`*_TH`, `OutSystemsUI`). Include those
  modules in the package, add their `.oml`, or capture them from Service Studio.
- **Runtime-only data** (effective site property values, user-role assignments)
  requires ServiceCenter access: `scripts/download_omls.py`,
  `scripts/scrape_site_properties.py`.
