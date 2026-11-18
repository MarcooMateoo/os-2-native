# <App>: action catalogue

<!-- Stage 3 of STAGES.md. Written by the extractor to
     projects/<Project>/OUTPUT/SPECS/<App>/action-catalogue.md (or, for a large
     app, one file per module in SPECS/<App>/actions/<Module>.md plus this file
     as the index). It documents EVERY server action, service action and client
     action of the in-scope modules: what it does and its logic flow.
     Replace every <placeholder>; delete these comments. -->

> **Source:** os2native extraction run `<run id>`,
> `OUTPUT/extraction/<Module>/logic/` (`server-actions.json`,
> `service-actions.json`, `client-actions.json`). Descriptions marked
> **(inferred)** were written from the flow because the model has none.
> Secret values and personal data are described, never reproduced.

## Summary

| Module | Server actions | Service actions | Client actions (module) | Screen and block actions | Documented |
|---|---|---|---|---|---|
| <Module> | <n> | <n> | <n> | <n> | <n> / <n> |

## <Module>

### Server action: `<ActionName>`

- **Purpose:** <the model's description, or one sentence written from the
  flow, marked (inferred)>
- **Kind:** server action | function | service action | client action |
  screen action of `<Screen>` | block action of `<Block>`
- **Folder / public:** <folder> · <public: yes/no>
- **Inputs:** `<Name>` (<type>, mandatory) — <meaning>
- **Outputs:** `<Name>` (<type>) — <meaning>
- **Called by:** <screens, actions or timers that call it>
- **Calls:** <actions, entity actions, integrations, Advanced SQL (sql-porting entry)>
- **Data:** reads <entities>; writes <entities>
- **Roles checked:** <Check<Role>Role calls, or "none">
- **Business rules:** BR<n>, … (product specification)

**Logic flow**

1. Get `<Aggregate>`: <entity> filtered by <condition>, sorted by <…>.
2. If `<condition in plain words>`:
   - **True:** <steps>
   - **False:** <steps> → end
3. For each `<record>` in `<list>`: <steps>
4. Call `<Action>` with <key arguments>.
5. Set `<output>` to <value>.
6. **On error** (<exception>, transaction <aborted/kept>): <steps>; the user
   sees "<message>".

<Optional: a Mermaid flowchart for flows with several branches, or a link to
`deliverables/specs/<Module>/state-flows.md`.>

**Notes:** <behaviour to keep or not to port (a hard-coded value, a test back
door), open questions Q<n>>
