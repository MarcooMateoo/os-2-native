"""End-to-end extraction against a real module XML.

Point OS2NATIVE_GOLDEN_XML at a module XML produced by
``os2native extract --oml <Module>.oml --xml-only``. Skipped when unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

GOLDEN = os.environ.get("OS2NATIVE_GOLDEN_XML")


@pytest.fixture(scope="module")
def pkg_root(tmp_path_factory) -> Path:
    if not GOLDEN or not Path(GOLDEN).is_file():
        pytest.skip("OS2NATIVE_GOLDEN_XML not set")
    from os2native.pipeline import _run_extraction
    from os2native.xml_loader import load_oml_xml

    out = tmp_path_factory.mktemp("golden")
    return _run_extraction(load_oml_xml(Path(GOLDEN)), out)


@pytest.mark.parametrize(
    "rel",
    [
        "package.json",
        "summary.md",
        "interfaces/screens.json",
        "logic/server-actions.json",
        "data/entities.json",
        "data/roles-and-permissions.json",
        "dependencies/graph.json",
    ],
)
def test_package_files_written(pkg_root, rel):
    assert (pkg_root / rel).is_file()


def test_screens_is_list(pkg_root):
    screens = json.loads((pkg_root / "interfaces" / "screens.json").read_text(encoding="utf-8"))
    assert isinstance(screens, list)
