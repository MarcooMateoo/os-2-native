# OutSystems Platform Context

Quick reference for AI agents working with extracted OutSystems application data.
This is not exhaustive — consult https://success.outsystems.com/documentation/
for full platform documentation.

## What is OutSystems

OutSystems is a low-code application development platform. Applications are built
visually in Service Studio (the IDE) and compiled to .NET/Java web applications.
The platform provides:
- Visual data modeling (Entities)
- Screen design with drag-and-drop widgets
- Visual logic flows (Actions) instead of writing code
- Built-in authentication, deployment, and lifecycle management
- Mobile app generation via Apache Cordova/Capacitor wrappers

## OML Files

`.oml` files are OutSystems Module Language binary files — the serialized form of
a module as edited in Service Studio. They use a proprietary binary format
(FastInfoset + SharpZipLib compressed binary XML). Each `.oml` contains the
complete definition of one module: UI, logic, data, dependencies, and assets.

## Module Types and Architecture Layers

OutSystems enforces a layered architecture. Module naming conventions indicate
the layer:

### End-User Modules (top layer)
The application entry points. Contain screens, navigation, and user-facing logic.
These consume services from lower layers but should not be consumed by other modules.
- Example: `Spitfire` (consumer app), `Spitfire_Ops` (operations portal)

### Core Modules (middle layer)
Reusable business services shared across end-user modules. Subdivided into:
- **Core Widgets (`_CW`)** — Reusable UI blocks/components
- **Core Services (`_CS`)** — Data entities and CRUD operations
- **Business Logic (`_BL`)** — Business rules and orchestration

### Foundation Modules (bottom layer)
Integration and utility services with no business logic:
- **Integration Services (`_IS`)** — REST/SOAP API wrappers
- **Libraries (`_Lib`)** — Stateless utility functions
- **Themes (`_TH`)** — CSS and design tokens

## Key Concepts

### Entities
Database tables in OutSystems. Each Entity has Attributes (columns) with types
like Text, Integer, Boolean, DateTime, etc. Entities can be:
- **Local Entities** — Owned by the current module (creates a DB table)
- **Reference Entities** — Consumed from another module (read-only reference)
- **Static Entities** — Lookup tables with predefined records (enums)

### Screens and Blocks
- **Screens** — Full pages with a URL. Contain widget trees, data fetches,
  and event handlers.
- **Blocks (Web Blocks)** — Reusable UI components that can be placed inside
  screens or other blocks. Have input/output parameters and events.

### Widget Trees
The UI is a tree of nested widgets:
- `Container` — div equivalent
- `Expression` — dynamic text/data binding
- `If` / `IfBranch` — conditional rendering
- `Link` — navigation
- `Button` — action trigger
- `Input` / `TextArea` — form fields
- `Form` — form container
- `Image` — static or dynamic images
- `WebBlockInstance` — embedded reusable block
- `Placeholder` — slot for parent content injection
- `List` / `ListItem` — repeating content
- `Table` / `TableRow` / `TableCell` — tabular layout

### Actions (Logic Flows)
Visual flow graphs that define behavior:
- **Server Actions** — Run on the server. Can access DB, call APIs.
- **Client Actions** — Run in the browser/device. Handle UI events.
- **Service Actions** — Exposed for consumption by other modules.

Flow nodes include:
- `Start` / `End` — Entry and exit points
- `Assign` — Variable assignment
- `If` — Conditional branch (True/False paths)
- `Switch` — Multi-way branch
- `ForEach` — Loop over a list
- `ExecuteAction` — Call another action
- `JavaScript` — Inline JS code node
- `RaiseError` / `ErrorHandler` — Error handling
- `FeedbackMessage` — UI toast/notification

### REST APIs
OutSystems modules can:
- **Consume** REST APIs — Call external services (wrapped in `_IS` modules)
- **Expose** REST APIs — Publish endpoints for external consumers

Consumed REST APIs are defined with URL, method, request/response structures,
authentication, and error handling.

### Processes and Timers
- **Processes** — Long-running workflows with human activities and wait states
- **Timers** — Scheduled background jobs (cron-like)

### Themes and Styling
Themes define the visual appearance:
- CSS stylesheets
- Design tokens (colors, spacing, typography)
- Base theme inheritance (typically from `OutSystemsUI`)
- Asset references (images, fonts, icons)

### Site Properties
Module-level configuration values (key-value pairs). Used for feature flags,
environment-specific settings, API keys, etc. These are the OutSystems equivalent
of environment variables.

### Dependencies
Modules declare dependencies on other modules to consume their:
- Entities (data)
- Actions (logic)
- Blocks (UI components)
- Structures (data types)
- Resources (assets)

The dependency graph defines the build and deployment order.

## Reactive vs Traditional Web

OutSystems supports two web application models:

### Reactive Web (Modern)
- Single-page application (SPA) architecture
- Client-side rendering with server data fetches
- Used by the Giga! consumer app (`Spitfire`)

### Traditional Web
- Server-side rendering with full page loads
- Classic request/response model
- Used by the Giga! operations portal (`Spitfire_Ops`)

## Mobile Apps

OutSystems mobile apps are hybrid apps:
- Built on Apache Cordova / Capacitor
- Native shell with embedded WebView
- Plugins provide access to device capabilities (camera, GPS, push, etc.)
- Distributed via App Store / Google Play
- Can work offline with local storage sync

## Relevance to This Migration

When reading extracted module data, keep in mind:
- **Widget trees** map to Flutter widget hierarchies
- **Server Actions** map to Go backend service methods
- **Client Actions** map to Flutter state management / BLoC logic
- **Entities** map to database tables (PostgreSQL or similar)
- **REST API consumers** map to Go HTTP client code (contracts are frozen)
- **Themes/CSS** map to Flutter ThemeData and design tokens
- **Site Properties** map to environment configuration
- **Processes** map to workflow/state machine implementations
- **Timers** map to cron jobs or scheduled Go tasks
