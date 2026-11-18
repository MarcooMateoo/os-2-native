# OutSystems to native: stages

Every project goes through five stages. The **extractor** agent
([`extractor/AGENTS.md`](extractor/AGENTS.md), the os2native tool) runs stages 1–4; the
**converter** agents ([`converter/AGENTS.md`](converter/AGENTS.md)) run
stage 5. Two stages end in a user review, and nothing moves on until the user
approves.

```
1 EXTRACTION ─▶ 2 VERIFICATION ─▶ 3 PROTOTYPE ─▶ 4 CONVERSION PLAN ─▶ 5 CONVERSION
  (extractor)     (extractor)       (extractor)     (extractor)          (converter: STARTER,
                  ◆ user verifies                   ◆ user reviews        BA, ARCHITECT, DEV, QA;
                                                                          iterates)
```

| # | Stage | Owner | What happens | Output (in `projects/<Project>/`) | Gate |
|---|---|---|---|---|---|
| 1 | **Extraction** | extractor | Extract the package; inventory and dependencies limited to the project's scope (`project.json`). | `OUTPUT/` (`inventory.json`, `inventory-details.*`, `dependency-check.json`, `extraction/`) | No ERROR, no `unknown` dependency (or accepted by the user) |
| 2 | **Verification** | extractor | Check the extraction against the live modules through the Service Studio MCP; the user verifies. | `OUTPUT/verification/verification-report.md` | ◆ **User approves the verification** |
| 3 | **Prototype** | extractor | Generate the deliverables, write the product specification per app, build a clickable prototype from them. | `OUTPUT/deliverables/`, `OUTPUT/SPECS/<App>/product-specification.md`, `OUTPUT/SPECS/<App>/prototype/`; optionally `OUTPUT/SPECS/<App>/portable-spec/` (a technology-neutral product specification to share) | Shown to the user for feedback |
| 4 | **Conversion plan** | extractor | Write the conversion plan and handover for the converter. | `OUTPUT/SPECS/CONVERTER_HANDOVER.md` | ◆ **User approves the handover** |
| 5 | **Conversion** | converter | Intake, then STARTER → BA → ARCHITECT → DEV → QA, repeated per iteration of the plan until the app is rebuilt. | `projects/<Project>/converted/` | User review at each role's gate and after each iteration |

Rules that hold across stages:

- **One project at a time, confirmed by the user** before anything runs.
- **The scope is the user's** (`project.json`). Inventory, verification,
  specs and plan cover the apps in scope only.
- **Approvals are tied to one extraction run.** Re-extracting changes the
  run id: re-verify what changed, update the spec and handover, and get them
  approved again. The converter re-runs its intake gate.
- **Each side writes only its own output.** The extractor writes
  `projects/<Project>/OUTPUT/` (and `project.json`), never `converter/` or
  `converted/`. The converter writes only `projects/<Project>/converted/`,
  never `INPUTS/`, `OUTPUT/`, `project.json` or `extractor/`. Problems the
  converter finds in the extraction go back as change requests (`OS2N-n`).
- **The target stack** is [`TECH_STACK.md`](TECH_STACK.md), with the
  project's choices in `project.json` (`"stack"`).
