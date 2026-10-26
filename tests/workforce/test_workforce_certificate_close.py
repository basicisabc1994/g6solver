# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A coding mission's certificate at close (Runtime Validator Constructor R6.3-R6.6, 2026-10-14).

With ``G6_WORKFORCE_CERTIFICATES`` on, G6 runs every frozen required test against the unpatched baseline after the
standard is frozen and before any patch exists (one ``control`` event per test: killed when the test failed there), each
test check's receipt names the hash of the frozen test file that ran, and the close issues the certificate from those
records. A verified certificate lifts the qualified-draft caps; anything else closes at the stricter of the two states,
and a blocked mission is never lifted. With the flag off nothing changes: no control, no receipt, no certificate.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from g6.core import confine
from g6.workforce import mutation_check as mc
from g6.evidence import certificate as C
from g6.workforce import coding_certificate as CC
from g6.workforce import service, truth
from g6.workforce.missions import EVENT_KINDS

_hic_spec = importlib.util.spec_from_file_location("certificate_hic_fixture",
                                                   Path(__file__).with_name("test_harness_intelligence.py"))
_hic = importlib.util.module_from_spec(_hic_spec)
_hic_spec.loader.exec_module(_hic)
_rig = _hic._fixture
objective_rig = _rig.objective_rig
bounded_development_checks = _hic.bounded_development_checks
AFTER, BEFORE, BOUNDARY, OBJECTIVE = _rig.AFTER, _rig.BEFORE, _rig.BOUNDARY, _rig.OBJECTIVE
LABEL = "tests/test_objective.py::test_label"
CONSTANT = "tests/test_constant.py::test_constant"
#: A change that passes the frozen test but breaks a boundary the regression probe finds (test_coding_hidden_defect).
BAD = AFTER.replace(">= 3", "> 3")
CLOSED_KEYS = {"status", "state", "acceptance", "actor"}


def test_the_control_event_is_added_to_the_closed_set_of_event_kinds():
    assert EVENT_KINDS == {
        "coding.draft_authorized", "coding.draft_returned", "coding.output_refused", "plan.generation",
        "mission.created", "mission.contract", "mission.status", "mission.closed", "plan.revision", "plan.reserved",
        "unit.materialized", "run.opened", "run.closed", "artifact", "verdict", "message", "approval.requested",
        "approval.decided", "wake", "lease.taken", "cost", "cost.reserved", "budget", "retry", "intervention",
        "unmet_demand", "schedule", "escalation", "preflight", "reconfiguration", "standard", "control", "formal"}


def _flag(monkeypatch, on: bool) -> None:
    """On: certificates on under the owner's ``founder-d2`` override, the archive's ruling D2 these tests pin. The
    library's default (``honest``, DECISIONS #22: repository tests never lift) is pinned in test_certificate_honesty."""
    if on:
        monkeypatch.setenv(service.ENV_CERTIFICATES, "1")
        monkeypatch.setenv(CC.ENV_POLICY, CC.FOUNDER_D2)
    else:  # S0 (2026-10-16): unset means on now, so off is said explicitly
        monkeypatch.setenv(service.ENV_CERTIFICATES, "0")


def _closed(wf, mid) -> dict:
    found = wf.missions.events(mid, kind="mission.closed")
    assert len(found) == 1, found
    return found[0]["detail"]


def _records(wf, mid) -> list[dict]:
    return [r for e in wf.missions.events(mid, kind="run.closed") for r in e["detail"].get("verification") or []]


def _statement(cert: dict, predicate: str) -> dict:
    return next(s for s in cert["predicates"] if s["predicate"] == predicate)


def _hic_repaired(rig) -> tuple[str, dict]:
    """HIC-05: the constructed replay check fails on the baseline, the repository's own tests pass there."""
    mid = _hic._created(rig)["mission_id"]
    rig.human(mid)
    bad = (_hic.POPULATION / "inventory.py").read_text(encoding="utf-8")
    good = bad.replace('if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:',
                       'if event["id"] not in result["seen_ids"]:')
    assert good != bad
    done = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                    artifact_json=json.dumps({"files": {"inventory.py": good}}))
    assert done["ok"] and done["status"] == "done", done
    return mid, done


