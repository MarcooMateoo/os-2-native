# How to extract an OutSystems app

Step-by-step instructions for taking any OutSystems solution (`.osp`) or
application (`.oap`) through the first four stages of
[`../STAGES.md`](../STAGES.md) with **os2native**, using PowerShell or cmd:

| Stage | You | The agent |
|---|---|---|
| [1. Extraction](#stage-1-extraction) | Put the exports in `INPUTS`, choose the apps in scope | Extracts, checks dependencies |
| [2. Verification](#stage-2-verification) | Open the modules in Service Studio, **approve the report** | Compares the extraction with Service Studio through the MCP |
| [3. Prototype](#stage-3-prototype) | Choose the back end, give feedback | Generates the deliverables, writes the product spec, builds a prototype |
| [4. Conversion plan](#stage-4-conversion-plan) | **Approve the handover** | Writes `CONVERTER_HANDOVER.md` |
| [5. Conversion](#stage-5-conversion) | Start the converter | (converter agents) |

You can ask the extractor agent ([AGENTS.md](AGENTS.md)) in Claude Code to
run them and guide you through the reviews (new here? start with
[Getting started with Claude Code](#getting-started-with-claude-code)), or
run the commands yourself.

## Getting started with Claude Code

Most people run this through **Claude Code**: the extractor agent runs the
commands and stops at each review for you. These are the steps for a new
user on Windows.

### What you need

| | Why |
|---|---|
| **Claude Code** (desktop app, Code tab, or the `claude` CLI) | Runs the extractor and converter agents |
| **Git** | To get the repository |
| **Python 3.12 or later** (`py --version`) | Runs os2native |
| **.NET 10 runtime** | Decodes `.oml` files. A per-user install in `%LOCALAPPDATA%\Microsoft\dotnet` is enough; you don't need to change PATH |
| **Exports from OutSystems** | The `.osp`/`.oap` of the solution, plus any extra `.oml`/`.xif` it depends on |
| **Service Studio 11** with its MCP Server (optional) | Lets the agent verify the extraction against the live modules (stage 2) and capture missing modules |

### 1. Get the repository

```powershell
git clone <repository URL> OutSystems_2_Native
```

The repository looks like this. `projects\` is created by you, and it's
git-ignored: customer packages and everything generated from them stay on
your machine and are never committed.

```
OutSystems_2_Native\
  STAGES.md               the five stages, from extraction to conversion
  TECH_STACK.md           the target stack
  extractor\              stages 1–4 (the os2native tool); start Claude Code here
  converter\              stage 5 (conversion); start Claude Code here later
  projects\               your projects (not in git)
    MyProject\
      INPUTS\             your exports from OutSystems (+ screenshots)
      OUTPUT\             everything the extractor generates
      project.json        your decisions: scope, target stack
      converted\          the rebuilt app (written by the converter)
```

### 2. Set up the extractor (once per machine)

```powershell
cd OutSystems_2_Native\extractor
```

```powershell
py -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
```

To check the setup, run the tests (`.venv\Scripts\python -m pytest`); they
should all pass. If you move or rename the folder later, run the `pip install`
line again.

### 3. Create your project folder

Create `projects\MyProject\INPUTS\` (use your project's name instead of
`MyProject`; a short name without spaces is easiest) and copy in:
- the `.osp`/`.oap`;
- any extra `.oml`/`.xif` you already know it needs;
- screenshots of the live app, if you have them.

One project per folder: never put two solutions in the same project.

### 4. Open Claude Code in the `extractor` folder

- **Desktop app:** start a new session in the Code tab and choose the
  `OutSystems_2_Native\extractor` folder as its working folder.
- **CLI:** `cd OutSystems_2_Native\extractor`, then run `claude`.

It must be the `extractor` folder, not the repository root: that is where
`CLAUDE.md` loads the extractor's instructions (`AGENTS.md`). If Claude Code
asks whether to use the project's MCP server (`servicestudio`, from
`.mcp.json`), allow it if you will use Service Studio; you can decline
otherwise.

### 5. Ask for the extraction

Start with a plain request, for example:

> Extract the project MyProject.

The agent then:
1. **Confirms the project** with you, and which apps in the package you're
   migrating. It records your answers in `project.json`.
2. **Runs stage 1**, asks about any missing dependencies in one message, and
   tells you the next step.
3. **Verifies the extraction** (stage 2) with you, through Service Studio if
   you have it open, and waits for your approval.
4. **Writes the deliverables, the product specification, the action
   catalogue and a prototype** (stage 3), after asking about the target stack
   choices that are still open.
5. **Writes the conversion plan** (`CONVERTER_HANDOVER.md`, stage 4), waits
   for your approval, and ends with a **Next steps** message for the
   converter.

You can stop at any time. In a later session, ask "Where is MyProject?"; the
agent works out the stage from the files and carries on.

### 6. Hand over to the converter

Follow the agent's **Next steps** message: open a new Claude Code session in
the `OutSystems_2_Native\converter` folder and paste the prompt it gives you.
See [Stage 5](#stage-5-conversion).

### Good to know

- **You approve, the agent proposes.** It never decides the scope, the
  stack or a missing dependency for you, and never changes your modules in
  Service Studio.
- **Don't edit `OUTPUT\` by hand,** except `OUTPUT\SPECS\`, which holds the
  reviewed documents (product specification, action catalogue, prototype,
  handover).
- **Never commit `projects\`.** It holds customer data; it's git-ignored on
  purpose.
- **Without Claude Code**, you can run every step yourself with the
  commands below.

## Before you start

- **Once per machine:** the extractor is set up (`.venv` exists, see
  [Getting started](#2-set-up-the-extractor-once-per-machine)) and the .NET 10
  runtime is installed. os2native finds a .NET 10 install in your user folder
  (`%LOCALAPPDATA%\Microsoft\dotnet`) automatically, so you don't need to
  change PATH.
- **One folder per project** in `projects\`, next to `extractor\`.
  A project is the solution you're migrating; its package can contain
  several OutSystems apps. Each project has its own `INPUTS` and `OUTPUT`:

```
OutSystems_2_Native\
  extractor\              <- this folder (the os2native tool)
  projects\
    MyProject\
      INPUTS\             <- .osp/.oap + extra .oml/.xif (+ screenshots)
      OUTPUT\             <- results for MyProject
    OtherProject\
      INPUTS\
      OUTPUT\
```

`INPUTS` holds your exports from OutSystems, and os2native never changes it
(except `INPUTS\mcp\`, which only holds modules captured from Service Studio
in step 5 if you choose that).
`OUTPUT` is rebuilt on every run, so don't hand-edit files there. The
exception is `OUTPUT\SPECS\`, which holds hand-reviewed documents that no
command writes or deletes: the product specification and prototype per app,
and `CONVERTER_HANDOVER.md`.

Never extract two projects into the same `OUTPUT`: `OUTPUT\extraction` is not
cleared between runs, so their modules would mix in the dependency check and
the report.

The examples below use `MyProject`; replace it with your project's folder name.

## Stage 1: Extraction

### 1. Open PowerShell in the extractor folder

```powershell
cd "<path where you cloned the repo>\extractor"
```

Use the folder that contains `README.md` and `pyproject.toml`. Every command
below is run from there, and reaches the project folder through
`..\projects\MyProject`.

You don't need to activate anything: the commands call `.venv` directly.

### 2. Put the files in the project's INPUTS folder

Create `..\projects\MyProject\INPUTS\` and copy in:

- the `.osp` or `.oap` exported from OutSystems
- any extra `.oml` modules or `.xif` extensions the app depends on, if you
  already know it needs them (step 4 tells you if any are missing, and step 5
  how to fill the gap)
- screenshots of the live app, if you have them: they help the product spec
  and prototype in stage 3

Other files, such as screenshots, can live there too; the PowerShell command
in step 3 skips them.

### 3. Run the extraction

**PowerShell** (takes every `.osp`, `.oap`, `.oml` and `.xif` in the folder
and its subfolders, including modules captured from Service Studio in
`INPUTS\mcp\`):

```powershell
.venv\Scripts\os2native package (Get-ChildItem ..\projects\MyProject\INPUTS -File -Recurse | ? Extension -in '.osp','.oap','.oml','.xif' | % FullName) --output ..\projects\MyProject\OUTPUT
```

If you later export a module that's also in `INPUTS\mcp\`, delete the
`INPUTS\mcp\` copy so the module isn't included twice.

**cmd** (list the files yourself):

```bat
.venv\Scripts\os2native package ..\projects\MyProject\INPUTS\MyApp.osp ..\projects\MyProject\INPUTS\Extra.oml --output ..\projects\MyProject\OUTPUT
```

Always pass `--output`. Without it, the output goes to an `OUTPUT` folder
inside the os2native repo.

Expect about 20–30 seconds per 15 modules. The exit code is 1 if any errors
were logged.

### 4. Check the result

Open these in `..\projects\MyProject\OUTPUT\`:

| File | What to look for |
|---|---|
| `extraction-issues.log` | **Read first.** The last line gives the error and warning count. **ERROR** = a module or extension failed and is missing from the output. **WARNING** = extraction finished but something is incomplete; check whether it's expected. |
| `applications.json` | The apps in the package manifest and their modules. Loose files you added to `INPUTS` are under `(extra inputs)`. |
| `dependency-check.json` | Dependencies the package doesn't include, labelled `platform` (expected), `forge` (needs a native replacement), `accepted`, `indirect` or `unknown` (see below). |
| `inventory-details.csv` | Every item with its Service Studio location (open in Excel). Compare it against Service Studio. |
| `inventory-details.md` | The same list, readable as a document, with totals at the top. |
| `inventory.json` | Totals per app and per module, plus the issue counts. |

#### Scope and missing dependencies

The dependency check only asks about what the apps you're migrating use
directly. By default that's every app in the package manifest. To narrow it,
or to record a missing module you don't need, create
`..\projects\MyProject\project.json` next to `INPUTS` and `OUTPUT` (the
agent does this when you answer its questions):

```json
{
  "scope": { "applications": ["Sales Portal", "Sales Portal Core"] },
  "accepted_missing": { "SVGUtilities": "Only converts icons; rebuilt natively" }
}
```

Use app names from `applications.json`. Then run the check. It rewrites
`dependency-check.json` and exits with 1 if any dependency is `unknown`:

```powershell
.venv\Scripts\python scripts\check_dependencies.py ..\projects\MyProject\OUTPUT
```

| Kind | Meaning | What to do |
|---|---|---|
| `platform` | OutSystems built-in or mobile plugin (`(System)`, `OutSystemsUI`, `Users`, ...) | Nothing. Mapped to native equivalents at rebuild time. |
| `forge` | Known Forge component | Nothing now; it needs a native replacement library. |
| `accepted` | In `accepted_missing` in `project.json` | Nothing. |
| `indirect` | Only used by modules outside the scope, e.g. by a dependency you added to `INPUTS` | Nothing. You don't need to chase dependencies of dependencies. |
| `unknown` | Used directly by an app in scope | Fill the gap (step 5), or add it to `accepted_missing`. The check lists what the app uses from it, to help you decide. |

Folders in `OUTPUT\extraction\` left over from an earlier run are ignored
and listed under `stale_extraction_folders`; delete them to tidy up.

### 5. Fill the gaps (if INPUTS isn't enough)

Do this when step 4 shows an `unknown` dependency, a `WARNING ... is not in
the package` line, an ERROR for a module, or no theme with CSS in any module.
Pick one option per missing module:

**Option A: add files to INPUTS**

1. Export it: the `.oml` from Service Studio, or the `.xif` from Integration
   Studio (or a fresh `.osp`/`.oap` that includes it).
2. Put the file in `..\projects\MyProject\INPUTS\`.
3. Run step 3 again.

**Option B: open it in Service Studio and let the agent capture it**

Use this when you can open the module but exporting it is awkward. It needs
an AI agent connected to the Service Studio MCP Server (the repo's
`.mcp.json` does this for Claude Code). It doesn't work for extensions
(`.xif`); use option A for those.

1. In Service Studio, open the missing module and start the MCP Server from
   its MCP Server dialog (default port 41820).
2. Ask the agent to capture the module. Approve its connection request when
   Service Studio asks. The agent only reads the module; it doesn't change,
   merge or publish anything.
3. The agent copies the module to `INPUTS\mcp\<Module>.oml` with
   `scripts\collect_mcp_snapshot.py` and re-runs step 3. Don't restart
   Service Studio before it's done: that clears the snapshot.

**Not needed?** Add it to `accepted_missing` in `project.json` with the
reason. If it's a public Forge or platform component, add its name to
`scripts\known-external.json` instead, so every project knows it. Don't add
a name just to make the check pass.

**Stage 1 is done when** `extraction-issues.log` has no ERROR (or you
accepted what is missing), the dependency check exits with 0, and every
WARNING is explained.

## Stage 2: Verification

The agent checks the extraction against your modules in Service Studio, and
you approve the result before anything is built from it.

1. In Service Studio, open the modules of the apps in scope and start the
   MCP Server from its MCP Server dialog (default port 41820).
2. Ask the agent to verify the extraction. Approve its connection request
   when Service Studio asks. It only reads the modules; it doesn't change,
   merge or publish anything.
3. The agent compares each module's entities, screens, actions, roles,
   timers, site properties, integrations and so on with
   `inventory-details.csv`, by name, and spot-checks details such as entity
   attributes and screen parameters. It writes
   `..\projects\MyProject\OUTPUT\verification\verification-report.md`, with
   every difference and what caused it (an extraction bug, a counting rule,
   or a module that changed after you exported it).
4. **Review the report and approve it.** Open `inventory-details.csv` for
   anything you want to check yourself. If a module changed after the
   export, decide which version is the truth; the agent re-exports or
   captures it and re-runs stage 1.

**Without the MCP**, check the counts yourself: open `inventory-details.csv`
and compare them per module with Service Studio. They're meant to match
exactly. Things that are easy to misread:

- `themes`/`stylesheets` are the module's own; `*_consumed` come from dependencies.
- `client_actions` are under Logic › Client Actions; actions inside screens
  and blocks are `screen_actions`.
- Platform site properties (`TenantId`, `TenantName`) and the built-in roles
  (Anonymous, Registered) aren't counted.
- `rest_*`/`soap_*` are real integrations only.

Then tell the agent what you found, so it can write the report.

## Stage 3: Prototype

Starts once you have approved the verification report.

### Record the target stack

`project.json` holds the project's complete target stack: every layer of
`..\TECH_STACK.md` (back end, web, mobile, database, auth, platform, local
development). Fill it in with the TECH_STACK.md defaults:

```powershell
.venv\Scripts\python scripts\project_stack.py init ..\projects\MyProject
```

Then see every layer and what is still open:

```powershell
.venv\Scripts\python scripts\project_stack.py show ..\projects\MyProject
```

An open decision is a layer where TECH_STACK.md gives a choice (e.g. shadcn/ui
or MUI, Cognito or the corporate identity provider). Record your choices,
or ask the agent to, as `group.layer=value`:

```powershell
.venv\Scripts\python scripts\project_stack.py set ..\projects\MyProject backend.language=dotnet web.ui=shadcn auth.identity_provider=cognito
```

`backend.language` (`dotnet` for ASP.NET Core on .NET 10, or `node` for
Node.js + TypeScript) must be chosen before the specs are generated. The
others can stay open for later; the handover lists them with what they
block. Use `"n/a"` for a layer that doesn't apply, such as mobile offline
storage when there is no mobile app. To try the other back end for one run,
add `--backend node` (or `dotnet`) to the `generate_specs.py` command.

The result looks like this (shortened):

```json
{
  "scope": { "applications": ["Sales Portal", "Sales Portal Core"] },
  "stack": {
    "backend":  { "language": "dotnet", "data_access": "efcore+dapper", "jobs": null },
    "web":      { "framework": "react", "ui": "shadcn" },
    "database": { "engine": "postgresql", "case_insensitive_text": false },
    "auth":     { "identity_provider": "cognito" },
    "platform": { "iac": null, "cicd": "github-actions" }
  }
}
```

### Generate the deliverables

```powershell
.venv\Scripts\python scripts\generate_db_schema.py ..\projects\MyProject\OUTPUT
```

```powershell
.venv\Scripts\python scripts\generate_sql_porting.py ..\projects\MyProject\OUTPUT
```

```powershell
.venv\Scripts\python scripts\generate_api_catalog.py ..\projects\MyProject\OUTPUT
```

```powershell
.venv\Scripts\python scripts\generate_specs.py ..\projects\MyProject\OUTPUT
```

```powershell
.venv\Scripts\python scripts\generate_site_properties.py ..\projects\MyProject\OUTPUT
```

These write to `..\projects\MyProject\OUTPUT\deliverables\`, for the apps in
scope and the parts of other modules they use:

- `db-schema\`: PostgreSQL schema (delete rules as `ON DELETE`, indexes,
  Text and Decimal sizes), seed data, ER diagram, `cross-module-fks.md` and
  `client-entities.md` (mobile local-storage entities, which get no table).
  Text columns are case-insensitive (CITEXT, as OutSystems is on SQL Server)
  when `database.case_insensitive_text` is `true`; `--citext`/`--no-citext`
  override it for one run.
- `sql-porting\`: what each Advanced SQL query needs to run on PostgreSQL.
- `api-catalog\`: the API catalog and OpenAPI stubs.
- `configuration\site-properties.md`: every site property (module, type,
  description, default, secret or not, where it goes in the new stack and its
  config key). Secret values and defaults that look personal are masked.
  The handover copies the whole table.
- `specs\`: backend/frontend specs per module for the chosen stack.
  `index.md` shows the stack and whether each module's front end is web or
  mobile. The back-end spec has EF Core entities (or TypeScript row types) on
  the `db-schema` tables, request/response types and signatures for server
  and service actions, and Refit (or TypeScript) clients for consumed APIs.
  The front-end spec has React pages (or Expo Router screens) with their
  parameters, TanStack Query hooks for each data source, components,
  event handlers and conditional rendering.

Optional: `scripts\generate_assessment.py ..\projects\MyProject\OUTPUT`
writes a migration assessment (blockers and gaps) to `OUTPUT\assessment\`.
It's thinner than in Catalyst because the Catalyst scoring files aren't here.

### Product specification and prototype

The agent writes, for each app in scope that has screens:

- `OUTPUT\SPECS\<App>\product-specification.md`: features, journeys,
  business rules and open questions with stable IDs (`F1`, `A1`, `J1`,
  `BR1`, `Q1`), each citing its source module, with anything inferred marked
  **(inferred)** ([template](docs/templates/product-specification.md)).
- `OUTPUT\SPECS\<App>\action-catalogue.md`: every server, service and
  client action (including screen and block actions), each with its purpose
  and its logic flow in plain steps: branches, loops, calls, data read and
  written, error paths and the roles it checks
  ([template](docs/templates/action-catalogue.md)).
- `OUTPUT\SPECS\<App>\prototype\index.html`: a clickable prototype of the
  screens, with the app's theme and fictional data. Open it in a browser.

Go through them and tell the agent what's wrong or missing; it updates them
before the plan.

## Stage 4: Conversion plan

The agent writes `OUTPUT\SPECS\CONVERTER_HANDOVER.md`
([template](docs/templates/CONVERTER_HANDOVER.md)): the sources, the
decisions made and still open, a map from OutSystems modules to the new
app's capabilities, and the plan in iterations (a foundation, then vertical
slices of features), with what blocks each one.

### Readiness checklist

The handover lists every line as done, open (with an owner) or accepted by
you:

- [ ] `extraction-issues.log` has no ERROR, and every WARNING is explained.
- [ ] `check_dependencies.py` exits with 0: no `unknown` dependencies, and
      every `accepted_missing` entry in `project.json` has a reason.
- [ ] The verification report is approved for the current run.
- [ ] A theme module with CSS is in the package.
- [ ] Every `forge` component has a chosen replacement (or an owner deciding).
- [ ] Every extension in `extensions\` has an owner for porting its `source\` code.
- [ ] Every query in `sql-porting\sql-porting.md` marked `manual` has an owner.
- [ ] Database choices are made (`database.*` in `project.json`):
      case-insensitive text, OutSystems default values (`1900-01-01`, `0`,
      `''`) vs NULL, binary data, and what replaces `(System)` users and
      roles (`auth.identity_provider`, `cross-module-fks.md`).
- [ ] Every site property is in the handover (§4, same count as
      `configuration\site-properties.md`), each with a target store and
      config key, and the secrets confirmed with their owner.
- [ ] Runtime-only values are collected: site property values per
      environment, timer schedules set in Service Center, user-role
      assignments.
- [ ] Every layer of the stack is recorded in `project.json`
      (`project_stack.py show` lists no open decision), or each open one is
      in the handover with the iteration it blocks; `specs\` was generated
      for the chosen back end.
- [ ] The product specification, action catalogue and prototype of each app
      have been reviewed; the catalogue documents every server and client action.

**Review the handover and approve it.** The converter doesn't start until
you do. If you re-extract later, the approvals no longer match the run: the
agent re-verifies what changed, updates the spec and handover, and asks you
to approve again.

## Stage 5: Conversion

Conversion happens in the `converter` folder, not here. When you approve the
handover, the extractor agent ends with a **Next steps** message filled in
for your project. It contains:

1. **A new Claude Code session in `converter\`**, so it loads `AGENTS.md`
   and `CONVERTER_AGENT.md`.
2. **The intake.** A prompt such as:
   > Follow AGENTS.md for project "MyProject". The handover is
   > `..\projects\MyProject\OUTPUT\SPECS\CONVERTER_HANDOVER.md` (approved for run <run id>).
   > Start with the intake.

   The converter checks the approvals and runs its intake gate:
   ```powershell
   ..\extractor\.venv\Scripts\python tools\intake_check.py "..\projects\MyProject\OUTPUT" --osp "..\projects\MyProject\INPUTS\MyApp.osp" --out "..\projects\MyProject\converted\intake\intake-check.md"
   ```
3. **STARTER**, which sets up `..\projects\MyProject\converted\` (the converted app) from the handover once
   you approve its proposal.
4. **The iterations**, I0 first. Each one goes through BA → ARCHITECT → DEV →
   QA, and you accept it before the next:
   > Follow CONVERTER_AGENT.md and agents/AGENTS_BA.md for MyProject, iteration I0.
5. **What is still open:** decisions, runtime values to collect, and
   questions, each with the iteration it blocks.

Quote paths that contain spaces (`"..\projects\My Project\OUTPUT"`). If you
re-extract later, go back to the extractor first: the approvals are tied to
one extraction run.

## Tips

- **Close `inventory-details.csv` in Excel before re-running.** If it's open,
  the run writes a timestamped copy (`inventory-details-YYYYMMDD-HHMMSS.csv`)
  and logs a warning.
- **Re-run after adding files:** repeat stage 1, step 3. The log starts
  fresh on each `package` run.
- **Start completely clean:** delete everything in
  `..\projects\MyProject\OUTPUT` except `SPECS\`. `INPUTS` is never touched.
  Stage 2 has to be redone after that.
- **Rebuild only the report** (e.g. after closing Excel), without re-extracting:

  ```powershell
  .venv\Scripts\os2native report ..\projects\MyProject\OUTPUT
  ```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR [Module] OML to XML conversion failed` | .NET 10 is missing or the `.oml` is damaged. Check `dotnet --list-runtimes`, or that `%LOCALAPPDATA%\Microsoft\dotnet` has `Microsoft.NETCore.App 10.x`. |
| `ERROR [input] ...: unsupported file type` | Only `.osp`, `.oap`, `.oml` and `.xif` are accepted. Use the PowerShell command in step 3, which filters by extension, or leave other files out of the list. |
| `ERROR [input] ...: could not unpack package` | The `.osp`/`.oap` is incomplete or corrupt. Export it again. |
| `.venv\Scripts\os2native` not found | First-time setup is missing. Run `py -m venv .venv`, then `.venv\Scripts\python -m pip install -e ".[dev]"`. |
| `os2native` exits with 1 and prints nothing, or `No module named 'os2native'` | The repo folder was moved or renamed after setup, so the install points at the old path. Run `.venv\Scripts\python -m pip install -e ".[dev]"` again. |
| `No Service Studio MCP snapshot of '<Module>'` | The agent hasn't read the module through the MCP yet, a different module is open, or Service Studio was restarted. Open the module, make sure the MCP Server is running, and ask the agent to try again. |
| Output appeared in `extractor\OUTPUT` | `--output` was left out. Delete that folder and re-run with `--output ..\projects\MyProject\OUTPUT`. |
