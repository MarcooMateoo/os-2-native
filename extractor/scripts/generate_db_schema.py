"""Generate PostgreSQL DDL and documentation from extraction output.

Reads entity data from all extracted modules and produces migration-ready
SQL schema, seed data, documentation, and ER diagrams.

Output goes to ``<pass_dir>/deliverables/db-schema/``.

Usage::

    python scripts/generate_db_schema.py out
"""
from __future__ import annotations

import argparse

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from generator_utils import (
    OS_TO_PG_TYPE,
    deliverables_dir,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    pg_type,
    write_json,
    write_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snake_case(name: str) -> str:
    """Convert PascalCase / camelCase to snake_case.

    ``AlertType`` → ``alert_type``
    ``UserId``    → ``user_id``
    ``HTMLParser`` → ``html_parser``
    """
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


# PostgreSQL reserved key words (SQL Key Words appendix, "reserved" and
# "reserved (can be function or type)"); these must be double-quoted when
# used as identifiers. OutSystems entities commonly use Order, From, To, User.
PG_RESERVED = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both
case cast check collate collation column concurrently constraint create cross
current_catalog current_date current_role current_schema current_time
current_timestamp current_user default deferrable desc distinct do else end
except false fetch for foreign freeze from full grant group having ilike in
initially inner intersect into is isnull join lateral leading left like limit
localtime localtimestamp natural not notnull null offset on only or order outer
overlaps placing primary references returning right select session_user
similar some symmetric system_user table tablesample then to trailing true
union unique user using variadic verbose when where window with
""".split())

_PLAIN_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_MAX_IDENT = 63  # PostgreSQL NAMEDATALEN - 1


def _ident(name: str) -> str:
    """Quote ``name`` as a PostgreSQL identifier when it needs it."""
    if _PLAIN_IDENT.match(name) and name not in PG_RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def _table_name(module: str, entity: str) -> str:
    """Build a fully-qualified snake_case table name (unquoted)."""
    return f"{_snake_case(module)}__{_snake_case(entity)}"


def _constraint_name(table: str, column: str) -> str:
    """``fk_<table>__<column>``, shortened with a hash to fit 63 chars."""
    name = f"fk_{table}__{column}"
    if len(name) <= _MAX_IDENT:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:_MAX_IDENT - 9]}_{digest}"


def _is_identity(attr: dict[str, Any]) -> bool:
    """True when ``_column_def`` renders the attribute as an identity column."""
    return attr.get("name") == "Id" or bool(attr.get("auto_number"))


def _int_or_none(value: Any) -> int | None:
    """Parse an extracted Length/Decimals value (int, numeric string, or None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


# OutSystems stores Text longer than this as NVARCHAR(MAX) on SQL Server, so a
# length above it is a "long text" field with no real limit.
_MAX_BOUNDED_TEXT = 2000


def _type_name(attr: dict[str, Any]) -> str:
    t = attr.get("type", "Text")
    if isinstance(t, str):
        return t
    return t.get("name", "") if t.get("kind") == "primitive" else ""


def column_type(attr: dict[str, Any], ci_text: bool = False) -> str:
    """PostgreSQL type for an attribute, keeping Text length and Decimal size.

    - Text with a length up to 2000 becomes ``VARCHAR(n)`` (``TEXT`` above it).
    - Decimal with a length (precision) becomes ``NUMERIC(p,s)``.
    - With ``ci_text`` every Text column is ``CITEXT``, which compares
      case-insensitively like the SQL Server collation OutSystems uses. Its
      length is enforced with a CHECK in :func:`_column_def`.
    """
    name = _type_name(attr)
    base = pg_type(attr.get("type", "Text"))
    if name == "Text":
        if ci_text:
            return "CITEXT"
        length = _int_or_none(attr.get("length"))
        if length and length <= _MAX_BOUNDED_TEXT:
            return f"VARCHAR({length})"
        return "TEXT"
    if name == "Decimal":
        precision = _int_or_none(attr.get("length"))
        scale = _int_or_none(attr.get("decimals"))
        if precision:
            scale = min(scale if scale is not None else 8, precision)
            return f"NUMERIC({precision},{scale})"
    return base


def _column_def(attr: dict[str, Any], ci_text: bool = False) -> str:
    """Return the SQL column definition for a single attribute."""
    col = _ident(_snake_case(attr["name"]))
    col_type = column_type(attr, ci_text)

    parts = [col, col_type]

    # Identity columns
    is_id = attr.get("name") == "Id"
    if is_id:
        parts = [col, "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"]
        return "    " + " ".join(parts)

    if attr.get("auto_number"):
        # Non-PK auto-number
        parts = [col, "BIGINT GENERATED ALWAYS AS IDENTITY"]
        if attr.get("mandatory"):
            parts.append("NOT NULL")
        return "    " + " ".join(parts)

    if attr.get("mandatory"):
        parts.append("NOT NULL")

    if ci_text and col_type == "CITEXT":
        length = _int_or_none(attr.get("length"))
        if length and length <= _MAX_BOUNDED_TEXT:
            parts.append(f"CHECK (char_length({col}) <= {length})")

    return "    " + " ".join(parts)


# ---------------------------------------------------------------------------
# Module data loading
# ---------------------------------------------------------------------------

def _load_module_data(
    pass_dir: Path, module_name: str
) -> dict[str, Any]:
    """Load entities, relationships, and static data for a module."""
    entities = load_domain_file(pass_dir, module_name, "data", "entities.json")
    relationships = load_domain_file(pass_dir, module_name, "data", "relationships.json")
    static_data = load_domain_file(pass_dir, module_name, "data", "static-data.json")
    return split_client_entities({
        "module": module_name,
        "entities": entities if isinstance(entities, list) else [],
        "relationships": relationships if isinstance(relationships, list) else [],
        "static_data": static_data if isinstance(static_data, list) else [],
    })


def split_client_entities(md: dict[str, Any]) -> dict[str, Any]:
    """Move client (local storage) entities out of the database schema.

    Mobile client entities live on the device, not in the server database, so
    they get no table. They are listed in ``client-entities.md`` instead.
    """
    client = [e for e in md["entities"] if e.get("is_client_entity")]
    names = {e.get("name") for e in client}
    return {
        **md,
        "entities": [e for e in md["entities"] if not e.get("is_client_entity")],
        "client_entities": client,
        "relationships": [
            r for r in md["relationships"] if r.get("from_entity") not in names
        ],
        "static_data": [
            sd for sd in md["static_data"] if sd.get("entity_name") not in names
        ],
    }


# ---------------------------------------------------------------------------
# DDL generation
# ---------------------------------------------------------------------------

def _fk_target_table(
    rel: dict[str, Any],
    module: str,
    module_entities: dict[str, set[str]],
) -> str | None:
    """Return the referenced table for a foreign key, or ``None``.

    A reference to another module resolves when that module is in the
    package and has a table for the entity, since every table lands in the
    same database. ``None`` means the target is outside the package (e.g.
    ``(System)``) or has no table; those go into cross-module-fks.md.
    """
    ref_module = rel.get("reference_module") or module
    to_entity = rel.get("to_entity", "")
    if to_entity in module_entities.get(ref_module, set()):
        return _table_name(ref_module, to_entity)
    return None


# OutSystems delete rule -> ON DELETE action. "Ignore" means OutSystems
# creates no database constraint at all, so no FK is emitted for it.
_ON_DELETE = {"Protect": "RESTRICT", "Delete": "CASCADE"}


def _index_name(table: str, columns: list[str], unique: bool) -> str:
    name = f"{'ux' if unique else 'ix'}_{table}__{'_'.join(columns)}"
    if len(name) <= _MAX_IDENT:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:_MAX_IDENT - 9]}_{digest}"


