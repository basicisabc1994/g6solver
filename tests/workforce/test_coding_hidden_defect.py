# SPDX-License-Identifier: Apache-2.0
"""Hidden regressions are found through the real workforce entry, then repaired."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from g6.workforce import acceptance, coding, service

_spec = importlib.util.spec_from_file_location("objective_fixture", Path(__file__).with_name("test_coding_objective.py"))
_rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rig)
AFTER, BEFORE, OBJECTIVE = _rig.AFTER, _rig.BEFORE, _rig.OBJECTIVE
_status, objective_rig = _rig._status, _rig.objective_rig

BAD = AFTER.replace(">= 3", "> 3")
PROBE = "check-regression"


@pytest.fixture
def hidden_rig(objective_rig):
    rig = objective_rig
    (rig.repo / "tests/test_boundary.py").unlink()
    (rig.repo / "tests/test_policy.py").write_text(
        "from policy import accepts\n\ndef test_policy():\n"
        "    assert accepts(4) is True\n    assert accepts(1) is False\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=rig.repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-m", "obvious checks only"], cwd=rig.repo, check=True, capture_output=True)
    return rig


def _start(rig):
    found = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label"}))
    assert found["ok"], found
    rig.human(found["mission_id"])
    return found["mission_id"]


def _submit(rig, mid, text):
    found = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                     artifact_json=json.dumps({"files": {"policy.py": text}}))
    assert found["ok"], found
    return _status(rig, mid)


def _probe(found, index=-1):
    checks = [r for r in found["coding"]["checks"] if r["check"] == PROBE]
    assert checks, "the differential probe must run through the workforce door"
    return checks[index]


def _counterexample(found):
    check = _probe(found)
    assert check["result"] == "fail" and check["executed"]
    assert check["probe"]["status"] == "fail"
    assert check["probe"]["counterexamples"] == [
        {"module": "policy.py", "function": "accepts", "input": [3],
         "baseline_output": True, "candidate_output": False}]
    return check


def test_hidden_boundary_defect_is_found_repaired_and_rechecked(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    first = _submit(rig, mid, BAD)
    ordinary = [r for r in first["coding"]["checks"] if r["check"] == "check-patch"]
    assert ordinary and ordinary[0]["measured"] == {"passed": 2, "total": 2}
    rejected = _counterexample(first)
    for function in rejected["probe"]["functions"]:
        assert function["inputs"] == [2, 3, 4]
        for side in ("baseline", "candidate"):
            assert function[side]["artifact_sha"] == rejected["artifact_sha"]
            assert function[side]["executed"] and function[side]["returncode"] == 0
    assert first["status"] != "done" and first["awaiting_chat"]
    done = _submit(rig, mid, AFTER)
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert "Failure found and repaired" in json.dumps(done["result"])
    assert done["coding"]["checks"][:2] == first["coding"]["checks"]
    assert _probe(done)["artifact_sha"] != rejected["artifact_sha"]
    assert _probe(done)["probe"]["counterexamples"] == []
    assert _probe(done)["probe"]["status"] == "pass", "the discovered boundary must execute again after repair"
    assert _probe(done)["probe"]["functions"][0]["inputs"] == [2, 3, 4]
    for side in ("baseline", "candidate"):
        receipt = _probe(done)["probe"]["functions"][0][side]
        assert receipt["executed"] and receipt["artifact_sha"] == _probe(done)["artifact_sha"]
        assert receipt["execution_id"] != rejected["probe"]["functions"][0][side]["execution_id"]
    assert (rig.repo / "policy.py").read_text(encoding="utf-8") == BEFORE


def test_counterexample_reaches_pull_worker_feedback(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    first = _submit(rig, mid, BAD)
    assert first["awaiting_chat"], "a counterexample must automatically resume the pull worker"
    feedback = [m for m in first["awaiting_chat"][0]["messages"] if m["from"] == PROBE]
    assert feedback, "the second brief must contain verifier feedback"
    check = _counterexample(first)
    assert feedback[0]["refs"]["run_id"] == check["run_id"]
    assert feedback[0]["refs"]["artifact_id"] == check["artifact_id"]
    for field in ('"function": "accepts"', '"input": [3]', '"baseline_output": true',
                  '"candidate_output": false'):
        assert field in feedback[0]["text"]


def test_new_patch_invalidates_every_candidate_dependent_check(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    first = _submit(rig, mid, BAD)
    assert first["awaiting_chat"], "failed candidate must reopen the producer and its checks"
    old = first["coding"]["checks"]
    done = _submit(rig, mid, AFTER)
    artifact = service.bind().ledger.last_artifact(mid, "patch")
    fresh = [r for r in done["coding"]["checks"] if r["artifact_id"] == artifact["artifact_id"]]
    assert {r["check"] for r in fresh} == {"check-patch", PROBE}
    assert len(fresh) == 2 and all(r["artifact_sha"] == artifact["sha256"] for r in fresh)
    assert {r["execution_id"] for r in fresh}.isdisjoint(r["execution_id"] for r in old)
    assert all(r["result"] == "pass" for r in fresh)
    assert all(v["artifact_sha"] == artifact["sha256"] for v in service.bind().ledger.verdicts(mid)
               if v["artifact_id"] == artifact["artifact_id"])


@pytest.mark.parametrize("repair", [BAD, BAD + "\n# still wrong\n"])
def test_non_improving_repair_stops_with_standard_tests_and_permissions_unchanged(hidden_rig, repair):
    rig = hidden_rig
    mid = _start(rig)
    frozen = _status(rig, mid)["acceptance"]
    first = _submit(rig, mid, BAD)
    assert first["awaiting_chat"], "first failure must allow a bounded repair"
    # Lead ruling R-B6-L1 (Build 6): the frozen ladder supersedes Brief 2's immediate stop. No progress moves the
    # repair to the focused strategy, still awaiting the chat, with the failing check's evidence named.
    focused = _submit(rig, mid, repair)
    assert focused["status"] != "done" and focused["awaiting_chat"], "no progress moves to the focused strategy"
    packet = [m for m in focused["awaiting_chat"][0]["messages"] if m["refs"].get("strategy") == "focused"]
    assert packet and packet[-1]["refs"]["focused"]["failing"] == [{"check": PROBE, "counterexamples": [
        {"module": "policy.py", "function": "accepts", "input": [3],
         "baseline_output": True, "candidate_output": False}]}]
    second = service.bind().ledger.last_artifact(mid, "patch")
    if repair == BAD:  # the byte-identical proposal was never judged again
        assert [c for c in focused["coding"]["checks"] if c["artifact_id"] == second["artifact_id"]] == []
        assert len(focused["coding"]["checks"]) == 2
    stopped = _submit(rig, mid, repair)  # the same proposal again, under the focused strategy: a person
    assert stopped["status"] == "done" and stopped["completion_state"] == "blocked-escalated"
    assert stopped["awaiting_chat"] == []
    assert stopped["coding"]["stop_reason"]
    assert stopped["acceptance"] == frozen
    shown = stopped["result"]["markdown"]  # the default answer (review I-1): the true cause, never a false one
    assert "repeats an earlier one" in shown and "found unavailable" not in shown and "G6_E_INTERNAL" not in shown
    final =service.bind().ledger.last_artifact(mid, "patch")
    assert len(service.bind().ledger.runs(mid, "patch")) == 3
    assert [c for c in stopped["coding"]["checks"] if c["artifact_id"] == final["artifact_id"]] == []
    assert len(stopped["coding"]["checks"]) == (2 if repair == BAD else 4)


def test_unrunnable_probe_is_not_reported_as_detection_or_as_pass(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    candidate = AFTER.replace("return value >= 3", "return str(value) >= '3'")
    done = _submit(rig, mid, candidate)
    check = _probe(done)
    assert check["probe"]["status"] == "not_run" and check["probe"]["reason"]
    assert check["probe"]["counterexamples"] == []
    assert not check["executed"] and check["result"] == "unmeasured"
    assert "Failure found and repaired" not in json.dumps(done["result"])
    assert len(service.bind().ledger.runs(mid, "patch")) == 1
    # Founder, 2026-10-08: an unmeasured advisory probe leaves the mission qualified-draft with the unchecked function
    # named; it never stops correct work and never confers "verified".
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert check["probe"]["unchecked"] == [{"module": "policy.py", "name": "accepts"}]
    assert "Not compared: accepts (policy.py)." in done["result"]["markdown"], "the person is told what nothing checked"


def test_in_scope_change_is_not_flagged_by_the_differential_probe(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    done = _submit(rig, mid, AFTER)
    check = _probe(done)
    assert check["probe"]["status"] == "not_applicable"
    assert check["probe"]["counterexamples"] == []
    assert done["completion_state"] == "qualified-draft"
    assert not done["awaiting_chat"] and len(service.bind().ledger.runs(mid, "patch")) == 1


def test_intake_does_not_depend_on_pending_copy(hidden_rig, monkeypatch):
    monkeypatch.setattr(coding, "OBJECTIVE_INSTRUCTIONS", "Production")
    monkeypatch.setattr(coding, "STANDARD_EXPLANATION", "Production")
    mid = _start(hidden_rig)
    assert _status(hidden_rig, mid)["awaiting_chat"], "copy must not govern deterministic intake"


def test_out_of_scope_rebinding_is_not_a_passing_probe(hidden_rig):
    mid = _start(hidden_rig)
    done = _submit(hidden_rig, mid, AFTER + "\naccepts = lambda value: value > 3\n")
    check = _probe(done)
    assert check["probe"]["status"] == "not_run", "unsupported rebinding must not pass as in-scope"
    assert check["result"] == "unmeasured" and not check["executed"]
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert check["probe"]["unchecked"] == [{"module": "policy.py", "name": "accepts"}]
    assert "Not compared: accepts (policy.py)." in done["result"]["markdown"]


def test_interrupted_send_back_resumes_from_closed_checks(hidden_rig, monkeypatch):
    from g6.workforce.runtime import Runtime

    rig = hidden_rig
    mid = _start(rig)
    original = Runtime._send_back

    def interrupted(self, *args):
        monkeypatch.setattr(Runtime, "_send_back", original)
        raise RuntimeError("synthetic interrupted send-back")

    monkeypatch.setattr(Runtime, "_send_back", interrupted)
    submitted = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                         artifact_json=json.dumps({"files": {"policy.py": BAD}}))
    assert not submitted["ok"]
    resumed = rig.call("mission_run", mission_id=mid)
    assert resumed["ok"] and resumed["awaiting_chat"], "closed checks must resume their interrupted feedback"
    first = _status(rig, mid)
    assert len(first["coding"]["checks"]) == 2
    assert len([m for m in first["awaiting_chat"][0]["messages"] if m["from"] == PROBE]) == 1
    done = _submit(rig, mid, AFTER)
    assert done["completion_state"] == "qualified-draft" and len(done["coding"]["checks"]) == 4


def test_prior_boundary_replay_cannot_pass_unsynthesizable_change(hidden_rig):
    mid = _start(hidden_rig)
    first = _submit(hidden_rig, mid, BAD)
    _counterexample(first)
    done = _submit(hidden_rig, mid, AFTER.replace("return value >= 3", "return str(value) >= '3'"))
    check = _probe(done)
    assert check["probe"]["status"] == "not_run", "old replay inputs cannot certify an unsupported new comparison"
    assert check["probe"]["counterexamples"] == [] and check["result"] == "unmeasured"
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert check["probe"]["unchecked"] == [{"module": "policy.py", "name": "accepts"}]
    assert "Failure found and repaired" not in json.dumps(done["result"])

def test_a_correct_patch_that_only_adds_a_function_is_not_refused_for_want_of_a_comparison(hidden_rig):
    # A function the change adds has no old behaviour to differ from: nothing to compare, nothing left unchecked.
    rig = hidden_rig
    mid = _start(rig)
    candidate = ('def accepts(value):\n    return value >= 3\n\n\ndef _word():\n    return "ready"\n\n\n'
                 'def label():\n    return _word()\n')
    done = _submit(rig, mid, candidate)
    check = _probe(done)
    assert check["probe"]["status"] == "not_applicable" and check["result"] == "pass"
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"


def test_a_changed_constant_is_named_unchecked_and_the_correct_patch_is_not_refused(hidden_rig):
    # Founder, 2026-10-08: changed imports, constants and classes are not compared either; they are named, not refused.
    rig = hidden_rig
    mid = _start(rig)
    candidate = 'LIMIT = 3\n\n\ndef accepts(value):\n    return value >= LIMIT\n\n\ndef label():\n    return "ready"\n'
    done = _submit(rig, mid, candidate)
    check = _probe(done)
    assert check["probe"]["status"] == "not_run" and check["result"] == "unmeasured" and not check["executed"]
    assert check["probe"]["unchecked"] == [{"module": "policy.py", "name": "LIMIT"},
                                           {"module": "policy.py", "name": "accepts"}]
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert not done["awaiting_chat"] and len(service.bind().ledger.runs(mid, "patch")) == 1
    assert "Not compared: LIMIT (policy.py), accepts (policy.py)." in done["result"]["markdown"]


def test_a_failing_test_is_still_sent_back_when_the_probe_could_not_compare(hidden_rig):
    # The probe only advises: it never holds a repair back. The label is wrong (the objective's test fails) and the
    # rule was rewritten in a way the probe cannot compare; the worker is told, repairs, and the result is draft.
    rig = hidden_rig
    mid = _start(rig)
    unmeasured = BEFORE.replace("return value >= 3", "return str(value) >= '3'")
    first = _submit(rig, mid, unmeasured)
    assert first["status"] != "done" and first["awaiting_chat"], "a failed test goes back to the worker"
    states = {c["check"]: c["result"] for c in first["coding"]["checks"]}
    assert states == {"check-patch": "fail", PROBE: "unmeasured"}
    done = _submit(rig, mid, AFTER.replace("return value >= 3", "return str(value) >= '3'"))
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert "Not compared: accepts (policy.py)." in done["result"]["markdown"]


def test_a_defect_is_still_caught_when_the_patch_also_adds_an_import(hidden_rig):
    # Review of e613fcd598, finding 1: the bail on a changed module used to discard every comparison.
    rig = hidden_rig
    mid = _start(rig)
    first = _submit(rig, mid, "import math\n\n\n" + BAD)
    check = _counterexample(first)
    assert check["probe"]["unchecked"] == [{"module": "policy.py", "name": "math"}]
    assert first["status"] != "done" and first["awaiting_chat"], "the counterexample goes back to the worker"
    done = _submit(rig, mid, "import math\n\n\n" + AFTER)
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft"
    assert "Not compared: math (policy.py)." in done["result"]["markdown"]


def test_a_candidate_that_does_not_return_where_the_baseline_did_still_stops_the_mission(hidden_rig):
    # Review of e613fcd598, finding 3: a comparison that began and could not finish is evidence, not "could not compare".
    rig = hidden_rig
    mid = _start(rig)
    raises = AFTER.replace("    return value >= 3\n", "    if value == 3:\n        raise ValueError('boom')\n    return value >= 3\n")
    done = _submit(rig, mid, raises)
    check = _probe(done)
    assert check["probe"]["status"] == "not_run" and check["result"] == "unmeasured"
    function, = check["probe"]["functions"]
    assert function["baseline"]["executed"] is True and function["candidate"]["executed"] is False
    assert done["status"] == "done" and done["completion_state"] == "blocked-escalated"


def _start_declaring(rig, helpers, tier="R0"):
    return rig.call("mission_create", objective=OBJECTIVE, risk_tier=tier, budget_cents=0,
                    repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label", "helpers": helpers}))


CHANGED_AS_ASKED = "A function declared as part of this change now behaves differently: accepts(3) gave True, now False."


def test_a_difference_in_a_helper_the_caller_declared_is_disclosed_and_not_sent_back(hidden_rig):
    # Founder, 2026-10-08: "this should be a disclosure", for a helper the objective asks to change (declared by the caller)
    rig = hidden_rig
    created = _start_declaring(rig, ["accepts"])
    assert created["ok"], created
    # review of S1+S2, finding 1: the owner reads what counts as done before confirming it, and it names what was declared
    shown = "\n".join(acceptance.describe(service.bind().missions.acceptance(created["mission_id"])))
    assert acceptance.DECLARED.format(names="accepts") in shown
    plain = "\n".join(acceptance.describe(service.bind().missions.acceptance(_start(rig))))
    assert acceptance.DECLARED.split("{")[0] not in plain, "control: nothing declared, nothing said"
    rig.human(created["mission_id"])
    done = _submit(rig, created["mission_id"], BAD)
    check = _probe(done)
    assert check["probe"]["status"] == "pass" and check["probe"]["counterexamples"] == []
    assert check["probe"]["disclosures"] == [{"module": "policy.py", "function": "accepts", "input": [3],
                                              "baseline_output": True, "candidate_output": False}]
    assert done["status"] == "done" and done["completion_state"] == "qualified-draft" and not done["awaiting_chat"]
    assert CHANGED_AS_ASKED in done["result"]["markdown"], "the person is told what changed"
    assert len(service.bind().ledger.runs(created["mission_id"], "patch")) == 1
    control = _submit(rig, _start(rig), BAD)  # the same change with nothing declared is the defect it always was
    assert control["awaiting_chat"] and _counterexample(control)


@pytest.mark.parametrize("helpers", [["no_such_function"], "accepts", [3], [["accepts"]], ["accepts"] * 9])
def test_a_declaration_that_names_nothing_real_refuses_the_mission(hidden_rig, helpers):
    created = _start_declaring(hidden_rig, helpers)
    assert created["ok"] is False, created


def test_a_declaration_that_is_not_a_list_of_names_is_refused_cleanly(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for helpers in ([["f"]], [{"f": 1}], "f", [3], ["f"] * (coding.MAX_HELPERS + 1)):
        with pytest.raises(ValueError, match=coding.REFUSAL):  # a refusal, never a crash on an unhashable name
            coding._declared(tmp_path, "m.py", "g", helpers)
    assert coding._declared(tmp_path, "m.py", "g", ["f", "f"]) == [{"module": "m.py", "function": "f"}]
    assert coding._declared(tmp_path, "m.py", "f", ["f"]) == [], "the named function is not a helper"
    assert coding._declared(tmp_path, "m.py", "g", None) == [] and coding._declared(tmp_path, "m.py", "g", []) == []


def _checks(found, name):
    return [r for r in found["coding"]["checks"] if r["check"] == name]


def test_a_declared_helper_cannot_be_changed_past_the_tests_the_task_requires(hidden_rig):
    """Adversarial test (founder, 2026-10-09): a declaration excuses a different answer in the differential probe, never a
    patch that fails a test the task requires. Same patch, declared or not, is sent back by the ordinary check."""
    rig = hidden_rig
    broken = AFTER.replace("    return value >= 3\n", "    return False\n")  # the repository's own test wants accepts(4) True
    results = {}
    for kind, created in (("declared", _start_declaring(rig, ["accepts"])), ("undeclared", None)):
        mid = created["mission_id"] if created else _start(rig)
        if created:
            assert created["ok"], created
            rig.human(mid)
        done = _submit(rig, mid, broken)
        assert done["status"] != "done", kind
        ordinary, = _checks(done, "check-patch")
        results[kind] = (ordinary["result"], ordinary["measured"])
    assert results["declared"] == results["undeclared"] and results["declared"][0] == "fail", results


def test_a_declaration_does_not_widen_what_a_patch_may_touch(hidden_rig):
    """Adversarial test (founder, 2026-10-09): declaring a helper does not let the patch edit a test. The bench refuses it
    and the mission does not complete as it would with the same change to the helper alone."""
    rig = hidden_rig
    created = _start_declaring(rig, ["accepts"])
    assert created["ok"], created
    rig.human(created["mission_id"])
    weaker = "from policy import accepts\n\ndef test_policy():\n    assert True\n"
    found = rig.call("mission_submit", mission_id=created["mission_id"], unit_key="patch",
                     artifact_json=json.dumps({"files": {"policy.py": BAD, "tests/test_policy.py": weaker}}))
    done = _status(rig, created["mission_id"])
    assert not (found["ok"] and done["status"] == "done" and done["completion_state"] in ("verified", "qualified-draft")), \
        (found, done["status"], done.get("completion_state"))
    alone = _submit(rig, _start_declaring_ok(rig), BAD)  # control: the helper change alone is accepted as a disclosure
    assert alone["status"] == "done" and alone["completion_state"] == "qualified-draft"
    assert (rig.repo / "tests/test_policy.py").read_text(encoding="utf-8") != weaker, "the owner's test is untouched"


def _start_declaring_ok(rig):
    created = _start_declaring(rig, ["accepts"])
    assert created["ok"], created
    rig.human(created["mission_id"])
    return created["mission_id"]


def test_a_person_approving_a_high_consequence_change_is_told_what_changed_as_asked(hidden_rig):
    rig = hidden_rig
    created = _start_declaring(rig, ["accepts"], tier="R3")
    assert created["ok"], created
    rig.human(created["mission_id"])
    waiting = _submit(rig, created["mission_id"], BAD)
    assert waiting["status"] != "done", waiting["status"]
    asked, = waiting["awaiting_human"]
    assert asked["unit"] == "approve-patch" and CHANGED_AS_ASKED in asked["question"]


def test_a_person_approving_a_high_consequence_change_is_told_what_was_not_compared(hidden_rig):
    # Review of e613fcd598, finding 5: the approval opens after the checks; it must say what no check compared.
    rig = hidden_rig
    asked = {}
    for label, text in (("plain", AFTER), ("unmeasured", AFTER.replace("return value >= 3", "return str(value) >= '3'"))):
        created = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R3", budget_cents=0,
                           repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label"}))
        assert created["ok"], created
        rig.human(created["mission_id"])
        waiting = _submit(rig, created["mission_id"], text)
        assert waiting["status"] != "done", waiting["status"]
        asked[label], = waiting["awaiting_human"]
        assert asked[label]["unit"] == "approve-patch"
    assert "Not compared: accepts (policy.py)." in asked["unmeasured"]["question"]
    assert asked["unmeasured"]["question"].startswith(asked["plain"]["question"])
    assert "Before you approve" not in asked["plain"]["question"], "control: a fully compared change adds nothing"


def test_an_advisory_check_that_could_not_decide_an_older_output_is_reopened_for_the_newer_one():
    # Review of e613fcd598, finding 4: only a check that had passed an older output was reopened when the patch changed.
    from types import SimpleNamespace

    from g6.workforce.runtime import Runtime

    reopened = []
    verdicts = [{"unit_key": "check-regression", "result": "unmeasured", "artifact_id": "a1"}]
    fake = SimpleNamespace(
        _graph=SimpleNamespace(units=lambda mid: [{"key": "patch", "status": "complete"},
                                                  {"key": "check-regression", "status": "complete"}],
                               reopen=lambda mid, key, why: reopened.append(key)),
        _ledger=SimpleNamespace(last_artifact=lambda mid, key: {"artifact_id": "a2"}, verdicts=lambda mid, key: verdicts),
        _missions=SimpleNamespace(check_lease=lambda lease: None),
        _advisory_keys=lambda plan: {"check-regression"})
    plan = SimpleNamespace(units=[SimpleNamespace(key="patch", kind="do", verifies=""),
                                  SimpleNamespace(key="check-regression", kind="verify", verifies="patch")])
    assert Runtime._stale(fake, "m1", plan, None) == ["re-opened check-regression: patch has a newer output"]
    assert reopened == ["check-regression"]
    verdicts.append({"unit_key": "check-regression", "result": "unmeasured", "artifact_id": "a2"})
    reopened.clear()  # control: it already answered the current output
    assert Runtime._stale(fake, "m1", plan, None) == [] and reopened == []


def test_an_unmeasured_comparison_never_earns_verified_and_is_recorded_as_a_caveat(hidden_rig):
    rig = hidden_rig
    mid = _start(rig)
    done = _submit(rig, mid, AFTER.replace("return value >= 3", "return str(value) >= '3'"))
    assert done["completion_state"] == "qualified-draft"
    states = {c["check"]: c["result"] for c in done["coding"]["checks"]}
    assert states == {"check-patch": "pass", PROBE: "unmeasured"}
