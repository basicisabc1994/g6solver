# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-001 and WF-002 (docs/transition/DECISIONS.md #22), through the library facade.

WF-001: the certificate labelled the caller's own repository tests ``independent_executable`` and lifted the runtime's
qualified-draft (the checks are not independent of the caller) to ``verified``: a caller who commits ``f(1) == 2`` and
then submits a patch that hard-codes ``2`` got a verified certificate. WF-002: the premortem's constructed replay check,
recorded as nonindependent, entered the certificate as ``independent_executable`` too.

The library's default (``G6_WORKFORCE_CERTIFICATE_POLICY`` unset, or ``honest``): both enter the issuer as
``partial_falsifier``, so they can refute a change and never support ``verified``. ``founder-d2`` is the owner's
override of #22: repository tests may then support ``verified`` under the issuer's A4 rule (the archive's ruling D2);
a constructed check never may, under any policy.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from g6.workforce import api, coding_certificate, service

DATA = Path(__file__).resolve().parent / "data" / "hic05-dev-v1"
POLICY = "G6_WORKFORCE_CERTIFICATE_POLICY"


def _commit(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def wf(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    for name in (POLICY, service.ENV_CERTIFICATES):
        monkeypatch.delenv(name, raising=False)  # the defaults: certificates on, the honest policy
    return api.bind()


def _gaps(cert: dict) -> dict[str, str]:
    return {subject: why for subject, why in cert["gaps"]}


def _hard_coded_mission(wf) -> tuple[str, dict]:
    """The caller commits ``f(1) == 2`` and submits a patch that hard-codes 2 (WF-001's own case)."""
    _commit(api.repository_dir(wf) / "calc", {
        "calc.py": "def f(x):\n    return x\n",
        "tests/test_calc.py": "from calc import f\n\n\ndef test_f():\n    assert f(1) == 2\n"})
    api.allow_execution(wf, "calc", lambda lines: "regression-test approver")  # WF-007
    made = api.create_coding_mission(wf, "Fix f so that its test passes.", "calc", module="calc.py", function="f",
                                     risk="R0")
    api.confirm(wf, made["mission_id"], lambda lines: "regression-test approver")
    done = api.submit_patch(wf, made["mission_id"], {"calc.py": "def f(x):\n    return 2\n"})
    return made["mission_id"], done


def test_wf001_caller_authored_tests_never_lift_a_mission_to_verified(wf):
    mid, done = _hard_coded_mission(wf)
    assert done["status"] == "done", done
    assert done["completion_state"] != "verified"
    cert = api.certificate(wf, mid)
    assert cert["state"] != "verified"
    node = "tests/test_calc.py::test_f"
    # the check passed on the patch's bytes, but its evidence kind is not A4: named as the gap
    assert any(r["check_id"] == node and r["result"] == "pass" for r in cert["receipts"])
    assert "evidence_kind" in _gaps(cert)[f"predicate:{node}"]
    assert not any(kind == "independent_executable" for _, _, kind in cert["rerun"])


def _hic_mission(wf) -> tuple[str, dict]:
    task = json.loads((DATA / "task.json").read_text(encoding="utf-8"))
    _commit(api.repository_dir(wf) / "inventory", {
        "inventory.py": (DATA / "inventory.py").read_text(encoding="utf-8"),
        "tests/test_inventory.py": (DATA / "tests/test_inventory.py").read_text(encoding="utf-8")})
    api.allow_execution(wf, "inventory", lambda lines: "regression-test approver")  # WF-007
    made = api.create_coding_mission(wf, task["objective"], "inventory", module=task["module"],
                                     function=task["function"], risk="R0")
    api.confirm(wf, made["mission_id"], lambda lines: "regression-test approver")
    bad = (DATA / "inventory.py").read_text(encoding="utf-8")
    good = bad.replace('if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:',
                       'if event["id"] not in result["seen_ids"]:')
    assert good != bad
    return made["mission_id"], api.submit_patch(wf, made["mission_id"], {"inventory.py": good})


@pytest.mark.parametrize("policy", [None, "honest", "founder-d2"])
def test_wf002_a_constructed_check_is_never_independent_evidence(wf, monkeypatch, policy):
    if policy is not None:
        monkeypatch.setenv(POLICY, policy)
    mid, done = _hic_mission(wf)
    assert done["status"] == "done", done
    standard = wf.missions.acceptance(mid).metadata["coding"]
    constructed = [c["nodeid"] for c in standard["harness_intelligence"]["checks"]]
    assert constructed
    cert = api.certificate(wf, mid)
    for node in constructed:
        assert "evidence_kind" in _gaps(cert)[f"predicate:{node}"]  # it passed, and is not A4 evidence
    assert not any(check in constructed for check, _, _ in cert["rerun"])
    pack = api.status(wf, mid, "full")["coding"]["harness_pack"]
    assert pack["assurance"]["independence"] == "nonindependent"
    assert cert["state"] != "verified" and done["completion_state"] != "verified"


def test_the_policy_is_one_named_setting_honest_by_default(monkeypatch):
    assert coding_certificate.ENV_POLICY == "G6_WORKFORCE_CERTIFICATE_POLICY"
    monkeypatch.delenv(coding_certificate.ENV_POLICY, raising=False)
    assert coding_certificate.policy() == coding_certificate.HONEST
    for value, found in (("honest", "honest"), ("founder-d2", "founder-d2"), (" FOUNDER-D2 ", "founder-d2"),
                         ("verified", "honest"), ("", "honest"), ("independent", "honest")):
        monkeypatch.setenv(coding_certificate.ENV_POLICY, value)
        assert coding_certificate.policy() == found  # anything else is the honest default (fails closed)


def test_under_the_owners_override_repository_tests_may_support_verified(wf, monkeypatch, confined_bench):
    """The override reproduces the archive's ruling D2 for repository tests only: a test that failed on the baseline
    (a killed negative control) and passes on the patch supports its predicate. This repository's only test failed on
    the baseline, so the patch had nothing to be compared with: that unchecked part keeps the mission below verified
    (as in the archive), while the certificate itself is verified."""
    monkeypatch.setenv(POLICY, "founder-d2")
    mid, done = _hard_coded_mission(wf)
    cert = api.certificate(wf, mid)
    assert cert["state"] == "verified" and done["completion_state"] == "qualified-draft"
    assert [kind for _, _, kind in cert["rerun"]] == ["independent_executable"]


def test_evidence_01_records_g6_did_not_write_to_its_ledger_never_support_verified(wf, monkeypatch, confined_bench):
    """The override's case above, with the controls and receipts handed to the issuer as typed records only: none of
    them is in the ledger, so nothing supports and the certificate is a qualified draft that names them."""
    # Removing g6.evidence's `elif missing := _unresolved(c, p, r, resolves):` branch makes this fail (verified).
    monkeypatch.setenv(POLICY, "founder-d2")
    monkeypatch.setattr(coding_certificate.certificates, "record", lambda ledger, *found: None)
    mid, done = _hard_coded_mission(wf)
    cert = api.certificate(wf, mid)
    assert cert["state"] == "qualified-draft" and done["completion_state"] == "qualified-draft"
    assert cert["unresolved"] and any("not_in_ledger" in why for _, why in cert["gaps"])
