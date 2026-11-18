# Recommended Tech Stack: OutSystems 11 → Native on AWS

Target stack for rebuilding OutSystems 11 (O11) Reactive Web apps, Service Applications and Mobile apps as hand-written code, developed locally first and deployed to AWS. Each project records its own choices for every layer below in `projects/<Project>/project.json` (`"stack"`), with `extractor/scripts/project_stack.py` (`init` fills in these defaults, `show` lists the open decisions, `set` records them). The rebuild starts from the specification that [os2native](extractor/README.md) (in `extractor/`) extracts from the exported modules; see [What os2native provides](#what-os2native-provides-for-this-stack).

## Why this stack

- **O11 Reactive already compiles to React**, so screens, blocks and client actions map naturally to React components and hooks.
- **The O11 server runs on .NET (C#)**, so the C# in Integration Studio extensions is familiar territory, and the data model maps directly to EF Core. The extensions still have to be ported: they target .NET Framework 4.6–4.8 and use OutSystems runtime libraries (see [Key decisions](#key-decisions-to-make-early)).
- **Web and mobile share one language (TypeScript)**, along with API clients, types and validation.
- **The same Linux containers run on a laptop and on AWS**, so going live is a configuration change rather than a rewrite.

## Stack summary

| Layer | Recommendation |
|---|---|
| **Web front end** | React + TypeScript + Vite (single-page app) |
| UI components | shadcn/ui (Tailwind) or MUI |
| Server data | TanStack Query |
| Client state | Zustand |
| Forms and validation | react-hook-form + zod |
| Translations | i18next |
| **Back end** | ASP.NET Core on .NET 10 LTS (supported to Nov 2028; .NET 8 support ended Nov 2026), run as a modular monolith (API + worker). Alternative: Node.js + TypeScript (see Key decisions) |
| Data access | EF Core + Dapper (for ported Advanced SQL), EF Core migrations |
| API contract | OpenAPI → generated TypeScript clients (Orval / NSwag) |
| Consumed APIs | Refit / HttpClient |
| Jobs and workflows | Hangfire, or EventBridge Scheduler + SQS; a queue worker for BPT-style processes (Step Functions only if processes grow) |
| **Mobile** | React Native (Expo) + TypeScript |
| Mobile offline | expo-sqlite (+ Drizzle) or WatermelonDB |
| Mobile build and release | EAS Build / Submit / Update |
| Code structure | Monorepo (pnpm + Turborepo) with shared packages |
| **Auth** | Amazon Cognito or the corporate identity provider over OIDC; Roles become groups/claims |
| **Database** | Aurora PostgreSQL (the schema os2native generates); RDS SQL Server only if the live OutSystems database is kept (see Key decisions) |
| **Web hosting** | S3 + CloudFront + AWS WAF |
| **API hosting** | ECS Fargate + Application Load Balancer, images in ECR |
| File storage | S3 |
| Email | Amazon SES |
| Cache / sessions | ElastiCache (Redis / Valkey) |
| Configuration and secrets | SSM Parameter Store / AppConfig + Secrets Manager |
| Monitoring | CloudWatch + OpenTelemetry / X-Ray |
| Infrastructure as code | AWS CDK (TypeScript) or Terraform |
| CI/CD | GitHub Actions or AWS CodePipeline |
| Environments | One AWS account per environment (Dev / Test / Prod) under AWS Organizations |

## How OutSystems concepts map to the new stack

| OutSystems 11 | Target |
|---|---|
| Screens / Blocks | React components (web), React Native screens (mobile) |
| Aggregates / Data Actions | API endpoints + TanStack Query |
| Client Variables | Zustand / localStorage |
| Client Actions | TypeScript functions in shared packages |
| Server Actions | ASP.NET Core service classes |
| Service Actions / Service Applications | Internal modules of the monolith; split into separate services only when needed |
| Entities | EF Core entities on the PostgreSQL schema from `deliverables/db-schema/` (clean `module__entity` names, delete rules, indexes) |
| Mobile client entities | Not in the database: listed in `db-schema/client-entities.md`, rebuilt in expo-sqlite |
| Advanced SQL | Dapper queries, ported with the checklist in `deliverables/sql-porting/` |
| Exposed REST | ASP.NET Core controllers / Minimal APIs + OpenAPI; API Gateway for partner APIs |
| Consumed REST | Refit / HttpClient |
| C# Extensions | Ported from `extensions/<Name>/source/` to .NET 10 class libraries (dropping the OutSystems runtime types), or replaced by a library |
| Timers | Hangfire or EventBridge Scheduler → SQS → worker. Schedules usually live in Service Center, not in the module: collect them |
| BPT / Light processes | SQS + worker (Step Functions if they become complex) |
| Users app and Roles | Cognito / OIDC identity provider; roles become claims and policies (users need a password reset). References to `(System).User` become a local users table keyed by the IdP user id (`db-schema/cross-module-fks.md`) |
| Site Properties | SSM Parameter Store / AppConfig |
| Binary data in entities | S3 objects, with only the key stored in the database |
| Multilingual Locales | i18next (only if the app has more than one locale) |
| Email templates | SES + Razor / MJML templates |
| Local Storage (mobile) | expo-sqlite + custom sync endpoints |
| Cordova plugins | Expo modules and config plugins; plugins without an Expo Go module (e.g. OneSignal, root detection) need an Expo development build |
| MABS | EAS Build |
| Service Center logs | CloudWatch + OpenTelemetry |
| LifeTime | CI/CD pipeline + infrastructure as code |

## AWS architecture

```
Users ──► CloudFront (+ WAF) ──► S3 (React SPA)
                │
                └─► /api/* ──► ALB ──► ECS Fargate: ASP.NET Core API
                                         │
Mobile (Expo) ───────────────────────────┘     ECS Fargate: Worker (timers/jobs)
                                         │
                Aurora PostgreSQL · S3 (files)
                ElastiCache · SQS / EventBridge · SES
                Cognito · Secrets Manager · CloudWatch
```

## Local development (laptop first)

| AWS (later) | Laptop (now) |
|---|---|
| ECS Fargate (API + worker) | Docker Compose or .NET Aspire |
| S3 + CloudFront (web app) | Vite dev server |
| RDS / Aurora | PostgreSQL container (SQL Server container only if the live database is kept) |
| S3, SQS, EventBridge, SSM, Secrets Manager | LocalStack (or MinIO for S3) |
| SES | Mailpit |
| ElastiCache | Redis / Valkey container |
| Cognito | Keycloak container, or a real dev Cognito pool |
| CloudWatch / X-Ray | OpenTelemetry → Aspire Dashboard or Jaeger |

**Laptop tools (Windows):** .NET SDK, Node.js LTS, Git, Docker Desktop with WSL2 (needs a paid licence at large companies; Rancher Desktop and Podman Desktop are free alternatives), VS Code / Visual Studio / Rider, Android Studio. iOS builds need a Mac or EAS cloud builds.

**Rules that keep the AWS move simple:**

1. Build and test Linux containers from day one.
2. Put all settings (connection strings, bucket names, URLs) in configuration, never in code.
3. Use the real AWS SDKs, pointed at local emulator endpoints during development.
4. Write infrastructure as code alongside the application.
5. Use masked or synthetic data locally, never production dumps.
6. Deploy to an AWS dev account early to surface IAM, networking, Cognito and SES-sandbox differences.

## Key decisions to make early

1. **Database path.** The two options lead to different migrations:
   - **New Aurora PostgreSQL schema** (recommended, and what os2native supports): `generate_db_schema.py` produces the DDL and seed data, and Advanced SQL is ported with the `generate_sql_porting.py` checklist. Data moves with a one-off migration and a cut-over, so old and new apps can't share a database while running side by side.
   - **Keep the live OutSystems SQL Server database** (RDS SQL Server): old and new apps can run side by side on the same data, but the physical table names (`OSUSR_*`) aren't in the exported modules. They come from the platform database (`ossys_Entity`), which os2native doesn't read, and nothing generates SQL Server DDL. Babelfish for Aurora can bridge T-SQL if needed.
2. **Back-end language: .NET 10 or Node.js (TypeScript).** .NET is closer to OutSystems, but the extensions still need porting. The ones seen so far are small utilities (CSV, regex, HTML parsing, MIME detection, chunked upload) on .NET Framework 4.x with OutSystems runtime dependencies, and npm has equivalents for all of them. Node.js gives one language from database to mobile. Spring Boot (Java) is an option if that is the company standard. The rest of the stack is the same either way.
3. **Identity provider:** Cognito or the corporate IdP. Decide this before rebuilding screens, because role checks touch everything.
4. **Compute:** ECS Fargate for the API and worker. Use Lambda only for event-driven pieces.
5. **Binary data:** the generated schema stores Binary Data attributes as `BYTEA`. Moving them to S3 means changing those columns to an object key and migrating the files.

## What os2native provides for this stack

| os2native output | Used for | Ready? |
|---|---|---|
| `extraction/<Module>/` (screens, screen data flow, actions, entities, roles, timers, site properties, integrations) | The specification for every layer | Yes |
| `deliverables/db-schema/` | PostgreSQL DDL, seed data, ER diagram, cross-module FKs, client entities | Yes, for Aurora PostgreSQL |
| `deliverables/sql-porting/` | Advanced SQL → PostgreSQL checklist per query, including Expand Inline (SQL injection) findings | Yes |
| `deliverables/api-catalog/` | Consumed and exposed REST/SOAP APIs; OpenAPI stubs for **external** systems | Partly: the new back end's own API is designed from the screen data flow, not generated |
| `extensions/<Name>/source/` | Extension code to port | Source only, not translated |
| `deliverables/specs/` | Backend specs (EF Core entities or TypeScript row types on the `db-schema` tables, action signatures, Refit/TypeScript API clients) and frontend specs (React pages or Expo Router screens, TanStack Query hooks, components) per module | Yes, once `"stack": {"backend": ...}` (`dotnet` or `node`) is set in `project.json` |
| Runtime values | Timer schedules, site property values, user-role assignments | No: collect them from Service Center |

## Migration approach

1. **Extract** with os2native and pass its readiness gate (`extractor/HOW_TO_EXTRACT.md`): no unknown dependencies, counts matching Service Studio, owners for Forge replacements, extensions and manual SQL.
2. **Make the early decisions** above, at least the database path and the back-end language.
3. **Back end first:** create the database from `db-schema/`, build the APIs from the screen data flow and server actions, and generate the TypeScript clients.
4. **Move the web app module by module.** With the new PostgreSQL schema, cut over per release with a data migration; running old and new side by side only works if the live database is kept.
5. **Mobile last**, reusing the shared packages.
6. **Deploy to AWS** through the infrastructure code and CI/CD pipeline: Dev → Test → Prod.
