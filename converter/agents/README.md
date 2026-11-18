# Multi-agent project guides

This folder contains reusable instructions for a user-reviewed, multi-agent project workflow. The user remains the product owner and reviewer; agents propose work, explain changes, and wait at the review gates described in their role guides.

> **In the converter**, these guides run stage 5 (Conversion) of
> [`../../STAGES.md`](../../STAGES.md). Every role agent reads
> [`../CONVERTER_AGENT.md`](../CONVERTER_AGENT.md) first; it and
> [`../AGENTS.md`](../AGENTS.md) take precedence where they differ:
> - The "uploaded source documents" are the os2native files for the project,
>   starting with the user-approved `projects/<Project>/OUTPUT/SPECS/CONVERTER_HANDOVER.md`.
>   They are cited by path from `projects/<Project>/converted/project-documents/sources.md`,
>   never copied into a `source/` folder.
> - Bootstrap (STARTER) runs once per app, after the intake gate passes.
> - BA → Architect → Dev → QA then repeats once per iteration of the
>   handover's plan, and the user accepts each iteration before the next one starts.

## Recommended project flow

**User uploads source documents → Bootstrap → Business Analyst → Architect → Development → QA**

1. Bootstrap inspects the project and source files, proposes a structure, and waits for approval before creating folders or moving/copying files.
2. The Business Analyst (BA) analyzes the approved source material, presents traceable user stories and acceptance criteria, and waits for approval before writing the stories file.
3. The Architect analyzes the sources and approved stories, presents architecture options and tradeoffs, and waits for approval before writing the architecture file.
4. Development implements against approved artifacts, documents relevant changes, and runs checks.
5. QA tests the stable implementation against approved stories and architecture, records results, and reports findings for the user to decide.

Each stage updates the shared `project-documents/handoff.md` before you decide whether to engage the next agent. It captures the current stage, approved artifact versions, changed files or tested revision, checks and outcomes, open questions and risks, and the next agent's scope and edit permissions. You approve its location and format during Bootstrap; agents make routine updates without asking every time. It is a current-state summary, not an audit log. An approval at one stage does not approve changes at another.

## Guide files

| File | Purpose |
|---|---|
| `AGENTS_STARTER.md` | Bootstrap instructions, proposed project layout, review gates, and cross-agent handoffs. |
| `AGENTS_BA.md` | Analyze project documents and prepare user stories for review. |
| `AGENTS_ARCHITECT.md` | Analyze source documents and approved stories; prepare architecture for review. |
| `AGENTS_DEV.md` | Development workflow, implementation, documentation, secrets, and verification guardrails. |
| `AGENTS_QA.md` | Test design, execution safety, test records, and QA reporting. |
| `AGENT_STANDALONE_DEV.md` | Self-contained guidance for focused development, debugging, scripts, and integrations without the multi-agent workflow. |

## Suggested project-document layout

```text
project-documents/
├── source/          # Original user-provided project documents, kept unchanged
├── user_stories.md  # BA deliverable, written after user review and approval
├── architecture.md  # Architect deliverable, written after user review and approval
└── handoff.md       # Current stage status and next-agent handoff; updated at each stage
```

Keep other technical guides under `docs/`, automated tests under the framework's conventional test location, and QA case/result records under `qa/` when needed. Use the project's existing conventions where they differ.

## Using the guides

Copy or reference only the guides needed for the project, after reviewing the proposed file changes. Use `AGENTS_STARTER.md` for the multi-agent project lifecycle. Use `AGENT_STANDALONE_DEV.md` instead for a focused, ad hoc task that does not need the BA → Architect → Dev → QA composition. Keep the chosen workflow explicit and pause for user review at its stated gates.

Uploaded source files may contain sensitive or confidential information. Preserve originals, avoid putting secrets or personal data in generated documentation, and follow the project's access and retention requirements.

## Sample flow for a new project