def test_flag_off_an_hic_mission_closes_exactly_as_before(objective_rig, monkeypatch):
    _flag(monkeypatch, False)
    mid, done = _hic_repaired(objective_rig)
    wf = service.bind()
    assert wf.runtime.certificates is False
    assert done["completion_state"] == "qualified-draft"
    closed = _closed(wf, mid)
    assert set(closed) == CLOSED_KEYS and closed["state"] == "qualified-draft"
    assert wf.missions.events(mid, kind="control") == []
    assert _records(wf, mid) and not any("receipts" in r for r in _records(wf, mid))
    pack = objective_rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    assert "certificate" not in pack["assurance"]


def test_flag_on_hic05_supports_every_seen_to_fail_test_and_never_the_constructed_check(objective_rig, monkeypatch,
                                                                                         confined_bench):
    """R6.3 found HIC-05 qualified-draft: the repository's own tests pass on the baseline, so the baseline is no control
    for them. R6.7: each of them failed on a defect G6 seeded into the code, so each supports its predicate. Audit 010
    WF-002: the premortem's constructed check is task-derived, so it never supports, under any policy, and the change
    stays qualified-draft (the archive closed it verified)."""
    _flag(monkeypatch, True)
    mid, done = _hic_repaired(objective_rig)
    wf = service.bind()
    standard = wf.missions.acceptance(mid).metadata["coding"]
    node = standard["harness_intelligence"]["checks"][0]["nodeid"]
    original = standard["original_required_tests"]
    baseline = {e["detail"]["check_id"]: e["detail"] for e in wf.missions.events(mid, kind="control")
                if e["detail"]["target"] == "baseline"}
    assert set(baseline) == set(standard["required_tests"])
    assert baseline[node]["killed"] is True and baseline[node]["generated_by"] == "g6"
    assert baseline[node]["check_sha"] == standard["test_digests"][node.split("::")[0]]
    passed = [n for n in original if baseline[n]["executed"] and baseline[n]["killed"] is False]
    assert "tests/test_inventory.py::test_inputs_unchanged" in passed
    seeded = {d["check_id"]: d for d in _seeded(wf, mid)}
    assert set(seeded) == set(passed)  # each one caught a defect G6 seeded; none was left a gap
    assert seeded["tests/test_inventory.py::test_inputs_unchanged"]["mutant"]["file"] == "inventory.py"
    cert = _closed(wf, mid)["certificate"]
    assert cert["standard_sha"] == wf.missions.acceptance(mid).sha256
    assert cert["artifact_sha"] == wf.ledger.last_artifact(mid, "patch")["sha256"]
    assert not _statement(cert, node)["support"]
    assert "evidence_kind" in dict(map(tuple, cert["gaps"]))[f"predicate:{node}"]
    assert all(_statement(cert, test)["supported_by"] == [test] for test in original)
    assert cert["state"] == "qualified-draft"
    assert done["completion_state"] == _closed(wf, mid)["state"] == "qualified-draft"
    receipts = [r for r in cert["receipts"]]
    assert {r["check_id"] for r in receipts} == set(standard["required_tests"])
    assert all(r["check_sha"] == standard["test_digests"][r["check_id"].split("::")[0]] for r in receipts)
    pack = objective_rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    assert pack["assurance"]["certificate"] == cert


def test_flag_on_controls_are_recorded_after_the_freeze_and_before_any_artifact(objective_rig, monkeypatch):
    _flag(monkeypatch, True)
    mid, _ = _hic_repaired(objective_rig)
    wf = service.bind()
    events = wf.missions.events(mid)
    sha = wf.missions.acceptance(mid).sha256
    frozen = max(i for i, e in enumerate(events) if e["kind"] == "standard" and e["detail"]["acceptance_sha256"] == sha)
    controls = [i for i, e in enumerate(events) if e["kind"] == "control"]
    first_artifact = min(i for i, e in enumerate(events) if e["kind"] == "artifact")
    assert controls and frozen < min(controls) and max(controls) < first_artifact
    assert events[frozen]["seq"] < min(events[i]["seq"] for i in controls)
    assert max(events[i]["seq"] for i in controls) < events[first_artifact]["seq"]
    assert all(events[i]["detail"]["generated_by"] == "g6" and events[i]["detail"]["actor"] == "code" for i in controls)
    assert all(events[i]["detail"]["acceptance_sha256"] == sha for i in controls)


