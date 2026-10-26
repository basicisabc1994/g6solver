# SPDX-License-Identifier: Apache-2.0
"""Regression test for audit 010 WF-005: the certificate is given the mission's gates and a person's decisions.

The archive issued every coding certificate with no gates and no decisions, so an R3 mission's certificate said
nothing of the person's approval its plan requires, and ``verify(..., gates=...)`` reported the approval as missing on
an approved mission. The certificate now carries each gate of the plan, an approved gate as a decision, and an
undecided one as a named gap.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from g6.evidence import certificate as C
from g6.workforce import api, service

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
def r3(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv(service.ENV_CERTIFICATES, raising=False)
    wf = api.bind()
    _commit(api.repository_dir(wf) / "calc", {
        "calc.py": "def double(x):\n    return x + x + 1\n",
        "tests/test_calc.py": "from calc import double\n\n\ndef test_double():\n    assert double(2) == 4\n"})
    api.allow_execution(wf, "calc", lambda lines: "gate-test approver")  # WF-007
    made = api.create_coding_mission(wf, "Fix double so that its test passes.", "calc", module="calc.py",
                                     function="double", risk="R3")
    mid = made["mission_id"]
    api.confirm(wf, mid, lambda lines: "gate-test approver")
    waiting = api.submit_patch(wf, mid, {"calc.py": "def double(x):\n    return x + x\n"})
    return wf, mid, waiting


def test_wf005_an_undecided_gate_is_a_named_gap(r3):
    wf, mid, waiting = r3
    assert [q["unit"] for q in waiting["awaiting_human"]] == [GATE], waiting
    cert = api.certificate(wf, mid)  # issued now, before the person decides
    assert dict(map(tuple, cert["gaps"]))[f"gate:{GATE}"] == "human_decision_missing"
    assert cert["decisions"] == [] and cert["state"] != "verified"


def test_wf005_the_persons_approval_is_a_decision_the_certificate_carries(r3):
    wf, mid, _ = r3
    done = api.decide(wf, mid, GATE, "approve", lambda lines: "gate-test approver", reason="read the checked patch")
    assert done["status"] == "done", done
    cert = api.certificate(wf, mid)
    assert cert["decisions"] == [GATE]
    assert f"gate:{GATE}" not in dict(map(tuple, cert["gaps"]))
    latest = wf.ledger.last_artifact(mid, "patch")
    body = json.dumps(latest["content"], sort_keys=True, default=str).encode("utf-8")
    assert C.verify(cert, body, gates=[GATE]) == []
    dropped = copy.deepcopy(cert)  # a certificate that drops the decision is caught by verify given the gates
    dropped["decisions"] = []
    dropped["digest"] = C.digest_of(dropped)
    assert f"gate:{GATE} was not decided and is not named in the gaps" in C.verify(dropped, body, gates=[GATE])
