"""Generate a consolidated REST API contract catalog from extraction data.

Reads integration files (external-systems.json, exposed-apis.json,
consumed-apis.json, public-actions.json, dependency-actions.json) from all
modules in a pass and produces:

- ``catalog.json``           — machine-readable consolidated catalog
- ``catalog.md``             — human-readable catalog
- ``per-module/<module>.md`` — per-module API contract detail
- ``openapi-stubs/<sys>.yaml`` — OpenAPI 3.0 stub per external system
- ``coverage-matrix.md``     — module × system cross-reference
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from generator_utils import (
    decode_http_method,
    deliverables_dir,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    slugify,
    write_json,
    write_text,
)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _system_key(system: dict[str, Any]) -> str:
    """Deduplication key for an external system."""
    name = system.get("name", "")
    url = system.get("endpoint_url", "")
    return f"{name}||{url}"


def collect_integrations(
    pass_dir: Path,
    packages: list[dict[str, Any]],
    total_pkgs: int = 0,
) -> dict[str, Any]:
    """Walk all modules and collect integration data.

    Returns a dict with:
    - ``systems``  — deduplicated external systems (merged across modules)
    - ``modules``  — per-module detail (external, exposed, consumed)
    """
    # system_key → merged system dict
    systems: dict[str, dict[str, Any]] = {}
    # system_key → set of consuming module names
    system_consumers: dict[str, set[str]] = {}
    # per-module integration detail
    modules: dict[str, dict[str, Any]] = {}

    if not total_pkgs:
        total_pkgs = len(packages)
    width = len(str(total_pkgs))

    for idx, pkg in enumerate(packages, 1):
        module_name: str = pkg.get("module_name", "unknown")
        print(f"  [{idx:>{width}}/{total_pkgs}] Scanning {module_name}...")

        externals = load_domain_file(pass_dir, module_name, "integrations", "external-systems.json")
        exposed = load_domain_file(pass_dir, module_name, "integrations", "exposed-apis.json")
        consumed = load_domain_file(pass_dir, module_name, "integrations", "consumed-apis.json")
        public = load_domain_file(pass_dir, module_name, "integrations", "public-actions.json")
        deps = load_domain_file(pass_dir, module_name, "integrations", "dependency-actions.json")

        modules[module_name] = {
            "external_systems": externals if isinstance(externals, list) else [],
            # REST/SOAP only; consumed ones are detailed via external_systems
            "exposed_apis": exposed if isinstance(exposed, list) else [],
            "consumed_apis": consumed if isinstance(consumed, list) else [],
            # module-to-module calls (not REST/SOAP)
            "public_actions": public if isinstance(public, list) else [],
            "dependency_actions": deps if isinstance(deps, list) else [],
        }

        if not isinstance(externals, list):
            continue

        for ext in externals:
            if not isinstance(ext, dict):
                continue
            key = _system_key(ext)

            if key not in systems:
                systems[key] = {
                    "name": ext.get("name", ""),
                    "type": ext.get("type", "REST"),
                    "description": ext.get("description", ""),
                    "endpoint_url": ext.get("endpoint_url", ""),
                    "authentication": ext.get("authentication", ""),
                    "operations": [],
                }
                system_consumers[key] = set()

            system_consumers[key].add(module_name)

            # Merge operations (deduplicate by operation name)
            existing_op_names = {
                op["name"] for op in systems[key]["operations"]
            }
            for op in ext.get("operations", []):
                if not isinstance(op, dict):
                    continue
                if op.get("name", "") not in existing_op_names:
                    systems[key]["operations"].append(_normalize_operation(op))
                    existing_op_names.add(op.get("name", ""))

    # Attach consumer lists and sort
    system_list = []
    for key, sys in sorted(systems.items(), key=lambda kv: kv[1]["name"].lower()):
        sys["consumed_by"] = sorted(system_consumers.get(key, set()))
        system_list.append(sys)

    return {"systems": system_list, "modules": modules}


def _normalize_operation(op: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw operation dict into the catalog schema."""
    return {
        "name": op.get("name", ""),
        "method": decode_http_method(op.get("http_method")),
        "path": op.get("url_path", ""),
        "input_parameters": op.get("input_parameters", []),
        "output_parameters": op.get("output_parameters", []),
    }