@pytest.fixture
def discriminating_rig(objective_rig):
    """Every frozen required test fails on the baseline; the boundary stays covered by the whole-suite comparison
    (``policy_test.py`` is collected by pytest but is not one of the frozen tests)."""
    rig = objective_rig
    (rig.repo / "tests/test_boundary.py").unlink()
    (rig.repo / "tests/policy_test.py").write_text(
        "from policy import accepts\n\ndef test_policy():\n"
        "    assert accepts(4) is True\n    assert accepts(1) is False\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=rig.repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-m", "one frozen test"], cwd=rig.repo, check=True, capture_output=True)
    return rig


def _start(rig) -> str:
    found = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label"}))
    assert found["ok"], found
    rig.human(found["mission_id"])
    return found["mission_id"]


def _submit(rig, mid, text) -> dict:
    found = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                     artifact_json=json.dumps({"files": {"policy.py": text}}))
    assert found["ok"], found
    return rig.call("mission_status", mission_id=mid, detail="full")


def test_a_certificate_never_lifts_a_change_whose_tests_ran_unconfined(discriminating_rig, monkeypatch):
    """The same correct patch, every test killed on the baseline, under founder-d2: but the bench's tests ran with the
    owner's permissions (no operating-system isolation), so the certificate cannot support it and nothing lifts it."""
    _flag(monkeypatch, True)
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    mid = _start(discriminating_rig)
    wf = service.bind()
    done = _submit(discriminating_rig, mid, AFTER)
    closed = _closed(wf, mid)
    # fails if coding_certificate.certify stops counting a check whose receipts ran unconfined as a partial falsifier
    assert closed["certificate"]["state"] == "qualified-draft", closed["certificate"]
    assert done["completion_state"] == closed["state"] == "qualified-draft"
    assert "switched off by this test" in json.dumps(done)

def test_a_certificate_never_supports_a_test_whose_controls_ran_unconfined(discriminating_rig, monkeypatch,
                                                                            confined_bench):
    """The receipts run confined, but the controls that showed each test can fail ran with the owner's permissions:
    the certificate cannot support the change on them."""
    _flag(monkeypatch, True)
    real = confine._select
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    mid = _start(discriminating_rig)   # the controls run here
    monkeypatch.setattr(confine, "_select", real)
    wf = service.bind()
    done = _submit(discriminating_rig, mid, AFTER)
    closed = _closed(wf, mid)
    # fails if coding_certificate.certify counts a check as independent when its controls ran unconfined
    assert closed["certificate"]["state"] == "qualified-draft", closed["certificate"]
    assert done["completion_state"] == closed["state"] == "qualified-draft"

@pytest.mark.parametrize("on", [False, True])
def test_a_correct_patch_whose_every_test_was_killed_on_the_baseline_closes_verified_only_with_the_flag(
        discriminating_rig, monkeypatch, on, request):
    _flag(monkeypatch, on)
    if on:  # a certificate supports only tests that ran confined by the operating system
        request.getfixturevalue("confined_bench")
    rig = discriminating_rig
    mid = _start(rig)
    wf = service.bind()
    assert wf.missions.acceptance(mid).metadata["coding"]["required_tests"] == [LABEL]
    done = _submit(rig, mid, AFTER)
    assert done["status"] == "done", done
    closed = _closed(wf, mid)
    if not on:
        assert done["completion_state"] == closed["state"] == "qualified-draft"
        assert set(closed) == CLOSED_KEYS
        return
    cert = closed["certificate"]
    assert cert["state"] == "verified" and cert["gaps"] == []
    assert _statement(cert, LABEL)["supported_by"] == [LABEL]
    assert done["completion_state"] == closed["state"] == "verified"
    latest = wf.ledger.last_artifact(mid, "patch")
    body = json.dumps(latest["content"], sort_keys=True, default=str).encode("utf-8")
    standard = wf.missions.acceptance(mid)
    assert C.verify(cert, body, standard_sha=standard.sha256, standard_predicates=[LABEL], predicates=[LABEL]) == []


