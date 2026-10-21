# AGENTS.md

Instructions for AI coding agents working in **os2native**. This tool extracts
OutSystems solution packages (`.osp`), application packages (`.oap`) and
modules (`.oml`) into JSON specification packages that are used to rebuild the
app on a native stack. Read [README.md](README.md) for the user-facing overview.

## Project map

| Path | What it is |
|---|---|
| `bin/oml-utilities/` | OML → XML decoder: `oml.dll`, its 3 supporting DLLs and 2 config files. Binary, never edit. |
| `src/os2native/cli.py` | Commands: `extract`, `package`, `closure` |
| `src/os2native/package.py` | Unpacks `.osp`/`.oap`, runs every module, writes `inventory.json` |
| `src/os2native/run_log.py` | `extraction-issues.log`: the `os2native` logger, run header/footer, unresolved-item warnings |
| `src/os2native/dependencies.py` | Missing-dependency check (run by `package`, and by `scripts/check_dependencies.py`) |
| `src/os2native/extension.py` | Parses `.xif` (zlib-compressed XML): actions, structures, embedded source files |
| `src/os2native/inventory_report.py` | One row per counted item with its Service Studio location → `inventory-details.csv`/`.md`; the source of all inventory counts |
| `src/os2native/pipeline.py` | Stage 1 (OML→XML), Stage 2 (workers), Stage 3 (orchestrator); finds `dotnet` |
| `src/os2native/workers/` | `assets`, `interface`, `logic`, `process`, `data`: one worker per domain |
| `src/os2native/orchestrator.py` | Dependencies, integrations, readiness, `summary.md` |
| `src/os2native/shared/` | Key index, type and expression resolvers, coverage, ServiceCenter client |
| `scripts/` | Generators (DB schema, API catalog, specs, assessment), `check_dependencies.py`, ServiceCenter tools |
| `scripts/known-external.json` | Known platform modules, mobile plugins and Forge components |
| `docs/oml-xml-knowledge-base.md` | XML tag reference: read it before changing a worker |
| `docs/outsystems-context.md` | OutSystems concepts and how they map to native ones |
| `docs/module-output-package-contract.md` | Output format of each module package |
| `tests/` | pytest suite |

## Setup and commands (Windows, PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

- **.NET:** `oml.dll` needs the .NET 10 runtime. `pipeline.py` uses
  `OS2NATIVE_DOTNET` if set, then `%LOCALAPPDATA%\Microsoft\dotnet\dotnet.exe`,
  then `dotnet` on PATH. Never change PATH or `DOTNET_ROOT` globally: the user
  has other projects on .NET 8.
- **PowerShell 5.1:** no `&&`. Write multi-line Python to a file instead of
  passing it with `python -c` (the quoting breaks). `ConvertFrom-Json` handles
  arrays badly, so inspect JSON with Python.

## Workflow for a new package

