# Module Output Package Contract

## Purpose

Define the normalized output layout for one extracted OutSystems module.

The package must support consumers across target stacks:

- humans reviewing implementation readiness
- agents implementing the module into Flutter, Next.js, Go, DB, or another
  target scaffold

This contract defines not only file layout, but also the canonical module
boundary, dependency context, and safe downstream work partitions for one
module package.

## Boundary Rules

The package is the canonical extraction unit for one module.

It must make clear:

- which facts are owned by the module and represented directly inside the
  package
- which facts depend on other modules and must be emitted as explicit
  references instead of duplicated inline
- which evidence supports each major claim
- which sub-units are valid for downstream implementation or review as
  independent work items

## Package Layout

```text
  <output-root>/
  <module-name>/
    package.json
    summary.md
    coverage-report.md
    dependencies/
      readiness.json
      graph.json
      references.json
    interfaces/
      inventory.json
      partitions.json
      traversal-log.json
      coverage.json
      screens.json
      blocks.json
      screen-dataflow.json
      themes.json
      assets.json
      wireframes.md
    logic/
      inventory.json
      actions.json
      partitions.json
      traversal-log.json
      coverage.json
      client-actions.json
      server-actions.json
      service-actions.json        # Service Actions (ServiceAPIMethods)
      contracts.json
    processes/
      inventory.json
      partitions.json
      traversal-log.json
      coverage.json
      transitions.json
    data/
      inventory.json
      entities.json
      partitions.json
      traversal-log.json
      coverage.json
      relationships.json
      static-data.json
      roles-and-permissions.json
      site-properties.json        # is_platform=true for TenantId/TenantName
      site-properties-runtime.json
      user-exceptions.json
    integrations/
      consumed-apis.json          # consumed REST/SOAP only (kind: REST|SOAP)
      exposed-apis.json           # exposed REST/SOAP only (kind: REST|SOAP)
      public-actions.json         # public server actions (module-to-module)
      dependency-actions.json     # actions referenced from dependencies
      external-systems.json
      compatibility-notes.md
    scaffold/
      requirements.json
      shared-components.json
      backend-adapters.json
      persistence-requirements.json
      evidence/
        index.json
        assets/
        trees/
        metadata/
```

Pass-level runtime enrichment writes `<pass-root>/runtime/site-properties-summary.json`.

App-level projection also writes `_evidence/oml-behavior.json`. That artifact is
the normalized, app-neutral behavior contract consumed by validator
dev-readiness. It is not a target-app implementation map. Its canonical unit is
the OutSystems source surface, usually `module.flow.screen`, with entry points,
action chain, exits, UI states, API endpoint ids, dataflow, UI controls, widget
actions, inferred/authored forms, and journey candidates attached. Target
mapping into Maverick, Next.js, Flutter, or another implementation is layered on
later by the validator/profile.

App-level projection also writes `_evidence/source-surface-aliases.json` from
`output/<app>/source-surface-aliases.json`. That artifact covers generated
validator pages that are backed by source evidence but are not standalone
OutSystems screens: webviews opened by actions, stateful modal variants inside a
screen, and native delegation dialogs backed by a shared block plus plugin
calls. Every alias must carry the generated page id, target surface ids, source
screen/block/action refs, and concrete evidence signals. Direct source screens
remain outside this alias list.

## Required Top-Level Files

### `package.json`

Must include:

- module name
- application name
- extraction timestamp
- run id
- source evidence summary
- completeness by domain
- implementation-readiness status
- package boundary status
- dependency readiness summary
- blocked dependency references
- valid downstream work partitions by domain
- cross-module contract summary

### `summary.md`

Must include:

- module purpose
- key user journeys
- top dependencies
- top risks
- implementation recommendation
- boundary assumptions and notable dependency blockers

### `coverage-report.md`

Must include:

- complete domains
- partial domains
- blocked domains
- missing evidence
- unresolved gaps
- blocked downstream work partitions
- dependency-related readiness blockers

## Dependency Requirements

### `dependencies/graph.json`

Must include:

- inbound and outbound module references
- dependency edge type
- extraction impact or criticality
- whether the dependency is resolved, partial, blocked, or unknown

### `dependencies/references.json`

Must include:

