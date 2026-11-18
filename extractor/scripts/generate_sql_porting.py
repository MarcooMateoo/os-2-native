"""List what each Advanced SQL query needs to run on PostgreSQL.

OutSystems Advanced SQL is extracted as text in OutSystems notation
(``{Entity}``, ``{Entity}.[Attr]``, ``@Param``) and usually in the SQL Server
dialect. This script does not translate it. It scans every query for constructs
that must change and writes a checklist per query, so the porting work can be
sized and assigned.

Output goes to ``<pass_dir>/deliverables/sql-porting/``:

- ``sql-porting.md``   checklist per query, most work first
- ``sql-porting.json`` the same findings, machine-readable

Usage::

    python scripts/generate_sql_porting.py ..\\projects\\<Project>\\OUTPUT
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from generator_utils import (
    deliverables_dir,
    load_domain_file,
    load_pass_packages,
    make_arg_parser,
    md_table,
    write_json,
    write_text,
)

# (id, severity, regex, what to do in PostgreSQL)
# severity: "manual" needs a rewrite, "review" needs a decision or check,
# "mechanical" is a direct substitution.
RULES: list[tuple[str, str, str, str]] = [
    ("entity-placeholder", "mechanical", r"\{[A-Za-z_][A-Za-z0-9_]*\}",
     "Replace {Entity} with the generated table name (see db-schema/schema.sql)."),
    ("bracket-identifier", "mechanical", r"\[[A-Za-z_][A-Za-z0-9_ ]*\]",
     "Replace [Attr] with the snake_case column name (double-quote it if reserved)."),
    ("parameter", "mechanical", r"@[A-Za-z_][A-Za-z0-9_]*",
     "Bind @Param as a driver parameter ($1, $2, ... or named)."),
    ("top", "mechanical", r"\bSELECT\s+(DISTINCT\s+)?TOP\b",
     "TOP n -> LIMIT n at the end of the query."),
    ("isnull", "mechanical", r"\bISNULL\s*\(", "ISNULL(a, b) -> COALESCE(a, b)."),
    ("len", "mechanical", r"\bLEN\s*\(", "LEN(x) -> char_length(x) (LEN ignores trailing spaces)."),
    ("getdate", "mechanical", r"\b(?:GETDATE|SYSDATETIME)\s*\(\s*\)",
     "GETDATE() -> now() / LOCALTIMESTAMP; decide on time zone handling."),
    ("getutcdate", "mechanical", r"\b(GETUTCDATE|SYSUTCDATETIME)\s*\(",
     "GETUTCDATE() -> (now() AT TIME ZONE 'utc')."),
    ("rand", "mechanical", r"\bRAND\s*\(", "RAND() -> random()."),
    ("newid", "mechanical", r"\bNEWID\s*\(", "NEWID() -> gen_random_uuid()."),
    ("charindex", "mechanical", r"\bCHARINDEX\s*\(",
     "CHARINDEX(sub, s) -> strpos(s, sub) (argument order flips)."),
    ("iif", "mechanical", r"\bIIF\s*\(", "IIF(c, a, b) -> CASE WHEN c THEN a ELSE b END."),
    ("nolock", "mechanical", r"WITH\s*\(\s*NOLOCK\s*\)|\bNOLOCK\b",
     "Remove (NOLOCK); PostgreSQL readers never block writers."),
    ("sqlserver-types", "mechanical",
     r"\b(NVARCHAR|NCHAR|NTEXT|DATETIME2?|SMALLDATETIME|BIT|UNIQUEIDENTIFIER|TINYINT|MONEY|IMAGE)\b",
     "Map SQL Server types: NVARCHAR->VARCHAR/TEXT, DATETIME->TIMESTAMP, BIT->BOOLEAN, "
     "UNIQUEIDENTIFIER->UUID, TINYINT->SMALLINT, MONEY->NUMERIC(19,4), IMAGE->BYTEA."),
    ("date-functions", "manual", r"\b(DATEADD|DATEDIFF|DATEPART|DATENAME|EOMONTH|DATEFROMPARTS)\s*\(",
     "Rewrite with interval arithmetic, date_part/EXTRACT, date_trunc or make_date."),
    ("convert", "manual", r"\bCONVERT\s*\(",
     "CONVERT(type, x[, style]) -> CAST(x AS type) or to_char/to_date with a format."),
    ("string-plus", "review", r"'\s*\+|\+\s*'",
     "String concatenation with + -> || (or concat(), which treats NULL as '')."),
    ("apply", "manual", r"\b(CROSS|OUTER)\s+APPLY\b",
     "CROSS APPLY -> CROSS JOIN LATERAL; OUTER APPLY -> LEFT JOIN LATERAL ... ON TRUE."),
    ("for-xml", "manual", r"\bFOR\s+XML\b", "FOR XML PATH('') concatenation -> string_agg()."),
    ("stuff", "manual", r"\bSTUFF\s*\(", "STUFF(...) -> overlay() or string_agg() pattern."),
    ("identity", "manual", r"@@IDENTITY|\bSCOPE_IDENTITY\s*\(",
     "@@IDENTITY / SCOPE_IDENTITY() -> INSERT ... RETURNING id."),
    ("temp-table", "manual", r"(?<![A-Za-z0-9_])#[A-Za-z_][A-Za-z0-9_]*",
     "#temp table -> CREATE TEMP TABLE (or a CTE)."),
    ("merge", "review", r"\bMERGE\s+(INTO\s+)?[{\[A-Za-z]",
     "MERGE -> INSERT ... ON CONFLICT, or MERGE on PostgreSQL 15+ (semantics differ)."),
    ("offset-fetch", "review", r"\bOFFSET\b[^;]*?\bFETCH\s+(NEXT|FIRST)\b",
     "OFFSET ... FETCH works in PostgreSQL; LIMIT/OFFSET is more common. Check ORDER BY exists."),
    ("like", "review", r"\bLIKE\b",
     "LIKE is case-sensitive in PostgreSQL (SQL Server isn't by default): use ILIKE or CITEXT."),
    ("null-date", "review", r"1900-01-01",
     "'1900-01-01' is the OutSystems NullDate sentinel; decide whether the native schema uses NULL."),
    ("transaction", "review", r"\b(BEGIN\s+TRAN(SACTION)?|COMMIT\s+TRAN|ROLLBACK\s+TRAN)\b",
     "Explicit T-SQL transactions; OutSystems wraps each request in one. Move to the service layer."),
]

_SEVERITY_ORDER = {"manual": 0, "review": 1, "mechanical": 2}


def scan_sql(sql: str) -> list[dict[str, Any]]:
    """Return the porting findings for one query, one entry per rule hit."""
    findings: list[dict[str, Any]] = []
    for rule_id, severity, pattern, advice in RULES:
        matches = re.findall(pattern, sql, flags=re.IGNORECASE)
        if not matches:
            continue
        samples = sorted({m if isinstance(m, str) else "".join(m) for m in matches})
        findings.append({
            "rule": rule_id,
            "severity": severity,
            "count": len(matches),
            "examples": [s for s in samples if s][:5],
            "advice": advice,
        })
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f["severity"]], f["rule"]))
    return findings


def analyse_query(module: str, q: dict[str, Any]) -> dict[str, Any]:
    sql = q.get("sql") or ""
    findings = scan_sql(sql)
    inline = [p.get("name") for p in q.get("parameters", []) if p.get("expand_inline")]
    if inline:
        findings.insert(0, {
            "rule": "expand-inline",
            "severity": "manual",
            "count": len(inline),
            "examples": inline,
            "advice": "Expand Inline parameters are pasted into the SQL text: rebuild them "
                      "with bound parameters or a whitelist to avoid SQL injection.",
        })
    effort = (
        "manual" if any(f["severity"] == "manual" for f in findings)
        else "review" if any(f["severity"] == "review" for f in findings)
        else "mechanical"
    )
    return {
        "module": module,
        "action": q.get("action"),
        "node": q.get("node"),
        "node_key": q.get("node_key"),
        "effort": effort,
        "parameters": [p.get("name") for p in q.get("parameters", [])],
        "output_structure": q.get("output_structure", []),
        "findings": findings,
        "sql": sql,
    }


def build_report(results: list[dict[str, Any]]) -> str:
    lines = ["# Advanced SQL porting checklist", ""]
    lines.append(
        "Each OutSystems Advanced SQL query and what must change for PostgreSQL. "
        "`manual` needs a rewrite, `review` needs a decision, `mechanical` is a direct "
        "substitution. Nothing here is translated automatically."
    )
    lines.append("")
    if not results:
        lines.append("_No Advanced SQL queries found._")
        return "\n".join(lines) + "\n"

    totals = {k: sum(1 for r in results if r["effort"] == k) for k in _SEVERITY_ORDER}
    lines.append(md_table(
        ["Queries", "Manual", "Review", "Mechanical"],
        [[str(len(results)), str(totals["manual"]), str(totals["review"]), str(totals["mechanical"])]],
    ))
    lines.append("")
    lines.append(md_table(
        ["Module", "Action", "Query", "Effort", "Findings"],
        [
            [r["module"], r["action"] or "", r["node"] or "", r["effort"],
             ", ".join(f["rule"] for f in r["findings"])]
            for r in results
        ],
    ))
    lines.append("")
    for r in results:
        lines.append(f"## {r['module']} / {r['action']} / {r['node']} ({r['effort']})")
        lines.append("")
        if r["parameters"]:
            lines.append(f"Parameters: {', '.join(f'`{p}`' for p in r['parameters'])}")
            lines.append("")
        for f in r["findings"]:
            examples = ", ".join(f"`{e.strip()}`" for e in f["examples"])
            lines.append(f"- [ ] **{f['rule']}** ({f['severity']}, {f['count']}x) {f['advice']}"
                         + (f" Seen: {examples}" if examples else ""))
        lines.append("")
        lines.append("```sql")
        lines.append(r["sql"].strip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = make_arg_parser("List what each Advanced SQL query needs to run on PostgreSQL")
    args = parser.parse_args()
    pass_dir: Path = args.pass_dir

    packages = load_pass_packages(pass_dir)
    if not packages:
        print("ERROR: No packages found in extraction", file=sys.stderr)
        sys.exit(1)

    results: list[dict[str, Any]] = []
    for pkg in packages:
        module = pkg.get("module_name", "")
        if not module:
            continue
        queries = load_domain_file(pass_dir, module, "logic", "advanced-sql.json")
        for q in queries if isinstance(queries, list) else []:
            results.append(analyse_query(module, q))

    results.sort(key=lambda r: (_SEVERITY_ORDER[r["effort"]], r["module"], r["action"] or ""))
    out = deliverables_dir(pass_dir, "sql-porting")
    write_json(out / "sql-porting.json", results)
    write_text(out / "sql-porting.md", build_report(results))
    manual = sum(1 for r in results if r["effort"] == "manual")
    print(f"{len(results)} Advanced SQL queries, {manual} need a manual rewrite -> {out}")


if __name__ == "__main__":
    main()
