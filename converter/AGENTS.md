# AGENTS.md

Rules for AI agents working in **converter**, the agents, rules and tools
that rebuild OutSystems applications natively. The converted application of
each project is written to **`projects/<Project>/converted/`**, next to that
project's `INPUTS/` and `OUTPUT/`. Read [README.md](README.md) first. This
file covers the stage 5 flow and the converter manager; the rules every role
agent (BA, ARCHITECT, DEV, QA) shares are in
[CONVERTER_AGENT.md](CONVERTER_AGENT.md).

The converter runs **stage 5, Conversion**, of
[`../STAGES.md`](../STAGES.md). Stages 1–4 (extraction, verification,
prototype, conversion plan) are done by the extractor agent in
`../extractor/`. Stage 5 starts from what stage 4 hands over:
`projects/<Project>/OUTPUT/SPECS/CONVERTER_HANDOVER.md`, approved by the
user.

## Before starting

1. **Confirm the project with the user.** List the projects that have a
   `projects/<Project>/OUTPUT/SPECS/CONVERTER_HANDOVER.md`, and ask which one
   to work on. Don't assume one.
2. **Find where the project is.** Read the handover (status line, run id,
   plan), `projects/<Project>/converted/intake/intake-review.md` and, if the app exists,
   `projects/<Project>/converted/project-documents/handoff.md` and `iterations.md`. Tell the
   user the current stage, iteration and role before doing anything.
3. **Check the handover is approved for the current extraction.** Its status
   must read `Approved by the user on <date> for run <run id>`, and the run
   id must match `projects/<Project>/OUTPUT/inventory.json`. The verification
   report (`OUTPUT/verification/verification-report.md`) must be approved for
   the same run. If not, stop: the project goes back to the extractor
   (stage 2–4), and you tell the user so.

## Roles

| Role | Guide | Does |
|---|---|---|
| **Converter manager** | this file | Runs the intake gate, keeps `projects/<Project>/converted/intake/intake-review.md`, raises change requests to os2native (`OS2N-n`), keeps `iterations.md` and the repository structure consistent. |
| **STARTER** | [`agents/AGENTS_STARTER.md`](agents/AGENTS_STARTER.md) | Sets up `projects/<Project>/converted/` from the handover (once per app). |
| **BA** | [`agents/AGENTS_BA.md`](agents/AGENTS_BA.md) | User stories per iteration, traced to spec IDs. |
| **ARCHITECT** | [`agents/AGENTS_ARCHITECT.md`](agents/AGENTS_ARCHITECT.md) | Architecture for the app, then the changes each iteration needs. |
| **DEV** | [`agents/AGENTS_DEV.md`](agents/AGENTS_DEV.md) | Builds the iteration's stories. |
| **QA** | [`agents/AGENTS_QA.md`](agents/AGENTS_QA.md) | Tests the iteration against the stories, the spec and the prototype. |

**Every role agent reads [CONVERTER_AGENT.md](CONVERTER_AGENT.md) before
its role guide.** The role guides are generic; that file makes them specific
to this repo:
- how to start a session;
- the sources of truth (the os2native files, cited by path, never copied);
- the decided stack;
- traceability IDs;
- the OutSystems behaviour to preserve;
- configuration and site properties;
- security;
- how to raise findings;
- the handoff;
- the definition of done.

The documents folder for an app is `projects/<Project>/converted/project-documents/`:
`sources.md`, `iterations.md`, `user_stories.md`, `architecture.md`,
`handoff.md`. There is no `project-documents/source/` folder.

## Stage 5: conversion

### 5.0 Intake (converter manager)

Run the gate after every os2native run on the project:

```powershell
..\extractor\.venv\Scripts\python tools\intake_check.py ..\projects\<Project>\OUTPUT --osp ..\projects\<Project>\INPUTS\<package>.osp --out "..\projects\<Project>\converted\intake\intake-check.md"
```

Then check by hand what the script doesn't yet (contract §6: verification
report and handover approved for the current run). Record findings,
change requests (`OS2N-n`) and hand-off conditions in
`projects/<Project>/converted/intake/intake-review.md`. A BLOCKER stops work on the affected
modules until it is fixed or the user accepts it.

### 5.1 STARTER (once per project)

Follow `AGENTS_STARTER.md`, with these specifics. Propose, wait for the
user's approval, then create, in `projects/<Project>/converted/` (an older
handover may name a `converter/apps/<app>/` folder; the output still goes to
`converted/`):

- `projects/<Project>/converted/README.md`: source project and run id, stack, status, how to
  run.
