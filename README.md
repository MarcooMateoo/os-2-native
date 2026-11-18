# OutSystems to native

Rebuild OutSystems 11 applications (Reactive Web, Mobile and Service apps) as
hand-written code on a modern stack: React, React Native (Expo), ASP.NET Core
(.NET 10) or Node.js + TypeScript, and PostgreSQL, deployed to AWS. See
[TECH_STACK.md](TECH_STACK.md).

The work is done by **AI agents in Claude Code**, with you approving each
important step:

- the **extractor** (`extractor/`, the os2native tool) reads your OutSystems
  exports and turns them into a verified specification of the app;
- the **converter** (`converter/`) rebuilds the app from that specification,
  iteration by iteration.

## What's in the repository

```
OutSystems_2_Native/
  README.md            this file
  STAGES.md            the five stages and who does what
  TECH_STACK.md        the target stack and how OutSystems concepts map to it
  extractor/           stages 1–4: extraction, verification, prototype, conversion plan
    HOW_TO_EXTRACT.md  the step-by-step guide (start here)
    AGENTS.md          instructions for the extractor agent
  converter/           stage 5: conversion
    AGENTS.md          stage 5 flow and the converter manager
    CONVERTER_AGENT.md rules shared by the BA, ARCHITECT, DEV and QA agents
    agents/            role guides: STARTER, BA, ARCHITECT, DEV, QA
  projects/            your projects: created by you, never in git
    <Project>/
      INPUTS/          your exports from OutSystems
      OUTPUT/          everything the extractor generates
      project.json     your decisions: apps in scope, target stack
      converted/       the rebuilt application (written by the converter)
```

## How it works

| # | Stage | Who | You |
|---|---|---|---|
| 1 | **Extraction** | extractor | Put the exports in `INPUTS`, choose the apps in scope |
| 2 | **Verification** | extractor | Check the extraction against Service Studio, **approve the report** |
| 3 | **Prototype** | extractor | Choose the stack, review the product specification, action catalogue and clickable prototype, and say whether you want a portable product specification |
| 4 | **Conversion plan** | extractor | **Approve `CONVERTER_HANDOVER.md`**: the plan, decisions and site properties |
| 5 | **Conversion** | converter | Approve each role's work (BA → ARCHITECT → DEV → QA) and accept each iteration |

Details and rules: [STAGES.md](STAGES.md).