# ---------------------------------------------------------------------------
# Catalog JSON
# ---------------------------------------------------------------------------

def build_catalog_json(data: dict[str, Any]) -> dict[str, Any]:
    """Build the consolidated ``catalog.json`` payload."""
    systems = data["systems"]
    modules_with_apis = {
        m for s in systems for m in s.get("consumed_by", [])
    }
    total_ops = sum(len(s.get("operations", [])) for s in systems)

    return {
        "systems": systems,
        "summary": {
            "total_systems": len(systems),
            "total_operations": total_ops,
            "modules_with_apis": len(modules_with_apis),
        },
    }


# ---------------------------------------------------------------------------
# Catalog markdown
# ---------------------------------------------------------------------------

def build_catalog_md(catalog: dict[str, Any]) -> str:
    """Render the human-readable ``catalog.md``."""
    lines: list[str] = []
    summary = catalog["summary"]

    lines.append("# REST API Catalog")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **External systems:** {summary['total_systems']}")
    lines.append(f"- **Total operations:** {summary['total_operations']}")
    lines.append(f"- **Modules with APIs:** {summary['modules_with_apis']}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Systems")
    lines.append("")

    for sys in catalog["systems"]:
        name = sys.get("name", "unnamed")
        lines.append(f"### {name}")
        lines.append("")

        url = sys.get("endpoint_url", "")
        auth = sys.get("authentication", "")
        consumers = sys.get("consumed_by", [])

        if url:
            lines.append(f"- **Base URL:** `{url}`")
        if auth:
            lines.append(f"- **Authentication:** {auth}")
        if consumers:
            lines.append(f"- **Consumed by:** {', '.join(consumers)}")
        lines.append("")

        ops = sys.get("operations", [])
        if ops:
            headers = ["Operation", "Method", "Path", "Input", "Output"]
            rows = []
            for op in ops:
                inp = _format_param_list(op.get("input_parameters", []))
                out = _format_param_list(op.get("output_parameters", []))
                rows.append([
                    op.get("name", ""),
                    op.get("method", "GET"),
                    f"`{op.get('path', '')}`" if op.get("path") else "",
                    inp,
                    out,
                ])
            lines.append(md_table(headers, rows))
        else:
            lines.append("_No operations defined._")
        lines.append("")

    return "\n".join(lines) + "\n"


def _format_param_list(params: list) -> str:
    """Format a parameter list for display in a table cell."""
    if not params:
        return "—"
    # params may be plain strings or dicts with a "name" key
    names = []
    for p in params:
        if isinstance(p, dict):
            names.append(p.get("name", "?"))
        else:
            names.append(str(p))
    return ", ".join(names)


# ---------------------------------------------------------------------------
# Per-module markdown
# ---------------------------------------------------------------------------

