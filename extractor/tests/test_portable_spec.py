"""The portable product specification must be complete and reveal nothing of the current build."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_portable_spec import REQUIRED_FILES, check, main  # noqa: E402

CLEAN = {
    "README.md": "# Order desk\n\nRead PRD.md first.\n",
    "PRD.md": "# PRD\n\n- F1 Place an order (BR1, J1)\n- Q1 Which currencies are allowed?\n",
    "domain-model.md": "# Domain model\n\nAn order has a number and a status.\n",
    "screens-and-navigation.md": "# Screens\n\nThe order list shows open orders to sales users.\n",
    "behaviour.md": "# Behaviour\n\n## B1 Submit an order\n\n1. Check the order has at least one line.\n",
    "integrations.md": "# Integrations\n\nThe HR system provides employee details.\n",
    "configuration-and-jobs.md": "# Configuration\n\n- Approval email recipients (setting, per environment)\n",
    "glossary.md": "# Glossary\n\n- Order: a customer request for services.\n",
}


def _project(tmp_path: Path, pack: dict[str, str] | None = None) -> Path:
    out = tmp_path / "OUTPUT"
    mod = out / "extraction" / "SP_Orders_CS"
    (mod / "interfaces").mkdir(parents=True)
    (mod / "interfaces" / "themes.json").write_text(json.dumps([{"name": "PortalTheme"}]), encoding="utf-8")
    css = mod / "evidence" / "assets" / "css"
    css.mkdir(parents=True)
    (css / "theme.css").write_text(
        "body { font-family: 'Brandon Grotesque', sans-serif; color: #e60028; }", encoding="utf-8")
    (out / "extraction" / "Country").mkdir()
    app = out / "SPECS" / "SalesPortal"
    (app / "portable-spec").mkdir(parents=True)
    (app / "product-specification.md").write_text("F1 orders, J1 journey, BR1 rule, Q1 question", encoding="utf-8")
    (app / "action-catalogue.md").write_text(
        "# Catalogue\n\n## Summary\n\n## SP_Orders_CS\n\n### Server action: `SubmitOrder`\n", encoding="utf-8")
    (app / "portable-spec-trace.md").write_text("| ID | Source |\n|---|---|\n| B1 | SP_Orders_CS › SubmitOrder |\n",
                                                encoding="utf-8")
    for name, text in (pack or CLEAN).items():
        (app / "portable-spec" / name).write_text(text, encoding="utf-8")
    return out


def _rules(findings):
    return {(f.rule, f.text) for f in findings}


def test_clean_pack_passes(tmp_path):
    out = _project(tmp_path)
    assert check(out, "SalesPortal") == []
    assert main([str(out), "--app", "SalesPortal"]) == 0


def test_implementation_details_are_flagged(tmp_path):
    leaky = dict(CLEAN)
    leaky["behaviour.md"] += (
        "\nThe Server Action reads an Aggregate and calls SP_Orders_CS, Billing_IS and CryptoAPI.\n"
        "Built with React and PostgreSQL. Uses `NullIdentifier()`.\n"
        "Header colour #e60028, rgb(0, 0, 0), font Brandon Grotesque and Roboto. Theme PortalTheme.\n"
        "Send to jane.doe@corp.com; password: Sup3rSecret!\n"
    )
    found = _rules(check(_project(tmp_path, leaky), "SalesPortal"))
    for expected in [("platform term", "Server Action"), ("platform term", "Aggregate"),
                     ("platform term", "NullIdentifier"), ("module name", "SP_Orders_CS"),
                     ("module-style name", "Billing_IS"), ("component or library", "CryptoAPI"),
                     ("technology", "React"), ("technology", "PostgreSQL"), ("colour", "#e60028"),
                     ("colour", "rgb("), ("font", "Brandon Grotesque"), ("font", "Roboto"),
                     ("theme name", "PortalTheme"), ("email address", "jane.doe@corp.com")]:
        assert expected in found, expected
    assert any(rule == "secret value" for rule, _ in found)


def test_ordinary_words_are_not_flagged(tmp_path):
    words = dict(CLEAN)
    words["PRD.md"] += ("\nUsers aggregate totals per Country; the team will forge ahead. See [list](#add-order).\n"
                        "Contact support@example.com.\n")
    assert check(_project(tmp_path, words), "SalesPortal") == []


def test_allow_list_and_template_comments(tmp_path):
    allowed = dict(CLEAN)
    allowed["integrations.md"] += "\nSingle sign-on through Okta.\n<!-- a Server Action note for the writer -->\n"
    out = _project(tmp_path, allowed)
    (out / "SPECS" / "SalesPortal" / "portable-spec-allow.txt").write_text(
        "Okta  # the new product must keep this identity provider\n", encoding="utf-8")
    assert check(out, "SalesPortal") == []


def test_coverage(tmp_path):
    partial = dict(CLEAN)
    partial["PRD.md"] = "# PRD\n\n- F1 Place an order (BR1)\n"
    partial["behaviour.md"] = "# Behaviour\n\nNothing yet.\n"
    del partial["glossary.md"]
    out = _project(tmp_path, partial)
    (out / "SPECS" / "SalesPortal" / "action-catalogue.md").write_text(
        "## SP_Orders_CS\n\n### Server action: `SubmitOrder`\n\n### Client action: `ShowOrder`\n", encoding="utf-8")
    found = _rules(check(out, "SalesPortal"))
    assert ("missing ID", "J1 from product-specification.md") in found
    assert ("missing ID", "Q1 from product-specification.md") in found
    assert ("unmapped action", "SP_Orders_CS › ShowOrder has no behaviour ID in portable-spec-trace.md") in found
    assert ("missing behaviour", "B1 (SP_Orders_CS › SubmitOrder) is not in behaviour.md") in found
    assert ("missing", "required file is missing") in found


def test_required_files_and_no_other_formats(tmp_path):
    out = _project(tmp_path)
    (out / "SPECS" / "SalesPortal" / "portable-spec" / "logo.png").write_bytes(b"\x89PNG")
    assert ("not markdown", "only Markdown files belong in the pack") in _rules(check(out, "SalesPortal"))
    assert "behaviour.md" in REQUIRED_FILES
