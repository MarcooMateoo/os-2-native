# CONVERTER_AGENT.md

Global instructions for every role agent in **converter**: **BA**,
**ARCHITECT**, **DEV** and **QA** (and STARTER where they apply). Read this
before your role guide in [`agents/`](agents/README.md).

The role guides are generic; this file makes them specific to rebuilding
OutSystems apps. The order of precedence is:

1. The user's latest explicit decision.
2. [`../STAGES.md`](../STAGES.md) and [`AGENTS.md`](AGENTS.md) (the stage 5
   flow, intake and hard rules).
3. **This file.**
4. Your role guide (`agents/AGENTS_<ROLE>.md`).

If two of them conflict in a way that matters, stop and ask the user. Don't
pick one silently.

## 1. Start every session the same way

1. **Confirm the project, the app and your role** with the user. Never
   assume them from an earlier session.
2. **Read the current state:** `projects/<Project>/converted/project-documents/handoff.md`,
   `iterations.md` and `sources.md`.
3. **Check the source is still current.** The run id in `sources.md` must
   match `projects/<Project>/OUTPUT/inventory.json`, and the handover must be
   approved for that run. If it isn't, stop: the project needs a new intake
   (`AGENTS.md` §5.0).
4. **Tell the user** the iteration, its status, your role's scope and edit
   permissions (from `handoff.md`), and what you will do first. Then wait
   for your role's gate as the role guide describes.

## 2. Sources of truth

Everything comes from the os2native output for the project, under
`projects/<Project>/OUTPUT/`. **Cite by path and run id; never copy those
files into `projects/<Project>/converted/` or `converter/`.** Everything the
roles produce (documents, code, tests, QA records) goes in
`projects/<Project>/converted/`, and nowhere else in `projects/`.

| Source | Use it for |
|---|---|
| `SPECS/CONVERTER_HANDOVER.md` | The plan (iterations), decisions, target stack, capability map, **all site properties** (§4), open questions, work outside the slices |
| `SPECS/<App>/product-specification.md` | What the app does: features `F`/`A`, journeys `J`, business rules `BR`, open questions `Q` |
| `SPECS/<App>/action-catalogue.md` | Every server, service and client action: purpose, inputs/outputs, logic flow (branches, loops, calls, data, error paths, roles) |
| `SPECS/<App>/prototype/` | Screens, navigation and visibility by role: the reference for UI parity |
| `deliverables/db-schema/` | Tables, keys, delete rules, indexes, seed data, cross-module and `(System)` references, mobile client entities |
| `deliverables/sql-porting/` | What each Advanced SQL query needs on PostgreSQL; `manual` queries |
| `deliverables/specs/<Module>/` | Entity types, action signatures, API clients, screens, data hooks, state flows |
| `deliverables/api-catalog/` | Consumed and exposed REST/SOAP APIs, OpenAPI stubs for external systems |
| `deliverables/configuration/site-properties.md` | Site properties with target store and config key (copied into the handover §4) |
| `extraction/<Module>/` | The detail behind all of the above: flows, widgets, expressions, roles |
| `extensions/<Name>/source/` | Extension code to port |
| `verification/verification-report.md` | Known differences from Service Studio |

When sources disagree, don't choose. The reviewed product spec states the
intent, and the extraction states what OutSystems actually does. Raise the
conflict (§8) and wait.

Anything not in these sources is an **assumption**. Label it as one in
stories, architecture and code comments, and confirm it with the user.

## 3. Target stack

- The stack is fixed per project: `stack` in `projects/<Project>/project.json`,
  copied into the handover §3, following [`../TECH_STACK.md`](../TECH_STACK.md).
  Use exactly those choices: language, data access, UI library, state, forms,
  identity provider, hosting, IaC, CI/CD.
- An **open decision** (`null` in `project.json`) is never decided by an
  agent. If your work needs it, ask the user; record the answer in
  `iterations.md` and ask the extractor owner to record it in
  `project.json`. The ARCHITECT may propose options with trade-offs.
- Changing a decided layer is a proposal to the user, never a silent change.

## 4. Traceability

- **IDs:** spec IDs (`F3`, `A2`, `J1`, `BR7`, `Q4`), iterations (`I0`,
  `I1`…), stories (`I2-S3`: iteration 2, story 3), converter questions
  (`CQ1`…) and change requests to the extractor (`OS2N-1`…).
