"""Timer names must match Service Studio.

A timer named after its action has CustomNameIsSuffix="Yes"; a timer renamed in
Service Studio has its full name in CustomName instead.
"""

from __future__ import annotations

from pathlib import Path

from os2native.shared.coverage import CoverageTracker
from os2native.workers.process import _extract_timers
from os2native.xml_loader import load_oml_xml

_XML = """<?xml version="1.0" encoding="utf-8"?>
<OML>
  <ESpace Key="esp" Name="Mod_CS" FragmentName="eSpace" />
  <eSpaceFragment FragmentName="Timers" Count="2">
    <Timer Key="T1" CustomNameIsSuffix="Yes" Action="Flows.UserAction:/UserActions.A1" Schedule="02:00" />
    <Timer Key="T2" CustomName="Timer_CloneOption" Action="Flows.UserAction:/UserActions.A2" Schedule="03:00" />
  </eSpaceFragment>
  <eSpaceFragment FragmentName="UserActions" Count="2">
    <Flows.UserAction Key="A1" Name="Timer_Purge" />
    <Flows.UserAction Key="A2" Name="Timer_CloneRestriction" />
  </eSpaceFragment>
</OML>
"""


def test_timer_names_follow_service_studio(tmp_path: Path) -> None:
    path = tmp_path / "Mod_CS.xml"
    path.write_text(_XML, encoding="utf-8")
    timers = _extract_timers(load_oml_xml(path), CoverageTracker(domain="processes"))
    by_key = {t["key"]: t for t in timers}

    assert by_key["T1"]["name"] == "Timer_Purge"
    assert by_key["T1"]["name_from_action"] is True

    assert by_key["T2"]["name"] == "Timer_CloneOption"
    assert by_key["T2"]["name_from_action"] is False
    assert by_key["T2"]["action_name"] == "Timer_CloneRestriction"
