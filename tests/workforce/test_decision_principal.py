# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-013: a decision records its principal, never an independent person by default.

The archive recorded every confirmation and gate answer as the actor ``human`` once a unit's key was typed at an
interactive terminal, or once the library's approver callable returned a name: the caller's agent, which can drive a
terminal (a pty) or pass any callable, was recorded as the person. Each confirmation and answer now records its
principal: the identity the channel gave, the channel (``approver`` or ``terminal``), and that G6 authenticated no one,
so it is not independent of the caller.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from g6.workforce import api, decide, service

GATE = "approve-patch"


def _commit(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def mission(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv(service.ENV_CERTIFICATES, raising=False)
    wf = api.bind()
    monkeypatch.setenv(service.ENV_VENTURE, wf.venture_id)  # the decide command binds from the environment
    _commit(api.repository_dir(wf) / "calc", {
        "calc.py": "def double(x):\n    return x + x + 1\n",
        "tests/test_calc.py": "from calc import double\n\n\ndef test_double():\n    assert double(2) == 4\n"})
    api.allow_execution(wf, "calc", lambda lines: "principal-test approver")
    made = api.create_coding_mission(wf, "Fix double so that its test passes.", "calc", module="calc.py",
                                     function="double", risk="R3")
    return wf, made["mission_id"]


def _unauthenticated(found: dict, channel: str, identity: str) -> None:
    assert found["channel"] == channel and found["identity"] == identity
    assert found["authenticated"] is False and found["independent_of_caller"] is False


def test_wf013_an_approvers_confirmation_is_the_callers_own_claim(mission):
    wf, mid = mission
    api.confirm(wf, mid, lambda lines: "principal-test approver")
    [confirmed] = [i for i in wf.runtime.decisions.interventions(mid) if i.kind == "clarification"]
    found = confirmed.metadata["principal"]
    _unauthenticated(found, "approver", "approver:principal-test approver")
    assert found["same_process_as_caller"] is True  # the approver is called in the caller's own process


def test_wf013_a_terminal_answer_is_recorded_as_unauthenticated(mission, monkeypatch):
    """The caller's agent drives the decide command through a terminal it opened (simulated: the TTY check passes, as
    it does under a pty): the confirmation and the gate's answer say they are not an independent person's."""
    wf, mid = mission
    monkeypatch.setattr(decide, "_interactive", lambda: True)
    assert decide.main(["confirm", mid, "--reason", "typed at a terminal"], ask=lambda _: mid) == 0
    owner = f"terminal:{wf.missions.owner}"
    [confirmed] = [i for i in wf.runtime.decisions.interventions(mid) if i.kind == "clarification"]
    _unauthenticated(confirmed.metadata["principal"], "terminal", owner)
    api.submit_patch(wf, mid, {"calc.py": "def double(x):\n    return x + x\n"})
    assert decide.main(["approve", mid, GATE, "--reason", "read the checked patch"], ask=lambda _: GATE) == 0
    [decided] = wf.missions.events(mid, kind="approval.decided")
    _unauthenticated(decided["detail"]["principal"], "terminal", owner)
    assert decided["detail"]["principal"]["same_process_as_caller"] is None  # not known at a terminal
    [approval] = [i for i in wf.runtime.decisions.interventions(mid) if i.kind == "approval"]
    assert approval.metadata["principal"] == decided["detail"]["principal"]


def test_wf013_an_approvers_gate_answer_is_the_callers_own_claim(mission):
    wf, mid = mission
    api.confirm(wf, mid, lambda lines: "principal-test approver")
    api.submit_patch(wf, mid, {"calc.py": "def double(x):\n    return x + x\n"})
    api.decide(wf, mid, GATE, "approve", lambda lines: "principal-test approver", reason="read the checked patch")
    [decided] = wf.missions.events(mid, kind="approval.decided")
    _unauthenticated(decided["detail"]["principal"], "approver", "approver:principal-test approver")