#: The demo repository's own acceptance rule as a formal standard (as test_formal_r3_control.py states it).
ACCEPTS = {"requirement": {"inputs": ["(declare-const value Int)"], "signatures": ["(declare-fun accepts (Int) Bool)"],
                           "sorts": [], "property": "(= (accepts value) (>= value 3))", "assumptions": [],
                           "entry_points": [["accepts", "policy:accepts"]], "input_types": [["value", {"t": "int"}]]},
           "model": {"text": "(define-fun accepts ((value Int)) Bool (>= value 3))"},
           "adapter": "import policy\n\ndef observe(entry, inputs):\n    return policy.accepts(**inputs)\n",
           "explanation": "accepts says yes exactly when value is at least 3."}




@pytest.mark.parametrize("on", [False, True])
def test_a_repeated_defective_patch_is_never_verified_and_never_weaker_than_without_the_flag(
        discriminating_rig, monkeypatch, on):
    _flag(monkeypatch, on)
    rig = discriminating_rig
    mid = _start(rig)
    first = _submit(rig, mid, BAD)
    assert first["status"] != "done" and first["awaiting_chat"], first
    _submit(rig, mid, BAD)
    stopped = _submit(rig, mid, BAD)
    # pinned without the flag by test_coding_hidden_defect: the repeated defective proposal stops for a person
    assert stopped["status"] == "done" and stopped["completion_state"] == "blocked-escalated", stopped
    wf = service.bind()
    closed = _closed(wf, mid)
    assert closed["state"] == "blocked-escalated"
    if not on:
        assert set(closed) == CLOSED_KEYS
        return
    cert = closed["certificate"]  # the repeat was never judged again: no receipt on its bytes
    assert cert["artifact_sha"] == wf.ledger.last_artifact(mid, "patch")["sha256"]
    assert cert["state"] == "qualified-draft" and dict(cert["gaps"])[f"predicate:{LABEL}"] == "no_receipt"


def _seeded(wf, mid) -> list[dict]:
    return [e["detail"] for e in wf.missions.events(mid, kind="control") if e["detail"]["target"] == "mutant"]


def _counting(monkeypatch) -> list:
    runs, real = [], CC._run_mutant
    monkeypatch.setattr(CC, "_run_mutant", lambda *a: runs.append(a) or real(*a))
    return runs


def test_flag_on_a_regression_test_a_seeded_defect_made_fail_supports_a_verified_change(objective_rig, monkeypatch,
                                                                                         confined_bench):
    """RVC R6.7: the boundary test passes on the unchanged code, so the baseline is no control for it; G6 seeds defects
    into the code and keeps the one that made it fail. The correct change then closes verified."""
    _flag(monkeypatch, True)
    mid = _start(objective_rig)
    wf = service.bind()
    standard = wf.missions.acceptance(mid).metadata["coding"]
    assert standard["required_tests"] == [BOUNDARY, LABEL]
    seeded = _seeded(wf, mid)
    assert [d["check_id"] for d in seeded] == [BOUNDARY]  # the label test already failed on the baseline
    assert seeded[0]["killed"] and seeded[0]["executed"] and seeded[0]["generated_by"] == "g6"
    assert seeded[0]["mutant"]["file"] == "policy.py" and seeded[0]["mutant"]["line"] >= 1
    assert seeded[0]["check_sha"] == standard["test_digests"]["tests/test_boundary.py"]
    done = _submit(objective_rig, mid, AFTER)
    closed = _closed(wf, mid)
    cert = closed["certificate"]
    assert cert["state"] == "verified" and cert["gaps"] == []
    assert _statement(cert, BOUNDARY)["supported_by"] == [BOUNDARY]
    assert done["completion_state"] == closed["state"] == "verified"


@pytest.fixture
def unexercised_rig(objective_rig):
    """A frozen test that runs none of the repository's code: no defect G6 can seed makes it fail."""
    rig = objective_rig
    (rig.repo / "tests/test_constant.py").write_text("def test_constant():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=rig.repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-m", "a test of nothing"], cwd=rig.repo, check=True, capture_output=True)
    return rig


