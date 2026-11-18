"""Tests for scripts/generate_sql_porting.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_sql_porting import analyse_query, build_report, scan_sql  # noqa: E402


def _rules(sql: str) -> set[str]:
    return {f["rule"] for f in scan_sql(sql)}


def test_sql_server_constructs_are_flagged():
    sql = (
        "SELECT TOP 10 ISNULL({User}.[Name], '') + ' (' + CONVERT(NVARCHAR(10), {User}.[Id]) + ')' "
        "FROM {User} WITH (NOLOCK) "
        "WHERE DATEADD(day, -7, GETDATE()) < {User}.[CreatedOn] AND {User}.[Name] LIKE @Search"
    )
    rules = _rules(sql)
    assert {"top", "isnull", "string-plus", "convert", "sqlserver-types", "nolock",
            "date-functions", "getdate", "like", "entity-placeholder",
            "bracket-identifier", "parameter"} <= rules
    assert "rand" not in rules


def test_portable_sql_needs_only_mechanical_changes():
    q = {"action": "A", "node": "Q", "sql": "SELECT {T}.[Id] FROM {T} WHERE {T}.[Id] = @Id",
         "parameters": [{"name": "Id"}]}
    result = analyse_query("Mod", q)
    assert result["effort"] == "mechanical"


def test_manual_findings_sort_first_and_set_effort():
    result = analyse_query("Mod", {"sql": "SELECT 1 FROM {T} CROSS APPLY x; SELECT @@IDENTITY",
                                   "parameters": []})
    assert result["effort"] == "manual"
    assert result["findings"][0]["severity"] == "manual"


def test_expand_inline_parameter_is_a_manual_finding():
    q = {"sql": "SELECT * FROM {T} WHERE @Filter",
         "parameters": [{"name": "Filter", "expand_inline": True}]}
    result = analyse_query("Mod", q)
    assert result["findings"][0]["rule"] == "expand-inline"
    assert result["findings"][0]["examples"] == ["Filter"]
    assert result["effort"] == "manual"


def test_null_date_and_temp_tables():
    rules = _rules("SELECT * INTO #tmp FROM {T} WHERE {T}.[D] = '1900-01-01 00:00:00'")
    assert {"temp-table", "null-date"} <= rules


def test_report_lists_every_query():
    results = [analyse_query("Mod", {"action": "A", "node": "Q1", "sql": "SELECT GETDATE()",
                                     "parameters": []})]
    md = build_report(results)
    assert "Mod / A / Q1 (mechanical)" in md
    assert "- [ ] **getdate**" in md
    assert "_No Advanced SQL queries found._" in build_report([])