def build_module_md(module_name: str, detail: dict[str, Any]) -> str:
    """Render per-module API contract markdown."""
    lines: list[str] = []
    lines.append(f"# {module_name} — API Contracts")
    lines.append("")

    # Consumed external systems
    externals = detail.get("external_systems", [])
    lines.append("## Consumed External Systems")
    lines.append("")
    if externals:
        for ext in externals:
            name = ext.get("name", "unnamed")
            lines.append(f"### {name}")
            lines.append("")
            url = ext.get("endpoint_url", "")
            auth = ext.get("authentication", "")
            if url:
                lines.append(f"- **Base URL:** `{url}`")
            if auth:
                lines.append(f"- **Authentication:** {auth}")
            desc = ext.get("description", "")
            if desc:
                lines.append(f"- **Description:** {desc}")
            lines.append("")

            ops = ext.get("operations", [])
            if ops:
                headers = ["Operation", "Method", "Path", "Input", "Output"]
                rows = []
                for op in ops:
                    norm = _normalize_operation(op)
                    inp = _format_param_list(norm.get("input_parameters", []))
                    out = _format_param_list(norm.get("output_parameters", []))
                    rows.append([
                        norm["name"],
                        norm["method"],
                        f"`{norm['path']}`" if norm["path"] else "",
                        inp,
                        out,
                    ])
                lines.append(md_table(headers, rows))
            lines.append("")
    else:
        lines.append("_None._")
        lines.append("")

    # Exposed REST / SOAP APIs
    exposed = detail.get("exposed_apis", [])
    lines.append("## Exposed APIs")
    lines.append("")
    if exposed:
        headers = ["Name", "Kind", "Operations"]
        rows = [
            [
                api.get("name", ""),
                api.get("kind", ""),
                ", ".join(op.get("name", "") for op in api.get("operations", [])),
            ]
            for api in exposed
        ]
        lines.append(md_table(headers, rows))
    else:
        lines.append("_None._")
    lines.append("")

    # Public server actions (callable by modules that reference this one)
    public = detail.get("public_actions", [])
    lines.append("## Public Server Actions")
    lines.append("")
    if public:
        headers = ["Name", "Function?", "Input", "Output"]
        rows = []
        for api in public:
            inp = _format_param_list(api.get("input_parameters", []))
            out = _format_param_list(api.get("output_parameters", []))
            rows.append([
                api.get("name", ""),
                "Yes" if api.get("is_function") else "No",
                inp,
                out,
            ])
        lines.append(md_table(headers, rows))
    else:
        lines.append("_None._")
    lines.append("")

    # Actions consumed from dependency modules
    deps = detail.get("dependency_actions", [])
    lines.append("## Dependency Actions")
    lines.append("")
    if deps:
        headers = ["Name", "Source Module"]
        rows = []
        for api in deps:
            rows.append([
                api.get("name", ""),
                api.get("module", ""),
            ])
        lines.append(md_table(headers, rows))
    else:
        lines.append("_None._")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# OpenAPI stubs
# ---------------------------------------------------------------------------

def _extract_path_params(url_path: str) -> list[str]:
    """Extract ``{placeholder}`` parameter names from a URL path."""
    return re.findall(r"\{(\w+)\}", url_path)


def _param_schema(params: list) -> dict[str, Any]:
    """Build an object schema from a parameter list."""
    properties: dict[str, Any] = {}
    for p in params:
        if isinstance(p, dict):
            name = p.get("name", "param")
            properties[name] = {"type": "string"}
        else:
            properties[str(p)] = {"type": "string"}
    if not properties:
        return {}
    return {"type": "object", "properties": properties}