The user-facing version of these steps is [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md);
keep both in sync. Use one subfolder per app: `INPUTS\<App>\` and
`--output OUTPUT\<App>`.

Follow these steps in order. Do not move on to rebuilding or code generation
until steps 2 and 3 are clean or the user has accepted what is left.

1. **Extract**
   ```powershell
   .venv\Scripts\os2native package <path>\App.osp --output OUTPUT
   ```
   Then read **`OUTPUT\extraction-issues.log`**. Every error and warning from the
   run is there (one line each, with the module in `[brackets]`, and full
   tracebacks for errors); `issues` in `inventory.json` has the counts.
   - **ERROR** = a module or extension failed, or an input was missing or
     unsupported. Report it to the user; those items are missing from the
     output.
   - **WARNING** = extraction finished but something is incomplete (unresolved
     items, unknown dependencies, invalid XML characters removed, decoder
     messages, a locked report file). Mention these to the user and use them
     in step 2.
   `package` starts a fresh log; `extract` and `report` append to it. New code
   must report problems with `os2native.run_log.log.warning/error(...)`
   (message starting with `[Module]`), never `print`.

2. **Check for missing modules and dependencies in the package**
   ```powershell
   .venv\Scripts\python scripts\check_dependencies.py OUTPUT
   ```
   This compares every module's `dependencies\graph.json` against the modules
   actually in the package and writes `OUTPUT\dependency-check.json`. It exits
   with 1 when anything is `unknown`. Handle each kind like this:

   | Kind | Meaning | What to do |
   |---|---|---|
   | `platform` | OutSystems built-in or mobile plugin (`(System)`, `OutSystemsUI`, `Users`, ...) | Expected. Map to native equivalents at rebuild time. |
   | `forge` | Known Forge component (e.g. `CryptoAPI`) | Not in the package by design. List it as needing a native replacement library. |
   | `unknown` | Not in the package and not a known component | **Stop and report to the user.** Ask for the missing `.oml` or `.xif`, then re-run step 1 with it added (see below), or, if the user confirms it's a Forge/platform component, add it to `known-external.json`. |

   **Adding missing dependencies:** pass the extra files after the package:
   ```powershell
   .venv\Scripts\os2native package App.osp INPUTS\HiQRCoder_v2.oml INPUTS\CSVUtilExtension_v1.xif --output OUTPUT
   ```
   Keep every input (the package and extra `.oml`/`.xif` files) in `INPUTS\`,
   never in `OUTPUT\`, because `OUTPUT\` is rebuilt. A dependency is satisfied when a module in
   `extraction\` or an extension in `extensions\` has its name.

   Also check:
   - **Extensions (`.xif`)** are parsed into `OUTPUT\extensions\<Name>\`:
     `extension.json` (actions, parameters, structures, entities, resources)
     and `source\` (the embedded C#/Java project). The logic is not
     translated, so port it from `source\` by hand. Anything in
     `extensions_not_extracted` failed to parse.
   - **Theme and UI modules:** if no module in the package has a theme with
     CSS (`interfaces\themes.json`, stylesheets in `evidence\assets\css\`),
     the theme module is probably missing. Ask for it.
   - Only add a name to `known-external.json` after the user confirms what it
     is. Never add it just to make the check pass.

3. **Check the inventory against Service Studio**
   Read `OUTPUT\inventory.json` (`totals` and per-module `counts`), and use
   `OUTPUT\inventory-details.csv` / `.md` to find where any item is in Service
   Studio (UI flow, folder, owning screen, producer module). Counts in
   `inventory.json` are the row counts of that report (both come from
   `inventory_report.module_rows`), so change counting rules there only.
   The user checks these against Service Studio, so they must match it exactly:
   - **Own vs consumed:** `themes` / `stylesheets` are defined in the module;
     `themes_consumed` / `stylesheets_consumed` come from dependencies.
   - **Client actions:** `client_actions` are module-level (Logic › Client
     Actions); actions inside screens and blocks are `screen_actions`.
   - **One item per Key:** elements in a Service Studio folder appear twice in
     the XML (e.g. `Flows.UserAction` plus a `Flows.UserActionInsideFolder`
     stub). Never count the same `Key` twice.
   - Platform site properties (`TenantId`, `TenantName`, which are
     `IsReadOnlySiteProperty="Yes"`) are not counted.
   - Built-in system roles (Anonymous, Registered) are not roles.
   - Empty folder elements (e.g. an empty `<Themes>`) are not items.
   - Stylesheets count only when they contain CSS.
   - `rest_consumed`/`rest_exposed`/`soap_*` are real integrations only.
     Dependency actions and public server actions have their own counts.

   If a count looks wrong, confirm it against the raw XML in `OUTPUT\_xml\`
   before changing code.

4. **Generate deliverables**
   ```powershell
   .venv\Scripts\python scripts\generate_db_schema.py OUTPUT
   .venv\Scripts\python scripts\generate_api_catalog.py OUTPUT
   .venv\Scripts\python scripts\generate_specs.py OUTPUT
   ```
   Output goes to `OUTPUT\deliverables\`.

## Rules for changing code

- **Confirm against the XML.** Before changing a worker, find the real
  element in a converted module (`OUTPUT\_xml\*.xml`) and look up the tag in
  `docs/oml-xml-knowledge-base.md`.
- **Add a test with every fix or feature.** Build small XML snippets by hand
  (see `tests/test_advanced_sql.py`, `tests/test_inventory_accuracy.py`).
  Never commit customer XML or output as fixtures.
- **Run the full suite before you finish.** `.venv\Scripts\python -m pytest`.
  The `test_pipeline_golden.py` tests only run when `OS2NATIVE_GOLDEN_XML`
  points at a converted module XML.
- **Re-run on real data.** After changing a worker or the orchestrator,
  re-run step 1 on a real package and compare `inventory.json` before and after.
- **Standard library only** in `src/`. `pytest` and `pglast` (used to check
  generated SQL) are dev-only.
- **Always pass `encoding="utf-8"`** on file reads and writes. The Windows
  default encoding breaks on non-ASCII text.
- **Worker order matters:** `assets` must run before `interface` (see
  `WORKER_ORDER` in `pipeline.py`).
- **Generated SQL must be valid PostgreSQL.** Quote reserved words, add
  foreign keys after all tables, and parse the output with `pglast` after
  changing `generate_db_schema.py`.
- **Keep it generic.** Do not add app-specific names (modules, plugins, URLs)
  to `src/`. App-specific knowledge belongs in config such as
  `known-external.json`.
- **Don't touch `../catalysts/`.** It's the original Giga/Torpedo source and is
  kept as is.
- **`INPUTS/` and `OUTPUT/` are the user's working folders.** `INPUTS/` holds
  what the user exports from OutSystems (`.osp`, `.oap`, extra `.oml`/`.xif`).
  Never modify or delete files there. `OUTPUT/` is the default output of
  every command and is rebuilt on each run: read it freely, but don't hand-edit
  generated files there (a re-run overwrites them). `OUTPUT/SPECS/` is the
  exception (see below). `tmp/` and `.venv/` are disposable.
- **`OUTPUT/SPECS/<App>/`** holds hand-reviewed documents derived from an
  app's output (e.g. `product-specification.md`). No command writes or
  deletes them: update them by editing, and keep "(inferred)" markers and the
  open-questions list accurate. Don't delete them when cleaning `OUTPUT`.

## Known gaps

- Advanced SQL is extracted as text in OutSystems/SQL Server notation
  (`{Entity}.[Attr]`, `@Param`, `GETDATE()`, `OFFSET ... FETCH`), not
  translated to PostgreSQL.
- `.xif` extensions are parsed (signatures and embedded source code), but
  their logic is not translated.
- Runtime-only data (effective site property values, user-role assignments)
  needs ServiceCenter access.
- No code generation yet. The target stack has not been chosen.
