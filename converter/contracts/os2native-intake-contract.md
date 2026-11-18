# os2native → converter intake contract

What the converter needs from **os2native** for each project before any code
is written. os2native owns producing it; the converter manager owns checking
it (`tools/intake_check.py`) and raising change requests when it falls short.

```
projects/<Project>/INPUTS ──os2native──▶ projects/<Project>/OUTPUT ──intake gate──▶ projects/<Project>/converted/
                          (stages 1–4)    (generated, verification,  (this contract,
                                           SPECS, CONVERTER_HANDOVER) stage 5 starts)
```

The stages are in [`../../STAGES.md`](../../STAGES.md). The entry point is
`OUTPUT/SPECS/CONVERTER_HANDOVER.md`, approved by the user.

The converter **reads** `projects/<Project>/OUTPUT`; it never copies generated
files into `converted/` or `converter/` and never edits them. Its own output
goes to `projects/<Project>/converted/`. Project documents in
`projects/<Project>/converted/project-documents/` cite them by path and run id.

## 1. One extraction, one truth

| # | Requirement | Why | Checked by |
|---|---|---|---|
| R1 | Every `_xml/<Module>.xml` was decoded from the `.oml` of **this** run (XML not older than its `.oml`). | os2native reuses existing XML; a stale XML silently describes an older module version. | `stale-xml` |
| R2 | `extraction/` contains exactly the modules in `inventory.json`. No leftovers from earlier runs. | Generators and the dependency check read every folder in `extraction/`. | `orphan-modules` |
| R3 | No second OUTPUT tree nested inside `OUTPUT/` (e.g. `OUTPUT/<App>/inventory.json`). | Two versions of the truth; specs end up citing the old one. | `nested-output` |
| R4 | `inventory.json` has 0 `failed_modules`, 0 `extensions_not_extracted`, 0 errors. | Missing items are missing features. | `inventory`, `issues-log` |
| R5 | Every warning in `extraction-issues.log` has a disposition (fixed, accepted, or an open question in the product spec). | Warnings are usually real gaps (unscheduled timers, unresolved descriptions). | `issues-log` (manual) |

## 2. Complete dependencies

| # | Requirement | Checked by |
|---|---|---|
| R6 | `dependency-check.json` is current and has **no `unknown`** dependencies. Each one is either extracted (its `.oml`/`.xif` added to INPUTS) or confirmed by the user as platform/Forge in `known-external.json`. | `dependencies` |
| R7 | Each `forge`/`platform` dependency the app uses has a named native replacement in the product spec's rebuild notes. | manual |

## 3. Module packages are complete and placed

| # | Requirement | Checked by |
|---|---|---|
| R8 | Each `extraction/<Module>/` holds the files in `REQUIRED_MODULE_FILES` (`tools/intake_check.py`), following `extractor/docs/module-output-package-contract.md`. | `module-files` |
| R9 | `package.json` gives the module's **application** and **module kind** (Mobile, ReactiveLibrary, Service, …). The converter organises code by application and capability, so it needs this map. Source: the `.osp`'s `Manifest.xml` (`<Application>` → `<Module ESpaceType=…>`). | `app-mapping` |

## 4. Deliverables match the extraction

| # | Requirement | Checked by |
|---|---|---|
| R10 | `deliverables/db-schema/`, `api-catalog/` and `specs/` were generated **after** the current `inventory.json`, from the same module set (no specs for removed modules, none missing). | `deliverables` |
| R11 | `schema.sql` parses as PostgreSQL (os2native checks it with `pglast`). | os2native tests |
| R12 | Advanced SQL porting work is listed per query (`deliverables/sql-porting/`, when available). | manual |

## 5. Reviewed product specification per application

`OUTPUT/SPECS/<App>/product-specification.md` is the converter's primary
input. It must:

| # | Requirement |
|---|---|
| R13 | Cite the extraction it was written from (run id or date) and match that extraction's numbers (modules, screens, roles, timers…). **Re-verify it after every re-extraction.** (`product-spec` warns when the spec is older than `inventory.json`.) |
| R14 | Give every feature, journey and rule a stable ID (`F1`, `A1`, `J1`, `BR1`, `Q1`). Stories, code and tests trace back to these IDs. |
| R15 | Cite the source module for every statement, and mark everything not read directly from the model as **(inferred)**. |
| R16 | List open questions with why each matters. Before a story that depends on a question is built, the question is answered or the user accepts a default. |
| R17 | Contain no secret values, personal data or real customer data (describe, never reproduce). |
| R18 | Include rebuild notes: target component per OutSystems piece, SQL-dialect changes, native replacements for plugins/Forge. |
| R18a | Come with `SPECS/<App>/action-catalogue.md`: **every** server, service and client action (including screen and block actions) of the in-scope modules, with its purpose and its logic flow (branches, loops, calls, data, error paths, roles). Its counts match `inventory.json`. |

A clickable prototype (`SPECS/<App>/prototype/`) is produced in stage 3 for
every app with screens; QA uses it, and the screenshots in `INPUTS/`, for
visual parity.

## 6. Verification and handover (stages 2 and 4)

| # | Requirement | Checked by |
|---|---|---|
| R19 | `OUTPUT/verification/verification-report.md` exists, covers every in-scope module, and its status reads `Approved by the user on <date> for run <run id>` with the run id of the current `inventory.json`. | manual |
| R20 | `OUTPUT/SPECS/CONVERTER_HANDOVER.md` exists and its status reads `Approved by the user on <date> for run <run id>` with the same run id. | manual |
| R21 | The handover's Decisions match `project.json` (`stack`) and `TECH_STACK.md`, and every iteration lists its spec IDs and blockers. | manual |
| R22 | The handover lists **every site property** (§4), as many as `OUTPUT/deliverables/configuration/site-properties.md`, each with its target store (Secrets Manager or Parameter Store) and config key, and no secret value. | manual |

`tools/intake_check.py` doesn't check R18a or R19–R22 yet; the converter manager
checks them by hand and records the result in the intake review.

## Gate

```powershell
..\extractor\.venv\Scripts\python tools\intake_check.py ..\projects\<Project>\OUTPUT --osp ..\projects\<Project>\INPUTS\<package>.osp --out "..\projects\<Project>\converted\intake\intake-check.md"
```

- **Exit 1 / any BLOCKER** → do not start BA/Architect/Dev work on the affected
  modules. Raise change requests to os2native in `projects/<Project>/converted/intake/intake-review.md`.
- **Exit 0** → the converter manager reviews the warnings, checks R18a and R19–R22,
  records dispositions in the intake review, and hands the project to
  STARTER (`agents/AGENTS_STARTER.md`), then the conversion loop
  (`AGENTS.md`).

Re-run the gate after every os2native run on that project.
