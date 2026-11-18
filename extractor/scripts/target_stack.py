"""Target stack of a project (see ``TECH_STACK.md`` in the repo root).

The stack is a project decision, stored as ``"stack"`` in
``projects/<Project>/project.json`` (next to ``INPUTS`` and ``OUTPUT``), one
group per layer of TECH_STACK.md::

    {
      "stack": {
        "backend":  {"language": "dotnet", "data_access": "efcore+dapper", ...},
        "web":      {"framework": "react", "ui": "shadcn", ...},
        "mobile":   {"framework": "expo", "offline": "expo-sqlite+drizzle", "modules": []},
        "database": {"engine": "postgresql", "case_insensitive_text": false, ...},
        "auth":     {"identity_provider": "cognito", ...},
        "platform": {"cloud": "aws", "iac": "cdk", ...},
        "local":    {"orchestration": "docker-compose", ...}
      }
    }

``CATALOGUE`` lists every layer with its TECH_STACK.md default and options.
A ``null`` value is an **open decision**: TECH_STACK.md gives a choice and
the project hasn't made it yet. ``scripts/project_stack.py`` shows the stack,
fills the defaults in and records decisions.

Only ``backend.language`` blocks a generator (``generate_specs.py``); the
other open decisions are reported. The older flat form
(``"stack": {"backend": "dotnet", "web": "react", "mobile_modules": []}``) is
still read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PROJECT_CONFIG = "project.json"
OPEN = None  # value of an open decision


class Layer:
    """One entry of the catalogue.

    ``default`` is a value, ``OPEN``, or a dict keyed by back-end language
    (``"dotnet"``/``"node"``) for defaults that depend on it. ``supported``
    restricts the values a generator can handle; other layers accept any
    value, and ``options`` only lists the TECH_STACK.md choices.
    """

    def __init__(self, key: str, label: str, default: Any, options: dict[Any, str],
                 supported: bool = False) -> None:
        self.key = key
        self.label = label
        self.default = default
        self.options = options
        self.supported = supported

    def default_for(self, language: str | None) -> Any:
        if isinstance(self.default, dict):
            return self.default.get(language, OPEN)
        return self.default

    def describe(self, value: Any) -> str:
        if value is OPEN:
            choices = ", ".join(f"`{_fmt(k)}`" for k in self.options)
            return f"**open** ({choices})" if choices else "**open**"
        return self.options.get(value, _fmt(value))


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "-"
    return str(value)


YES_NO = {True: "yes", False: "no"}

# Group key → (group label, layers). Order and wording follow TECH_STACK.md.
CATALOGUE: dict[str, tuple[str, list[Layer]]] = {
    "backend": ("Back end", [
        Layer("language", "Language", OPEN, {
            "dotnet": "ASP.NET Core on .NET 10 (C#)",
            "node": "Node.js + TypeScript",
        }, supported=True),
        Layer("architecture", "Architecture", "modular-monolith", {
            "modular-monolith": "Modular monolith (API + worker)",
        }),
        Layer("framework", "Web framework", {"dotnet": "aspnetcore"}, {
            "aspnetcore": "ASP.NET Core (Minimal APIs / controllers)",
            "fastify": "Fastify",
            "nestjs": "NestJS",
            "express": "Express",
        }),
        Layer("data_access", "Data access", {"dotnet": "efcore+dapper"}, {
            "efcore+dapper": "EF Core + Dapper (ported Advanced SQL), EF Core migrations",
            "drizzle": "Drizzle ORM",
            "prisma": "Prisma",
            "kysely": "Kysely",
        }),
        Layer("api_contract", "API contract", "openapi", {
            "openapi": "OpenAPI document",
        }),
        Layer("api_client_generator", "Generated TypeScript clients", OPEN, {
            "orval": "Orval",
            "nswag": "NSwag",
        }),
        Layer("consumed_apis", "Consumed APIs", {"dotnet": "refit"}, {
            "refit": "Refit / HttpClient",
            "openapi-fetch": "openapi-fetch / fetch",
        }),
        Layer("jobs", "Jobs and workflows", OPEN, {
            "hangfire": "Hangfire",
            "eventbridge+sqs": "EventBridge Scheduler + SQS + worker",
        }),
    ]),
    "web": ("Web front end", [
        Layer("framework", "Framework", "react", {
            "react": "React + TypeScript + Vite (single-page app)",
        }, supported=True),
        Layer("ui", "UI components", OPEN, {
            "shadcn": "shadcn/ui (Tailwind)",
            "mui": "MUI",
        }),
        Layer("server_state", "Server data", "tanstack-query", {"tanstack-query": "TanStack Query"}),
        Layer("client_state", "Client state", "zustand", {"zustand": "Zustand"}),
        Layer("forms", "Forms and validation", "react-hook-form+zod", {
            "react-hook-form+zod": "react-hook-form + zod",
        }),
        Layer("i18n", "Translations", OPEN, {
            "i18next": "i18next (more than one locale)",
            "none": "None (one locale)",
        }),
    ]),
    "mobile": ("Mobile", [
        Layer("framework", "Framework", "expo", {
            "expo": "React Native (Expo) + TypeScript, Expo Router",
        }, supported=True),
        Layer("offline", "Offline storage", OPEN, {
            "expo-sqlite+drizzle": "expo-sqlite (+ Drizzle)",
            "watermelondb": "WatermelonDB",
        }),
        Layer("build", "Build and release", "eas", {"eas": "EAS Build / Submit / Update"}),
        Layer("modules", "Modules treated as mobile apps", [], {}),
    ]),
    "database": ("Database", [
        Layer("engine", "Engine", "postgresql", {
            "postgresql": "PostgreSQL (the schema os2native generates)",
        }, supported=True),
        Layer("hosting", "Hosting", "aurora-postgresql", {
            "aurora-postgresql": "Aurora PostgreSQL",
            "rds-postgresql": "RDS for PostgreSQL",
        }),
        Layer("case_insensitive_text", "Case-insensitive text (CITEXT)", OPEN, YES_NO),
        Layer("outsystems_defaults", "OutSystems defaults (1900-01-01, 0, '')", OPEN, {
            "null": "Converted to NULL in the data migration",
            "keep": "Kept as they are",
        }),
        Layer("binary_data", "Binary Data attributes", OPEN, {
            "s3": "S3 objects, key in the database",
            "bytea": "BYTEA columns",
        }),
    ]),
    "auth": ("Auth", [
        Layer("identity_provider", "Identity provider", OPEN, {
            "cognito": "Amazon Cognito",
            "corporate-oidc": "Corporate identity provider over OIDC",
        }),
        Layer("roles", "Roles", "claims", {"claims": "Groups / claims and policies"}),
    ]),
    "platform": ("Platform (AWS)", [
        Layer("cloud", "Cloud", "aws", {"aws": "AWS"}),
        Layer("code_structure", "Code structure", "pnpm+turborepo", {
            "pnpm+turborepo": "Monorepo (pnpm + Turborepo) with shared packages",
        }),
        Layer("web_hosting", "Web hosting", "s3+cloudfront+waf", {
            "s3+cloudfront+waf": "S3 + CloudFront + AWS WAF",
        }),
        Layer("api_hosting", "API hosting", "ecs-fargate", {
            "ecs-fargate": "ECS Fargate + ALB, images in ECR",
        }),
        Layer("file_storage", "File storage", "s3", {"s3": "S3"}),
        Layer("email", "Email", "ses", {"ses": "Amazon SES"}),
        Layer("cache", "Cache / sessions", "elasticache", {"elasticache": "ElastiCache (Redis / Valkey)"}),
        Layer("config", "Configuration and secrets", "ssm+secrets-manager", {
            "ssm+secrets-manager": "SSM Parameter Store / AppConfig + Secrets Manager",
        }),
        Layer("monitoring", "Monitoring", "cloudwatch+opentelemetry", {
            "cloudwatch+opentelemetry": "CloudWatch + OpenTelemetry / X-Ray",
        }),
        Layer("iac", "Infrastructure as code", OPEN, {
            "cdk": "AWS CDK (TypeScript)",
            "terraform": "Terraform",
        }),
        Layer("cicd", "CI/CD", OPEN, {
            "github-actions": "GitHub Actions",
            "codepipeline": "AWS CodePipeline",
        }),
        Layer("environments", "Environments", "account-per-environment", {
            "account-per-environment": "One AWS account per environment (Dev / Test / Prod)",
        }),
    ]),
    "local": ("Local development", [
        Layer("orchestration", "Containers", OPEN, {
            "docker-compose": "Docker Compose",
            "aspire": ".NET Aspire",
        }),
        Layer("aws_emulation", "AWS services", "localstack", {"localstack": "LocalStack (or MinIO for S3)"}),
        Layer("email", "Email", "mailpit", {"mailpit": "Mailpit"}),
        Layer("identity", "Identity provider", "keycloak", {
            "keycloak": "Keycloak container",
            "cognito-dev": "A dev Cognito pool",
        }),
        Layer("telemetry", "Telemetry", "aspire-dashboard", {
            "aspire-dashboard": "OpenTelemetry → Aspire Dashboard",
            "jaeger": "OpenTelemetry → Jaeger",
        }),
    ]),
}

# Older flat keys → (group, key)
_FLAT = {
    "backend": ("backend", "language"),
    "web": ("web", "framework"),
    "mobile": ("mobile", "framework"),
    "database": ("database", "engine"),
    "mobile_modules": ("mobile", "modules"),
}

# Kept for callers that list the supported values
BACKENDS = CATALOGUE["backend"][1][0].options
WEB = CATALOGUE["web"][1][0].options
MOBILE = CATALOGUE["mobile"][1][0].options
DATABASES = CATALOGUE["database"][1][0].options


class StackError(ValueError):
    """The project's stack is missing or not supported."""


