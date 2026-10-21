# os2native

Extracts OutSystems solution packages (`.osp`), application packages (`.oap`)
and modules (`.oml`) into structured specification packages for rebuilding the
application on a native stack (e.g. Flutter + Go/Node + PostgreSQL).

Built from the reusable core of `catalysts/catalyst-extractor`, with the
Giga/Torpedo-specific layers (dual-output pipeline, Iceman projection,
journeys, Storybook) left out.

## Prerequisites

- **Python 3.12+**: the pipeline uses only the standard library
- **.NET 10 runtime**: `bin/oml-utilities/oml.dll` targets `net10.0`
  (`dotnet --list-runtimes` must show `Microsoft.NETCore.App 10.x`).
  A per-user install at `%LOCALAPPDATA%\Microsoft\dotnet` (from
  `dotnet-install.ps1`) is picked up automatically without touching PATH.
  To use a different host, set `OS2NATIVE_DOTNET` to its `dotnet.exe` path.

## Setup

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Usage

Step-by-step guide for extracting an app: [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md).

```bash
# Whole solution / application package -> every module + inventory.json
os2native package MySolution.osp --output OUTPUT

# ...plus dependencies the package doesn't include (.oml modules, .xif extensions)
os2native package MySolution.osp INPUTS\HiQRCoder_v2.oml INPUTS\CSVUtilExtension_v1.xif --output OUTPUT

# Single module
os2native extract --oml MyModule.oml --output OUTPUT
os2native extract --xml OUTPUT/MyModule.xml --output OUTPUT --domain data

# Dependency closure of a set of module XMLs
os2native closure --help

# Rebuild inventory.json + inventory-details.csv/.md without re-extracting
os2native report OUTPUT

# Find modules/dependencies the package is missing (exit code 1 if any are unknown)
python scripts/check_dependencies.py OUTPUT

# Downstream generators (run on a `package` output directory)
python scripts/generate_db_schema.py OUTPUT     # PostgreSQL DDL, seed data, ER diagram
python scripts/generate_api_catalog.py OUTPUT   # API catalog + OpenAPI stubs
python scripts/generate_specs.py OUTPUT         # backend/frontend specs per module
```

`package` output layout:

```
OUTPUT/
  _source/                 unpacked .oml / .xif files
  extensions/<Name>/       extension.json + source/ (embedded C#/Java) per .xif
  _xml/                    oml-utilities XML per module
  extraction/<Module>/     spec package per module (see docs/module-output-package-contract.md)
  deliverables/            written by scripts/generate_*.py
  inventory.json           counts per module + totals (screens, blocks, roles,
                           entities, actions, APIs, timers, site properties, ...)
  extraction-issues.log    every error and warning from the run (failed modules,
                           unresolved items, missing dependencies, ...)
  dependency-check.json    dependencies not in the package, classified
  inventory-details.csv    one row per counted item: module, category, name,
  inventory-details.md     own/consumed, Service Studio location, details, source file
```

## Layout

```
bin/oml-utilities/   OML -> XML decoder (oml.dll + its 3 dependency DLLs + 2 config files)
src/os2native/
  cli.py             extract / package / closure commands
  package.py         .osp/.oap unpacking and inventory
  pipeline.py        Stage 1 (OML->XML), Stage 2 (workers), Stage 3 (orchestrator)
  xml_loader.py      XML loading and fragment index
  orchestrator.py    dependencies, stubs, integrations, readiness, summary
  closure.py         module dependency closure
  shared/            key index, type and expression resolvers, coverage,
                     ServiceCenter client (optional)
  workers/           assets, interface, logic, process, data
scripts/             generic generators + optional ServiceCenter download/scrape
docs/                OML XML knowledge base, OutSystems concepts, output contract
tests/
```

## Tests

```bash
.venv\Scripts\python -m pytest
```

For end-to-end tests, convert a module of your own and point
`OS2NATIVE_GOLDEN_XML` at it:

```bash
os2native extract --oml MyModule.oml --output tmp --xml-only
set OS2NATIVE_GOLDEN_XML=tmp\MyModule.xml
.venv\Scripts\python -m pytest
```

## Known gaps

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
- `scripts/generate_assessment.py` also reads Catalyst-specific scoring files
  where they exist. It runs without them, but its output is thinner.
