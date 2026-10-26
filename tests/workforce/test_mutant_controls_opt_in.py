# SPDX-License-Identifier: Apache-2.0
"""Regression test for audit 010 WF-006: seeded-mutant controls are not on the default coding path.

The archive ran up to 12 whole-suite runs with a defect seeded into the repository's code on every certified coding
mission, contrary to the ECHA default that mutation testing is opt-in and targeted. Under the library's honest
certificate policy (the default) a killed control cannot lead to support (the frozen tests are ``partial_falsifier``)
nor to a refutation (a code change has no known-good reference), so the runs would buy nothing: they happen only when
the owner's override (``G6_WORKFORCE_CERTIFICATE_POLICY=founder-d2``) makes a repository test able to support.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from g6.workforce import api, coding_certificate, service

POLICY = "G6_WORKFORCE_CERTIFICATE_POLICY"
BEFORE = 'def accepts(value):\n    return value >= 3\n\n\ndef label():\n    return "wrong"\n'


def _commit(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.parametrize(("policy", "seeded"), [(None, False), ("honest", False), ("founder-d2", True)])
def test_wf006_no_mutant_run_without_the_explicit_setting(tmp_path, monkeypatch, policy, seeded):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv(service.ENV_CERTIFICATES, raising=False)
    monkeypatch.delenv(POLICY, raising=False)
    if policy is not None:
        monkeypatch.setenv(POLICY, policy)
    runs, real = [], coding_certificate._run_mutant
    monkeypatch.setattr(coding_certificate, "_run_mutant", lambda *a: runs.append(a) or real(*a))
    wf = api.bind()
    _commit(api.repository_dir(wf) / "sample", {  # the boundary test passes on the baseline: a mutant candidate
        "policy.py": BEFORE,
        "tests/test_boundary.py": ("from policy import accepts\n\ndef test_boundary():\n"
                                   "    assert accepts(3) is True\n    assert accepts(2) is False\n"),
        "tests/test_objective.py": 'from policy import label\n\ndef test_label():\n    assert label() == "ready"\n'})
    api.allow_execution(wf, "sample", lambda lines: "mutant-test approver")  # WF-007
    made = api.create_coding_mission(wf, "Fix the label beside the acceptance rule and preserve its behavior.",
                                     "sample", module="policy.py", function="label", risk="R0")
    api.confirm(wf, made["mission_id"], lambda lines: "mutant-test approver")
    seeded_events = [e for e in wf.missions.events(made["mission_id"], kind="control")
                     if e["detail"].get("target") == "mutant"]
    assert bool(runs) is seeded and bool(seeded_events) is seeded