- normalized references to external modules, contracts, assets, or services
- the package location that depends on each reference
- whether the reference is satisfied in-package or external

### `dependencies/readiness.json`

Must include:

- module-level readiness inputs
- blocked prerequisites
- unresolved upstream contracts
- dependency-constrained sequencing notes for downstream work

## Domain Requirements

Every exhaustive domain must emit:

- `inventory.json`: every discovered node or item with stable identifiers,
  parent relationships, and source path context
- `traversal-log.json`: ordered extraction steps showing what was parsed,
  extracted, resolved, blocked, or completed
- `coverage.json`: summary counts for discovered, traversed, opened, blocked,
  skipped, and unresolved items

No domain may be marked `complete` if its traversal or coverage artifacts show
unvisited discovered items.

### Flow-Tree Validation Evidence

Any artifact that emits an action, screen, process, or route `flow` must include
enough structured evidence for downstream flow-tree validation without asking
the validator to re-parse raw XML. Keep the data code-derived and practical;
when a fact cannot be resolved, emit an explicit gap instead of an empty field.

Flow-bearing records must preserve or derive:

- clear entry points: lifecycle handlers, widget events, deep links, timers,
  public actions, process starts, and any screen/block event handoff that can
  begin traversal
- reachability/navigation edges: `from_key`, `to_key`, edge/link type, branch
  label, triggering action or widget, source and target screen/block/action,
  route/deep-link target, and evidence path
- broken references: unresolved screen, action, API, entity, structure, route,
  or parameter refs with raw ref, owner, ref type, source XML path, and coverage
  impact
- circular/recursive evidence: action-call, navigation, process, or block-event
  cycles with the cycle path, recursion target, traversal depth, and bailout
  reason when traversal is bounded
- decision branch coverage: every `If`, `Switch`, error handler, and guarded
  transition must list observed true/false, case, otherwise/default, success,
  and error branches; missing expected branches are explicit gaps
- terminal states: end, success, cancel, error, feedback-message,
  download/send-email, navigation-out, and unhandled-exception outcomes, plus
  output/return bindings where present
- route, parameter, and context propagation: navigation arguments, query params,
  screen input params, local/session/client variables, and block-event payloads
  passed between nodes or screens
- async/API evidence: `is_async`, execute/run server action calls, consumed API
  operations, dataset refreshes, awaited vs fire-and-forget behavior where
  visible, and request/response parameter bindings
- complexity inputs: node count, edge count, branch count, maximum graph depth,
  terminal count, API call count, entity touch count, unresolved ref count, and
  cycle count
- stable grouping metadata: journey ids, screen ids, module names, and route
  provenance needed by consumers to derive feature filters. Do not emit a
  separate feature-lens graph; entry/reachability evidence is the source of
  truth, and feature grouping is a downstream view over that same evidence.

These fields may live on the existing `flow` object or a companion validation
summary, but they must carry stable identifiers and source evidence references.
Do not invent route targets, branch outcomes, API calls, or entity touches from
names alone.

### Interfaces

Must capture, where observable:

- flow names
- screens and blocks
- widget tree
- element tree
- themes
- forms and fields
- visible text
- conditions
- events
- screen-scoped dataflow from Preparation, ScreenActions, DataActions, and
  embedded client actions, including Assign target/source pairs, UI value
  bindings, input/control bindings, layout ancestry, and called action
  references
- assets and resource references
- layout coordinates

#### Screen dataflow (`interfaces/screen-dataflow.json`)

Every screen or block should have a UI-scoped dataflow record. Records must carry:

- flow, screen name, screen key, and screen kind
- extracted screen actions with action name, key, kind, container, assignments,
  execute-action calls, and branch conditions
- normalized field bindings with target expression, source expression,
  source action/root, source field/leaf, action key, node key, evidence path,
  `binding_role`, and `rendering_hint`. Preparation/DataAction population may
  feed rendered values, but field bindings remain dataflow evidence; no field
  binding should be treated as visible UI unless matching `ui_bindings`,
  `input_bindings`, or `widget_actions` evidence exists
- normalized UI value bindings with `label` for static labels or
  `label_expression` for source-backed dynamic labels, widget key/type, target
  expression, resolved source expression, source action/root, source
  field/leaf, evidence path, and `via_assignment` when a widget expression
  resolves through an object-copy assignment such as
  `Customer = GetCustomer.Customer`