1. **Prepare the project folder.** Make the user-provided source documents accessible to the Bootstrap session. Do not move or copy them into the project until Bootstrap proposes the action and you approve it.
2. **Start Bootstrap.** Start a session using `AGENTS_STARTER.md`. Ask it to inspect the project and source documents, then propose the folder structure and `project-documents/handoff.md` format. Review and approve the proposed scaffolding before it creates folders or files.
3. **Run BA.** After reviewing Bootstrap's handoff, explicitly engage the BA using `AGENTS_BA.md`. BA analyzes the source documents and proposes traceable user stories and acceptance criteria. Clarify open questions, then approve the proposal before BA writes `project-documents/user_stories.md`.
4. **Run Architect.** Once you approve the stories and review the handoff, engage the Architect using `AGENTS_ARCHITECT.md`. Review the architecture recommendation and alternatives; approve it before the Architect writes `project-documents/architecture.md`.
5. **Run Development.** Engage Development using `AGENTS_DEV.md` with the approved stories, architecture, and current handoff. Review its proposed approach and intended file changes, then give an explicit go-signal before it edits code. Review any material scope changes. Development documents relevant changes, runs checks, and updates the handoff.
6. **Run QA.** After Development stops editing, engage QA using `AGENTS_QA.md` against the exact implementation revision and approved artifacts. Review its test results, findings, and edge cases, then decide what to fix, defer, or accept.

Agents do not automatically start or notify the next role. You control each transition: review the completed work and updated `project-documents/handoff.md`, then explicitly start or instruct the next session. Do not run dependent stages in parallel. You can use separate sessions or reuse a session with a clear role change; ensure each agent receives the correct guide and current approved artifacts.

### Example prompts

**Bootstrap**

> Follow `AGENTS_STARTER.md` to bootstrap this project. Inspect the project and the documents I provided. First report what you found, then propose the folder structure, explain each planned file and folder, and recommend an alternative with tradeoffs. Include `project-documents/handoff.md` and propose its format. Do not create, move, or modify project files until I review and approve the plan. Preserve my original documents.

**Business Analyst**

> Follow `AGENTS_BA.md`. Use the source documents and approved Bootstrap handoff. Analyze them and propose traceable user stories and acceptance criteria. Ask me to clarify material ambiguities before seeking approval. Show the proposed contents and file changes; do not write `project-documents/user_stories.md` until I approve.

**Architect**

> Follow `AGENTS_ARCHITECT.md`. Use the source documents, user-approved `project-documents/user_stories.md`, and current handoff. Propose an architecture with components, key flows, assumptions, risks, and alternatives with tradeoffs. Show the proposed document outline and file changes; do not write `project-documents/architecture.md` until I approve.

**Development**

> Follow `AGENTS_DEV.md`. Before editing code, propose the architecture and implementation approach, intended behavior, files to change and their purpose, assumptions or risks, and a reasonable alternative with tradeoffs. Wait for my explicit go-signal. After I approve, implement only within that scope; update relevant documentation and tests, run appropriate checks, and update `project-documents/handoff.md` with changed files or revision, check outcomes, remaining risks, and a QA assignment. Ask before materially changing the approved scope or taking a high-impact action.

**QA**

> Follow `AGENTS_QA.md`. Test the current implementation against the approved stories and architecture at the revision identified in the handoff. Do not edit application code. Use the approved QA record location and format, create a new timestamped result record for this run, and update `project-documents/handoff.md` with results, findings, gaps, and decisions needed. Report reproducible defects and edge cases for me to decide.

**Standalone development**

> Follow `AGENT_STANDALONE_DEV.md` for this focused task. First inspect the current project and propose the architecture/approach, intended behavior, files to change and their purpose, assumptions or risks, and a reasonable alternative with tradeoffs. Do not edit code until I review the proposal and give an explicit go-signal. After approval, implement within that scope, run relevant checks, update relevant documentation, and report results and any action I need to take.

For a small, well-defined task that does not need the full sequence, use `AGENT_STANDALONE_DEV.md` instead. It uses the same proposal-and-go-signal gate before code edits.