def build_openapi_stub(system: dict[str, Any]) -> str:
    """Build an OpenAPI 3.0 YAML stub for an external system.

    Uses plain string formatting — no YAML library needed.
    """
    lines: list[str] = []
    name = system.get("name", "API")
    desc = system.get("description", "").replace('"', '\\"')
    url = system.get("endpoint_url", "")

    lines.append('openapi: "3.0.0"')
    lines.append("info:")
    lines.append(f'  title: "{name}"')
    lines.append(f'  description: "{desc}"')
    lines.append('  version: "1.0.0"')

    if url:
        lines.append("servers:")
        lines.append(f"  - url: {url}")

    # Security schemes derived from authentication metadata
    auth = system.get("authentication", "")
    security_scheme_name = ""
    if isinstance(auth, dict):
        auth_type = auth.get("type", "")
        headers = auth.get("headers", []) or auth.get("inferred_headers", [])
        oauth_params = auth.get("oauth_params", [])
        if auth_type == "oauth" or oauth_params:
            security_scheme_name = "oauth2"
            lines.append("components:")
            lines.append("  securitySchemes:")
            lines.append("    oauth2:")
            lines.append("      type: oauth2")
            lines.append("      flows:")
            lines.append("        clientCredentials:")
            lines.append("          tokenUrl: /oauth/token")
            lines.append("          scopes: {}")
        elif auth_type == "1":
            security_scheme_name = "basicAuth"
            lines.append("components:")
            lines.append("  securitySchemes:")
            lines.append("    basicAuth:")
            lines.append("      type: http")
            lines.append("      scheme: basic")
        elif auth_type == "header" or headers:
            # Check if Authorization header → bearer, otherwise apiKey
            has_auth_header = any(h.lower() == "authorization" for h in headers)
            if has_auth_header:
                security_scheme_name = "bearerAuth"
                lines.append("components:")
                lines.append("  securitySchemes:")
                lines.append("    bearerAuth:")
                lines.append("      type: http")
                lines.append("      scheme: bearer")
            else:
                hdr_name = headers[0] if headers else "X-API-Key"
                security_scheme_name = "apiKeyAuth"
                lines.append("components:")
                lines.append("  securitySchemes:")
                lines.append("    apiKeyAuth:")
                lines.append("      type: apiKey")
                lines.append(f"      name: {hdr_name}")
                lines.append("      in: header")
    elif auth == "1":
        security_scheme_name = "basicAuth"
        lines.append("components:")
        lines.append("  securitySchemes:")
        lines.append("    basicAuth:")
        lines.append("      type: http")
        lines.append("      scheme: basic")

    if security_scheme_name:
        lines.append("security:")
        lines.append(f"  - {security_scheme_name}: []")

    paths: dict[str, list[dict[str, Any]]] = {}
    for op in system.get("operations", []):
        path = op.get("path", "") or f"/{slugify(op.get('name', 'operation'))}"
        method = op.get("method", "GET").lower()
        if path not in paths:
            paths[path] = []
        paths[path].append({"method": method, **op})

    if paths:
        lines.append("paths:")
        for path, ops in paths.items():
            lines.append(f"  {path}:")
            for op in ops:
                method = op["method"]
                op_name = op.get("name", "operation")
                lines.append(f"    {method}:")
                lines.append(f"      operationId: {op_name}")
                lines.append(f'      summary: "{op_name}"')

                # Path parameters
                path_params = _extract_path_params(path)
                if path_params:
                    lines.append("      parameters:")
                    for pp in path_params:
                        lines.append(f"        - name: {pp}")
                        lines.append("          in: path")
                        lines.append("          required: true")
                        lines.append("          schema:")
                        lines.append("            type: string")

                # Request body
                inp = op.get("input_parameters", [])
                if inp and method in ("post", "put", "patch"):
                    schema = _param_schema(inp)
                    if schema:
                        lines.append("      requestBody:")
                        lines.append("        content:")
                        lines.append("          application/json:")
                        lines.append("            schema:")
                        _append_schema_lines(lines, schema, indent=14)

                # Response
                lines.append("      responses:")
                lines.append('        "200":')
                lines.append("          description: Successful response")
                out = op.get("output_parameters", [])
                if out:
                    out_schema = _param_schema(out)
                    if out_schema:
                        lines.append("          content:")
                        lines.append("            application/json:")
                        lines.append("              schema:")
                        _append_schema_lines(lines, out_schema, indent=16)
    else:
        lines.append("paths: {}")

    return "\n".join(lines) + "\n"


def _append_schema_lines(
    lines: list[str],
    schema: dict[str, Any],
    indent: int,
) -> None:
    """Append inline YAML lines for a simple object schema."""
    pad = " " * indent
    lines.append(f"{pad}type: object")
    props = schema.get("properties", {})
    if props:
        lines.append(f"{pad}properties:")
        for prop_name, prop_schema in props.items():
            lines.append(f"{pad}  {prop_name}:")
            for k, v in prop_schema.items():
                lines.append(f"{pad}    {k}: {v}")