- `projects/<Project>/converted/module-map.md`: from the handover's capability map.
- `projects/<Project>/converted/project-documents/sources.md`: the paths of the inputs and the
  run id.
- `projects/<Project>/converted/project-documents/iterations.md`: the handover's iterations
  (`I0`, `I1`, …) with a status per iteration (planned, in progress, in QA,
  accepted) and any change to the plan with the user's approval.
- `projects/<Project>/converted/project-documents/handoff.md`: the format in
  `AGENTS_STARTER.md`, plus the current iteration.

Component folders (`backend/`, `db/`, `web/`, `mobile/`, `qa/`) are created
when an iteration first needs them.

### 5.2 The conversion loop

Each iteration of the plan goes through the roles in order, with the user's
review at each role's gate:

```
           ┌────────────────────────── next iteration ◀──────────────────────────┐
           ▼                                                                      │
  BA (stories for In) ─▶ ARCHITECT (architecture / changes for In) ─▶ DEV ─▶ QA ─▶ user accepts In
```

1. **BA** writes the stories for the iteration's features (spec IDs from the
   handover), adding to `user_stories.md`. Every story cites its spec IDs
   (`F3`, `A2`, `BR7`, `J1`), and its acceptance criteria cover the rules and
   the prototype's behaviour. Stories that depend on an open question (`Qn`)
   wait until it is answered or the user accepts a default.
2. **ARCHITECT** writes `architecture.md` in iteration I0 and, after that,
   only the changes the iteration needs (new capability, integration, data).
3. **DEV** builds the stories within the approved architecture, using the
   deliverables: migrations from `schema.sql`, entities and signatures from
   `deliverables/specs/`, Advanced SQL from the porting checklist, extension
   logic from `extensions/<Name>/source/`.
4. **QA** tests against the stories, the spec's business rules and the
   prototype (screens, navigation, visibility by role), and records the run in
   `projects/<Project>/converted/qa/`.
5. **The user accepts the iteration** (or asks for fixes). Update
   `iterations.md` and `handoff.md`, then start the BA on the next iteration.

Findings during the loop are routed as in
[CONVERTER_AGENT.md §8](CONVERTER_AGENT.md#8-when-you-find-a-problem). The
converter manager records change requests (`OS2N-n`) in the intake review.

When the project is re-extracted and the handover is approved again for the
new run, re-run the intake, read the handover's "changes since" section, and
update `sources.md`, `iterations.md` and the affected stories.

## Hard rules

1. **No build work without an approved handover and a passing intake
   gate** for the current run. Acceptances of open BLOCKERs are recorded in
   the intake review.
2. **Write only to `projects/<Project>/converted/`.** The converter
   reads `projects/<Project>/OUTPUT` and `project.json` and never writes to
   them, not even the handover; it never touches `INPUTS/` or `extractor/`.
   Problems found there become change requests (`OS2N-n`) for the
   extractor. Inside `converter/` itself, change only the shared rules,
   tools and `packages/`, never a project's code. `extractor/` (the os2native tool) is a separate folder and is often being
   changed by another session.
3. **Structure by capability, not by OutSystems module.** Follow
   [docs/repository-structure.md](docs/repository-structure.md). Name
   modules after spec features (`orders`, `invoices`), never after
   OutSystems modules (`SP_Orders_CS`). Update `module-map.md` when the
   mapping changes.
4. **Follow [CONVERTER_AGENT.md](CONVERTER_AGENT.md)** in every role:
   trace everything to the spec, preserve OutSystems behaviour, take every
   site property from the handover as configuration, don't copy security
   defects, and keep customer data out of the repo. Don't build behaviour
   that depends on an open question (`Qn`, `CQn`) until it is answered or the
   user accepts a default.
5. **One iteration at a time.** Don't start the next iteration's stories
   before the user accepts the current one, unless the user asks to overlap.
6. **Shared code goes in `packages/` only when a second app needs it.**
7. **Create folders only when work starts in them.** No empty scaffolding.
8. Don't commit unless the user asks.

## Commands (Windows, PowerShell 5.1)

```powershell
# Intake gate (uses os2native's Python; standard library only)
..\extractor\.venv\Scripts\python tools\intake_check.py ..\projects\<Project>\OUTPUT --osp ..\projects\<Project>\INPUTS\<package>.osp --out "..\projects\<Project>\converted\intake\intake-check.md"
```

PowerShell 5.1 has no `&&`. Inspect JSON with Python, not `ConvertFrom-Json`.