- Every story cites its spec IDs. Every architecture decision cites the
  stories or spec IDs it serves. Every test (automated and QA case) cites
  the story and business rule it checks. Commit messages and PRs cite the
  story IDs.
- `iterations.md` lists, per iteration, the spec IDs it covers. A feature
  is done only when all of its business rules have a passing test.

## 5. OutSystems behaviour to preserve

The rebuild must behave like the OutSystems app unless the user decides
otherwise. These are the usual traps:

| OutSystems | In the rebuild |
|---|---|
| **Roles** checked on screens and in server actions | Enforce them **on the server** for every endpoint. Hiding a button is not security. Role names come from the spec; users and roles come from the identity provider (`auth.identity_provider`). |
| **Null values**: empty Text is `''`, empty identifier is `0`, empty date is `1900-01-01` | Follow `database.outsystems_defaults` (handover §3): converted to `NULL` or kept. Code, queries and tests must agree with that choice. |
| **Text comparisons** are case-insensitive (SQL Server) | Follow `database.case_insensitive_text`. If it's `false`, searches and uniqueness checks that relied on case-insensitivity need explicit handling (`ILIKE`, `lower()`), listed by the BA as acceptance criteria. |
| **DateTime** is stored without a time zone | Confirm the time zone with the user before the first date-related story; store UTC and convert at the edges unless decided otherwise. |
| **Identifiers** (Auto Number) | `BIGINT` identity columns as in `schema.sql`; keep the delete rules (`RESTRICT`/`CASCADE`). |
| **Static entities** | Seed data from `seed-data.sql` (reference data only), optionally mirrored as enums. Records are referenced by Id, so keep the Ids. |
| **Aggregates and Data Actions** | API endpoints with the same filters, sorting and paging. Never expose tables directly to the client. |
| **Advanced SQL** | Port it with the `sql-porting` checklist. Always parameterise; `Expand Inline` parameters were an SQL injection risk, so use an allow-list or query builder, never string concatenation. |
| **Server and service actions** | Service methods with the signatures in `deliverables/specs/`. Exception handlers become explicit error handling with the same user-visible messages. |
| **Timers and BPT processes** | Jobs on the stack's job runner (`backend.jobs`). Schedules come from Service Center, not the model, and must be collected. |
| **Site properties** | Configuration (§6), never constants in code. |
| **Extensions** | Port from `extensions/<Name>/source/` to the target language, dropping the OutSystems runtime types, or replace with a library named in the handover. |
| **Forge and plugins** | The replacement named in the handover. Don't pick another without the user. |
| **Consumed APIs** | Typed clients (`backend.consumed_apis`). Base URLs and credentials come from configuration. |
| **Exposed APIs** | Keep paths, payloads and status codes, so callers don't need to change. |
| **Mobile client entities and offline** | The local store (`mobile.offline`), plus the sync endpoints the handover plans. |
| **Multilingual locales** | Only if `web.i18n` says so. Keep the texts' meaning; don't rewrite labels. |
| **Validation messages** | The same messages, enforced on both client (zod) and server. |

When you find OutSystems behaviour that looks like a defect (a hard-coded
password, a test back door, a dummy user, a secret in a log), **don't port
it**. Record it as a question or a security finding and let the user decide.

## 6. Configuration and site properties

- The handover §4 lists **every site property**, with its target store and
  config key (`<Module>__<Name>`; .NET reads it as `<Module>:<Name>`). Every
  property the iteration's code reads must use that key, unless the
  ARCHITECT maps keys to capability names; the mapping then goes in
  `architecture.md`.
- **Secrets** (handover column *Secret* = yes) live in Secrets Manager, or
  locally in an ignored file or user secrets. Never put them in source, tests,
  seeds, fixtures, logs, client bundles or documentation. Masked defaults
  (`(set, not shown)`) are never looked up and copied from the extraction.
- **Non-secret defaults** become the development defaults. The values for
  each environment come from Service Center (handover: runtime values). Until
  they are collected, they are placeholders, and `handoff.md` says so.
- Properties the handover marks as not to port (dummy users, test switches,
  back doors) are left out, and the decision is cited.
- Each app keeps a configuration table (key, store, used by, default for
  development) in its README or `docs/`, with a placeholder-only example
  file (`appsettings.Development.example.json` or `.env.example`).

## 7. Security and data

- **No customer data in the converted code.** Seeds hold static/reference
  data only. Never copy XML, inventory files, screenshots, personal data or
  real records from `INPUTS/` or `OUTPUT/` into `converted/` or `converter/`.
  Test data is fictional.
