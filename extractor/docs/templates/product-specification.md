# <App>: product specification

<!-- Stage 3 of STAGES.md. Written by the extractor to
     projects/<Project>/OUTPUT/SPECS/<App>/product-specification.md, one per
     in-scope application with screens. It is the converter's primary input
     (converter/contracts/os2native-intake-contract.md, R13–R18).
     Replace every <placeholder>; delete these comments and unused sections. -->

> **Source:** derived from the os2native extraction run `<run id>` of
> `<package>` (+ <extra inputs>): <n> modules in scope, <n> extensions,
> <n> errors, <n> warnings, in `projects/<Project>/OUTPUT`. Verified against
> Service Studio on <date> (`OUTPUT/verification/verification-report.md`).
> <Screenshots used, if any.>
>
> **How to read it:** each statement cites the module it comes from (e.g.
> *SP_Orders_CS*). Anything not stated directly in the model is marked
> **(inferred)**. Unresolved points are in [§13 Open questions](#13-open-questions).
> All example values are fictional. Secrets, passwords and personal data
> found in the model are described, never reproduced.

## 1. Product overview

<What the app is for, who uses it, the problem it solves. Goals (inferred
from the implemented features).>

## 2. Solution structure

<OutSystems applications and modules in scope, their kind (Reactive, Mobile,
Service, Library), what each contains, and the external dependencies
(platform, Forge, extensions) with what the app uses from them.>

## 3. Users and roles

| Role | Module | Who has it | What it allows |
|---|---|---|---|

## 4. Features

<One subsection per feature, grouped by app (e.g. 4.1 web, 4.2 mobile).
IDs: F1… for end-user features, A1… for admin/back-office features.>

#### F1. <Feature name>

- **Screens:** <Screen> (*Module*), …
- **Roles:** <roles that can use it>
- **Data:** <entities read/written>
- **Logic:** <server/client actions and what they do>
- **Integrations:** <external systems called>
- **Rules:** BR<n>, …
- **Notes:** <edge cases, (inferred) items>

## 5. Key user journeys

#### J1. <Journey>

<Steps across screens and features, citing F/A IDs.>

## 6. Screen inventory

| Screen | Module | Flow | Feature | Roles | Prototype view |
|---|---|---|---|---|---|

## 7. Business rules

| ID | Rule | Source (module › action/screen) | Feature |
|---|---|---|---|
| BR1 | | | |

Each server and client action, with its purpose and logic flow, is
documented in [`action-catalogue.md`](action-catalogue.md); cite its entries
(`Module › Action`) here and in the features instead of repeating flows.

## 8. Data model

<Main entities and relationships, static entities and their values (no
customer data), delete rules that matter, (System) references. Point to
`deliverables/db-schema/` for the full schema.>

## 9. Integrations

| System | Direction | Module | Operations used | Auth | Notes |
|---|---|---|---|---|---|

## 10. Configuration and background jobs

<Site properties (names and purpose, never secret values), timers (what they
do; schedules come from Service Center), processes (BPT), emails.>

## 11. Non-functional requirements (derived)

<Security, audit, performance, availability, localisation, accessibility:
what the model implies, marked (inferred) where it does.>

## 12. Native rebuild notes (<stack from project.json>)

| OutSystems piece | Target (TECH_STACK.md) | Source in deliverables | Notes |
|---|---|---|---|
| Entities | EF Core / PostgreSQL | `deliverables/db-schema/` | |
| Advanced SQL | Dapper / SQL porting checklist | `deliverables/sql-porting/` | <n> manual |
| Server actions | <back end> services | `deliverables/specs/<Module>/backend-spec.md` | |
| Screens | React / Expo | `deliverables/specs/<Module>/frontend-spec.md` | |
| Consumed APIs | <client library> | `deliverables/api-catalog/` | |
| Extensions | Ported from `extensions/<Name>/source/` | | |
| Forge / plugins | <native replacement> | | |

<Security defects found in the model that must not be ported (hard-coded
credentials, secrets in logs, test back doors), described without values.>

## 13. Open questions

| ID | Question | Why it matters | Blocks |
|---|---|---|---|
| Q1 | | | F<n> |

## 14. Changes since <previous run or version>

<Only when updating an existing spec: what changed and why.>