def generate_schema_sql(all_data: list[dict[str, Any]], ci_text: bool = False) -> str:
    """Produce the complete ``schema.sql`` DDL."""
    lines: list[str] = []
    lines.append("-- PostgreSQL DDL generated by os2native generate_db_schema.py")
    lines.append("-- Do not edit manually; regenerate from extraction output.")
    lines.append("")
    if ci_text:
        lines.append("-- Text columns are CITEXT: case-insensitive like OutSystems on SQL Server.")
        lines.append("CREATE EXTENSION IF NOT EXISTS citext;")
        lines.append("")

    # Build lookup: module → set of entity names that get a table
    module_entities: dict[str, set[str]] = {}
    for md in all_data:
        module_entities[md["module"]] = {
            e["name"] for e in md["entities"] if e.get("attributes")
        }

    # Foreign keys are emitted as ALTER TABLE after every CREATE TABLE so
    # table order (and reference cycles) never matter.
    fk_statements: list[str] = []
    index_statements: list[str] = []

    # Build relationship lookup: (module, entity, attribute) → rel
    rel_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for md in all_data:
        for rel in md["relationships"]:
            key = (md["module"], rel.get("from_entity", ""), rel.get("from_attribute", ""))
            rel_lookup[key] = rel

    for md in all_data:
        module = md["module"]
        entities = md["entities"]
        if not entities:
            continue

        lines.append(f"-- {'='*68}")
        lines.append(f"-- Module: {module}")
        lines.append(f"-- {'='*68}")
        lines.append("")

        for entity in entities:
            ename = entity.get("name", "unknown")
            tname = _table_name(module, ename)
            attrs = entity.get("attributes", [])

            if not attrs:
                lines.append(f"-- Skipping {tname}: no attributes defined")
                lines.append("")
                continue

            lines.append(f"CREATE TABLE {_ident(tname)} (")

            col_lines: list[str] = []
            comments: dict[int, str] = {}
            for attr in attrs:
                col_line = _column_def(attr, ci_text)

                # Check for FK
                rel_key = (module, ename, attr.get("name", ""))
                rel = rel_lookup.get(rel_key)
                if rel:
                    target = _fk_target_table(rel, module, module_entities)
                    rule = rel.get("delete_rule") or attr.get("delete_rule") or "Protect"
                    if target and rule == "Ignore":
                        comments[len(col_lines)] = (
                            f"  -- refs {target}.id (delete rule Ignore: no constraint)"
                        )
                    elif target:
                        col = _snake_case(attr["name"])
                        on_delete = _ON_DELETE.get(rule, "RESTRICT")
                        fk_statements.append(
                            f"ALTER TABLE {_ident(tname)} "
                            f"ADD CONSTRAINT {_ident(_constraint_name(tname, col))} "
                            f"FOREIGN KEY ({_ident(col)}) REFERENCES {_ident(target)} (id) "
                            f"ON DELETE {on_delete};"
                        )
                    else:
                        # Cross-module / system ref — add comment
                        ref_mod = rel.get("reference_module", "")
                        to_ent = rel.get("to_entity", "")
                        comments[len(col_lines)] = (
                            f"  -- FK → {ref_mod or module}.{to_ent} (cross-module)"
                        )

                col_lines.append(col_line)

            # Comments go after the comma so they don't swallow it.
            for i, col_line in enumerate(col_lines):
                sep = "," if i < len(col_lines) - 1 else ""
                lines.append(col_line + sep + comments.get(i, ""))
            lines.append(");")
            lines.append("")

            for idx in entity.get("indexes", []):
                cols = [_snake_case(c) for c in idx.get("attributes", []) if c]
                if not cols or cols == ["id"]:
                    continue
                unique = bool(idx.get("unique"))
                index_statements.append(
                    f"CREATE {'UNIQUE ' if unique else ''}INDEX "
                    f"{_ident(_index_name(tname, cols, unique))} ON {_ident(tname)} "
                    f"({', '.join(_ident(c) for c in cols)});"
                )

            # Table comment
            desc = entity.get("description", "")
            if desc:
                escaped = desc.replace("'", "''")
                lines.append(f"COMMENT ON TABLE {_ident(tname)} IS '{escaped}';")
                lines.append("")

    if fk_statements:
        lines.append(f"-- {'='*68}")
        lines.append("-- Foreign keys")
        lines.append(f"-- {'='*68}")
        lines.append("")
        lines.extend(fk_statements)
        lines.append("")

    if index_statements:
        lines.append(f"-- {'='*68}")
        lines.append("-- Indexes")
        lines.append(f"-- {'='*68}")
        lines.append("")
        lines.extend(index_statements)
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def _sql_value(val: Any) -> str:
    """Format a Python value as a SQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def generate_seed_sql(all_data: list[dict[str, Any]]) -> str:
    """Produce INSERT statements from static-data.json files."""
    lines: list[str] = []
    lines.append("-- Seed data generated by os2native generate_db_schema.py")
    lines.append("")

    for md in all_data:
        module = md["module"]
        identity_cols = {
            e.get("name", ""): {
                a["name"] for a in e.get("attributes", []) if _is_identity(a)
            }
            for e in md["entities"]
        }
        for sd in md["static_data"]:
            entity_name = sd.get("entity_name", "unknown")
            records = sd.get("records", [])
            if not records:
                continue

            tname = _table_name(module, entity_name)
            identities = identity_cols.get(entity_name, set())
            lines.append(f"-- {module}.{entity_name}")
            explicit_identities: set[str] = set()

            for rec in records:
                # A NULL identity means "let the database number it"; an
                # explicit one needs OVERRIDING SYSTEM VALUE (GENERATED ALWAYS).
                values = {
                    k: v for k, v in rec.get("values", {}).items()
                    if not (k in identities and v is None)
                }
                if not values:
                    continue
                explicit_identities.update(k for k in values if k in identities)
                override = (
                    " OVERRIDING SYSTEM VALUE"
                    if any(k in identities for k in values) else ""
                )
                cols = [_ident(_snake_case(c)) for c in values.keys()]
                vals = [_sql_value(v) for v in values.values()]
                lines.append(
                    f"INSERT INTO {_ident(tname)} ({', '.join(cols)}){override} "
                    f"VALUES ({', '.join(vals)});"
                )

            # Explicit ids don't advance the identity sequence; move it past
            # them so the next normal INSERT doesn't collide.
            for col_name in sorted(explicit_identities):
                col = _ident(_snake_case(col_name))
                table_lit = _ident(tname).replace("'", "''")
                lines.append(
                    f"SELECT setval(pg_get_serial_sequence('{table_lit}', "
                    f"'{_snake_case(col_name)}'), (SELECT MAX({col}) FROM {_ident(tname)}));"
                )

            lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Markdown documentation
# ---------------------------------------------------------------------------

def generate_schema_md(all_data: list[dict[str, Any]], stats: dict[str, int]) -> str:
    """Produce the human-readable schema documentation."""
    lines: list[str] = []
    lines.append("# Database Schema")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total tables:** {stats['total_tables']}")
    lines.append(f"- **Total columns:** {stats['total_columns']}")
    lines.append(f"- **Total foreign keys:** {stats['total_fks']}")
    lines.append(f"- **Modules:** {stats['total_modules']}")
    lines.append("")

    for md in all_data:
        module = md["module"]
        entities = md["entities"]
        if not entities:
            continue

        lines.append(f"## {module}")
        lines.append("")

        for entity in entities:
            ename = entity.get("name", "unknown")
            tname = _table_name(module, ename)
            attrs = entity.get("attributes", [])
            desc = entity.get("description", "")

            lines.append(f"### `{tname}`")
            if desc:
                lines.append(f"\n{desc}\n")
            lines.append("")

            if not attrs:
                lines.append("_No attributes defined._\n")
                continue

            # Column table
            headers = ["Column", "Type", "Nullable", "Notes"]
            rows: list[list[str]] = []
            for attr in attrs:
                col = _snake_case(attr["name"])
                col_type = column_type(attr)
                nullable = "NO" if attr.get("mandatory") else "YES"
                notes_parts: list[str] = []
                if attr.get("name") == "Id":
                    col_type = "BIGINT"
                    nullable = "NO"
                    notes_parts.append("PK, identity")
                elif attr.get("auto_number"):
                    col_type = "BIGINT"
                    notes_parts.append("auto-number")
                if attr.get("default") is not None:
                    notes_parts.append(f"default: {attr['default']}")
                if attr.get("delete_rule"):
                    notes_parts.append(f"delete rule: {attr['delete_rule']}")
                rows.append([f"`{col}`", col_type, nullable, ", ".join(notes_parts)])

            lines.append(md_table(headers, rows))
            lines.append("")

            # Relationships for this entity
            entity_rels = [
                r for r in md["relationships"]
                if r.get("from_entity") == ename
            ]
            if entity_rels:
                lines.append("**Relationships:**\n")
                for rel in entity_rels:
                    ref_mod = rel.get("reference_module", "")
                    target = rel.get("to_entity", "")
                    from_attr = rel.get("from_attribute", "")
                    if ref_mod and ref_mod != module:
                        lines.append(f"- `{_snake_case(from_attr)}` → `{ref_mod}.{target}` (cross-module)")
                    else:
                        lines.append(f"- `{_snake_case(from_attr)}` → `{_table_name(module, target)}`")
                lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ER diagram (Mermaid)
# ---------------------------------------------------------------------------

def _mermaid_id(module: str, entity: str) -> str:
    """Create a Mermaid-safe identifier."""
    return re.sub(r"[^a-zA-Z0-9]", "_", f"{module}__{entity}")


def generate_er_diagram(all_data: list[dict[str, Any]], stats: dict[str, int]) -> str:
    """Produce Mermaid ER diagrams."""
    lines: list[str] = []
    lines.append("# ER Diagram")
    lines.append("")

    split = stats["total_tables"] > 30

    if split:
        lines.append("Diagrams are split by module (>30 entities total).\n")

    modules_to_render: list[list[dict[str, Any]]]
    if split:
        modules_to_render = [[md] for md in all_data if md["entities"]]
    else:
        modules_to_render = [all_data]

    for group in modules_to_render:
        if split:
            lines.append(f"## {group[0]['module']}")
            lines.append("")

        lines.append("```mermaid")
        lines.append("erDiagram")

        for md in group:
            module = md["module"]
            for entity in md["entities"]:
                ename = entity.get("name", "unknown")
                mid = _mermaid_id(module, ename)
                attrs = entity.get("attributes", [])

                lines.append(f"    {mid} {{")
                for attr in attrs[:10]:  # limit to avoid huge diagrams
                    col_type = pg_type(attr.get("type", "Text")).split("(")[0]  # strip params
                    col_name = _snake_case(attr["name"])
                    pk = "PK" if attr.get("name") == "Id" else ""
                    fk = ""
                    # Check if this attr is an FK
                    for rel in md["relationships"]:
                        if rel.get("from_entity") == ename and rel.get("from_attribute") == attr.get("name"):
                            ref_mod = rel.get("reference_module", "")
                            if not ref_mod or ref_mod == module:
                                fk = "FK"
                            break
                    constraint = pk or fk
                    lines.append(f"        {col_type} {col_name} {constraint}".rstrip())
                if len(attrs) > 10:
                    lines.append(f"        TEXT _and_{len(attrs) - 10}_more")
                lines.append("    }")

            # Relationships
            for rel in md["relationships"]:
                from_ent = rel.get("from_entity", "")
                to_ent = rel.get("to_entity", "")
                ref_mod = rel.get("reference_module", "")

                if ref_mod and ref_mod != module:
                    continue  # skip cross-module in diagram

                from_id = _mermaid_id(module, from_ent)
                to_id = _mermaid_id(module, to_ent)
                from_attr = _snake_case(rel.get("from_attribute", ""))
                lines.append(f"    {from_id} }}o--|| {to_id} : \"{from_attr}\"")

        lines.append("```")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cross-module FK report
# ---------------------------------------------------------------------------

def generate_cross_module_fks(all_data: list[dict[str, Any]]) -> str:
    """Produce a Markdown report of cross-module foreign keys."""
    lines: list[str] = []
    lines.append("# Cross-Module Foreign Keys")
    lines.append("")
    lines.append("Foreign keys that reference entities in other modules or system entities.")
    lines.append("When the target module is in the package the constraint is in schema.sql;")
    lines.append("otherwise (e.g. `(System)`) it needs application-level handling or a")
    lines.append("replacement table (users, roles) in the native stack.")
    lines.append("")

    headers = ["Source Module", "Source Table", "Column", "Target Module", "Target Entity", "Kind", "Status"]
    rows: list[list[str]] = []
    module_entities = {
        md["module"]: {e["name"] for e in md["entities"] if e.get("attributes")}
        for md in all_data
    }

    for md in all_data:
        module = md["module"]
        for rel in md["relationships"]:
            ref_mod = rel.get("reference_module", "")
            if not ref_mod or ref_mod == module:
                continue  # same-module, handled in schema.sql

            from_ent = rel.get("from_entity", "")
            from_attr = rel.get("from_attribute", "")
            to_ent = rel.get("to_entity", "")
            kind = rel.get("kind", "foreign_key")
            in_package = _fk_target_table(rel, module, module_entities) is not None
            if not in_package:
                status = "application-level"
            elif rel.get("delete_rule") == "Ignore":
                status = "no constraint (Ignore)"
            else:
                status = "constraint in schema.sql"

            rows.append([
                module,
                f"`{_table_name(module, from_ent)}`",
                f"`{_snake_case(from_attr)}`",
                ref_mod,
                to_ent,
                kind,
                status,
            ])

    if rows:
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        lines.append(md_table(headers, rows))
    else:
        lines.append("_No cross-module foreign keys found._")

    lines.append("")
    lines.append(f"\n**Total:** {len(rows)} cross-module references")
    lines.append("")
    return "\n".join(lines) + "\n"


def generate_client_entities_md(all_data: list[dict[str, Any]]) -> str:
    """List client (on-device local storage) entities, which get no table."""
    lines = [
        "# Client Entities (local storage)",
        "",
        "Mobile client entities live in on-device storage, not the server database,",
        "so schema.sql has no table for them. Rebuild them with the app's local",
        "storage (e.g. SQLite / IndexedDB) and its sync logic.",
        "",
    ]
    rows = [
        [md["module"], e.get("name", ""), str(len(e.get("attributes", [])))]
        for md in all_data for e in md.get("client_entities", [])
    ]
    if rows:
        lines.append(md_table(["Module", "Entity", "Attributes"], rows))
    else:
        lines.append("_No client entities found._")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _compute_stats(all_data: list[dict[str, Any]]) -> dict[str, int]:
    """Compute summary statistics across all modules."""
    total_tables = 0
    total_columns = 0
    total_fks = 0
    total_modules = 0

    for md in all_data:
        entities = md["entities"]
        if not entities:
            continue
        total_modules += 1
        for entity in entities:
            attrs = entity.get("attributes", [])
            if not attrs:
                continue
            total_tables += 1
            total_columns += len(attrs)

        total_fks += len(md["relationships"])

    return {
        "total_tables": total_tables,
        "total_columns": total_columns,
        "total_fks": total_fks,
        "total_modules": total_modules,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = make_arg_parser("Generate PostgreSQL DDL and documentation from extraction output")
    parser.add_argument(
        "--citext",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Make Text columns CITEXT (case-insensitive, like OutSystems on SQL Server). "
             "Default: stack.database.case_insensitive_text in project.json, else no",
    )
    args = parser.parse_args()
    pass_dir: Path = args.pass_dir
    if args.citext is None:
        from target_stack import stack_value

        args.citext = stack_value(pass_dir, "database", "case_insensitive_text") is True
    print(f"Case-insensitive text (CITEXT): {'yes' if args.citext else 'no'}")

    # Load packages to get module list
    packages = load_pass_packages(pass_dir)
    if not packages:
        print("ERROR: No packages found in extraction", file=sys.stderr)
        sys.exit(1)

    total_pkgs = len(packages)
    print(f"Loading data from {total_pkgs} modules...")

    # Load entity data for each module
    all_data: list[dict[str, Any]] = []
    for idx, pkg in enumerate(packages, 1):
        module_name = pkg.get("module_name", "")
        if not module_name:
            continue
        print(f"  [{idx:>{len(str(total_pkgs))}}/{total_pkgs}] Loading {module_name}...")
        md = _load_module_data(pass_dir, module_name)
        if md["entities"] or md["client_entities"]:
            all_data.append(md)

    if not all_data:
        print("WARNING: No entities found in any module", file=sys.stderr)
        # Still create output dir and write empty files
        out = deliverables_dir(pass_dir, "db-schema")
        write_text(out / "schema.sql", "-- No entities found in extraction\n")
        write_text(out / "seed-data.sql", "-- No static data found in extraction\n")
        write_text(out / "schema.md", "# Database Schema\n\nNo entities found.\n")
        write_text(out / "er-diagram.md", "# ER Diagram\n\nNo entities found.\n")
        write_json(out / "type-mapping.json", OS_TO_PG_TYPE)
        write_text(out / "cross-module-fks.md", "# Cross-Module Foreign Keys\n\nNo data.\n")
        return

    stats = _compute_stats(all_data)
    out = deliverables_dir(pass_dir, "db-schema")

    # Generate all outputs
    print("Generating schema.sql...")
    write_text(out / "schema.sql", generate_schema_sql(all_data, ci_text=args.citext))

    print("Generating seed-data.sql...")
    write_text(out / "seed-data.sql", generate_seed_sql(all_data))

    print("Generating schema.md...")
    write_text(out / "schema.md", generate_schema_md(all_data, stats))

    print("Generating er-diagram.md...")
    write_text(out / "er-diagram.md", generate_er_diagram(all_data, stats))

    print("Writing type-mapping.json...")
    write_json(out / "type-mapping.json", OS_TO_PG_TYPE)

    print("Generating cross-module-fks.md...")
    write_text(out / "cross-module-fks.md", generate_cross_module_fks(all_data))

    print("Generating client-entities.md...")
    write_text(out / "client-entities.md", generate_client_entities_md(all_data))

    # Summary
    cross_fks = sum(
        1 for md in all_data for rel in md["relationships"]
        if rel.get("reference_module") and rel.get("reference_module") != md["module"]
    )
    print(f"\n{'='*60}")
    print(f"  Tables:             {stats['total_tables']}")
    print(f"  Columns:            {stats['total_columns']}")
    print(f"  Foreign keys:       {stats['total_fks']}")
    print(f"  Cross-module FKs:   {cross_fks}")
    print(f"  Modules with data:  {stats['total_modules']}")
    print(f"  Static data sets:   {sum(len(md['static_data']) for md in all_data)}")
    print(f"  Output:             {out}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
