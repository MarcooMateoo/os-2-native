# <Project>: converter handover

<!-- Stage 4 of STAGES.md. Written by the extractor to
     projects/<Project>/OUTPUT/SPECS/CONVERTER_HANDOVER.md, one per project.
     It is the conversion plan and the converter's entry point for stage 5.
     Replace every <placeholder>; delete these comments. -->

- **Status:** Draft, waiting for the user's review
  <!-- After approval: Approved by the user on <YYYY-MM-DD> for run <run id> -->
- **Extraction run:** `<run id>`; verification approved on <date>
  (`OUTPUT/verification/verification-report.md`)
- **Converted output:** `projects/<Project>/converted/` (written by the converter)
- **Written by:** <agent>, <date>

## 1. Summary

<What is being converted, for whom, and the shape of the plan in 3–5
sentences: number of iterations, what comes first, what is blocked.>

## 2. Source of truth

| Input | Path (under `projects/<Project>/`) |
|---|---|
| Package | `INPUTS/<package>` (+ <extra inputs>, + `INPUTS/mcp/` captures: <list or none>) |
| Scope and decisions | `project.json` |
| Inventory | `OUTPUT/inventory.json`, `OUTPUT/inventory-details.md` |
| Verification | `OUTPUT/verification/verification-report.md` |
| Product specification | `OUTPUT/SPECS/<App>/product-specification.md` (one per app) |
| Action catalogue | `OUTPUT/SPECS/<App>/action-catalogue.md`: every server and client action, purpose and logic flow |
| Prototype | `OUTPUT/SPECS/<App>/prototype/index.html` |
| Database | `OUTPUT/deliverables/db-schema/` (`schema.sql`, `seed-data.sql`, `cross-module-fks.md`, `client-entities.md`) |
| Advanced SQL | `OUTPUT/deliverables/sql-porting/sql-porting.md` |
| APIs | `OUTPUT/deliverables/api-catalog/` |
| Per-module specs | `OUTPUT/deliverables/specs/` |
| Site properties | `OUTPUT/deliverables/configuration/site-properties.md` (copied in §4) |
| Extensions | `OUTPUT/extensions/<Name>/source/` |
| Module detail | `OUTPUT/extraction/<Module>/` |

## 3. Target stack and decisions

The complete stack is `stack` in `project.json` (`scripts/project_stack.py
show`); copy its table here, one row per layer, so the converter reads the
stack in one place.

| Layer (`group.key`) | Choice | Status |
|---|---|---|
| `backend.language` | <dotnet / node> | Decided |
| `backend.data_access` | | Decided / Default / Open |
| `web.ui` | | |
| `database.case_insensitive_text` | | |
| `database.outsystems_defaults` | | |
| `database.binary_data` | | |
| `auth.identity_provider` | | |
| `platform.iac`, `platform.cicd` | | |
| <every other layer> | | |

**Open decisions:** <each `null` layer, the options, and the iteration it
blocks (e.g. `auth.identity_provider` blocks I0).>

Other decisions not in the stack: database path (new PostgreSQL schema vs
keeping the live SQL Server database, `TECH_STACK.md` Key decisions), <…>.

## 4. Configuration: site properties

Every site property of the modules in the deliverables, from
`OUTPUT/deliverables/configuration/site-properties.md`
(`scripts/generate_site_properties.py`). Copy the **whole** table, one row per
property; the count must match the generated file. Platform properties
(`TenantId`, `TenantName`) are not listed. Never write a secret's value or a
masked default here.

- **Site properties:** <n> in <n> modules (<n> in scope, <n> from used dependencies)
- **Secrets:** <n> → Secrets Manager (<n> recognised by name only: confirm with the owner)
- **Runtime values from Service Center:** <collected for <n> / not collected yet: needed by I<n>>

| Module | Scope | Name | Type | Description | Default | Secret | Target | Config key | Runtime value | Needed by | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| <Module> | in scope / used dependency | `<Name>` | <type> | <description> | <default / (set, not shown) / -> | yes / no | Secrets Manager / SSM Parameter Store | `<Module>__<Name>` | collected / not collected | I<n> | <e.g. drop: test back door; per-environment; replace with IdP setting> |

Add **Needed by** (the iteration whose stories read it) and **Notes** for
properties that change meaning in the rebuild: replaced by the identity
provider, a test back door or dummy user not to port (open question),
different per environment, or no longer needed.

## 5. Capability map

<The converter turns this into `projects/<Project>/converted/module-map.md`. Name
capabilities after spec features, never after OutSystems modules.>

| OutSystems application / module | Capability | Component | Spec IDs |
|---|---|---|---|
| <Module> | <capability> | backend / db / web / mobile | F1, BR3 |

## 6. Conversion plan

Iterations are vertical slices: each one ends with working, tested features
the user can review against the prototype.

### I0. Foundation

- Repository layout for `projects/<Project>/converted/` (`converter/docs/repository-structure.md`)
- Database: first migration from `schema.sql`, seeds from `seed-data.sql`
- Identity and roles, configuration and secrets (every site property in §4 has a config key and store), CI
- **Done when:** the app starts locally, signs a user in and reads one table.

### I1. <Slice name>

- **Features:** F<n>, A<n>
- **Screens:** <screens>
- **Data:** <entities>
- **Logic and integrations:** <server actions, external systems>
- **Depends on:** I0, <Q-ids that must be answered>
- **Done when:** the stories for these features pass QA and the screens match the prototype.

<I2…In in priority and dependency order.>

## 7. Work outside the slices

| Item | Detail | Needed by | Owner |
|---|---|---|---|
| Forge replacements | <component → library> | I<n> | |
| Extensions to port | <extension → target> | I<n> | |
| Advanced SQL marked `manual` | <query> | I<n> | |
| Data migration and cut-over | <approach> | before go-live | |
| Runtime values | site property values per environment (§4), timer schedules, user-role assignments (Service Center) | I<n> | |

## 8. Open questions

| ID | Question (spec ID) | Blocks | Default if unanswered |
|---|---|---|---|
| Q1 | | I<n> | |

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|

## 10. Readiness checklist

<The checklist from extractor/HOW_TO_EXTRACT.md, each line marked Done,
Open (with owner) or Accepted by the user.>

## 11. User review

<What the user asked to change, and their approval in their own words.>

## 12. Changes since the last approved version

<Only after a re-extraction or a revised plan.>
