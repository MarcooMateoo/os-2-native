# AGENTS.md

Instructions for AI coding agents working in **os2native**. This tool extracts
OutSystems solution packages (`.osp`), application packages (`.oap`) and
modules (`.oml`) into JSON specification packages that are used to rebuild the
app on a native stack. As the **extractor** agent you run stages 1–4 of
[`../STAGES.md`](../STAGES.md): extraction, verification, prototype and
conversion plan. Read [README.md](README.md) for the user-facing overview.

## Project map

| Path | What it is |
|---|---|
| `bin/oml-utilities/` | OML → XML decoder: `oml.dll`, its 3 supporting DLLs and 2 config files. Binary, never edit. |
| `src/os2native/cli.py` | Commands: `package`, `extract`, `report`, `closure` |
| `src/os2native/xml_loader.py` | XML loading and fragment index |
| `src/os2native/closure.py` | Module dependency closure (`closure` command) |
| `src/os2native/package.py` | Unpacks `.osp`/`.oap`, runs every module, writes `inventory.json` |
| `src/os2native/run_log.py` | `extraction-issues.log`: the `os2native` logger, run header/footer, unresolved-item warnings |
| `src/os2native/dependencies.py` | Missing-dependency check (run by `package`, and by `scripts/check_dependencies.py`) |
| `src/os2native/extension.py` | Parses `.xif` (zlib-compressed XML): actions, structures, embedded source files |
| `src/os2native/inventory_report.py` | One row per counted item with its Service Studio location → `inventory-details.csv`/`.md`; the source of all inventory counts |
| `src/os2native/pipeline.py` | Stage 1 (OML→XML), Stage 2 (workers), Stage 3 (orchestrator); finds `dotnet` |
| `src/os2native/workers/` | `assets`, `interface`, `logic`, `process`, `data`: one worker per domain |
| `src/os2native/orchestrator.py` | Dependencies, stubs, integrations, readiness, `summary.md` |
| `src/os2native/shared/` | Key index, type and expression resolvers, folders, coverage, ServiceCenter client |
| `scripts/` | Generators (DB schema, SQL porting, API catalog, specs, assessment), `check_dependencies.py`, `collect_mcp_snapshot.py`, ServiceCenter tools |
| `scripts/known-external.json` | Known platform modules, mobile plugins and Forge components |
| `.mcp.json` | The Service Studio MCP Server (`servicestudio`, `http://127.0.0.1:41820/mcp`), used only to fill gaps in `INPUTS`. See [When INPUTS are not enough](#when-inputs-are-not-enough-service-studio-mcp). |
| `docs/oml-xml-knowledge-base.md` | XML tag reference: read it before changing a worker |
| `docs/outsystems-context.md` | OutSystems concepts and how they map to native ones |
| `docs/module-output-package-contract.md` | Output format of each module package |
| `docs/templates/` | Templates for the stage 2–4 documents: `verification-report.md`, `product-specification.md`, `action-catalogue.md`, `CONVERTER_HANDOVER.md`, and `portable-spec/` (the optional portable product specification) |
| `tests/` | pytest suite |
| `../projects/<Project>/` | Outside the repo. One folder per project: `INPUTS/` (exports from OutSystems), `OUTPUT/` (extraction output, `deliverables/`, `SPECS/`) and `project.json` (the user's scope and accepted-missing decisions). See [Working folders](#working-folders). |

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

## Stages

The extractor runs the first four stages of [`../STAGES.md`](../STAGES.md):

| # | Stage | Ends with |
|---|---|---|
| 1 | [Extraction](#stage-1-extraction) | A clean extraction of the apps in scope |
| 2 | [Verification](#stage-2-verification) | ◆ The user approves `OUTPUT\verification\verification-report.md` |
| 3 | [Prototype](#stage-3-prototype) | Deliverables, a product specification, an action catalogue and a clickable prototype per app (plus a portable product specification if the user wants one) |
| 4 | [Conversion plan](#stage-4-conversion-plan) | ◆ The user approves `OUTPUT\SPECS\CONVERTER_HANDOVER.md` |

Stage 5 (conversion) belongs to the converter agents in `../converter/`,
which write the converted app to `..\projects\<Project>\converted\`.
Never start it, and never write to `../converter/` or `converted\`. When stage 4 is approved,
tell the user how to start the converter (see stage 4) and stop.

Run the stages in order. **End every stage by telling the user the next
step:** what was done, what they need to do or decide (an approval, a file to
add, a decision), and what you will do once they have. Stage 4 ends with the
full [Next steps](#stage-4-conversion-plan) message for the converter.

At the start of a session, find where the project is: read `project.json`, the run id in `OUTPUT\inventory.json`, and the
status lines of the verification report and the handover. Tell the user which
stage the project is in before doing anything. Approvals are tied to a run
id: after a new extraction, the stages after it must be redone for what
changed.

The user-facing version of these steps is [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md);
keep both in sync.

Run every command from the `extractor` folder. Each project lives outside the
repo in `..\projects\<Project>\` (e.g. `..\projects\MyProject\`), with its
inputs in `INPUTS\` and its output in `OUTPUT\`. In the steps below, `INPUTS\`
and `OUTPUT\` mean `..\projects\<Project>\INPUTS\` and
`..\projects\<Project>\OUTPUT\`. Always pass `--output` explicitly: its
default is an `OUTPUT` folder inside the repo.

**Before stage 1, confirm the project with the user.** List the folders in
`..\projects\` (and the files in each one's `INPUTS\`), then ask which
project to extract. Don't assume one, even when there is only one project,
when one was used earlier in the session, or when the request mentions an
app name: a package can hold several apps, and the user may have added a new
project. If the project they name doesn't exist yet, ask whether to create
`..\projects\<Project>\INPUTS\` and wait for them to put the exports there.
Run nothing that writes to an `OUTPUT\` folder until the user has confirmed.

## Stage 1: Extraction

Goal: an inventory of the apps **in the project's scope**, with no failed
modules and no unexplained dependencies.

1. **Extract**
   ```powershell
   .venv\Scripts\os2native package ..\projects\<Project>\INPUTS\App.osp --output ..\projects\<Project>\OUTPUT
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
     in step 3.
   `package` starts a fresh log; `extract` and `report` append to it. New code
   must report problems with `os2native.run_log.log.warning/error(...)`
   (message starting with `[Module]`), never `print`.

2. **Confirm the scope.** `OUTPUT\applications.json` lists the apps in
   the package manifest and their modules; loose files from `INPUTS` are under
   `(extra inputs)`. If `..\projects\<Project>\project.json` has no `scope`
   yet, show the user the app names and ask which ones they are migrating
   (default: every app in the manifest, never `(extra inputs)`). Write their
   answer to `project.json`:
   ```json
   { "scope": { "applications": ["Sales Portal", "Sales Portal Core"] } }
   ```
   Ask once per project; when `project.json` already has a scope, use it.
   Everything after this (dependency check, verification, specs, plan) covers
   the apps in scope and only the parts of other modules they use.

3. **Check for missing modules and dependencies**
   ```powershell
   .venv\Scripts\python scripts\check_dependencies.py ..\projects\<Project>\OUTPUT
   ```
   This compares the dependencies of every module from this run against the
   modules in the package and writes `OUTPUT\dependency-check.json`. It exits
   with 1 when anything is `unknown`. Only modules **used directly by an
   in-scope module** can be `unknown`: when a module added to `INPUTS` has
   dependencies of its own, those are `indirect`, because they matter only if
   the app itself uses them. Folders in `extraction\` left from an earlier run
   are ignored (`stale_extraction_folders`). Handle each kind like this:

   | Kind | Meaning | What to do |
   |---|---|---|
   | `platform` | OutSystems built-in or mobile plugin (`(System)`, `OutSystemsUI`, `Users`, ...) | Expected. Map to native equivalents at rebuild time. |
   | `forge` | Known Forge component (e.g. `CryptoAPI`) | Not in the package by design. List it as needing a native replacement library. |
   | `accepted` | The user already said it isn't needed (`accepted_missing` in `project.json`) | Nothing. Never ask about it again. |
   | `indirect` | Only used by modules outside the scope, e.g. by a dependency you added | Nothing. **Don't ask the user for it** and don't add files for it. Mention the count only. |
   | `unknown` | Used directly by an in-scope module; not in the package and not a known component | Ask the user (see below). |

   **Asking about `unknown` dependencies.** Ask about all of them in **one
   message**, never one at a time across re-runs. For each one show who uses
   it (`in_scope_users`) and what they use (`used_elements`), so the user can
   judge whether it's relevant. For each, the user picks one:
   - **Provide it:** add the `.oml`/`.xif` to `INPUTS`, or capture it from
     Service Studio ([When INPUTS are not enough](#when-inputs-are-not-enough-service-studio-mcp)).
     Then re-run step 1.
   - **Not needed:** record it in `project.json` with the user's reason, so it
     becomes `accepted` and is never asked again:
     ```json
     { "accepted_missing": { "SVGUtilities": "Only converts icons; rebuilt natively" } }
     ```
   - **It's a Forge or platform component:** add it to
     `scripts\known-external.json` (repo-wide, so only for public components,
     never the customer's own modules).

   After a re-run, ask only about dependencies that are newly `unknown`.
   Don't ask for a module just because it appears in a log or in `indirect`.

   **Adding missing dependencies:** pass the extra files after the package:
   ```powershell
   .venv\Scripts\os2native package ..\projects\<Project>\INPUTS\App.osp ..\projects\<Project>\INPUTS\HiQRCoder_v2.oml ..\projects\<Project>\INPUTS\CSVUtilExtension_v1.xif --output ..\projects\<Project>\OUTPUT
   ```
   Keep every input (the package and extra `.oml`/`.xif` files) in the
   project's `INPUTS\`, never in `OUTPUT\`, because `OUTPUT\` is rebuilt.
   A dependency is satisfied when a module in
   `extraction\` or an extension in `extensions\` has its name.

   Also check:
   - **Extensions (`.xif`)** are parsed into `OUTPUT\extensions\<Name>\`:
     `extension.json` (actions, parameters, structures, entities, resources)
     and `source\` (the embedded C#/Java project). The logic is not
     translated, so port it from `source\` by hand. Anything in
     `extensions_not_extracted` failed to parse.
   - **Theme and UI modules:** if no module in the package has a theme with
     CSS (`interfaces\themes.json`, stylesheets in `evidence\assets\css\`),
     the theme module is probably missing. Ask for it, with the same two
     options as an `unknown` dependency.
   - Only add a name to `known-external.json` or `accepted_missing` after the
     user confirms. Never add one just to make the check pass.

4. **Review the inventory.** Read `OUTPUT\inventory.json` (`totals` and
   per-module `counts`), and use `OUTPUT\inventory-details.csv` / `.md` to
   find where any item is in Service Studio (UI flow, folder, owning screen,
   producer module). Counts in `inventory.json` are the row counts of that
   report (both come from `inventory_report.module_rows`), so change counting
   rules there only. They must match Service Studio exactly, which stage 2
   checks:
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

**Stage 1 is done when** the log has no ERROR (or the user accepted the
missing items), `check_dependencies.py` exits with 0, and every WARNING is
explained. Report the in-scope totals, then give the next step: verification
(stage 2), with what the user needs to do for it (open the in-scope modules
in Service Studio and start its MCP Server, or check the counts by hand).

## Stage 2: Verification

Goal: evidence that the extraction matches the modules in Service Studio,
and the user's approval of it. **Don't start stage 3 until the user has
approved the verification report.**

1. **Choose the method with the user.**
   - **Service Studio MCP** (preferred): the user opens the in-scope modules
     in Service Studio and starts its MCP Server (default port 41820).
   - **Manual**, if the MCP isn't available: the user compares
     `inventory-details.csv` with Service Studio themselves. Skip to step 4.

2. **Read each in-scope module through the MCP.** Read only (see
   [Rules for the MCP](#rules-for-the-mcp)). Load the `servicestudio-mcp-oml`
   skill if your client has it.
   - Tell the user to approve the request in Service Studio, then call
     `createSessionToken` once and reuse the token.
   - `listApps`: check that each module in scope is open (name and
     `moduleType`). Ask the user to open any that are missing. Verify only
     the modules in the scope (`project.json`), not their dependencies.
   - Per module, collect its elements: the inventory probe from the skill (one
     throw-probe, saves nothing), or `getScreenNames`, `getActionNames`
     (`server`, `client`, `service`), `getRoleNames`, `getDataModel`,
     `getStructures`, `getTimers`, `getSettings`, `getIntegrations`,
     `getClientVariables`, `getSessionVariables`, `getThemeNames`,
     `getImageNames`, `getResourceNames`.
   - Spot-check details on the elements the rebuild depends on most: entity
     attributes (type, length, mandatory, delete rule), screen input
     parameters, roles on screens, and a sample of server action signatures.

3. **Compare** with `inventory-details.csv` (and `extraction\<Module>\`) by
   name, not only by count. Classify every difference:

   | Difference | What to do |
   |---|---|
   | Extraction bug (an element missing or wrong in the output) | Fix os2native (see [Rules for changing code](#rules-for-changing-code)), re-run stage 1, verify again |
   | Counting rule (the item exists, Service Studio shows it elsewhere) | Explain it in the report; don't change rules just to match |
   | The module changed after it was exported | Ask the user which version is the truth; re-export or capture it, and re-run stage 1 |
   | Platform or built-in item | Note it as expected |

4. **Write `OUTPUT\verification\verification-report.md`** from
   [`docs/templates/verification-report.md`](docs/templates/verification-report.md):
   the run id, the method, a table per module (element type, extraction
   count, Service Studio count, status), every difference with its
   classification, and anything not verified with the reason.

5. **The user verifies.** Show the summary, point the user to the report
   and `inventory-details.csv`, and ask them to check it and approve. Wait
   for an explicit approval. Then set the report's status line to
   `Approved by the user on <date> for run <run id>`, with any differences
   they accepted. If they ask for changes, fix, re-run and ask again.
   Then give the next step: stage 3, starting with the stack decisions
   that are still open (`project_stack.py show`).

## Stage 3: Prototype

Goal: everything the converter needs to understand the app: deliverables,
a reviewed product specification per app, and a clickable prototype.
Start only when the verification report is approved for the current run.

1. **Record the complete stack.** `"stack"` in `project.json` holds every
   layer of `../TECH_STACK.md`, in groups: `backend`, `web`, `mobile`,
   `database`, `auth`, `platform` and `local` (the catalogue is in
   `scripts/target_stack.py`). Fill in every layer, then list what is open:
   ```powershell
   .venv\Scripts\python scripts\project_stack.py init ..\projects\<Project>
   .venv\Scripts\python scripts\project_stack.py show ..\projects\<Project>
   ```
   `init` writes the TECH_STACK.md default for each layer not set yet and
   `null` for each **open decision** (where TECH_STACK.md gives a choice); it
   never changes a value already set. Then ask the user about **all open
   decisions in one message**, with the options `show` prints, and record
   their answers:
   ```powershell
   .venv\Scripts\python scripts\project_stack.py set ..\projects\<Project> backend.language=dotnet web.ui=shadcn database.case_insensitive_text=false
   ```
   - Never decide one yourself. `backend.language` (`dotnet`: ASP.NET Core
     on .NET 10, or `node`: Node.js + TypeScript) must be set before
     `generate_specs.py` runs; the script stops until it is. With `node`,
     `backend.framework`, `backend.data_access` and `backend.consumed_apis`
     become open too.
   - The user may leave a decision open for later: it stays `null`, and the
     handover lists it with the iteration it blocks. A layer that doesn't
     apply (e.g. `mobile.offline` without a mobile app) is set to `"n/a"`.
   - `web.framework`, `mobile.framework` and `database.engine` can only be
     `react`, `expo` and `postgresql`: the generators support nothing else.
   - `database.case_insensitive_text` drives `generate_db_schema.py` (CITEXT
     columns, like OutSystems on SQL Server); `--citext`/`--no-citext`
     override it for one run.
   - Mobile apps are detected from the module (only they have a splash
     screen); if one is misdetected, list it in `stack.mobile.modules`.

   In the same message, unless `project.json` already has the answer, ask:
   **"Do you also need a portable product specification: a
   technology-neutral set of Markdown documents you can share with other
   teams or platforms?"** Record the answer in `project.json` as
   `"portable_spec": {"enabled": true}` (or `false`). Ask once per project;
   the user can change it later. If it's `false`, skip step 6.

2. **Generate the deliverables**
   ```powershell
   .venv\Scripts\python scripts\generate_db_schema.py ..\projects\<Project>\OUTPUT
   .venv\Scripts\python scripts\generate_sql_porting.py ..\projects\<Project>\OUTPUT
   .venv\Scripts\python scripts\generate_api_catalog.py ..\projects\<Project>\OUTPUT
   .venv\Scripts\python scripts\generate_specs.py ..\projects\<Project>\OUTPUT
   .venv\Scripts\python scripts\generate_site_properties.py ..\projects\<Project>\OUTPUT
   ```
   Output goes to `OUTPUT\deliverables\`, limited to the scope and the parts
   of other modules it uses. `configuration\site-properties.md` lists every
   site property with its target store and config key; it masks secrets
   (flagged, or named like `ClientSecret`/`APIKey`) and defaults that look
   personal (emails, recipients). Never print or copy those values yourself.

3. **Write the product specification** for each in-scope application that
   has screens: `OUTPUT\SPECS\<App>\product-specification.md`, with `<App>`
   the application name without spaces (e.g. `SalesPortal`).
   Apps without screens (core services, libraries) are described in the
   spec of the app that uses them. Follow
   [`docs/templates/product-specification.md`](docs/templates/product-specification.md)
   and the converter's intake contract (`../converter/contracts/os2native-intake-contract.md`, R13–R18a):
   - Write it from `extraction\<Module>\` and `deliverables\`, plus
     screenshots in `INPUTS\` if there are any. Cite the run id in the
     header and the source module for every statement.
   - Give stable IDs to features (`F1`; `A1` for admin/back-office web
     features), journeys (`J1`), business rules (`BR1`) and open questions
     (`Q1`). Keep IDs stable when you revise the spec.
   - Mark anything not read directly from the model **(inferred)**. List
     open questions with why each matters.
   - Never reproduce secret values, personal data or customer data; describe
     them. Example values are fictional.
   - The rebuild notes use the project's stack (`project.json`,
     `TECH_STACK.md`) and point to the deliverables (schema, SQL porting,
     specs, API catalog).
   - If the spec already exists, update it instead of rewriting it: keep the
     IDs, update the run id and numbers, and add a short "changes since"
     section.

4. **Document every action**: `OUTPUT\SPECS\<App>\action-catalogue.md`,
   from [`docs/templates/action-catalogue.md`](docs/templates/action-catalogue.md).
   It covers **every** server action, service action and client action of
   the in-scope modules: module-level client actions, and screen and block
   actions. For a large app, write one file per module in
   `SPECS\<App>\actions\<Module>.md` and keep `action-catalogue.md` as the
   index with the counts. For each action:
   - **Purpose:** the model's `description`. If there is none, write one
     sentence from the flow and mark it **(inferred)**.
   - **Inputs and outputs** (name, type, mandatory, meaning), who calls it,
     what it calls, the entities it reads and writes, the roles it checks,
     and the business rules (`BR`) it implements.
   - **Logic flow:** numbered steps in plain words, following the flow from
     its Start node along the edges (`flow.nodes`, `flow.edges` in
     `logic\server-actions.json`, `service-actions.json` and
     `client-actions.json`). How to read the nodes:
     - `If`: a branch, with **True** and **False** sub-steps (edge `type`).
     - `Switch`: one sub-step per condition.
     - `ForEach`: a loop over its `record_list`.
     - `DataSet`: an aggregate; name the source entities and filters.
     - `AdvancedQuery`: Advanced SQL; cite its `sql-porting` entry.
     - `ExecuteAction` / `ExecuteClientAction`: a call (`action_ref`).
     - `Assign`: summarise what it sets; list values only when they matter
       to the business.
     - `RaiseError`, `ErrorHandler`: the error path, with the message the
       user sees and whether the transaction is aborted.
     - `FeedbackMessage`, `WebDestination`, `Download`: what the user sees,
       or where they go.
     - `JavascriptNode`: summarise what the script does.
     - `Comment` nodes are notes; use them when they explain intent.
   - **Notes:** behaviour that must not be ported (a hard-coded credential,
     a test back door) and open questions (`Q`).
   - Never reproduce secret values or personal data found in assignments or
     comments; describe them.
   - Check the counts against `inventory.json` (`server_actions`,
     `service_actions`, `client_actions`, `screen_actions`) and say in the
     summary if any action is left out, and why.
   The product specification's features and business rules refer to these
   entries (`Module › Action`), instead of repeating the flows.

5. **Build the prototype**: `OUTPUT\SPECS\<App>\prototype\index.html` (plus
   `assets\`), a clickable, static HTML prototype of the app:
   - One view per in-scope screen (`interfaces\screens.json`), with the
     layout from the widget tree, the navigation from the navigation map in
     `deliverables\specs\<Module>\frontend-spec.md`, and the visibility rules
     per role (a role switcher when roles change what is shown).
   - The theme's colours and fonts (`interfaces\themes.json`,
     `evidence\assets\css\`) and the module's images (`evidence\assets\`).
     Mobile apps in a phone-sized frame.
   - Fictional data shaped by the entities and the static seed data. No
     back end, no real data, no secrets, and no network calls except pinned
     CDN libraries.
   - Label each view with its spec IDs (`F3`, `A2`) so the user and the BA
     can trace it.
   Open it in the browser to check it, then show it to the user with the
   product spec and the action catalogue. Their feedback goes into the spec (and the prototype)
   before stage 4. The next step you give: review the spec and prototype,
   then the conversion plan (stage 4).

6. **Portable product specification (only if the user wants one).** This is
   a technology-neutral set of Markdown documents about the product, which
   the user can share with other teams or platforms that know nothing about
   OutSystems or the current build. The answer to "Do you need one?" is in
   `project.json` (`portable_spec`, see step 1). If it is `false`, skip this
   step.

   Write it to `OUTPUT\SPECS\<App>\portable-spec\` from the templates in
   [`docs/templates/portable-spec/`](docs/templates/portable-spec/):

   | File | Written from |
   |---|---|
   | `README.md` | What the product is, the reading order, the IDs |
   | `PRD.md` | The product specification: goals, roles, features as user stories with acceptance criteria, journeys, business rules, quality requirements, out of scope, open questions |
   | `domain-model.md` | Entities, static entities and relationships (`data\`, `deliverables\db-schema\`), as business concepts and reference data |
   | `screens-and-navigation.md` | Screens, navigation and visibility by role (`interfaces\`, the prototype), described in words |
   | `behaviour.md` | **Every** entry of the action catalogue, as behaviour `B1`, `B2`…: trigger, inputs, steps, rules, errors, result |
   | `integrations.md` | Consumed and exposed APIs (`deliverables\api-catalog\`), by the external system's business role |
   | `configuration-and-jobs.md` | Site properties (`deliverables\configuration\site-properties.md`), timers and emails |
   | `glossary.md` | The business terms used |

   **Write it for a reader who has never seen OutSystems or the current app:**
   - **No platform terms.** Translate them:

     | OutSystems | In the portable specification |
     |---|---|
     | Entity, attribute | business concept (e.g. Order), field |
     | Static entity | reference data (a fixed list of values) |
     | Entity identifier, foreign key | reference to <concept> |
     | Aggregate, Advanced SQL | find / list records where … |
     | Server, service or client action, screen action | behaviour `B<n>` (what it does) |
     | Screen, block | screen, reusable part of a screen |
     | Site property | setting |
     | Timer, BPT process | scheduled job, background process |
     | Role, `Check<Role>Role` | role, "only <role> may …" |
     | `NullIdentifier()`, `NullDate()`, empty text | "no value" |
     | Exception handler, raise exception | "when something goes wrong", error |
     | Consumed or exposed REST/SOAP | external system, operation |
   - **No implementation details:** no module names, no library, plugin,
     Forge component, framework, language or database names, no colours,
     fonts, logos, icons or theme names, and no OutSystems UI patterns.
     Describe the layout in words ("a list with filters").
   - **External systems by their role** ("HR system", "corporate single
     sign-on provider"). Name a real product only when the new product
     must keep integrating with that specific system, and only after the
     user agrees; record it in `SPECS\<App>\portable-spec-allow.txt` with
     the reason.
   - **Same IDs** as the product specification (`F`, `A`, `J`, `BR`, `Q`),
     plus `B<n>` for behaviours. Record which action each `B<n>` comes from
     in `SPECS\<App>\portable-spec-trace.md` (next to the folder, never
     inside it), from the template.
   - **No secrets, personal data or customer records:** settings that are
     secrets are marked secret without a value; people are named by role.
   - **Markdown only** in `portable-spec\`.
   - **Don't link to the prototype**: it carries the current branding.

   Then check it, and fix everything it finds before showing it:
   ```powershell
   .venv\Scripts\python scripts\check_portable_spec.py ..\projects\<Project>\OUTPUT --app <App>
   ```
   It flags platform terms, module, theme, library and technology names,
   colours, fonts, emails and secret values, and anything missing: every
   ID of the product specification and every action of the action
   catalogue must be covered. It exits with 1 until the pack is clean.

   **The user reviews it** with the product specification. On approval,
   set the status line in `portable-spec\README.md` to
   `Approved on <date>` (no run id or tool name: the pack is shared
   outside). If the project is re-extracted, update it with the product
   specification and check it again.

## Stage 4: Conversion plan

Goal: one document that tells the converter what to build, in what order,
from which sources, with which decisions made. **The user approves it before
the converter starts.**

1. **Write `OUTPUT\SPECS\CONVERTER_HANDOVER.md`** (one per project) from
   [`docs/templates/CONVERTER_HANDOVER.md`](docs/templates/CONVERTER_HANDOVER.md):
   - **Source of truth:** project, apps in scope, package, run id, the
     verification report and its approval, and the paths of every input
     (product specs, prototypes, deliverables, extensions).
   - **Decisions:** the stack from `project.json`, `--citext`, and the other
     early decisions in `TECH_STACK.md` (database path, identity provider,
     binary data, OutSystems default values vs NULL), each decided or open.
   - **Site properties: all of them.** Copy the whole table from
     `deliverables\configuration\site-properties.md` into §4, one row per
     property, and check the count matches. Add for each the iteration that
     needs it and a note where its meaning changes in the rebuild (replaced
     by the identity provider, a dummy user or test back door not to port,
     different per environment). Say whether runtime values were collected
     from Service Center (`scripts\scrape_site_properties.py`); if not, it's
     work outside the slices. Never write secret values or masked defaults.
   - **Capability map:** OutSystems application/module → capability (named
     after spec features, not modules) → component (`backend`, `db`, `web`,
     `mobile`). The converter turns it into `projects/<Project>/converted/module-map.md`.
   - **Conversion plan:** iteration 0 (foundation: repository, database
     from `schema.sql`, identity, configuration, CI), then iterations of
     vertical slices by spec ID, in dependency and business-priority order.
     For each: features, screens, entities, server actions, integrations,
     what blocks it, and how it's accepted (stories pass QA, prototype
     parity).
   - **Work outside the slices:** Forge replacements, extensions to port,
     Advanced SQL marked `manual`, data migration and cut-over, runtime
     values to collect (site property values per environment, timer
     schedules, user-role assignments).
   - **Open questions** (which iteration each one blocks), **risks**, and
     the **readiness checklist** from [HOW_TO_EXTRACT.md](HOW_TO_EXTRACT.md#readiness-checklist),
     each line done, open, or accepted.

2. **The user reviews it.** Present a short summary (iterations, decisions
   still open, blockers) and ask for review. Revise until they approve
   explicitly; don't treat silence as approval. Then set the status line to
   `Approved by the user on <date> for run <run id>`.

3. **Hand over: tell the user the next steps.** Once the handover is
   approved, end the extractor's work with a **Next steps** message. Fill in
   every `<…>` from the project; the user should be able to copy each command
   and prompt as it is. Don't start the converter yourself, and don't write
   anything in `..\converter\` or `converted\`.

   First check that the handover is ready to pass:
   - its status line is approved for the run id in `OUTPUT\inventory.json`;
   - the verification report is approved for the same run;
   - `deliverables\configuration\site-properties.md` exists, and the
     handover's site-property table has the same count.

   If anything fails, say what is missing instead of giving the next steps.
   Then write the message in this shape:

   ````markdown
   ## Next steps: hand <Project> over to the converter

   **Ready to pass:**
   - handover approved for run `<run id>`
   - verification approved on <date>
   - <n> site properties listed (<n> secrets)

   1. **Open a new Claude Code session in the `converter` folder**
      (`<full path to converter>`), so it loads `AGENTS.md` and `CONVERTER_AGENT.md`.

   2. **Start the intake** with this prompt:
      > Follow AGENTS.md for project "<Project>". The handover is
      > `..\projects\<Project>\OUTPUT\SPECS\CONVERTER_HANDOVER.md` (approved for run <run id>).
      > Start with the intake.

      It runs the intake gate:
      ```powershell
      ..\extractor\.venv\Scripts\python tools\intake_check.py "..\projects\<Project>\OUTPUT" --osp "..\projects\<Project>\INPUTS\<package>.osp" --out "..\projects\<Project>\converted\intake\intake-check.md"
      ```

   3. **Approve STARTER's proposal.** It creates `projects\<Project>\converted\` (the
      folder named in the handover): `sources.md`, `module-map.md`,
      `iterations.md` and `handoff.md`.

   4. **Run each iteration**, starting with I0 (<what I0 covers>). Start the
      roles in order, and accept each iteration before the next one:
      > Follow CONVERTER_AGENT.md and agents/AGENTS_BA.md for <Project>, iteration I0.

      Then `AGENTS_ARCHITECT.md`, `AGENTS_DEV.md` and `AGENTS_QA.md` with the
      same prompt.

   **Before or during the conversion** (from the handover):
   - **Open decisions:** <each, and the iteration it blocks>
   - **Runtime values to collect:** <site property values per environment,
     timer schedules, role assignments>, needed by <iteration>
   - **Open questions blocking I0/I1:** <Q-ids>

   **Portable product specification** (only if the user asked for one):
   `..\projects\<Project>\OUTPUT\SPECS\<App>\portable-spec\`, approved on <date>.
   Share that folder as it is; `portable-spec-trace.md` and
   `portable-spec-allow.txt` next to it are not for sharing.

   **If you re-extract <Project>,** come back to the extractor first: the
   approvals are tied to run `<run id>`.
   ````

   Rules for filling it in:
   - Quote every path that contains a space (e.g. `"..\projects\My Project\OUTPUT"`).
   - `<package>` is the `.osp`/`.oap` in `INPUTS\`.
   - Leave out a bullet under "Before or during" when there is nothing for
     it, rather than writing "none".

**After an approved handover:** if the project is re-extracted, the
approvals no longer match the run id. Re-verify the changed modules (stage
2), update the spec and prototype (stage 3), update the handover with a
"changes since" note, and ask the user to approve it again.

## When INPUTS are not enough: Service Studio MCP

Use this when stage 1 shows that the export is incomplete: an `unknown`
dependency, a missing theme module, an ERROR for a module, or when stage 2
finds that a module changed after it was exported. **Don't decide for the user. Ask which option they want:**

1. **Upload more files to `INPUTS`**: the `.oml` from Service Studio, the
   `.xif` from Integration Studio, or a fresh `.osp`/`.oap`. Then re-run stage 1.
2. **Open the module in Service Studio** so you can capture it through the
   Service Studio MCP Server (`servicestudio` in `.mcp.json`).

Option 2 needs the user to have Service Studio running with the module open
and the MCP Server started (Service Studio's MCP Server dialog, default port
41820). It doesn't work for extensions (`.xif`), which only Integration Studio
opens: those always need option 1. Then:

1. Load the `servicestudio-mcp-oml` skill if your client has it; it describes
   the tools and their argument shapes.
2. Tell the user to approve the request in Service Studio, then call
   `createSessionToken` once and reuse the token for the whole session.
3. `listApps` and check that the module the user opened is the one that's
   missing (name and `moduleType`). If two open modules could share a name or
   come from different environments, ask the user which one to use.
4. Make one read of it, e.g. `getDataModel` with `includeJson: "Never"`. That
   makes Service Studio save a snapshot to
   `%TEMP%\ServiceStudio.MCPServer\<runId>-in.oml`.
5. Copy the snapshot into the project:
   ```powershell
   .venv\Scripts\python scripts\collect_mcp_snapshot.py ..\projects\<Project>\INPUTS --module <Module>
   ```
   It writes `INPUTS\mcp\<Module>.oml` and `<Module>.capture.json` (where
   it came from).
6. Re-run stage 1 with `INPUTS\mcp\<Module>.oml` added.
   Tell the user the module came from Service Studio rather than an export.

### Rules for the MCP

These hold in every stage (gap filling and verification):

- **Read only.** Use `listApps`, the `get*` tools, `runQuery`,
  `getValidationMessages`, and `applyModelApiCode` only as a throw-probe
  (`throw new Exception("PROBE:…")`, which saves nothing). Never call
  `applyModelApiCode` to change the module, nor `omlMerge`, `omlReset`,
  `omlRefreshReferences` or `omlPublish`: the module belongs to the user and
  the extraction must reflect it as it is.
- Restarting Service Studio clears `%TEMP%\ServiceStudio.MCPServer`, so copy
  the snapshot before that.
- In verification (stage 2), report differences rather than changing
  counting rules to match.
- The MCP only sees design-time data. Runtime values (effective site
  property values, timer schedules set in Service Center, user-role
  assignments) still need ServiceCenter (`scripts/scrape_site_properties.py`).
- If the MCP isn't reachable (no tools, or connection refused on port 41820),
  say so and fall back to option 1. Don't install or configure anything in
  Service Studio yourself.

## Rules for changing code

- **Confirm against the XML.** Before changing a worker, find the real
  element in a converted module (`..\projects\<Project>\OUTPUT\_xml\*.xml`)
  and look up the tag in `docs/oml-xml-knowledge-base.md`.
- **Add a test with every fix or feature.** Build small XML snippets by hand
  (see `tests/test_advanced_sql.py`, `tests/test_inventory_accuracy.py`).
  Never commit customer XML or output as fixtures.
- **Run the full suite before you finish.** `.venv\Scripts\python -m pytest`.
  The `test_pipeline_golden.py` tests only run when `OS2NATIVE_GOLDEN_XML`
  points at a converted module XML.
- **Re-run on real data.** After changing a worker or the orchestrator,
  re-run the extraction (stage 1) on a real package and compare `inventory.json` before and after.
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

## Working folders

Project data lives outside the tool's folder, in `../projects/<Project>/`
(next to `extractor/` and `converter/`), so customer packages and output stay
apart from the code.

- **`../projects/<Project>/INPUTS/`** holds what the user exports from
  OutSystems (`.osp`, `.oap`, extra `.oml`/`.xif`, screenshots). Never modify
  or delete files there. The only thing you may add is
  `INPUTS/mcp/<Module>.oml` (+ `.capture.json`) via
  `collect_mcp_snapshot.py`, after the user chose the Service Studio option.
- **`../projects/<Project>/OUTPUT/`** is where every command writes for that
  project, and is rebuilt on each run: read it freely, but don't hand-edit
  generated files there (a re-run overwrites them). One project per `OUTPUT`:
  `extraction/` is not cleared between runs, so two projects in one folder
  mix their modules.
- **`../projects/<Project>/project.json`** holds the user's decisions for the
  project: `scope` (apps being migrated), `accepted_missing` (missing
  dependencies they said aren't needed, with the reason), `stack` (every
  layer of the target stack, see stage 3) and `portable_spec` (whether they
  want a portable product specification). Write it only with the user's
  answers, except the TECH_STACK.md defaults that `project_stack.py init`
  fills in; keep their wording for reasons, and never delete it.
- **`../projects/<Project>/OUTPUT/verification/`** holds the stage 2 report
  for the current run. You write it; a re-extraction makes it out of date.
- **`../projects/<Project>/OUTPUT/SPECS/`** is the exception: it holds
  hand-reviewed documents that are kept across runs: per app,
  `SPECS/<App>/product-specification.md`, `SPECS/<App>/action-catalogue.md`,
  `SPECS/<App>/prototype/` and, if requested, `SPECS/<App>/portable-spec/`
  with its trace and allow files (stage 3), and for the project `SPECS/CONVERTER_HANDOVER.md` (stage 4). No
  command writes or deletes them: update them by editing, and keep
  "(inferred)" markers, IDs and the open-questions list accurate. Don't
  delete them when cleaning `OUTPUT`.
- **Inside the repo**, `tmp/` and `.venv/` are disposable. An `OUTPUT/` folder
  in the repo only appears when `--output` was left out; it's git-ignored and
  safe to delete.

## Known gaps

- Advanced SQL is extracted as text in OutSystems/SQL Server notation
  (`{Entity}.[Attr]`, `@Param`, `GETDATE()`, `DATEADD`), not translated to
  PostgreSQL. `generate_sql_porting.py` only lists what each query needs.
- References to `(System)` entities (e.g. `User`) get no FK in `schema.sql`;
  they're listed in `cross-module-fks.md`.
- `.xif` extensions are parsed (signatures and embedded source code), but
  their logic is not translated.
- Runtime-only data (effective site property values, user-role assignments)
  needs ServiceCenter access.
- No code generation. The specs describe the target code for the stack in
  `project.json` (types, signatures, routes, data hooks); the converter
  writes it in stage 5.
- Verification (stage 2) through the MCP is done by the agent with the MCP
  tools; there is no script that compares the MCP's element lists with
  `inventory-details.csv` yet.