# ---------------------------------------------------------------------------
# Coverage matrix
# ---------------------------------------------------------------------------

def build_coverage_matrix(data: dict[str, Any]) -> str:
    """Render a module × system cross-reference matrix."""
    systems = data["systems"]
    if not systems:
        return "# API Coverage Matrix\n\n_No external systems found._\n"

    # Collect all module names that consume at least one system
    all_consumers: set[str] = set()
    for s in systems:
        all_consumers.update(s.get("consumed_by", []))
    module_names = sorted(all_consumers)

    if not module_names:
        return "# API Coverage Matrix\n\n_No module-to-system relationships found._\n"

    system_names = [s.get("name", "?") for s in systems]

    lines: list[str] = []
    lines.append("# API Coverage Matrix")
    lines.append("")
    lines.append("Rows = modules, Columns = external systems. ✓ = module consumes the system.")
    lines.append("")

    headers = ["Module"] + system_names
    rows: list[list[str]] = []
    for mod in module_names:
        row = [mod]
        for sys in systems:
            if mod in sys.get("consumed_by", []):
                row.append("✓")
            else:
                row.append("")
        rows.append(row)

    lines.append(md_table(headers, rows))
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_arg_parser("Generate a consolidated REST API contract catalog")
    args = parser.parse_args()

    pass_dir: Path = args.pass_dir
    extraction_dir = pass_dir / "extraction"
    if not extraction_dir.is_dir():
        print(f"ERROR: {extraction_dir} does not exist or is not a directory", file=sys.stderr)
        sys.exit(1)

    # Load packages
    packages = load_pass_packages(pass_dir)
    if not packages:
        print(f"ERROR: No package.json files found in {extraction_dir}", file=sys.stderr)
        sys.exit(1)

    total_pkgs = len(packages)
    print(f"Scanning {total_pkgs} modules for integration data...")

    # Collect
    data = collect_integrations(pass_dir, packages, total_pkgs)
    catalog = build_catalog_json(data)

    out_dir = deliverables_dir(pass_dir, "api-catalog")

    # catalog.json
    write_json(out_dir / "catalog.json", catalog)

    # catalog.md
    write_text(out_dir / "catalog.md", build_catalog_md(catalog))

    # per-module markdowns
    modules_written = 0
    for module_name, detail in sorted(data["modules"].items()):
        has_content = (
            detail.get("external_systems")
            or detail.get("exposed_apis")
            or detail.get("consumed_apis")
            or detail.get("public_actions")
            or detail.get("dependency_actions")
        )
        if not has_content:
            continue
        md = build_module_md(module_name, detail)
        write_text(out_dir / "per-module" / f"{module_name}.md", md)
        modules_written += 1

    # OpenAPI stubs (disambiguate duplicate names with a numeric suffix)
    stubs_written = 0
    stub_name_counts: dict[str, int] = {}
    for sys in catalog["systems"]:
        stub = build_openapi_stub(sys)
        base = slugify(sys.get("name", "api"))
        count = stub_name_counts.get(base, 0)
        stub_name_counts[base] = count + 1
        filename = f"{base}.yaml" if count == 0 else f"{base}-{count}.yaml"
        write_text(out_dir / "openapi-stubs" / filename, stub)
        stubs_written += 1

    # coverage-matrix.md
    write_text(out_dir / "coverage-matrix.md", build_coverage_matrix(data))

    # Summary
    summary = catalog["summary"]
    print(f"\n{'='*60}")
    print(f"  API Catalog generated")
    print(f"  External systems: {summary['total_systems']}")
    print(f"  Total operations: {summary['total_operations']}")
    print(f"  Modules with APIs: {summary['modules_with_apis']}")
    print(f"  Per-module contracts: {modules_written}")
    print(f"  OpenAPI stubs: {stubs_written}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
