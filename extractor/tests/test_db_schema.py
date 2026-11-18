"""Tests for scripts/generate_db_schema.py DDL and seed output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_db_schema import (  # noqa: E402
    _constraint_name,
    _ident,
    generate_schema_sql,
    generate_seed_sql,
)

INT = {"kind": "primitive", "name": "Integer"}
TEXT = {"kind": "primitive", "name": "Text"}


def _module() -> dict:
    # Child is listed BEFORE Parent and Parent points back at Child: inline
    # REFERENCES would fail on both counts.
    return {
        "module": "App_CS",
        "entities": [
            {
                "name": "Child",
                "attributes": [
                    {"name": "Id", "type": INT, "auto_number": True, "mandatory": True},
                    {"name": "ParentId", "type": INT},
                    {"name": "UserId", "type": INT},  # cross-module, not last
                    {"name": "Order", "type": INT, "mandatory": True},
                ],
            },
            {
                "name": "Parent",
                "attributes": [
                    {"name": "Id", "type": INT, "auto_number": True, "mandatory": True},
                    {"name": "FavouriteChildId", "type": INT},
                    {"name": "From", "type": TEXT},
                ],
            },
            {"name": "Empty", "attributes": []},
        ],
        "relationships": [
            {"from_entity": "Child", "from_attribute": "ParentId", "to_entity": "Parent"},
            {"from_entity": "Parent", "from_attribute": "FavouriteChildId", "to_entity": "Child"},
            {"from_entity": "Child", "from_attribute": "UserId", "to_entity": "User",
             "reference_module": "(System)"},
            {"from_entity": "Parent", "from_attribute": "From", "to_entity": "Empty"},
        ],
        "static_data": [
            {
                "entity_name": "Child",
                "records": [
                    {"values": {"Id": None, "ParentId": None, "Order": 1, "UserId": None}},
                    {"values": {"Id": 7, "ParentId": None, "Order": 2, "UserId": None}},
                ],
            }
        ],
    }


def test_ident_quotes_reserved_and_unsafe_names():
    assert _ident("label") == "label"
    assert _ident("order") == '"order"'
    assert _ident("user") == '"user"'
    assert _ident("from") == '"from"'
    assert _ident("1st") == '"1st"'
    assert _ident('we"ird') == '"we""ird"'


def test_constraint_name_fits_postgres_limit():
    long = _constraint_name("a" * 60, "b" * 30)
    assert len(long) <= 63
    assert long != _constraint_name("a" * 60, "c" * 30)


def test_schema_quotes_reserved_columns():
    sql = generate_schema_sql([_module()])
    assert '"order" INTEGER NOT NULL' in sql
    assert '"from" TEXT' in sql


def test_foreign_keys_come_after_all_tables():
    sql = generate_schema_sql([_module()])
    last_create = sql.rindex("CREATE TABLE")
    alters = [i for i in range(len(sql)) if sql.startswith("ALTER TABLE", i)]
    assert len(alters) == 2  # both same-module FKs, incl. the cycle
    assert all(i > last_create for i in alters)
    assert "REFERENCES" not in sql[:last_create]


def test_cross_module_fk_is_comment_after_comma():
    sql = generate_schema_sql([_module()])
    line = next(l for l in sql.splitlines() if l.strip().startswith("user_id"))
    assert line.startswith("    user_id BIGINT") or line.startswith("    user_id INTEGER")
    assert ",  -- FK → (System).User (cross-module)" in line


def test_entity_without_attributes_is_skipped_and_not_referenced():
    sql = generate_schema_sql([_module()])
    assert "-- Skipping app_cs__empty" in sql
    assert "REFERENCES app_cs__empty" not in sql


def test_seed_omits_null_identity_and_overrides_explicit_one():
    seed = generate_seed_sql([_module()])
    lines = [l for l in seed.splitlines() if l.startswith("INSERT")]
    assert lines[0] == (
        'INSERT INTO app_cs__child (parent_id, "order", user_id) VALUES (NULL, 1, NULL);'
    )
    assert lines[1] == (
        'INSERT INTO app_cs__child (id, parent_id, "order", user_id) '
        "OVERRIDING SYSTEM VALUE VALUES (7, NULL, 2, NULL);"
    )


def test_output_parses_as_postgresql():
    pglast = pytest.importorskip("pglast")
    pglast.parse_sql(generate_schema_sql([_module()]))
    pglast.parse_sql(generate_seed_sql([_module()]))