- input/control evidence under `input_bindings`, mapping rendered input widgets
  such as Input, ComboBox, Dropdown, RadioButton, CheckBox/Checkbox, Switch,
  Toggle, Select, TextArea, and DatePicker to
  widget key/type, binding properties, event handlers, visibility/enabled
  paths, layout path, layout group, text hints such as source-backed title or
  placeholder values, and option evidence from source record lists, source
  attributes, custom `List` / `Values` / `EmptyValue` properties, or
  special-list rows when declared on the widget. When an input value
  participates in a condition referencing
  `Entities.<Entity>.StaticRecords.<Record>`, the input record may carry
  `condition_required_options` so downstream fixtures include the minimum
  option values needed to exercise dependent visibility/state changes.
- widget tree and projected UI records must preserve source layout attributes
  when present, including `Width`, `Height`, `MarginLeft`, `MarginTop`,
  `Align`, `EffectiveWidth`, `EffectiveMarginLeft`, `IsDefault`, and
  `CustomStyle`, normalized under `layout_props` while keeping direct
  snake-case fields for common consumers.
- branch and access evidence where observable: assignments and action calls may
  carry `control_path` entries describing the parent `If` / `Switch` condition
  and branch, UI bindings may carry `visibility_path` / `enabled_path`, and
  records may carry `role_gates` for `CheckRole`, `AllowAdminOnly`, or
  `RestrictAccess`
- widget event evidence under `widget_actions`, mapping widget key/type/event to
  the destination action plus any `visibility_path` / `enabled_path` /
  `style_path` / `layout_path` / `layout_group` / `role_gates` inherited from
  the widget tree. Traditional Web event metadata such as `Method`,
  `Validation`, and `ConfirmationMessage` should be preserved as
  `method`, `validation`, and `confirmation_message`.
- table evidence under `tables` for TableRecords widgets, including source
  record list, empty message, line count, start index, header/row style names,
  source column order, `headers` with header labels or `label_expression`,
  display bindings, header actions, row/cell actions, and inherited
  visibility/style/layout context
- dialog evidence under `dialogs` for source-backed modal, popup, and dialog
  widgets, including dialog kind, source block, title label or
  `label_expression`, scoped input bindings, scoped widget actions, nested
  tables when present, and inherited visibility/style/layout context
- filter evidence under `filters` for source-backed filter/search containers,
  including scoped input bindings, scoped widget actions, and inherited
  visibility/style/layout context
- footer/action-bar evidence under `footer_actions` for source-backed footer,
  bottom navigation, button-container, or action-row containers, including
  scoped button/link actions and inherited visibility/style/layout context
- form evidence under `forms` for authored forms when present, or inferred
  widget forms when controls/actions are present without a first-class source
  form node. Form rows must carry control/action counts and scoped controls and
  actions when available, so downstream validators can say which fields and
  buttons are handled instead of reducing the surface to free-form text.
- condition-required option evidence under `condition_required_options` when a
  screen condition or assignment references `Entities.<Entity>.StaticRecords`
  values. These records are not complete option lists; they mark the minimum
  synthetic options downstream UI builders must include to exercise visible
  conditions when static-data rows are unavailable. Each required option should
  include a `synthetic_option` with at least a source-backed `value` expression
  and label, so interactions such as selecting `SixMonthly` can drive dependent
  visibility even when the concrete runtime identifier is unresolved.
- state and interaction behavior evidence where observable:
  `state_defaults` for parameter/local/output defaults, `initial_state` for
  Preparation assignments that run before first render, `ajax_refresh_targets`
  for AjaxRefresh/UpdateScreen/RefreshQuery targets, and `navigation_targets`
  for WebDestination/NR destination targets and arguments.
- known WebBlock semantic evidence where the source block name identifies
  common Traditional Web/RichWidgets/OutSystemsUIWeb primitives such as shell
  layout/navigation, modal/dialog, tabs, pagination, table sorting, or upload
  controls. These hints supplement, but never replace, recursive expansion of
  the raw widget tree and placeholder children.