def project_config_path(pass_dir: Path) -> Path:
    """``projects/<Project>/project.json`` for ``projects/<Project>/OUTPUT``."""
    return pass_dir.resolve().parent / PROJECT_CONFIG


def _layer(group: str, key: str) -> Layer | None:
    for layer in CATALOGUE.get(group, ("", []))[1]:
        if layer.key == key:
            return layer
    return None


def normalize(configured: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The configured stack as ``{group: {key: value}}`` (flat form converted)."""
    out: dict[str, dict[str, Any]] = {}
    for name, value in (configured or {}).items():
        if isinstance(value, dict):
            out.setdefault(name, {}).update(value)
        elif name in _FLAT:
            group, key = _FLAT[name]
            out.setdefault(group, {})[key] = value
        else:
            out.setdefault("other", {})[name] = value
    return out


def resolve(configured: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Every layer's value and where it came from.

    Returns ``(values, sources)``; a source is ``"project"`` (set in
    project.json), ``"default"`` (TECH_STACK.md) or ``"open"``.
    """
    given = normalize(configured)
    language = given.get("backend", {}).get("language")
    values: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, str]] = {}
    for group, (_label, layers) in CATALOGUE.items():
        values[group], sources[group] = {}, {}
        for layer in layers:
            if layer.key in given.get(group, {}) and given[group][layer.key] is not OPEN:
                values[group][layer.key] = given[group][layer.key]
                sources[group][layer.key] = "project"
            else:
                value = layer.default_for(language)
                values[group][layer.key] = value
                sources[group][layer.key] = "open" if value is OPEN else "default"
    for group, items in given.items():  # keep values the catalogue doesn't know
        for key, value in items.items():
            if key not in values.setdefault(group, {}):
                values[group][key] = value
                sources.setdefault(group, {})[key] = "project"
    return values, sources