**Portable product specification (optional).** In stage 3 you can also ask
for a technology-neutral set of Markdown documents about the product
(requirements, domain model, screens, behaviour, integrations,
configuration) to share with other teams or platforms. It never mentions
OutSystems or how the current app is built, and it's checked for that
before you approve it. See
[HOW_TO_EXTRACT.md](extractor/HOW_TO_EXTRACT.md#portable-product-specification-optional).

## How to use

### 1. Install the prerequisites (once)

- **Claude Code**: the desktop app (Code tab) or the `claude` CLI
- **Git**
- **Python 3.12 or later** (`py --version`)
- **.NET 10 runtime**: a per-user install is enough
- Optional: **Service Studio 11** with its MCP Server, so the agent can
  check the extraction against your live modules

### 2. Get the repository and set up the extractor (once)

Get the files in one of two ways.

**With Git** (recommended: updates are a `git pull`):

```powershell
git clone <repository URL> OutSystems_2_Native
```

**From a ZIP file** (no Git needed):

1. Get the ZIP: on the repository page, **Code → Download ZIP**, or a ZIP a
   colleague sent you (see [Sharing the repository as a ZIP](#sharing-the-repository-as-a-zip)).
2. **Unblock it before extracting.** Right-click the ZIP → **Properties** →
   tick **Unblock** → **OK**, or run:
   ```powershell
   Unblock-File .\os-2-native-main.zip
   ```
   Windows marks downloaded files as untrusted, and that mark would carry
   over to the decoder in `extractor\bin\oml-utilities\`.
3. Extract it to a short local path, e.g. `C:\Work\`. Avoid OneDrive or
   other synced folders: your projects will hold customer data.
4. Rename the extracted folder (e.g. `os-2-native-main`) to
   `OutSystems_2_Native`, so the paths in the guides match. Check that it
   directly contains `README.md`, `extractor\` and `converter\`, not
   another folder with the same name inside.

Then set up the extractor:

```powershell
cd OutSystems_2_Native\extractor
```

```powershell
py -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
```

**Updating a ZIP copy:** download the new ZIP and extract it to a new
folder. Move your `projects\` folder into it (it's never in the ZIP), then
run the `py -m venv .venv` and `pip install` lines again in the new
`extractor\` folder. Delete the old folder once everything works.

### 3. Create a project and add your exports

Create `projects\MyProject\INPUTS\` (use your own short project name) and copy
in:
- the `.osp`/`.oap` exported from OutSystems;
- any extra `.oml`/`.xif` modules it depends on;
- screenshots of the live app, if you have them.

### 4. Run the extractor in Claude Code (stages 1–4)

Open Claude Code **in the `extractor` folder**. In the desktop app, choose
`OutSystems_2_Native\extractor` as the session's folder; with the CLI, run
`claude` from that folder. Then ask:

> Extract the project MyProject.

The agent confirms the project and the apps in scope, then works through the
stages and stops for your decisions and approvals. Each stage ends with the
next step. To continue later, open a new session in the same folder and ask:

> Where is MyProject?

The full guide, including running every step without Claude Code, is
[extractor/HOW_TO_EXTRACT.md](extractor/HOW_TO_EXTRACT.md).

### 5. Run the converter in Claude Code (stage 5)

When you approve the handover, the extractor ends with a **Next steps**
message that has the exact prompts. In short: open a new Claude Code session
**in the `converter` folder** and ask:

> Follow AGENTS.md for project "MyProject". The handover is
> `..\projects\MyProject\OUTPUT\SPECS\CONVERTER_HANDOVER.md`. Start with the intake.

The converter checks the handover (intake), sets up
`projects\MyProject\converted\` (STARTER), and rebuilds the app iteration by
iteration:

> Follow CONVERTER_AGENT.md and agents/AGENTS_BA.md for MyProject, iteration I0.

Then ARCHITECT, DEV and QA in turn; you accept each iteration before the
next one starts. See [converter/README.md](converter/README.md).

## Sharing the repository as a ZIP

To send the repository to someone without Git access, make the ZIP from Git,
so it holds only the committed files: no `projects\` (customer data), no
`.venv`, no generated output. From the repository folder:

```powershell
git archive --format=zip --prefix=OutSystems_2_Native/ -o ..\OutSystems_2_Native.zip HEAD
```

The ZIP extracts to an `OutSystems_2_Native` folder, ready to use. Don't zip
the folder with Windows' **Send to → Compressed folder**: that would include
`projects\` and your local settings.

## Rules for project data

- **Never commit `projects/`.** It holds customer packages, extracted
  models and the converted code, and it's git-ignored on purpose. Only the
  tools, rules and templates are in this repository.
- **One folder per project.** Never extract two solutions into the same
  project.
- **You make the decisions.** The agents propose the scope, stack choices,
  missing dependencies, plans and code, and wait for your approval. They
  never change your modules in Service Studio.
- **Secrets and personal data** found in the model (passwords, keys,
  email addresses) are described, never copied into documents or code.

## More

| Document | What it covers |
|---|---|
| [STAGES.md](STAGES.md) | The five stages, gates and who writes what |
| [TECH_STACK.md](TECH_STACK.md) | Target stack, concept mapping, AWS architecture, key decisions |
| [extractor/HOW_TO_EXTRACT.md](extractor/HOW_TO_EXTRACT.md) | Step-by-step guide for stages 1–4 |
| [extractor/README.md](extractor/README.md) | How os2native works, its output and scripts |
| [converter/README.md](converter/README.md) | How the conversion works |
| [converter/CONVERTER_AGENT.md](converter/CONVERTER_AGENT.md) | Rules every conversion role follows |
