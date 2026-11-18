# <Project>: verification report

<!-- Stage 2 of STAGES.md. Written by the extractor to
     projects/<Project>/OUTPUT/verification/verification-report.md.
     Replace every <placeholder>; delete these comments. -->

- **Status:** Draft, waiting for the user's review
  <!-- After approval: Approved by the user on <YYYY-MM-DD> for run <run id> -->
- **Extraction run:** `<run id>` (`inventory.json`, <date/time>)
- **Package:** `<INPUTS\package.osp>` (+ extra inputs: <list or "none">)
- **Scope:** <apps from project.json>
- **Method:** Service Studio MCP (`servicestudio`) | Manual (user compared `inventory-details.csv`)
- **Verified by:** <agent>, <date>; reviewed by the user

## Summary

<2–4 sentences: modules verified, how many element types match, how many
differences, and whether any of them affect the rebuild.>

| Module | Application | Element types checked | Match | Differences | Not verified |
|---|---|---|---|---|---|
| <Module> | <App> | <n> | <n> | <n> | <n> |

## Per module

### <Module>

Opened in Service Studio as `<module name>` (`<moduleType>`), <environment if known>.

| Element | Extraction | Service Studio | Status |
|---|---|---|---|
| Entities | <n> | <n> | ✅ match / ⚠️ difference (D1) |
| Entity attributes (spot-check) | <entities checked> | | ✅ |
| Structures | | | |
| Screens | | | |
| Blocks | | | |
| Server actions | | | |
| Client actions (module-level) | | | |
| Service actions | | | |
| Roles | | | |
| Timers | | | |
| Site properties | | | |
| Client / session variables | | | |
| REST/SOAP consumed / exposed | | | |
| Themes, images, resources | | | |

## Differences

| ID | Module | Element | Extraction vs Service Studio | Classification | Disposition |
|---|---|---|---|---|---|
| D1 | <Module> | <element> | <what differs> | Extraction bug / Counting rule / Module changed after export / Platform item | <fixed in run …, explained, accepted by the user, …> |

## Not verified

<Modules or element types that could not be checked, and why (e.g. the
module could not be opened, the MCP was not available).>

## User review

<What the user checked, what they asked to change, and their approval in
their own words.>