- layout ancestry evidence where observable: widget tree entries and projected
  UI/action/input records may carry `layout_path`, while projected records may
  carry `layout_group` with nearest row/cell/container keys so downstream UI
  rebuilds preserve same-row and same-column groupings
- CSS and class evidence where observable: widgets may carry `style_evidence`,
  `style_classes`, `style_path`, and non-authoritative `css_effect_hints` for
  classes or inline styles that may hide or disable interaction; stylesheet
  assets may carry `css_effect_rules` for rules such as `display:none`,
  `visibility:hidden`, `pointer-events:none`, or `opacity:0`
- screen/block dataflow records may carry `embedded_block_dataflow_refs` so
  downstream UI builders know which visible WebBlockInstance dataflow records
  must be recursively expanded, and `css_class_inventory` listing class usage,
  cascade files declaring each class, and explicit missing-class CSS gaps.
- explicit gaps when a screen has extracted actions but no field bindings, or
  declares parameters/local variables/data sets but no embedded screen actions

This artifact is the UI-build-facing projection of the raw action graph. Raw
flow nodes remain available in `screens.json` / `logic/client-actions.json`,
but downstream consumers should not have to mine those files to discover field
mappings such as `Customer.DeliveryPostalCode =
GetCustomer.Customer.DeliveryPostalCode`.
It also proves UI labels and value expressions that are rendered from a copied
record object, such as `Singpass -> Customer.Singpass ->
GetCustomer.Customer.Singpass`.

Must also define valid downstream work partitions, such as:

- one screen at a time
- one reusable block at a time

Interfaces must also prove exhaustive extraction of nested flows, screen groups,
screens, reusable blocks, and placeholder hierarchies from the XML.

#### Image asset resolution (`interfaces/assets-resolved.json`)

Every Image widget is pre-resolved against the module's `evidence/index.json`
(which includes locally-extracted `ReferenceImage` entries for cross-module
assets). The manifest carries `total`, `resolved`, `unresolved`, and `skipped`
counts plus per-widget records. Resolution requires the `assets` worker to run
before `interface` (see domain-extraction-architecture.md).

Classification:
- **resolved** — GUID found in the evidence index; record carries
  `asset_name`/`package_path`/`runtime_path`.
- **skipped** — dynamic/runtime images with no static GUID (e.g. data-bound or
  AEM Headless URLs resolved at runtime). These set `runtime_fixture:
  "runtime/aem-assets.json"` and are recorded via `tracker.skip` (accounted
  for, not a silent gap), backed by the pass-level AEM runtime fixture. They do
  not force interfaces to `partial`.
- **unresolved** — GUID present but absent from the evidence index; an honest
  gap that keeps interfaces `partial`.

### Logic

Must capture:

- action names
- action types
- inputs
- outputs
- variables
- branches
- assignments
- called dependencies

Must also define valid downstream work partitions, such as:

- one client action at a time
- one server action at a time

Logic must also prove exhaustive extraction of action inventories, nested action
flow graphs, branches, handlers, and expression ASTs from the XML.

### Processes

Must capture:

- process definitions
- steps
- transitions
- timers
- roles where visible

Must also define valid downstream work partitions, such as:

- one process definition at a time

Processes must also prove exhaustive extraction of process inventories,
definitions, transitions, timers, outcomes, and any nested process flow
structures from the XML.

### Data

Must capture:

- entities
- attributes
- relationships
- static data
- roles
- permissions
- site properties

Must also define valid downstream work partitions, such as:

- one entity at a time
- one related entity group at a time

Data must also prove exhaustive extraction of the full data model, including:

- all local entities and their attributes
- all reference entities from other modules
- all structures (reference and anonymous)
- entity relationships and foreign key references
- static record data
- OML site property defaults
- ServiceCenter runtime Site Property values for each enabled tenant when the
  online enrichment stage is configured

Any discovered XML element that cannot be fully extracted must be recorded in
`coverage.json` and surfaced as a blocker or partial state.

#### Runtime Site Properties

`data/site-properties.json` is OML-derived and must remain unchanged by online
enrichment. Full pass orchestration writes ServiceCenter-effective values to
`data/site-properties-runtime.json` per module. Modules with no OML Site
Properties write a `not_applicable` runtime artifact.

Runtime artifacts use this shape:

```json
{
  "schema_version": "1.0",
  "module_name": "Matrixx_IS",
  "espace_id": "617",
  "servicecenter_url": "https://...",
  "environment": "prd",
  "scraped_at": "2026-05-07T00:00:00+00:00",
  "tenant_scope": "all",
  "status": "complete",
  "expected_property_count": 12,
  "expected_value_count": 24,
  "captured_value_count": 24,
  "tenants": [
    {"tenant_id": "20", "tenant_name": "Users (Default Tenant)", "enabled": true}
  ],
  "properties": [
    {
      "name": "SomeURL",
      "tenant_id": "20",
      "tenant_name": "Users (Default Tenant)",
      "type": "Text",
      "read_only": false,
      "is_secret": false,
      "oml_default": "https://default",
      "effective_value": "https://runtime",
      "is_overridden": true,
      "source": "servicecenter_site_properties"
    }
  ],
  "errors": []
}
```

The pass-level `runtime/site-properties-summary.json` records aggregate totals,
failed modules, failed tenants, retry counts, failure reasons, parser warnings,
tenant count, and secret value count. Runtime JSON artifacts may include visible
secret values because the pass output is a rewrite evidence bundle; scripts must
never print those values in logs, scorecards, or errors and must create runtime
artifacts owner read/write only.

Runtime data/assets that are not intrinsic to the OML package must be stored as
pass-level `runtime/*` artifacts, not left as ad-hoc local files. Full pass
reruns without explicit ServiceCenter/runtime credentials copy reusable runtime
artifacts from the previous comparable pass with
`scripts/import_runtime_evidence.py`, including `runtime/aem-assets.json` and
`runtime/site-properties-summary.json`, and record the source in
`runtime/runtime-fallback-provenance.json`.

#### Accounted Evidence Gaps

`package.json` separates true extraction gaps from accounted runtime or empty
source conditions:

- `source_evidence.gaps` contains only actionable blocked, unresolved, or
  unaccounted skipped extraction work.
- `source_evidence.accounted_gaps` contains skipped items that were deliberately
  accounted for by provenance or by an empty/non-material source condition.

Known accounted skip classes:

- Dynamic interface images with no static GUID, backed by
  `runtime/aem-assets.json`.
- Empty asset placeholders such as no CSS value, no CSS directory, no root CSS
  variables, or no favicon data.

Static asset nodes with missing binary payloads remain real gaps and must stay
in `source_evidence.gaps`.

### Integrations

Must capture:

- consumed APIs
- exposed APIs
- external systems
- module references
- compatibility assumptions

Integration artifacts must preserve cross-module and external dependency
assumptions explicitly rather than embedding them only in prose.

#### Endpoint URL Enrichment

The `external-systems.json` file lists consumed REST APIs extracted from the
OML `CustomClients` fragments. The `endpoint_url` field for REST consumers is
often empty in the OML because OutSystems resolves base URLs from site
properties at deploy time. The `scripts/scrape_integration_urls.py --enrich`
step backfills these URLs from a ServiceCenter scrape. Modules with enriched
URLs will have non-empty `endpoint_url` values; modules without enrichment
retain empty strings indicating the URL is environment-configured.

### Scaffold

Must capture:

- required reusable UI primitives
- required navigation patterns
- required shared services
- backend adapters
- persistence expectations
- cross-module contracts

## Completeness Rules

A package cannot be marked implementation-ready unless:

- all required domains exist
- every missing domain is explicitly marked blocked or partial
- dependency references are present
- scaffold requirements are present
- the package boundary is valid for the module
- dependency readiness is resolved or explicitly blocked
- valid downstream work partitions are present for applicable domains
- every domain marked `complete` also has traversal evidence consistent with
  exhaustive coverage

## Partition Rules

Work partitions must:

- stay inside the owning module boundary
- preserve dependency references needed by downstream implementers
- identify blocked units when upstream prerequisites are missing
- remain stable enough that different agents can consume them independently

Parallel downstream work is dependency-constrained. The existence of multiple
files in a package does not by itself imply that all units are safe to execute
concurrently.

## Notes

This contract is intentionally implementation-oriented. It is not merely a
descriptive export; it is the handoff format for downstream rewrite work.
