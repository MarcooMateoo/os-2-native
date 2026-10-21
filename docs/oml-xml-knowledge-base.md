# OML XML Knowledge Base

Comprehensive reference for extracting structured data from OutSystems OML files.
This document captures everything learned from building the Catalyst extraction
pipeline, intended to survive across sessions and serve future agents.

---

## 1. What Is an OML File?

An `.oml` file is OutSystems 11's proprietary binary module format. It contains
the complete definition of a single OutSystems module: UI screens, logic flows,
data models, processes, assets, dependencies, and CSS.

**Key fact**: The `.oml` binary is NOT directly parseable. It must first be
deserialized to XML using the `oml-utilities` tool.

## 2. OML-to-XML Conversion

### Tool: oml-utilities

- **Repo**: github.com/silviogarbes/oml-utilities (C# / .NET)
- **Requires**: .NET 10 SDK (or matching build target)
- **Uses**: Embedded OutSystems platform DLLs for deserialization
- **Supports**: OutSystems 9.1, 10, and 11 OML files

### Command

```bash
export DOTNET_ROOT="/opt/homebrew/opt/dotnet/libexec"
dotnet /tmp/oml-utilities/OmlUtilities/bin/Release/net10.0/oml.dll \
  manipulate "<input.oml>" "<output.xml>" --format xml
```

### Output

A single XML file with this top-level structure:

```xml
<OML>
  <eSpaceFragment FragmentName="ESpace" Count="1">
    <ESpace Name="ModuleName" ApplicationName="AppName" ...>
      <!-- Module-level metadata, inline children -->
    </ESpace>
  </eSpaceFragment>
  <eSpaceFragment FragmentName="Entities" Count="N">
    <Entity Name="..." Key="..." .../>
    ...
  </eSpaceFragment>
  <eSpaceFragment FragmentName="NRWebFlows" Count="N">
    ...
  </eSpaceFragment>
  <!-- Hundreds of fragments -->
</OML>
```

Typical sizes: 14 MB OML → 28 MB XML, ~395 fragments.

---

## 3. XML Structure: Fragments

The XML is organized into **fragments** — `<eSpaceFragment>` elements identified
by their `FragmentName` attribute. Key fragments:

| FragmentName | Contents |
|---|---|
| `ESpace` | Module root element with metadata |
| `Entities` | Local entity definitions |
| `NRWebFlows` | UI flows (screens, blocks) |
| `NRThemes` | Theme definitions |
| `References` | All external module dependencies |
| `AnonymousStructures` | Inline/anonymous record structures |
| `ListTypes` | List type wrappers |
| `SiteProperties` | Configuration properties |
| `Flows` | Server actions, processes (inside ESpace) |
| `NodesNotShownInESpaceTree#<Key>` | Flow nodes for a specific action (keyed by action Key) |
| `NodesShownInESpaceTree#<Key>` | Visible nodes for screens/processes |
| `Images` | Image assets with base64 binary data |

### Fragment Access Pattern

```python
from catalyst.xml_loader import load_oml_xml

doc = load_oml_xml("path/to/file.xml")
doc.module_name          # e.g., "CBG_Signup_CW"
doc.espace               # The <ESpace> element
doc.fragment("Entities") # The <eSpaceFragment> for entities
doc.fragment_children("Entities")  # Direct children of that fragment
```

**Important**: Flow nodes for a specific action are stored in fragments named
`NodesNotShownInESpaceTree#<action_key>`, where `<action_key>` is the `Key`
attribute of the action. This is how the pipeline finds flow nodes for each
server/client action.

---

## 4. The Key System

Every significant element in the XML has a `Key` attribute — a globally unique
identifier (e.g., `fWtJs57lI0qCTlwkcDoxDA`). Keys are used for:

1. **Cross-references**: One element refers to another via its Key
2. **Fragment addressing**: `NodesNotShownInESpaceTree#<Key>` fragments
3. **Type references**: `Entity:/Entities.<Key>`, `ReferenceStructure:/References.<Key>/ReferenceStructures.<Key>`
4. **Expression references**: `Variables.LocalVariable:/NRWebFlows.<Key>/LocalVariables.<Key>`

### Ref Path Format

References follow the pattern: `TypeHint:/<Tag>.<Key>/<Tag>.<Key>/...`

Example:
```
Variables.LocalVariable:/NRWebFlows.fWtJs57lI0qCTlwkcDoxDA/LocalVariables.7SSYGMnxY0S+_zwShl4INw
```

- `Variables.LocalVariable` — type hint (what kind of thing is referenced)
- After `:`, each `/`-separated segment is `Tag.Key`
- Resolved by looking up each Key in the KeyIndex to get its Name

### KeyIndex

The `KeyIndex` (in `src/catalyst/shared/key_index.py`) walks ALL elements in
the XML and builds a `Key → Name` mapping. It's the foundation for resolving
all cross-references.

```python
from catalyst.shared.key_index import build_key_index
ki = build_key_index(doc)
ki.name("fWtJs57lI0qCTlwkcDoxDA")      # → "MainFlow"
ki.resolve_ref("Variables.LocalVariable:/NRWebFlows.KEY1/LocalVariables.KEY2")
# → "NRWebFlows.MainFlow.LocalVariables.IsLoggedIn"
```

---

## 5. Type System

### Primitive Types

OutSystems encodes primitive types as `%key` hashes. These are platform-global
constants (same across all OML files):

| Key | Type |
|-----|------|
| `%j2Lc0phK0kuggij4gQYfAw` | Integer |
| `%H6G+eTAigEi7LPUbg3Ey0g` | Long Integer |
| `%uROOBXPvQEyU76NWO+1uxQ` | Text |
| `%oD0fxvc7hUOX_305zIXIlg` | Boolean |
| `%snOQIa2+VEi7MGyYOu7OpA` | Decimal |
| `%AIYAbYIrH0SUg8DUlzCMjQ` | Email |
| `%uCRxZE_fNUyUSGrF0nnRIw` | PhoneNumber |
| `%TI32kQokwEyLhBq6zdg2Tw` | DateTime |
| `%T8i8SBbo_EyGNoo0ssngsw` | Binary Data |

### Compound Type References

Non-primitive types are encoded as path strings:

| Pattern | Meaning | Regex |
|---------|---------|-------|
| `Entity:/Entities.<Key>` | Local entity | `^Entity:/Entities\.([^/]+)$` |
| `EntityIdentifierType:/Entities.<Key>/IdentifierType` | Local entity ID | `^EntityIdentifierType:/Entities\.([^/]+)/IdentifierType$` |
| `AnonymousStructure:/AnonymousStructures.<Key>` | Inline record | `^AnonymousStructure:/AnonymousStructures\.([^/]+)$` |
| `ListType:/ListTypes.<Key>` | List of something | `^ListType:/ListTypes\.([^/]+)$` |
| `ReferenceStructure:/References.<RefKey>/ReferenceStructures.<StructKey>` | External structure | See code |
| `ReferenceEntity:/References.<RefKey>/ReferenceEntities.<EntityKey>` | External entity | See code |
| `EntityIdentifierType:/References.<RefKey>/ReferenceEntities.<EntityKey>/IdentifierType` | External entity ID | See code |
| `ReferenceStructure:^/^/ReferenceStructures.<Key>` | Relative ref (within same reference module) | See code |
| `EntityIdentifierType:^/^/IdentifierType` | Relative entity ID (within current entity context) | See code |

### TypeResolver

`src/catalyst/shared/type_resolver.py` resolves all of these. Create from a doc:

```python
resolver = TypeResolver.from_doc(doc)
result = resolver.resolve(type_str, current_reference=ref_mod)
# Returns: {"kind": "primitive", "name": "Text"} or
#          {"kind": "reference_structure", "name": "SignUpConfig", ...} etc.
```

It handles recursive types (List of List of Structure) with depth limiting.

---

## 6. System Exception Keys

ErrorHandler nodes reference system-level exceptions via `%key` hashes (same
pattern as primitive types, but for exceptions). Known mappings:

| Key | Exception |
|-----|-----------|
| `%VI1iiDkgT0Szaid0LqgP_w` | AllExceptions (catch-all) |
| `%TcVUoQU5mE6mvZaG9PwyCg` | UserException |

These are in `SYSTEM_EXCEPTION_NAMES` in `type_resolver.py`. When new modules
are processed and new exception `%key` values appear, add them to this dict.

---

## 7. Expression Model (ParsedExpression AST)

Conditions, assignments, and argument values use a `<ParsedExpression>` AST.
The AST is a tree of typed nodes:

### AST Node Types

| Tag | Meaning | Key Attributes |
|-----|---------|----------------|
| `Integer` | Integer literal | `Value="42"` |
| `Text` | Text literal | `Value="hello"` |
| `Boolean` | Boolean literal | `Value="True"` |
| `Decimal` | Decimal literal | `Value="3.14"` |
| `Identifier` | Single variable ref | `Ref="<key_path>"` |
| `CompoundIdentifier` | Dotted path ref | `Ref="<key_path>"` |
| `BinaryOperation` | Binary op | `Operator="Eq"/"NEq"/"GT"/"Or"/"And"/"Add"/...` with 2 children |
| `UnaryOperation` | Unary op | `Operator="Not"` with 1 child |
| `Call` | Function call | `Ref="<function_key_path>"` with children as args |
| `Concat` | String concat | Children are parts to concatenate |
| `IfExpression` | Ternary | 3 children: condition, then, else |
| `NullLiteral` | Null value | (no attributes) |

### Operator Symbols

`Eq` → `=`, `NEq` → `<>`, `GT` → `>`, `GTE` → `>=`, `LT` → `<`, `LTE` → `<=`,
`Or` → `or`, `And` → `and`, `Add` → `+`, `Sub` → `-`, `Mul` → `*`, `Div` → `/`,
`Mod` → `mod`, `Like` → `like`

### Expression Resolver

`src/catalyst/shared/expression_resolver.py` walks the AST and produces
human-readable strings:

```python
from catalyst.shared.expression_resolver import resolve_expression
result = resolve_expression(parsed_expr_elem, key_index)
# → 'LoginEndUser.IsESSO = "ESSO"'
```

### Where ParsedExpressions Appear

- `Nodes.If/Condition/ParsedExpression` — if conditions
- `Nodes.Assign/Assignments/*/Variable/ParsedExpression` — assignment targets
- `Nodes.Assign/Assignments/*/Value/ParsedExpression` — assignment values
- `*/Arguments/*/Value/ParsedExpression` — action argument bindings
- `Nodes.RaiseError/Message/ParsedExpression` — error messages
- `NRNodes.FeedbackMessage/Message/ParsedExpression` — feedback messages
- `NRNodes.Download/Content/ParsedExpression` — file content
- `NRNodes.Download/Filename/ParsedExpression` — file name
- `NRNodes.SendEmail/From|To|CC|BCC/ParsedExpression` — email fields
- `Nodes.DataSet/MaxRecords/ParsedExpression` — max records
- `Nodes.ForEach/RecordList/ParsedExpression` — record list source
- `DefaultValue/ParsedExpression` — default values on parameters
- `Length/ParsedExpression` — field length specifications

---

## 8. XML Tag Catalog

### UI Domain

| Concept | XML Tag |
|---------|---------|
| UI Flow | `NRFlows.WebFlow` |
| Screen / Block | `NRNodes.WebBlock` (inside WebFlow's NodesShownInESpaceTree fragment) |
| Email Screen | `NRNodes.EmailScreen` |
| Container widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Container` |
| Button widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Button` |
| Text widget | `NRWebWidgets.Text` |
| Expression widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Expression` |
| Link widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Link` |
| Input widget | `NRWebWidgets.ReferenceCustomWidget-OutSystems.Plugin.NRWidgets.ReferenceInput` |
| Form widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Form` |
| Image widget | `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Image` |
| If/IfBranch | `NRWebWidgets.If` / `NRWebWidgets.IfBranch` |
| WebBlock instance | `NRWebWidgets.WebBlockInstance` |
| Placeholder | `NRWebWidgets.Placeholder` / `NRWebWidgets.CustomPlaceholderWidget` |
| Theme | `NewRuntime.Theme` / `NewRuntime.ReferenceTheme` |
| Stylesheet | `WebStyleSheet` / `ReferenceWebStyleSheet` with `<Value>` CSS |
| Client Action Flow | `NRFlows.ClientScreenActionFlow` |
| Data Action Flow | `NRFlows.DataScreenActionFlow` |

### Logic Domain — Flow Node Types

| Concept | XML Tag | Enrichment Fields |
|---------|---------|-------------------|
| If | `Nodes.If` | `condition` (resolved expression) |
| Execute Action | `Nodes.ExecuteAction` | `action_ref`, `arguments` |
| Run Client Action | `Nodes.RunClientAction` | `action_ref`, `arguments` |
| Run Server Action | `Nodes.RunServerAction` | `action_ref`, `arguments` |
| Assign | `Nodes.Assign` | `assignments` (variable/value pairs) |
| Start | `Nodes.Start` | (position only) |
| End | `Nodes.End` | (position only) |
| Switch | `Nodes.Switch` | `condition`, `switch_conditions` |
| Raise Error | `Nodes.RaiseError` | `message` (expression) |
| Error Handler | `Nodes.ErrorHandler` | `exception`, `abort_transaction`, `log_error` |
| Comment | `Nodes.Comment` | `text` |
| JSON Serialize | `Nodes.JSONSerialize` | `data_ref` |
| Refresh Query | `Nodes.RefreshQuery` | `query_ref` |
| For Each | `Nodes.ForEach` | `record_list` (expression) |
| DataSet (Aggregate) | `Nodes.DataSet` | `dataset_name`, `output_type`, `max_records`, `query` |
| JavaScript | `NRNodes.JavascriptNode` | `javascript_code`, `js_input_parameters`, `js_output_parameters`, `arguments` |
| Execute Client Action | `NRNodes.ExecuteClientAction` | `action_ref`, `arguments` |
| Execute Server Action | `NRNodes.ExecuteServerAction` | `action_ref`, `arguments` |
| Destination | `NRNodes.Destination` | `target_ref` |
| Feedback Message | `NRNodes.FeedbackMessage` | `message_type`, `message` |
| Trigger Event | `NRNodes.TriggerEvent` | `event_ref`, `arguments` |
| Download | `NRNodes.Download` | `content`, `filename` |
| Send Email | `NRNodes.SendEmail` | `email_screen`, `from_expr`, `to_expr`, `cc_expr`, `bcc_expr`, `arguments` |

### Flow Links (Edges)

Links are **nested inside** each node as `<Links>` child elements (not top-level
siblings). Link types:

| Tag | Meaning |
|-----|---------|
| `Links.Sequence` | Normal sequential flow |
| `Links.True` | True branch (from If/Switch) |
| `Links.False` | False branch (from If/Switch) |

Each link has `@TargetNode` attribute pointing to the Key of the destination node.

### Data Domain

| Concept | XML Tag |
|---------|---------|
| Local entity | `Entity` (in Entities fragment) |
| Entity attribute | `EntityAttribute` (child of Entity) |
| Static entity records | `StaticRecords` (child of Entity, `IsStaticEntity="Yes"`) |
| Reference entity | `ReferenceEntity` (in References fragment) |
| Reference entity attribute | `ReferenceEntityAttribute` |
| Reference structure | `ReferenceStructure` |
| Reference structure attribute | `ReferenceStructureAttribute` |
| Anonymous structure | `AnonymousStructure` (in AnonymousStructures fragment) |
| Site property | `Variables.SiteProperty` (in SiteProperties fragment) |

### Process Domain

| Concept | XML Tag |
|---------|---------|
| Process | `Flows.Process` |
| Process Activity | `ProcessNodes.Activity` |
| Process Input Parameter | `Variables.ProcessInputParameter` |

### Dependencies (References Fragment)

Each `<Reference>` element in the References fragment represents one external
module dependency. Structure:

```xml
<Reference Name="CBG_MSA_BL" ReferenceType="..." Key="...">
  <ReferenceEntities>
    <ReferenceEntity Name="User" Key="...">
      <Attributes>
        <ReferenceEntityAttribute Name="Id" Type="%j2L..." IsMandatory="Yes" .../>
      </Attributes>
    </ReferenceEntity>
  </ReferenceEntities>
  <ReferenceStructures>
    <ReferenceStructure Name="SignUpConfig" Key="...">
      <Attributes>
        <ReferenceStructureAttribute Name="EnableOTP" Type="%oD0..." .../>
      </Attributes>
    </ReferenceStructure>
  </ReferenceStructures>
  <ReferenceActions>
    <ReferenceAction Name="GetSignUpConfig" Key="...">
      <InputParameters>
        <Variables.ReferenceGenericInputParameter Name="..." Type="..." IsMandatory="..."/>
      </InputParameters>
      <OutputParameters>
        <Variables.ReferenceGenericOutputParameter Name="..." Type="..."/>
      </OutputParameters>
    </ReferenceAction>
  </ReferenceActions>
  <ReferenceClientActions>
    <ReferenceClientAction Name="..." Key="...">...</ReferenceClientAction>
  </ReferenceClientActions>
  <ReferenceNRWebFlows>
    <!-- Referenced UI flows/blocks/screens from other modules -->
  </ReferenceNRWebFlows>
</Reference>
```

---

## 9. DataSet (Aggregate) Query Structure

`Nodes.DataSet` elements contain inline query definitions:

```xml
<Nodes.DataSet Name="GetUsers" Key="..." Type="...">
  <MaxRecords><ParsedExpression>...</ParsedExpression></MaxRecords>
  <Table>
    <DataTable>
      <TableOperations>
        <DataSetOperations.AddSource Source="Entity:/Entities.KEY" Key="..."/>
        <DataSetOperations.CombineSources Key="...">
          <Sources>...</Sources>
          <Joins>
            <DataSetOperations.JoinCondition ... />
          </Joins>
          <Filters>
            <DataSetOperations.DynamicFilter>
              <Filter><ParsedExpression>...</ParsedExpression></Filter>
            </DataSetOperations.DynamicFilter>
          </Filters>
          <Sorts>
            <DataSetOperations.DynamicSort>
              <SortColumn><ParsedExpression>...</ParsedExpression></SortColumn>
            </DataSetOperations.DynamicSort>
          </Sorts>
          <CalculatedAttributes>...</CalculatedAttributes>
        </DataSetOperations.CombineSources>
        <DataSetOperations.GroupBy Key="..."/>
      </TableOperations>
    </DataTable>
  </Table>
</Nodes.DataSet>
```

The `Source` attribute on `AddSource` is a type reference (e.g.,
`Entity:/Entities.KEY`) that resolves to an entity name via the TypeResolver.

---

## 10. JavaScript Node Structure

JavaScript nodes embed source code in a special format with interleaved text
and OutSystems variable references:

```xml
<NRNodes.JavascriptNode Name="SetCookie" Key="...">
  <JavascriptSource>
    <JavascriptExpression>
      <Text JSText="document.cookie = "/>
      <Reference Ref="Variables.LocalVariable:/..."/>
      <Text JSText=" + '=' + "/>
      <Reference Ref="Variables.LocalVariable:/..."/>
    </JavascriptExpression>
  </JavascriptSource>
  <JSInputParameters>
    <Variables.JSInputParameter Name="CookieName" Type="%uROOB..." />
  </JSInputParameters>
  <JSOutputParameters>
    <Variables.JSOutputParameter Name="Result" Type="%oD0f..." />
  </JSOutputParameters>
  <Arguments>...</Arguments>
</NRNodes.JavascriptNode>
```

The `<Text>` and `<Reference>` elements are concatenated to reconstruct the
full JavaScript source, with references resolved to variable names via KeyIndex.

---

## 11. Widget Tree Structure

Screens and blocks contain widget trees. Widgets are nested via:

- `ChildWidgets` — direct children
- `ChildPlaceholders` / `PlaceholderArguments` — content inserted into placeholders
- `Condition/ParsedExpression` — visibility conditions on `NRWebWidgets.If`

Widget tags follow patterns:
- `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.<WidgetName>` — built-in widgets
- `NRWebWidgets.ReferenceCustomWidget-<module>.<WidgetName>` — referenced widgets
- `NRWebWidgets.WebBlockInstance` — instance of a web block (has `Block` attribute referencing the block Key)
- `NRWebWidgets.Text` — static text
- `NRWebWidgets.If` / `NRWebWidgets.IfBranch` — conditional rendering

Key widget attributes:
- `ExtendedClass` — CSS class applied
- `Style` — inline style
- `Visible` — visibility flag

---

## 12. Theme Structure

Themes are in the `NRThemes` fragment:

```xml
<NewRuntime.Theme Name="EmailTheme" Key="..." BaseTheme="..." 
    UseGrid="Yes" GridColumns="12" ColumnWidth="60" GutterWidth="20">
  <StyleSheet/>  <!-- Usually empty for these modules -->
</NewRuntime.Theme>
```

Grid layout attributes: `UseGrid`, `GridColumns`, `ColumnWidth`, `GutterWidth`,
`GutterPercentage`.

**Important limitation**: In the modules we've extracted, all 98 `StyleSheet`
elements are empty. Real CSS lives in referenced external theme modules (e.g.,
`OutSystemsUI`). Extracting those modules separately would yield the CSS.

---

## 13. Pipeline Architecture

```
src/catalyst/
  xml_loader.py           Load XML, build fragment index → OmlDocument
  shared/
    key_index.py          Build Key→Name mapping from all XML elements
    type_resolver.py      Resolve %key types and type path strings
    expression_resolver.py Walk ParsedExpression ASTs → human-readable strings
    coverage.py           Coverage tracking + JSON writer utility
  workers/
    interface.py          Extract screens, blocks, widgets, themes, CSS
    logic.py              Extract server/client actions, flow graphs, node enrichment
    process.py            Extract process definitions and transitions
    data.py               Extract entities, relationships, static data, site properties
    assets.py             Extract images, favicon, CSS files
  orchestrator.py         Assemble package: dependencies, stubs, integrations, scaffold
  pipeline.py             Top-level pipeline: Stage 1 → Stage 2 → Stage 3
  cli.py                  CLI entry point: `catalyst extract`
```

### Execution Flow

1. `load_oml_xml(path)` → `OmlDocument` with indexed fragments
2. Each domain worker receives `(doc, pkg_root)`:
   - Builds `KeyIndex` and `TypeResolver` from `doc`
   - Iterates over relevant fragments
   - Resolves all types and expressions
   - Writes JSON output files + coverage report
3. Orchestrator receives `(doc, pkg_root)`:
   - Builds dependency graph, references (with field definitions), stubs
   - Builds integrations, scaffold requirements, persistence requirements
   - Aggregates coverage → package.json, summary.md, coverage-report.md

### Output Package Structure

```
<ModuleName>/
  package.json              Module metadata + readiness assessment
  summary.md                Human-readable module summary
  coverage-report.md        Domain coverage report
  interfaces/
    screens.json            Screen definitions with widget trees + client actions
    blocks.json             Web block definitions
    themes.json             Theme configs with grid layout
    assets.json             Image asset manifest
    inventory.json          UI element inventory
    partitions.json         Extraction partitions
    coverage.json           Domain coverage metrics
    traversal-log.json      XML traversal audit log
  logic/
    server-actions.json     Server action definitions with flow graphs
    client-actions.json     Client action definitions with flow graphs
    contracts.json          Reference action contracts (external API surface)
    actions.json            Combined action inventory
    inventory.json          Logic element inventory
    partitions.json / coverage.json / traversal-log.json
  processes/
    transitions.json        Process definitions with activity transitions
    inventory.json / partitions.json / coverage.json / traversal-log.json
  data/
    entities.json           Entity definitions with attributes, static records
    relationships.json      Entity-to-entity relationships
    static-data.json        Static entity record data
    site-properties.json    Configuration properties
    roles-and-permissions.json
    inventory.json / partitions.json / coverage.json / traversal-log.json
  dependencies/
    graph.json              Module dependency graph (edges)
    references.json         All external references with field-level attributes
    stubs.json              Typed dependency stubs grouped by module
    readiness.json          Dependency resolution status
  integrations/
    consumed-apis.json      APIs consumed from other modules
    exposed-apis.json       APIs exposed by this module
    external-systems.json
    compatibility-notes.md
  scaffold/
    requirements.json       Theme/navigation/shared block requirements
    shared-components.json
    backend-adapters.json
    persistence-requirements.json
  evidence/
    index.json              Asset extraction manifest
    assets/                 Extracted binary assets (images, favicon)
    coverage.json / traversal-log.json
```

---

## 14. Known Gotchas and Edge Cases

1. **Fragment naming for flow nodes**: Flow nodes live in fragments named
   `NodesNotShownInESpaceTree#<action_key>` (note the `#` separator). Screen-level
   visible nodes use `NodesShownInESpaceTree#<screen_key>`.

2. **Links are nested**: Flow links (`Links.Sequence`, `Links.True`, `Links.False`)
   are children of the source node element under a `<Links>` wrapper — NOT
   top-level siblings. Each link has `@TargetNode` pointing to the destination Key.

3. **`%key` hashes are platform constants**: Both primitive types and system
   exceptions use `%key` hashes that are identical across all OML files. When
   encountering a new unrecognized `%key`, it's likely a new platform type or
   exception — log it and add to the static mapping.

4. **Empty StyleSheets**: For many modules, `<StyleSheet/>` elements contain no
   CSS. The actual CSS lives in external theme modules (like `OutSystemsUI`).
   This is normal, not a bug.

5. **Relative type references**: Inside a Reference block, type attributes may
   use relative paths like `ReferenceStructure:^/^/ReferenceStructures.<Key>`.
   The `^/^/` means "go up to the parent Reference element." The TypeResolver
   handles this via `current_reference` context.

6. **`expression_value()` for defaults/lengths**: Scalar values like default
   values and field lengths are wrapped in `ParsedExpression` ASTs even when
   they're just a single literal. The `expression_value()` helper extracts the
   scalar value directly.

7. **Widget tag names are very long**: OutSystems widget tags like
   `NRWebWidgets.CustomWidget-OutSystems.Plugin.NRWidgets.Container` should be
   simplified to just the last segment (`Container`) for readability.

8. **Test approach**: The pipeline is tested against a "golden module"
   (`CBG_Signup_CW`) with known characteristics. Tests use a shared fixture
   that runs the full pipeline once and validates the output. The golden XML
   is at `tmp/CBG_Signup_CW_v1180.xml` (referenced in `tests/conftest.py`).

9. **Python 3.12+ required**: The codebase uses modern Python features
   (type unions `X | Y`, `dict[str, Any]` generics).

10. **No external Python deps**: Only stdlib (`xml.etree.ElementTree`, `json`,
    `base64`, `pathlib`, `re`). The `.venv` has only `pytest` for testing.

---

## 15. Extending the Pipeline

### Adding a New Node Type

1. Add the tag to `_KNOWN_NODE_PREFIXES` in `logic.py`
2. Add an `elif tag == "NewNodes.Whatever":` branch in `_parse_flow_node()`
3. Extract relevant attributes and child expressions
4. Add a test in `test_pipeline.py` validating the enrichment

### Adding a New %key Mapping

1. Find the `%key` value in the XML
2. Determine what it maps to (examine context, search across modules)
3. Add to `PRIMITIVE_TYPE_NAMES` (for types) or `SYSTEM_EXCEPTION_NAMES`
   (for exceptions) in `type_resolver.py`

### Processing a New Module

```bash
# Convert OML to XML
dotnet oml.dll manipulate NewModule.oml tmp/NewModule.xml --format xml

# Run the pipeline
catalyst extract --xml tmp/NewModule.xml --output output/
```

The pipeline is module-agnostic — it handles any O11 module. New `%key` values
or unknown node types will be logged and can be added incrementally.

---

## 16. Modules Extracted So Far

| Module | Screens | Client Actions | Server Actions | Key Characteristics |
|--------|---------|---------------|----------------|---------------------|
| CBG_Signup_CW | 41 | 290 | 11 | Golden module, 1 SendEmail, 1 Process |
| CBG_Torpedo_CW | 407 | 585 | 5 | Largest module, 1 DataSet |
| Common_CW | 19 | 76 | 2 | Shared utilities, 40 DataSets, 3 Downloads |