- **Don't copy security defects** (§5). Fixes the spec calls for are in
  scope; other security improvements are proposals to the user.
- **Least privilege:** server-side role checks, scoped tokens, no privileged
  credentials in web or mobile apps.
- Don't log secrets, tokens, personal data or full request/response bodies.
- If a secret is exposed (in a file, a log or a commit), tell the user at
  once and recommend rotating it.

## 8. When you find a problem

| Finding | What to do |
|---|---|
| A defect in the new app | DEV fixes it in the same iteration; QA re-tests. |
| The spec, a deliverable or the extraction is wrong or incomplete | Propose a change request `OS2N-n` (what, evidence path, stories blocked); the converter manager records it in `projects/<Project>/converted/intake/intake-review.md`. The affected stories wait. Never edit `OUTPUT/`, `INPUTS/`, `project.json` or `extractor/`. |
| A missing decision or unclear behaviour | Use an existing spec question `Qn`, or add `CQn` to `iterations.md` with why it matters and what it blocks. Ask the user; record the answer in `iterations.md` and the affected stories. |
| The plan needs to change (order, scope, a new iteration) | Propose it to the user; record the approved change in `iterations.md`. The handover itself belongs to the extractor. |
| A conflict between sources (§2) | Stop and raise it as a `CQn` or `OS2N-n`; don't pick one. |

## 9. Gates, handoff and iterations

- **Your role's gate is in your role guide** (proposal → user approval →
  write). Approval for one role, artifact or iteration doesn't carry over to
  another.
- **One iteration at a time** (`AGENTS.md`): BA → ARCHITECT → DEV → QA →
  user acceptance. Don't start the next role or iteration; the user does.
- **Before you stop**, update `project-documents/handoff.md` with:
  - the iteration and your role's status;
  - the spec IDs and stories covered;
  - changed files or the revision;
  - checks and their outcomes;
  - open `Qn`/`CQn` and `OS2N-n`;
  - the next role's scope and edit permissions.
  Also update the iteration's status in `iterations.md`.

## 10. Definition of done for an iteration

- Every story of the iteration is accepted. Each one's acceptance criteria
  pass in automated tests, and in QA cases where automation isn't practical.
- Every business rule (`BR`) of the iteration's features has a passing test.
- The screens match the prototype in structure, navigation and visibility
  by role; QA records the parity check.
- The app builds, its migrations apply to an empty database, and the full
  test suite passes.
- Every configuration key the iteration reads is documented (§6), and no
  secret is in the repo.
- `handoff.md` and `iterations.md` are current, and the user has accepted
  the iteration.

## 11. Role reminders

| Role | In this repo, also |
|---|---|
| **BA** | Write stories per iteration from the handover's slice; one story per user goal, citing `F`/`A`/`J`/`BR`. Turn every business rule into acceptance criteria, including role visibility, validation messages, empty/`NULL` values (§5) and the error paths in the flows (from the action catalogue). Note which site properties a story depends on. |
| **ARCHITECT** | Write `architecture.md` in I0 within the decided stack, following [`docs/repository-structure.md`](docs/repository-structure.md) (capabilities, not OutSystems modules); afterwards only the iteration's changes. Decide how roles, configuration (§6), jobs, integrations and data migration work; map the handover's capability map to code. |
| **DEV** | Build only the approved stories. Start from the deliverables: migrations from `schema.sql`, types and signatures from `deliverables/specs/`, SQL from the porting checklist, extensions from `source/`. Port each action from its action-catalogue entry: every branch, loop and error path. Give the new function a doc comment with its purpose and the source (`Module › Action`); if the logic changes on purpose, say why and cite the decision. Add tests that cite story and `BR` IDs, keep secrets out (§6–7), and run the full suite before handing over. |
| **QA** | Test the exact revision in `handoff.md` against the stories, the business rules, the prototype (parity by role), the action catalogue (each branch and error path of the ported actions) and §5. Record each run in `projects/<Project>/converted/qa/` with case IDs that cite stories and rules. Report defects; don't fix application code. |

## 12. Working environment

- Windows, PowerShell 5.1: no `&&`; inspect JSON with Python, not
  `ConvertFrom-Json`. The extractor's Python is
  `..\extractor\.venv\Scripts\python` (standard library only for tools here).
- Create folders only when work starts in them; shared code goes in
  `packages/` only when a second app needs it.
- Don't commit or push unless the user asks.
