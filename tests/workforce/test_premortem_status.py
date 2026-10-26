# SPDX-License-Identifier: Apache-2.0
"""Regression test for audit 010 WF-015, through the library facade: the status says whether the premortem applied.

The archive's coding standard kept a construction under ``harness_intelligence`` and said nothing when its one template
did not match: a reworded replay requirement silently got no task-derived check. Every standard now carries
``premortem``: what the constructor is (fixed templates, not general), whether it applied, and, when it did not, a
sentence saying so.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from g6.workforce import api, coding, service

DATA = Path(__file__).resolve().parent / "data" / "hic05-dev-v1"
#: The benchmark requirement, reworded: the template's one sentence does not match it.
REWORDED = ("Make apply_event handle every stable event ID only once, even if a different event comes between two "
            "identical retries. Keep the existing tests passing.")


@pytest.fixture
def wf(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.setenv(service.ENV_READINESS, "0")  # no readiness question: the standard is frozen at once
    found = api.bind()
    repo = api.repository_dir(found) / "inventory"
    for relative in ("inventory.py", "tests/test_inventory.py"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((DATA / relative).read_bytes())
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    api.allow_execution(found, "inventory", lambda lines: "premortem-test approver")  # WF-007
    return found


def _standard(wf, objective: str) -> dict:
    made = api.create_coding_mission(wf, objective, "inventory", module="inventory.py", function="apply_event",
                                     risk="R0")
    return api.status(wf, made["mission_id"], "full")["coding"]["standard"]


def test_wf015_a_reworded_requirement_is_said_to_get_no_constructed_check(wf):
    standard = _standard(wf, REWORDED)
    assert "harness_intelligence" not in standard
    premortem = standard["premortem"]
    assert premortem["applied"] is False and premortem["general"] is False
    assert premortem["said"] == coding.NOT_CONSTRUCTED


def test_wf015_the_one_sentence_it_matches_is_said_to_be_a_fixed_template(wf):
    task = json.loads((DATA / "task.json").read_text(encoding="utf-8"))
    standard = _standard(wf, task["objective"])
    assert standard["harness_intelligence"]["checks"]
    assert standard["premortem"]["applied"] is True and "said" not in standard["premortem"]
    assert standard["premortem"]["kind"] == "fixed_templates"
