# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-007 (its scenario T-08): no repository code runs before a person allows it.

The archive's ``mission_create`` ran the repository's whole test suite (and so its ``conftest.py``), and the failing
tests again to derive the scope, on the chat's first call, before any person confirmed anything. The standard cannot be
frozen without those runs, so a person now allows G6 to run the repository's code at its present revision first
(``allow_execution``, or ``decide allow`` at a terminal); without that permission ``create_coding_mission`` refuses and
runs nothing. The permission is bound to the revision and content it was given for.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from g6.core import confine
from g6.workforce import api, coding, decide, service

POLICY = 'def accepts(value):\n    return value >= 3\n\n\ndef label():\n    return "wrong"\n'


def _commit(repo: Path, files: dict[str, str], *, first: bool = True) -> str:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in ((("init", "-q"),) if first else ()) + (("add", "."), (
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    # Unconfined, so a conftest that runs can write its marker: confined, its write outside the run's copy is
    # refused, and the marker could no longer show whether the repository's code ran. These tests are about the
    # permission, not the operating system's refusal (test_workforce_isolation).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    wf = api.bind()
    marker = tmp_path / "conftest-ran.txt"  # outside the repository: what a hostile conftest could write
    repo = api.repository_dir(wf) / "sample"
    _commit(repo, {
        "policy.py": POLICY,
        "tests/conftest.py": f"from pathlib import Path\n\nPath({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        "tests/test_objective.py": 'from policy import label\n\ndef test_label():\n    assert label() == "ready"\n'})
    return wf, repo, marker


def _create(wf):
    return api.create_coding_mission(wf, "Fix the label beside the acceptance rule and preserve its behavior.",
                                     "sample", module="policy.py", function="label", risk="R0")


def test_wf007_mission_create_runs_nothing_before_a_person_allows_it(rig):
    wf, _, marker = rig
    with pytest.raises(ValueError, match="G6_E_HUMAN_APPROVAL_REQUIRED") as refused:
        _create(wf)
    assert "G6 ran nothing" in str(refused.value)
    assert not marker.exists(), "the repository's conftest ran before any person allowed it"
    assert wf.missions.list(wf.venture_id) == []


def test_wf007_once_allowed_the_mission_is_created_and_the_permission_is_recorded(rig):
    """The control: the same request runs once a person allowed it, and the record says what they allowed and that
    G6 did not authenticate them (WF-013)."""
    wf, repo, marker = rig
    shown = []
    allowed = api.allow_execution(wf, "sample", lambda lines: shown.extend(lines) or "permission-test approver")
    assert any("not an operating-system sandbox" in line for line in shown)
    assert any(allowed["revision"] in line for line in shown)
    made = _create(wf)
    assert made["status"] == "awaiting_plan", made
    assert marker.exists()
    [record] = wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED)
    detail = record["detail"]
    assert (detail["repository"], detail["revision"], detail["digest"]) == (
        "sample", allowed["revision"], allowed["digest"])
    assert detail["decided_by"] == "approver:permission-test approver"
    assert detail["principal"]["channel"] == "approver" and detail["principal"]["authenticated"] is False


def test_wf007_a_permission_covers_only_the_revision_it_was_given_for(rig):
    wf, repo, marker = rig
    api.allow_execution(wf, "sample", lambda lines: "permission-test approver")
    _commit(repo, {"policy.py": POLICY + "\n\ndef other():\n    return 1\n"}, first=False)
    with pytest.raises(ValueError, match="G6_E_HUMAN_APPROVAL_REQUIRED"):
        _create(wf)
    assert not marker.exists()


def test_wf007_a_refused_permission_records_nothing(rig):
    wf, _, marker = rig
    with pytest.raises(api.NotConfirmed):
        api.allow_execution(wf, "sample", lambda lines: None)
    assert wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED) == []
    with pytest.raises(ValueError, match="G6_E_HUMAN_APPROVAL_REQUIRED"):
        _create(wf)
    assert not marker.exists()


def test_wf007_the_decide_command_allows_only_when_the_name_is_typed_at_a_terminal(rig, monkeypatch, tmp_path):
    wf, _, marker = rig
    monkeypatch.setenv(service.ENV_VENTURE, wf.venture_id)
    reason = ["--reason", "I read what it runs"]
    assert decide.main(["allow", "sample", *reason], ask=lambda _: "sample") == 2  # not an interactive terminal
    monkeypatch.setattr(decide, "_interactive", lambda: True)
    assert decide.main(["allow", "sample", *reason], ask=lambda _: "other") == 2  # the wrong name typed back
    assert wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED) == []
    assert decide.main(["allow", "sample", *reason], ask=lambda _: "sample") == 0
    [record] = wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED)
    assert record["detail"]["principal"]["channel"] == "terminal"
    assert record["detail"]["principal"]["independent_of_caller"] is False
    assert _create(wf)["status"] == "awaiting_plan"
    assert marker.exists()


def test_wf007_the_clarification_path_waits_for_a_permission_too(tmp_path, monkeypatch):
    """An underspecified replay objective asks one question before anything runs, and answering it runs the
    preparation: it is refused without a permission as well, and asks its question once allowed."""
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    wf = api.bind()
    data = Path(__file__).resolve().parent / "data" / "hic05-dev-v1"
    _commit(api.repository_dir(wf) / "inventory", {
        "inventory.py": (data / "inventory.py").read_text(encoding="utf-8"),
        "tests/test_inventory.py": (data / "tests/test_inventory.py").read_text(encoding="utf-8")})
    ask = ("Make this event handler idempotent.", "inventory")
    named = {"module": "inventory.py", "function": "apply_event", "risk": "R0"}
    with pytest.raises(ValueError, match="G6_E_HUMAN_APPROVAL_REQUIRED"):
        api.create_coding_mission(wf, *ask, **named)
    assert wf.missions.list(wf.venture_id) == []
    api.allow_execution(wf, "inventory", lambda lines: "permission-test approver")
    assert api.create_coding_mission(wf, *ask, **named).get("interaction_state") == "awaiting_input"