@pytest.mark.parametrize("cap", [None, 2])
def test_flag_on_a_test_no_seeded_defect_made_fail_stays_a_named_gap_and_the_runs_are_capped(
        unexercised_rig, monkeypatch, cap, request):
    _flag(monkeypatch, True)
    if cap is None:  # the boundary test is supported only when its tests ran confined by the operating system
        request.getfixturevalue("confined_bench")
    if cap is not None:
        monkeypatch.setattr(CC, "MAX_CONTROL_MUTANTS", cap)
    runs = _counting(monkeypatch)
    rig = unexercised_rig
    mid = _start(rig)
    wf = service.bind()
    assert CONSTANT in wf.missions.acceptance(mid).metadata["coding"]["required_tests"]
    available = len(mc.mutants(BEFORE, CC.MAX_CONTROL_MUTANTS, calls=True))
    assert len(runs) == (available if cap is None else cap)  # every seeded defect, up to the cap; never more
    assert CONSTANT not in {d["check_id"] for d in _seeded(wf, mid)}
    if cap is not None:
        return
    done = _submit(rig, mid, AFTER)
    cert = _closed(wf, mid)["certificate"]
    assert "control_not_killed" in dict(cert["gaps"])[f"predicate:{CONSTANT}"]
    assert _statement(cert, BOUNDARY)["supported_by"] == [BOUNDARY]
    assert cert["state"] == "qualified-draft" and done["completion_state"] == "qualified-draft"


def test_flag_on_no_seeded_control_is_kept_when_the_frozen_snapshot_changed_while_they_ran(objective_rig, monkeypatch):
    _flag(monkeypatch, True)
    real = CC._run_mutant

    def tampering(snapshot, relative, text):
        (snapshot / "stray.txt").write_text("written during a seeded run", encoding="utf-8")
        return real(snapshot, relative, text)

    monkeypatch.setattr(CC, "_run_mutant", tampering)
    found = objective_rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                               repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label"}))
    assert found["ok"], found
    with contextlib.suppress(Exception):  # what the changed snapshot does next is the mission's own refusal, not this test's
        objective_rig.human(found["mission_id"])
    assert _seeded(service.bind(), found["mission_id"]) == []


TOTAL = "tests/test_total.py::test_total"
TOTALS = "def fill(box, xs):\n    box.extend(sorted(xs))\n"


