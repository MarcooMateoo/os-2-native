"""Site property catalogue for the handover: complete, and never leaks sensitive values."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generator_utils  # noqa: E402
from generate_site_properties import MASKED, NO_DEFAULT, collect, is_secret, render, shown_default  # noqa: E402

TEXT = {"kind": "primitive", "name": "Text"}
INT = {"kind": "primitive", "name": "Integer"}


def _prop(name, default=None, type_=TEXT, secret=False, platform=False, description=""):
    return {"name": name, "description": description, "read_only": platform, "is_platform": platform,
            "is_secret": secret, "type": type_, "default": default}


def test_secrets_are_recognised_by_flag_and_by_name():
    assert is_secret(_prop("Anything", secret=True))
    for name in ("ClientSecret", "APIKey", "PayrollFileAPIKey", "CryptoKey", "Key", "DbPassword", "AccessToken"):
        assert is_secret(_prop(name)), name
    for name in ("Domain", "RetentionPeriod", "KeyboardLayout", "Monkey"):
        assert not is_secret(_prop(name)), name


def test_sensitive_defaults_are_masked():
    assert shown_default(_prop("ClientSecret", '"abc123"')) == MASKED
    assert shown_default(_prop("SalesOpsRecipient", '"ops@example.com"')) == MASKED
    assert shown_default(_prop("Notes", '"write to someone@example.com"')) == MASKED
    assert shown_default(_prop("Owner", '"x"', type_={"kind": "primitive", "name": "Email"})) == MASKED
    assert shown_default(_prop("RetentionPeriod", "3", type_=INT)) == "3"
    assert shown_default(_prop("Domain", '"intranet"')) == '"intranet"'
    assert shown_default(_prop("Domain", None)) == NO_DEFAULT
    assert shown_default(_prop("Domain", '""')) == NO_DEFAULT


def test_long_and_multiline_defaults_keep_the_table_intact():
    long_cfg = '"toolbar: [\n  { name: \'basic\' },\n' + "  x" * 200 + '\n]"'
    shown = shown_default(_prop("EditorConfig", long_cfg))
    assert "\n" not in shown and len(shown) < 160 and "chars, see the module" in shown
    table = generator_utils.md_table(["Name", "Default"], [["A|B", "line1\nline2"]])
    assert table.splitlines()[2] == "| A\\|B | line1 line2 |"


def test_credential_identifiers_are_masked_but_not_secrets():
    for name in ("PartnerAPI_AppId", "ClientId", "Tenant_Id"):
        assert shown_default(_prop(name, '"0f1e2d3c"')) == MASKED, name
        assert not is_secret(_prop(name)), name
    assert shown_default(_prop("EventId", "5", type_=INT)) == "5"


def _module(tmp_path: Path, name: str, props: list[dict]) -> None:
    mod = tmp_path / "extraction" / name
    (mod / "data").mkdir(parents=True)
    (mod / "package.json").write_text(json.dumps({"module_name": name}), encoding="utf-8")
    (mod / "data" / "site-properties.json").write_text(json.dumps(props), encoding="utf-8")


def test_every_site_property_is_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(generator_utils, "_ALL_MODULES", True)
    _module(tmp_path, "App", [_prop("TenantId", platform=True), _prop("Domain", '"intranet"'),
                              _prop("EnableInternalLogin", "True", {"kind": "primitive", "name": "Boolean"})])
    _module(tmp_path, "Login_IS", [_prop("TenantId", platform=True), _prop("ClientSecret", '"s3cr3t"')])
    rows, platform = collect(tmp_path, {"App"})
    assert platform == 2
    assert [(r["module"], r["name"]) for r in rows] == [
        ("App", "Domain"), ("App", "EnableInternalLogin"), ("Login_IS", "ClientSecret")]
    secret = rows[-1]
    assert secret["scope"] == "used dependency" and secret["secret"] and secret["target"] == "Secrets Manager"
    assert secret["config_key"] == "Login_IS__ClientSecret" and secret["runtime_value"] == "not collected"
    md = render(rows, platform)
    assert "s3cr3t" not in md and "3 in 2 module(s)" in md and "`Login_IS__ClientSecret`" in md