def open_decisions(values: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{g}.{k}" for g, items in values.items() for k, v in items.items() if v is OPEN]


def validate(values: dict[str, dict[str, Any]]) -> list[str]:
    """Errors for values a generator can't handle (``supported`` layers)."""
    errors = []
    for group, (_label, layers) in CATALOGUE.items():
        for layer in layers:
            value = values[group][layer.key]
            if layer.supported and value is not OPEN and value not in layer.options:
                errors.append(f"stack.{group}.{layer.key} = {value!r} is not supported; "
                              f"use one of: {', '.join(map(str, layer.options))}")
    return errors


def load_stack(
    pass_dir: Path,
    backend: str | None = None,
    project_config: Path | None = None,
) -> dict[str, Any]:
    """The project's resolved stack, with ``backend`` overriding the language.

    Returns the full ``layers`` plus the flat values the generators use
    (``backend``, ``web``, ``mobile``, ``database``, ``mobile_modules``) and
    the ``open`` decisions. Raises ``StackError`` when the back-end language
    isn't chosen or a value isn't supported.
    """
    path = project_config or project_config_path(pass_dir)
    configured: dict[str, Any] = {}
    if path.is_file():
        configured = json.loads(path.read_text(encoding="utf-8")).get("stack") or {}
    configured = normalize(configured)
    if backend:
        configured.setdefault("backend", {})["language"] = backend
    values, sources = resolve(configured)
    if values["backend"]["language"] is OPEN:
        raise StackError(
            f"No back end chosen. Set stack.backend.language to \"dotnet\" or \"node\" in {path} "
            "(scripts/project_stack.py set backend.language=dotnet), see TECH_STACK.md, "
            "Key decisions, or pass --backend."
        )
    errors = validate(values)
    if errors:
        raise StackError("; ".join(errors))
    return {
        "backend": values["backend"]["language"],
        "web": values["web"]["framework"],
        "mobile": values["mobile"]["framework"],
        "database": values["database"]["engine"],
        "mobile_modules": list(values["mobile"].get("modules") or []),
        "layers": values,
        "sources": sources,
        "open": open_decisions(values),
        "source": str(path) if path.is_file() and configured else "defaults",
    }


def stack_value(pass_dir: Path, group: str, key: str, project_config: Path | None = None) -> Any:
    """One resolved stack value (``OPEN`` when undecided), without requiring a back end."""
    path = project_config or project_config_path(pass_dir)
    configured = json.loads(path.read_text(encoding="utf-8")).get("stack") if path.is_file() else None
    values, _ = resolve(configured)
    return values.get(group, {}).get(key)


def describe(stack: dict[str, Any]) -> list[tuple[str, str]]:
    """``(layer, choice)`` rows for every layer, for the specs index."""
    values = stack.get("layers")
    if values is None:  # a minimal stack dict (tests, older callers)
        values, _ = resolve({k: stack[k] for k in ("backend", "web", "mobile", "database") if k in stack})
    rows = []
    for group, (glabel, layers) in CATALOGUE.items():
        for layer in layers:
            value = values[group][layer.key]
            if layer.key == "modules" and not value:
                continue
            rows.append((f"{glabel}: {layer.label}", layer.describe(value)))
    return rows

# ---------------------------------------------------------------------------
# Mobile or web
# ---------------------------------------------------------------------------

_ESPACE_TAG = re.compile(r"<ESpace\b[^>]*>", re.S)


def module_is_mobile(pass_dir: Path, module: str, stack: dict[str, Any]) -> bool:
    """Whether ``module`` is a mobile app.

    ``stack.mobile_modules`` decides when it lists the module. Otherwise the
    module's cached XML is checked: only mobile apps have a splash screen
    (``SplashScreen`` on the ``ESpace`` element).
    """
    if module in stack.get("mobile_modules", []):
        return True
    xml_dir = pass_dir / "_xml"
    candidates = [xml_dir / f"{module}.xml", *sorted(xml_dir.glob(f"{module}_v*.xml"))]
    for path in candidates:
        if path.is_file():
            tag = _espace_tag(path)
            return bool(tag and re.search(r"\sSplashScreen=", tag))
    return False


def _espace_tag(path: Path, chunk: int = 1 << 20) -> str | None:
    """The ``<ESpace …>`` start tag, read in chunks (it comes after the fragments)."""
    buf = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            data = fh.read(chunk)
            buf += data
            match = _ESPACE_TAG.search(buf)
            if match:
                return match.group(0)
            if not data:
                return None
            start = buf.rfind("<ESpace")
            buf = buf[start:] if start != -1 else buf[-16:]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return out if out and not out[0].isdigit() else f"_{out}"


def camel(name: str) -> str:
    p = pascal(name)
    return p[:1].lower() + p[1:] if not p.startswith("_") else p


def kebab(name: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s)
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

OS_TO_CSHARP: dict[str, str] = {
    "Text": "string",
    "Integer": "int",
    "Long Integer": "long",
    "Boolean": "bool",
    "DateTime": "DateTime",
    "Date": "DateOnly",
    "Time": "TimeOnly",
    "Decimal": "decimal",
    "Currency": "decimal",
    "Email": "string",
    "Phone Number": "string",
    "Binary Data": "byte[]",
}

# JSON-facing TypeScript types: dates travel as ISO 8601 strings.
OS_TO_TS: dict[str, str] = {
    "Text": "string",
    "Integer": "number",
    "Long Integer": "number",
    "Boolean": "boolean",
    "DateTime": "string",
    "Date": "string",
    "Time": "string",
    "Decimal": "number",
    "Currency": "number",
    "Email": "string",
    "Phone Number": "string",
    "Binary Data": "string",
}

_TS_NOTES = {"DateTime": "ISO 8601", "Date": "YYYY-MM-DD", "Time": "HH:mm:ss", "Binary Data": "base64 or S3 key"}

_ID_SUFFIX = " Identifier"
_LIST_SUFFIX = " List"


def _named(type_ref: dict[str, Any]) -> str:
    return pascal(type_ref.get("name") or "Record")


def is_identifier(type_ref: dict[str, Any] | str) -> str | None:
    """The entity name when ``type_ref`` is an entity identifier, else ``None``."""
    if isinstance(type_ref, str):
        return type_ref[: -len(_ID_SUFFIX)] if type_ref.endswith(_ID_SUFFIX) else None
    if type_ref.get("kind") == "entity_identifier":
        return type_ref.get("entity_name") or (type_ref.get("name") or "")[: -len(_ID_SUFFIX)] or "Entity"
    return None


def csharp_type(type_ref: dict[str, Any] | str | None) -> str:
    """C# type for an OutSystems type reference. Identifiers are ``long`` (``BIGINT`` in the schema)."""
    if not type_ref:
        return "string"
    if is_identifier(type_ref) is not None:
        return "long"
    if isinstance(type_ref, str):
        if type_ref.endswith(_LIST_SUFFIX):
            return f"List<{csharp_type(type_ref[: -len(_LIST_SUFFIX)])}>"
        return OS_TO_CSHARP.get(type_ref, pascal(type_ref))
    kind = type_ref.get("kind")
    if kind == "primitive":
        return OS_TO_CSHARP.get(type_ref.get("name", ""), "string")
    if kind == "list":
        return f"List<{csharp_type(type_ref.get('element_type'))}>"
    if kind in ("local_entity", "reference_entity", "local_structure", "reference_structure",
                "custom_structure"):
        return _named(type_ref)
    if kind == "anonymous_structure":
        return "object /* inline record */"
    return "string"


def ts_type(type_ref: dict[str, Any] | str | None) -> str:
    """TypeScript type for an OutSystems type reference, as it travels in JSON."""
    if not type_ref:
        return "string"
    if is_identifier(type_ref) is not None:
        return "number"
    if isinstance(type_ref, str):
        if type_ref.endswith(_LIST_SUFFIX):
            return f"{ts_type(type_ref[: -len(_LIST_SUFFIX)])}[]"
        return OS_TO_TS.get(type_ref, pascal(type_ref))
    kind = type_ref.get("kind")
    if kind == "primitive":
        return OS_TO_TS.get(type_ref.get("name", ""), "string")
    if kind == "list":
        return f"{ts_type(type_ref.get('element_type'))}[]"
    if kind in ("local_entity", "reference_entity", "local_structure", "reference_structure",
                "custom_structure"):
        return _named(type_ref)
    if kind == "anonymous_structure":
        return "Record<string, unknown>"
    return "string"


def ts_note(type_ref: dict[str, Any] | str | None) -> str:
    """Format note for a TypeScript field (``""`` when none)."""
    name = type_ref if isinstance(type_ref, str) else (type_ref or {}).get("name", "")
    if isinstance(type_ref, dict) and type_ref.get("kind") != "primitive":
        name = ""
    return _TS_NOTES.get(name, "")


def backend_type(stack: dict[str, Any], type_ref: Any) -> str:
    return csharp_type(type_ref) if stack["backend"] == "dotnet" else ts_type(type_ref)


def is_mandatory(param: dict[str, Any]) -> bool:
    """``is_mandatory`` is a bool on actions and ``"Yes"``/``"No"`` on screens."""
    value = param.get("is_mandatory")
    return value is True or value == "Yes"