def _commit(rig, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=rig.repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-m", message], cwd=rig.repo, check=True, capture_output=True)


def test_flag_on_a_test_only_a_dropped_call_breaks_is_caught_by_the_call_change(objective_rig, monkeypatch):
    """``totals.fill`` has no operator, number or return to change: dropping a call is the only seedable defect its
    test can see, so the test is caught only because the seeded defects include that change."""
    _flag(monkeypatch, True)
    rig = objective_rig
    (rig.repo / "totals.py").write_text(TOTALS, encoding="utf-8")
    (rig.repo / "tests/test_total.py").write_text(
        "from totals import fill\n\ndef test_total():\n    box = []\n    fill(box, [2, 1])\n    assert box == [1, 2]\n", encoding="utf-8")
    _commit(rig, "a total")
    assert mc.mutants(TOTALS, CC.MAX_CONTROL_MUTANTS) == [] and mc.mutants(TOTALS, 12, calls=True)
    mid = _start(rig)
    seeded = {d["check_id"]: d for d in _seeded(service.bind(), mid)}
    assert seeded[TOTAL]["mutant"]["file"] == "totals.py"


def test_flag_on_the_run_cap_holds_when_the_seeded_defects_spill_into_a_second_file(unexercised_rig, monkeypatch):
    """Each file's defects are already limited to the cap, so the cap is only tested once the runs cross a file."""
    _flag(monkeypatch, True)
    rig = unexercised_rig
    (rig.repo / "spare.py").write_text("def spare(x):\n    return x * 2 + 1\n", encoding="utf-8")
    _commit(rig, "a second module")
    first = len(mc.mutants(BEFORE, CC.MAX_CONTROL_MUTANTS, calls=True))
    assert first < CC.MAX_CONTROL_MUTANTS
    monkeypatch.setattr(CC, "MAX_CONTROL_MUTANTS", first + 1)  # every defect in the permitted module, then one more
    runs = _counting(monkeypatch)
    _start(rig)
    assert len(runs) == first + 1
    assert [Path(r[1]).name for r in runs][-2:] == ["policy.py", "spare.py"]


def test_flag_on_a_freeze_stopped_before_its_seeded_runs_seeds_them_when_resumed(objective_rig, monkeypatch):
    """The baseline controls are recorded and the process stops before any defect is seeded: running the controls
    again seeds them, although no baseline control is left to record."""
    _flag(monkeypatch, True)
    real = CC._seeded_controls
    monkeypatch.setattr(CC, "_seeded_controls", lambda *a: None)
    mid = _start(objective_rig)
    wf = service.bind()
    assert _seeded(wf, mid) == [] and len(wf.missions.events(mid, kind="control")) == 2
    monkeypatch.setattr(CC, "_seeded_controls", real)
    CC.record_controls(wf, mid, wf.missions.acceptance(mid).metadata["coding"])
    assert [d["check_id"] for d in _seeded(wf, mid)] == [BOUNDARY]


def test_flag_on_a_second_pass_spends_no_runs_on_a_test_already_caught(objective_rig, monkeypatch):
    """A resumed freeze must not re-spend whole-suite runs on a test a seeded defect has already caught."""
    _flag(monkeypatch, True)
    mid = _start(objective_rig)
    wf = service.bind()
    assert [d["check_id"] for d in _seeded(wf, mid)] == [BOUNDARY]
    runs = _counting(monkeypatch)
    CC.record_controls(wf, mid, wf.missions.acceptance(mid).metadata["coding"])
    assert runs == []
    assert [d["check_id"] for d in _seeded(wf, mid)] == [BOUNDARY]


@pytest.mark.parametrize("on", [False, True])
def test_a_verified_certificate_never_lifts_a_mission_its_other_checks_blocked(discriminating_rig, monkeypatch, on,
                                                                              request):
    """Each proposal passes the frozen test but breaks a test the whole-suite comparison holds (and the boundary the
    regression probe finds): the certificate over the frozen test is verified, the mission stays blocked-escalated."""
    _flag(monkeypatch, on)
    if on:  # a certificate supports only tests that ran confined by the operating system
        request.getfixturevalue("confined_bench")
    rig = discriminating_rig
    mid = _start(rig)
    for bound in (5, 6, 7):
        found = _submit(rig, mid, AFTER.replace(">= 3", f">= {bound}"))
    assert found["status"] == "done" and found["completion_state"] == "blocked-escalated", found
    wf = service.bind()
    closed = _closed(wf, mid)
    assert closed["state"] == "blocked-escalated"
    if not on:
        assert set(closed) == CLOSED_KEYS
        return
    cert = closed["certificate"]
    assert cert["artifact_sha"] == wf.ledger.last_artifact(mid, "patch")["sha256"]
    assert cert["state"] == "verified" and _statement(cert, LABEL)["supported_by"] == [LABEL]


@pytest.mark.parametrize("policy", [CC.FOUNDER_D2, CC.HONEST])
def test_a_verified_close_on_bench_receipts_names_the_recorder_gap_and_no_other_policy_changes(
        discriminating_rig, monkeypatch, policy, confined_bench):
    """WORKFORCE-01 (audit 2026-10-23; lead ruling: founder-d2 stands, its gap is never silent): the per-test pass
    record is written inside the process that runs the patch, so a patch aimed at the recorder could forge it. A
    founder-d2 change that closes verified on those receipts names that gap in its certificate and its reply; under the
    honest policy the same change closes as it did, and names nothing new."""
    _flag(monkeypatch, True)
    monkeypatch.setenv(CC.ENV_POLICY, policy)
    mid = _start(discriminating_rig)
    wf = service.bind()
    done = _submit(discriminating_rig, mid, AFTER)
    closed = _closed(wf, mid)
    if policy == CC.HONEST:   # as today: qualified-draft, no named gap anywhere
        assert done["completion_state"] == closed["state"] == "qualified-draft"
        assert "certificate_gaps" not in closed and truth.RECORDER_GAP not in json.dumps(done)
        return
    assert done["completion_state"] == closed["state"] == "verified" and closed["certificate"]["state"] == "verified"
    # fails if coding_certificate.decided stops keeping the named gap beside a verified certificate
    assert closed["certificate_gaps"] == [["receipts", truth.RECORDER_GAP]]
    # fails if coding_certificate.ruled lifts the outcome to verified without the gap among its reasons
    assert truth.RECORDER_GAP in done["outcome"]["reasons"]
    # fails if view.build stops showing it among the reply's caveats (what the default return carries)
    assert truth.RECORDER_GAP in done["result"]["markdown"], done["result"]["markdown"]
