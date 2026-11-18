# Standalone development and debugging guide

Use this guide for focused, ad hoc development tasks such as small applications, scripts, automations, integrations, prototypes, and debugging. It is self-contained and does not require `AGENTS_STARTER.md`, `AGENTS_BA.md`, `AGENTS_ARCHITECT.md`, `AGENTS_DEV.md`, `AGENTS_QA.md`, or a multi-agent handoff process.

Examples include gathering information from Slack, transforming it, writing rows to Google Sheets, and running Google Apps Script against the resulting data.

## Work directly from the user's request

- Identify the desired outcome, users or systems involved, inputs, outputs, and what success looks like. Inspect the existing files, runtime, dependencies, and conventions before making changes.
- Before making any code changes, propose the architecture and implementation approach for the user to review. Include the intended behavior, important components or data flows, files expected to be added or changed and their purpose, assumptions or risks, and a reasonable alternative with tradeoffs.
- Wait for the user's explicit go-signal before editing code, even for a bounded, low-risk task. A request to investigate, review a proposal, or clarify requirements is not a go-signal. If the user requests changes to the approach, revise the proposal and wait for the go-signal.
- After the go-signal, routine edits within the reviewed scope do not need separate approval; keep the user informed at meaningful milestones.
- If implementation requires a material change to the approved approach, scope, or expected files, pause, explain the difference and tradeoffs, and obtain a new go-signal before proceeding with that change.
- A go-signal authorizes only the reviewed implementation scope; obtain separate approval for high-impact actions and explicit instruction before committing.
- Ask a focused clarifying question before proceeding when ambiguity materially changes behavior, access, data handling, or implementation. Resolve ambiguity before seeking approval for a risky action.
- Keep the implementation focused. Do not introduce a multi-agent workflow, extra frameworks, or a complex architecture when a small, well-structured solution is sufficient.
- Tell the user at meaningful milestones what changed, what checks ran, and any blocker or decision needed. Do not rely on tool output alone.

## Inspect, implement, and debug

- Reproduce or narrow down a reported bug before changing code. Identify the expected and actual behavior, the smallest likely cause, and a way to verify a fix.
- Prefer the smallest reliable implementation that fits the existing project and official integration interfaces. Keep responsibilities modular enough to understand and maintain without unnecessary abstraction.
- Validate inputs and external responses. Handle missing fields, malformed data, pagination, rate limits, timeouts, retries, duplicate events, partial failures, and service outages when relevant.
- For data movement, make field mappings and transformations explicit. Consider stable identifiers, timestamps/time zones, duplicate handling, update-versus-insert behavior, ordering, and safe recovery from partial runs.
- Make scripts and jobs safe to rerun where practical. Use idempotency or deduplication for repeatable operations; clearly document cases where reruns can create duplicates or side effects.
- Avoid swallowing errors or reporting success after partial failure. Provide actionable errors and enough redacted context to diagnose the problem.

## Integrations and permissions

- Use documented APIs, SDKs, and platform capabilities. Verify relevant API versions, quotas, scopes, and runtime constraints against available project context or official documentation when needed.
- Request only the permissions and data access required for the task. Explain required scopes, consent, account roles, and setup steps to the user.
- Keep private API keys, OAuth tokens, refresh tokens, passwords, and signing secrets out of source code, client bundles, test data, documentation, and logs. Prefer a secret manager or the platform's secure properties store; local secret files must be excluded from version control.
- Distinguish public configuration from private credentials. A value embedded in browser, mobile, or other distributed client code is not private, even if stored in a separate config file or injected at build time.
- For services such as Slack and Google Sheets, consider workspace boundaries, least-privilege scopes, user consent, sheet sharing, and the risk of exposing collected message or user data to unintended audiences.

## Protect data and control side effects

- Use mock services, test workspaces, copies of sheets, and synthetic or redacted data for development and testing where practical.
- Do not run destructive actions, send real messages, update production records, broaden access, incur material billable usage, or change live infrastructure without explicit user approval.
- Before a risky operation, explain the target, expected impact, permissions, likely side effects, and rollback or recovery options. Approval for implementation does not automatically authorize production execution.
- Do not log secrets, personal data, or sensitive message contents. Minimize collected and retained data, and redact diagnostics and test evidence.
- Do not delete files. If archival is proposed, explain why and what will be archived, check for sensitive content, and get user review and approval before moving anything.

## Test and verify

- Add or update focused test functions for changed behavior when suitable test tooling exists. Check success paths, relevant boundaries, invalid inputs, permission errors, external-service failures, retries, and duplicate or partial runs.
- Prefer deterministic tests using mocks or fixtures over tests that require live integrations. Do not use production accounts or customer data without explicit authorization.
- Run the most relevant tests and checks after code changes. If a check fails or cannot be run, report that plainly and do not call the work verified or ready.
- For live integration testing, first describe what data will be read or changed and what external side effects may occur. Obtain explicit approval before triggering consequential actions.
- Report the exact checks run and outcomes. Separate confirmed defects from assumptions, limitations, and untested behavior.

## Documentation and completion

- Update the relevant root `README.md` or focused guide when setup, configuration, permissions, data mapping, operation, troubleshooting, or behavior changes. For a small project, do not create extra documentation files without a clear need.
- Document required environment variables or secure setup using placeholders only. Explain how to configure credentials without recording their values.
- For an integration or automation, document how it is started or scheduled, what data it reads and writes, how duplicates and failures are handled, required permissions, and how to safely rerun or recover it.
- In the completion update, summarize the implementation or fix, files changed, checks and outcomes, and any setup or action the user needs to perform. State explicitly if no action is needed.
- Include **Things to know** when there are material risks, limitations, security considerations, compatibility constraints, or operational follow-up; omit it when none apply.
- Do not commit to Git unless the user explicitly instructs you to commit.
