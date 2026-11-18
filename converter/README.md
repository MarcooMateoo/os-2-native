# converter

The agents, rules and tools that rebuild OutSystems applications on a
native stack ([`../TECH_STACK.md`](../TECH_STACK.md)). Each project's
converted application is written to `projects/<Project>/converted/`, built
from what **os2native** hands over for the project.

```
OutSystems_2_Native/
  extractor/                 stages 1–4 (the os2native tool): extraction, verification, prototype, conversion plan
  converter/                 stage 5: the conversion agents, rules and tools (this folder)
  projects/<Project>/
    INPUTS/                  exports from OutSystems
    OUTPUT/                  extraction, deliverables, SPECS (CONVERTER_HANDOVER.md)
    project.json             scope and stack decisions
    converted/               the converted application (written by the converter)
```

## How work flows

The full sequence is in [`../STAGES.md`](../STAGES.md). The converter starts
when the user has approved `projects/<Project>/OUTPUT/SPECS/CONVERTER_HANDOVER.md`:

```
CONVERTER_HANDOVER.md ──▶ intake ──▶ STARTER ──▶ ┌▶ BA ─▶ ARCHITECT ─▶ DEV ─▶ QA ─▶ user accepts ┐
  (approved, stage 4)      gate     (once per    │                                               │
                                     app)        └──────────────── next iteration ◀──────────────┘
```

1. **Intake.** Check the handover and os2native's output against the
   [intake contract](contracts/os2native-intake-contract.md):

   ```powershell
   ..\extractor\.venv\Scripts\python tools\intake_check.py ..\projects\<Project>\OUTPUT --osp ..\projects\<Project>\INPUTS\<package>.osp --out "..\projects\<Project>\converted\intake\intake-check.md"
   ```

   Record the findings, change requests to os2native and hand-off conditions in
   `projects/<Project>/converted/intake/intake-review.md`. Exit code 1 means there are blockers:
   don't start building.
2. **Start.** STARTER sets up `projects/<Project>/converted/`: README, `module-map.md` (from
   the handover's capability map) and `project-documents/` (sources,
   iterations, handoff).
3. **Iterate.** For each iteration in the handover's plan (I0 foundation,
   then feature slices): BA → ARCHITECT → DEV → QA, then the user accepts
   the iteration and the next one starts. Details in [AGENTS.md](AGENTS.md).

## Layout

| Path | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Rules for AI agents working in this repo, and the conversion loop |
| [`CONVERTER_AGENT.md`](CONVERTER_AGENT.md) | Global instructions shared by the BA, ARCHITECT, DEV and QA agents |
| [`agents/`](agents/README.md) | Role guides: Starter, BA, Architect, Dev, QA, standalone dev |
| [`contracts/`](contracts/os2native-intake-contract.md) | What os2native must deliver per project |
| [`tools/`](tools/intake_check.py) | Intake gate script (Python 3.12, standard library only) |
| [`docs/`](docs/repository-structure.md) | Layout of `projects/<Project>/converted/` and conventions |
| [`packages/`](packages/README.md) | Code shared by two or more converted apps (empty until needed) |

Per project, outside this folder:

| Path | Purpose |
|---|---|
| `projects/<Project>/converted/` | The converted application: backend, db, web, mobile, qa, project-documents |
| `projects/<Project>/converted/intake/` | Intake check report and review |

Full layout and module conventions: [docs/repository-structure.md](docs/repository-structure.md).

## Applications

Converted applications live in `projects/<Project>/converted/`, next to each
project's inputs. `projects/` is git-ignored, so no project or customer data
is in this repository.
