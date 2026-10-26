# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The first proof's objectives through one runtime (workforce OS plan, V1), driven exactly as the owner's
chat drives them: the service functions behind the workforce tools.

Objective (b), "make the failing test pass": the plan is a PULL unit (the patch, written by the owner's
chat model and submitted to G6) and a verify unit whose oracle is the test bench. Pinned: G6 runs nothing
for a pull unit and says it is waiting for the chat; the submitted patch is a draft until the bench runs
the named tests on a copy with it applied; the mission closes ``verified`` only then, and the owner's
repository is never changed; a patch that breaks another test, or edits a test, closes the mission
blocked-escalated with the reason; a submission G6 is not waiting for is refused.

Objective (a), "which invoices are overdue": the same shape through the same runtime. The chat ages a
synthetic export (a pull unit), and the real z3 solver, reached through the real registry door, proves
every bucket and both totals against the export's own amounts and days (facts bound from the chat's
artifact). It closes ``verified`` only then. A plausible ageing with the right totals and a wrong bucket
fails its verdict. The accountant pack is refused by the broker, because its probe answers
blocked-escalated.

No code here is specific to either objective: the plan, the repository and the export are the only
inputs. Offline, temp dirs, real pytest subprocesses on a synthetic repository.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from g6.core import confine
from g6.workforce import store
from g6.workforce import bench, coding_certificate, contracts, truth
from g6.workforce import service as ws

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
# The synthetic repository (as in test_workforce_bench.py): add is wrong, sub is right, one test each.
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
BREAKS_SUB = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n"
TESTS = "from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n"
NODES = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"]


def make_repo(root, venture: str) -> None:
    repo = root / bench.REPOS / venture / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")

TEST_PATCH = {"key": "patch", "kind": "do", "goal": "Make tests/test_calc.py::test_add pass; edit no test",
              "output_schema": "files"}
TEST_CHECK = {"key": "check-patch", "kind": "verify", "verifies": "patch",
              "goal": "Every named test passes on a copy of the repository with the patch applied",
              "capabilities": [[bench.COMPONENT, "run_tests"]],
              "oracle": {"capability": [bench.COMPONENT, "run_tests"], "params": {"repo": "calc", "tests": NODES},
                         "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True},
                         "kind": "test"}}
FIX_A_TEST = {"units": [TEST_PATCH, TEST_CHECK], "edges": []}


class NoRegistry:
    """Objective (b) needs nothing from the component registry."""

    def list_all(self):
        return []


@pytest.fixture
def wf(tmp_path, monkeypatch):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    make_repo(tmp_path, venture)
    env = {ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: "alice"}
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    return ws.bind(env, registry=NoRegistry())


def _planned(wf, plan: dict = FIX_A_TEST) -> str:
    mid = ws.create(wf, objective="Make the failing add test pass", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid, {"goal": "tests/test_calc.py::test_add passes", "constraints": ["edit no test file"]})
    assert ws.probe(wf, bench.COMPONENT, "run_tests")["level"] == "probe_passed"
    assert ws.plan(wf, mid, plan)["status"] == "active"
    return mid


@pytest.mark.parametrize("suite, words", [
    ({"status": "not_run", "reason": "the test runner could not start"}, "could not"),
    ({"status": "pass", "compared": 1, "broken": 0, "tests": [], "not_collected": 2}, "2"),
])
def test_an_incomplete_suite_comparison_never_certifies_or_reuses_the_patch(wf, monkeypatch, suite, words):
    """AB-003: actual named-test passes cannot certify a missing full-suite comparison."""
    monkeypatch.setattr(bench, "_suite", lambda source, copy: suite)
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "qualified-draft"), done["outcome"]
    assert done["verdicts"] == [{"unit": "check-patch", "verifies": "patch", "result": "pass",
                                  "state": "qualified-draft"}]
    assert words in " ".join(done["outcome"]["reasons"])
    receipt, = [record for event in wf.missions.events(mid, kind="run.closed")
                for record in event["detail"].get("verification", [])]
    assert receipt["executed"] is True and receipt["suite"] == suite
    assert receipt["outcome"]["state"] == "qualified-draft"
    artifact = wf.ledger.last_artifact(mid, "patch")
    assert artifact["content"] == {"files": {"calc.py": FIXED}}
    # A new process reads the same verdict and refuses it as a verified premise.
    reopened = ws.bind({ws.ENV_DB_DIR: wf.db_dir, ws.ENV_VENTURE: wf.venture_id, ws.ENV_OWNER: "alice"},
                       registry=NoRegistry())
    assert ws.status(reopened, mid)["completion_state"] == "qualified-draft"
    assert reopened.runtime.reusable(artifact["artifact_id"], "R1") is not None


def _bench_check(key: str, tests: list[str], goal: str) -> dict:
    return {**TEST_CHECK, "key": key, "goal": goal,
            "oracle": {**TEST_CHECK["oracle"], "params": {"repo": "calc", "tests": tests}}}


@contextmanager
def _legacy_suite_rules(monkeypatch, suite):
    """Write real pre-repair receipts without editing a closed row or weakening SQLite guards."""
    original = bench.run

    def legacy_reply(*args, **kwargs):
        reply = original(*args, **kwargs)
        return {key: value for key, value in reply.items() if key not in ("degraded", "degradation_reason")}

    with monkeypatch.context() as old:
        old.setattr(bench, "_suite", lambda source, copy: suite)
        old.setattr(bench, "run", legacy_reply)
        old.setattr(contracts, "suite_limits", lambda suite: (), raising=False)
        yield


def _stored_evidence(wf):
    return {table: [tuple(row) for row in wf.ledger._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("missions", "task_runs", "verdicts", "evidence")}


@pytest.mark.parametrize("suite, words", [
    ({"status": "not_run", "reason": "the test runner could not start"}, "could not be compared"),
    ({"status": "pass", "compared": 1, "broken": 0, "tests": [], "not_collected": 2}, "2 test files"),
])
def test_historical_incomplete_suite_evidence_is_qualified_without_rewriting_history(wf, monkeypatch, suite, words,
                                                                                   confined_bench):
    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)  # history recorded as verified
    with _legacy_suite_rules(monkeypatch, suite):
        mid = _planned(wf)
        ws.run(wf, mid)
        ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
        assert ws.run(wf, mid)["completion_state"] == "verified"
        artifact = wf.ledger.last_artifact(mid, "patch")
        assert wf.runtime.reusable(artifact["artifact_id"], "R1") is None
    other_venture = wf.missions._vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    before = _stored_evidence(wf)
    reopened = ws.bind({ws.ENV_DB_DIR: wf.db_dir, ws.ENV_VENTURE: wf.venture_id, ws.ENV_OWNER: "alice"},
                       registry=NoRegistry())
    done = ws.status(reopened, mid)
    assert (done["status"], done["completion_state"]) == ("done", "qualified-draft")
    assert done["result"]["state"] == "qualified-draft"
    assert done["result"]["confidence"]["evidence_coverage"] == "Partial"
    assert words in str(done["result"]) and words in " ".join(done["outcome"]["reasons"])
    assert done["verdicts"][0]["state"] == "qualified-draft"
    assert done["runs"][-1]["completion_state"] == "qualified-draft"
    assert reopened.missions.list(wf.venture_id)[0]["completion_state"] == "qualified-draft"
    assert reopened.runtime.roll_up(mid).state == "qualified-draft"
    assert "no independent check verified it" in reopened.runtime.reusable(artifact["artifact_id"], "R1")
    stranger = ws.bind({ws.ENV_DB_DIR: wf.db_dir, ws.ENV_VENTURE: other_venture, ws.ENV_OWNER: "bob"},
                       registry=NoRegistry())
    with pytest.raises(LookupError, match=store.NOT_FOUND):
        ws.status(stranger, mid)
    with pytest.raises(LookupError, match=store.NOT_FOUND):
        stranger.missions.events(mid, kind="run.closed")
    assert _stored_evidence(wf) == before  # including original verified labels and raw receipts


def test_historical_incomplete_suite_failure_stays_blocked(wf, monkeypatch):
    with _legacy_suite_rules(monkeypatch, {"status": "not_run"}):
        mid = _planned(wf)
        ws.run(wf, mid)
        ws.submit(wf, mid, "patch", {"files": {"calc.py": BREAKS_SUB}})
        assert ws.run(wf, mid)["completion_state"] == "blocked-escalated"
    done = ws.status(wf, mid)
    assert done["completion_state"] == "blocked-escalated"
    assert done["verdicts"][0]["state"] == "blocked-escalated"
    assert "could not be compared" in str(done["result"])


def test_historical_incomplete_suite_on_an_older_patch_does_not_taint_the_replacement(wf, monkeypatch, confined_bench):
    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)  # the replacement can verify
    with _legacy_suite_rules(monkeypatch, {"status": "not_run"}):
        mid = _planned(wf, {"units": [{**TEST_PATCH, "max_attempts": 2}, TEST_CHECK], "edges": []})
        ws.run(wf, mid)
        ws.submit(wf, mid, "patch", {"files": {"calc.py": BREAKS_SUB}})
        assert ws.run(wf, mid)["status"] == "waiting"
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert done["completion_state"] == "verified"
    assert [v["state"] for v in done["verdicts"]] == ["blocked-escalated", "verified"]
    assert "could not be compared" not in str(done["outcome"]) + str(done["result"])
    latest = wf.ledger.last_artifact(mid, "patch")
    assert wf.runtime.reusable(latest["artifact_id"], "R1") is None


#: A plan whose own check runs only the named test (as a hasty plan might).
NARROW = {"units": [TEST_PATCH, _bench_check("check-patch", NODES[:1], "The named test passes")], "edges": []}
#: The extra verifier the chat adds on its own unit (V4a): the rest of the suite, so nothing else regresses.
NO_REGRESSION = _bench_check("check-no-regression", NODES[1:], "No other test regresses")


@pytest.mark.parametrize(("patched", "state", "results"), [
    (FIXED, "qualified-draft", {"check-patch": "pass", "check-no-regression": "pass"}),
    (BREAKS_SUB, "blocked-escalated", {"check-patch": "pass", "check-no-regression": "fail"}),
])
def test_an_extra_verifier_the_chat_delegates_catches_a_regression_the_plans_check_missed(wf, patched, state,
                                                                                         results, monkeypatch):
    # The plan's check "misses" the regression only while the bench cannot compare the patch with the whole suite (ECHA
    # Stage A: with the comparison the bench catches it itself, pinned by the next test). The comparison is made
    # unavailable here so the delegation and the adjudication of disagreeing checks (V4a, V4c) are still what is tested.
    monkeypatch.setattr(bench, "_suite", lambda source, copy: {"status": "not_run", "reason": "unavailable in this test"})
    mid = _planned(wf, NARROW)
    ws.run(wf, mid)
    delegated = ws.delegate(wf, mid, "patch", [NO_REGRESSION])
    assert delegated["tree"] == {"patch": ["check-no-regression"]}
    ws.submit(wf, mid, "patch", {"files": {"calc.py": patched}})
    done = ws.run(wf, mid)
    if state == "blocked-escalated":  # disagreeing checks: a person adjudicates (V4c) and upholds the failure
        assert done["status"] == "waiting" and [a["unit"] for a in done["awaiting_human"]] == ["check-no-regression"]
        wf.runtime.decide(mid, "check-no-regression", "uphold", reason="test_sub must keep passing", actor="human")
        done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", state), done["outcome"]
    assert {v["unit"]: v["result"] for v in done["verdicts"]} == results


def test_a_hasty_plans_own_check_now_catches_a_regression_in_a_test_it_did_not_name(wf):
    """ECHA Stage A: the bench compares the patch with the whole suite, so the narrow plan's check fails by itself on the
    patch that breaks ``test_sub``; no second verifier is needed to see it, and the mission closes blocked-escalated."""
    mid = _planned(wf, NARROW)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": BREAKS_SUB}})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "blocked-escalated"), done["outcome"]
    assert {v["unit"]: v["result"] for v in done["verdicts"]} == {"check-patch": "fail"}
    assert "tests/test_calc.py::test_sub" in " ".join(done["outcome"]["reasons"]) + str(done["verdicts"])
    # review I-5: the person is told what broke, not only that a check failed
    assert "no longer pass" in " ".join(done["outcome"]["reasons"]), done["outcome"]["reasons"]


def test_a_self_planned_mission_on_tests_nobody_declared_closes_qualified_draft_by_default(wf, monkeypatch):
    """DECISIONS #22: G6 cannot say who wrote the repository's tests a plan names, so by default their pass can refute
    the patch but never verify it; the reason says so."""
    monkeypatch.delenv(coding_certificate.ENV_POLICY, raising=False)
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "qualified-draft"), done["outcome"]
    assert done["verdicts"] == [{"unit": "check-patch", "verifies": "patch", "result": "pass",
                                  "state": "qualified-draft"}]
    assert contracts.UNDECLARED_TESTS in " ".join(done["outcome"]["reasons"]), done["outcome"]["reasons"]
    broken = _planned(wf)
    ws.run(wf, broken)
    ws.submit(wf, broken, "patch", {"files": {"calc.py": BREAKS_SUB}})
    assert ws.run(wf, broken)["completion_state"] == "blocked-escalated"   # they can still refute


def test_objective_b_closes_verified_only_after_the_bench_ran_the_submitted_patch(wf, tmp_path, monkeypatch,
                                                                                   confined_bench):
    # the bench's tests run confined by the operating system (g6.core.confine): an unconfined pass never verifies
    # founder-d2 (the owner's override of DECISIONS #22) counts the repository's tests as independent
    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)
    mid = _planned(wf)
    waiting = ws.run(wf, mid)
    assert (waiting["status"], waiting["completion_state"]) == ("waiting", "")  # for the chat's patch (V2b)
    (asked,) = waiting["awaiting_chat"]
    assert asked["unit"] == "patch" and asked["goal"] == TEST_PATCH["goal"] and asked["output_schema"] == "files"
    assert waiting["runs"] == []  # G6 ran nothing for a unit only the chat can write
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "verified"), done["outcome"]
    assert done["verdicts"] == [{"unit": "check-patch", "verifies": "patch", "result": "pass", "state": "verified"}]
    patch_run, check_run = done["runs"]
    assert (patch_run["unit"], patch_run["completion_state"]) == ("patch", "qualified-draft")
    assert patch_run["model_calls"] >= 1 and check_run["model_calls"] == 0  # the chat wrote it; code judged it
    assert (tmp_path / bench.REPOS / wf.venture_id / "calc" / "calc.py").read_text(encoding="utf-8") == BUGGY


@pytest.mark.parametrize("policy", [coding_certificate.FOUNDER_D2, ""])
def test_a_verified_bench_pass_names_the_recorder_gap_and_no_other_policy_changes(wf, monkeypatch, policy,
                                                                                  confined_bench):
    """WORKFORCE-01 (audit 2026-10-23; lead ruling: founder-d2 stands, its gap is never silent): the bench reads each
    test's pass from a record written inside the process that ran the patch. A self-planned mission that closes
    verified on it says so; under any other policy the mission closes as it did, with nothing new said."""
    if policy:
        monkeypatch.setenv(coding_certificate.ENV_POLICY, policy)
    else:
        monkeypatch.delenv(coding_certificate.ENV_POLICY, raising=False)
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    if not policy:   # as today: qualified-draft (DECISIONS #22), and the gap is not said where nothing verified
        assert done["completion_state"] == "qualified-draft" and truth.RECORDER_GAP not in json.dumps(done)
        return
    assert (done["status"], done["completion_state"]) == ("done", "verified"), done["outcome"]
    # fails if rungs.run_verify stops naming the recorder gap on a verified bench pass
    assert truth.RECORDER_GAP in done["outcome"]["reasons"], done["outcome"]["reasons"]
    # fails if view.build stops showing it among the reply's caveats
    assert truth.RECORDER_GAP in done["result"]["markdown"], done["result"]["markdown"]


def test_an_unconfined_bench_pass_closes_qualified_draft_and_says_why(wf, monkeypatch):
    # founder-d2 counts the repository's tests as independent; a run with the owner's permissions still cannot verify
    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    # fails if rungs.run_verify stops holding an unconfined bench pass below verifier grade (UNCONFINED_RUN)
    assert (done["status"], done["completion_state"]) == ("done", "qualified-draft"), done["outcome"]
    assert done["verdicts"] == [{"unit": "check-patch", "verifies": "patch", "result": "pass", "state": "qualified-draft"}]
    assert "switched off by this test" in json.dumps(done["outcome"])

@pytest.mark.parametrize(("files", "why"), [
    ({"calc.py": BREAKS_SUB}, "fail"),
    ({"tests/test_calc.py": "def test_add():\n    pass\n"}, "test file"),
])
def test_a_patch_that_breaks_a_test_or_edits_one_closes_the_mission_blocked(wf, files, why):
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": files})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "blocked-escalated")
    assert done["verdicts"][0]["state"] == "blocked-escalated"
    reasons = " ".join(done["outcome"]["reasons"])
    assert why in reasons or why in " ".join(str(v) for v in done["verdicts"]), reasons


def test_a_failed_patch_goes_back_to_the_chat_naming_the_failing_test_and_the_second_patch_is_verified(wf,
                                                                                                       monkeypatch,
                                                                                                       confined_bench):
    """The supervisor's retry (V4b): the bench's failing answer reaches the chat as a message, never a guess. Under
    founder-d2, which counts the repository's tests as independent, the second patch closes verified."""
    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)
    mid = _planned(wf, {"units": [{**TEST_PATCH, "max_attempts": 2}, TEST_CHECK], "edges": []})
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": BREAKS_SUB}})
    back = ws.run(wf, mid)
    assert back["status"] == "waiting"
    (asked,) = back["awaiting_chat"]
    (message,) = asked["messages"]
    assert message["from"] == "check-patch" and "test_sub" in message["text"]
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", "verified"), done["outcome"]
    assert [v["result"] for v in done["verdicts"]] == ["fail", "pass"]


def test_a_submission_g6_is_not_waiting_for_is_refused(wf):
    mid = _planned(wf)
    for unit, why in (("check-patch", "not waiting for the chat"), ("nope", "no unit")):
        with pytest.raises(ValueError, match=why):
            ws.submit(wf, mid, unit, {"files": {}})
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    with pytest.raises(ValueError, match="not waiting for the chat"):
        ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})  # already taken: only the verifier is left
    ws.run(wf, mid)
    with pytest.raises(ValueError, match="not active"):
        ws.submit(wf, mid, "patch", {"files": {}})  # the mission is done


def test_a_submission_must_be_a_json_object(wf):
    mid = _planned(wf)
    with pytest.raises(ValueError, match="object"):
        ws.submit(wf, mid, "patch", ["calc.py"])


# --- Codex review of V1 ------------------------------------------------------------------------------


def test_an_oversized_submission_is_refused_and_spends_nothing(wf):
    mid = _planned(wf)
    with pytest.raises(ValueError, match="too large"):
        ws.submit(wf, mid, "patch", {"files": {"calc.py": "x = 1\n" * 20_000}})
    after = ws.status(wf, mid)
    assert after["runs"] == [] and [a["unit"] for a in after["awaiting_chat"]] == ["patch"]
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})  # the corrected one is still taken
    assert ws.run(wf, mid)["completion_state"] == "qualified-draft"   # checked; tests nobody declared cannot verify


def test_a_plan_interrupted_while_materializing_resumes_through_the_same_plan(wf, monkeypatch):
    mid = ws.create(wf, objective="Make the failing add test pass", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid, None)
    ws.probe(wf, bench.COMPONENT, "run_tests")
    real = wf.graph._build

    def crash(*args, **kwargs):
        raise RuntimeError("injected crash while materializing")

    monkeypatch.setattr(wf.graph, "_build", crash)
    with pytest.raises(RuntimeError, match="injected"):
        ws.plan(wf, mid, FIX_A_TEST)
    monkeypatch.setattr(wf.graph, "_build", real)
    other = {"units": [TEST_PATCH, {**TEST_CHECK, "goal": "something else"}], "edges": []}
    with pytest.raises(ValueError, match="same plan"):
        ws.plan(wf, mid, other)
    assert ws.plan(wf, mid, FIX_A_TEST)["status"] == "active"


def test_another_owner_cannot_name_alices_repository(wf, tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    bob_venture = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    bob = ws.bind({ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: bob_venture, ws.ENV_OWNER: "bob"},
                  registry=NoRegistry())
    mid = ws.create(bob, objective="Run Alice's tests", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(bob, mid, None)
    ws.probe(bob, bench.COMPONENT, "run_tests")
    ws.plan(bob, mid, FIX_A_TEST)
    ws.run(bob, mid)
    ws.submit(bob, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(bob, mid)
    assert done["completion_state"] == "blocked-escalated"
    assert "no repository" in " ".join(done["outcome"]["reasons"])


# --- objective (a): which invoices are overdue, from a synthetic export, checked by z3 ---------------------
#
# The chat's plan, built as the chat would build it. The do unit is the chat's to write (the pull rung): the
# accountant pack cannot age it, because its assess_compliance reaches a model on every call and answers
# blocked-escalated at the door (lane W-D, ruling 4), and no declared model-free op takes supplied receivables.
# The verify unit's z3 oracle takes each invoice's amount and days overdue as constants from the export, the
# bucket totals as FACTS from the chat's artifact, and passes only when every bucket is exactly the sum of the
# invoices in its range and the totals agree. No code here is specific to receivables.

EXPORT = [{"invoice": "INV-1", "amount": 120.0, "due_date": "2026-08-15", "days": 47},
          {"invoice": "INV-2", "amount": 80.0, "due_date": "2026-09-20", "days": 11},
          {"invoice": "INV-3", "amount": 50.0, "due_date": "2026-10-20", "days": -19},
          {"invoice": "INV-4", "amount": 40.0, "due_date": "2026-05-01", "days": 153}]
RANGES = ["(<= {d} 0)", "(and (> {d} 0) (<= {d} 30))", "(and (> {d} 30) (<= {d} 60))", "(and (> {d} 60) (<= {d} 90))",
          "(> {d} 90)"]
#: The aging the export implies as of 2026-10-01 (not yet due, 1-30, 31-60, 61-90, over 90 days).
RIGHT = {"buckets": [{"total": 50.0}, {"total": 80.0}, {"total": 120.0}, {"total": 0.0}, {"total": 40.0}],
         "total_receivables": 290.0, "past_due_total": 240.0}
#: Plausible and wrong: INV-2 (11 days) put in 31-60. The totals still agree; only the buckets are wrong.
WRONG = {"buckets": [{"total": 50.0}, {"total": 0.0}, {"total": 200.0}, {"total": 0.0}, {"total": 40.0}],
         "total_receivables": 290.0, "past_due_total": 240.0}


def collect_overdue_plan(export=EXPORT) -> dict:
    n = range(1, len(export) + 1)
    sums = [f"(= b{i} (+ {' '.join(f'(ite {r.format(d=f"d{k}")} a{k} 0.0)' for k in n)}))" for i, r in enumerate(RANGES)]
    formula = (f"(not (and {' '.join(sums)} (= total (+ b0 b1 b2 b3 b4)) (= overdue (+ b1 b2 b3 b4))))")
    variables = {**{f"b{i}": "Real" for i in range(5)}, "total": "Real", "overdue": "Real",
                 **{f"a{k}": "Real" for k in n}, **{f"d{k}": "Int" for k in n}}
    constants = [f"(= a{k} {inv['amount']})" for k, inv in zip(n, export, strict=True)]
    constants += [f"(= d{k} {inv['days']})" if inv["days"] >= 0 else f"(= d{k} (- {-inv['days']}))"
                  for k, inv in zip(n, export, strict=True)]
    facts = {**{f"b{i}": f"age.buckets.{i}.total" for i in range(5)}, "total": "age.total_receivables",
             "overdue": "age.past_due_total"}
    rows = json.dumps([{k: inv[k] for k in ("invoice", "amount", "due_date")} for inv in export])
    return {"units": [
        {"key": "age", "kind": "do", "output_schema": "receivables_aging",
         "goal": ("Age these receivables as of 2026-10-01 into five buckets by days past due (not yet due, 1-30, "
                  "31-60, 61-90, over 90), as {\"buckets\": [{\"total\": n} x5], \"total_receivables\": n, "
                  f"\"past_due_total\": n}}: {rows}")},
        {"key": "check-age", "kind": "verify", "verifies": "age",
         "goal": "Every bucket is exactly the export's invoices in its range, and the totals agree",
         "capabilities": [["formal_methods", "solve"]],
         "oracle": {"capability": ["formal_methods", "solve"],
                    "params": {"formula": formula, "variables": variables, "assertions": constants},
                    "produced_facts": list(facts),
                    "facts": facts, "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}],
        "edges": []}


@pytest.fixture
def books(tmp_path):
    """The real registry and the real door: the accountant pack and the z3 solver as shipped."""
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    return ws.bind({ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: "alice"})


def _aged(books, submitted: dict) -> dict:
    assert ws.probe(books, "formal_methods", "solve")["level"] == "probe_passed"
    mid = ws.create(books, objective="Which invoices are overdue, and by how much?", risk_tier="R1",
                    budget_cents=0)["mission_id"]
    ws.accept(books, mid, {"goal": "the receivables aged as of 2026-10-01, every bucket proven against the export"})
    ws.plan(books, mid, collect_overdue_plan())
    waiting = ws.run(books, mid)
    (asked,) = waiting["awaiting_chat"]
    assert asked["unit"] == "age" and "INV-4" in asked["goal"] and waiting["runs"] == []
    ws.submit(books, mid, "age", submitted)
    return ws.run(books, mid)










def test_the_fix_tests_result_says_what_was_tested(wf, monkeypatch, confined_bench):
    """Marketing spec §3, §4 and §10: the fix-tests mission reads answer first, Verified, and how many tests ran (under
    founder-d2, which counts the repository's tests as independent)."""
    from g6.workforce.result_view import render_trust_report
    from g6.workforce import view as wview

    monkeypatch.setenv(coding_certificate.ENV_POLICY, coding_certificate.FOUNDER_D2)
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    done = ws.run(wf, mid)
    assert done["completion_state"] == "verified", done["outcome"]
    md = done["result"]["markdown"]
    assert "**✓ Verified**" in md and "- ✓ Tests were run on the result: 2 of 2 passed" in md
    assert "Tests: 2 of 2 passed" in render_trust_report(wview.build(wf, mid))
    # Fix pass (c): the patch reads as code, one fenced block per file, never as escaped JSON.
    quoted = [line.removeprefix("> ").removeprefix(">") for line in md.splitlines() if line.startswith(">")]
    assert quoted[:2] == [TEST_PATCH["goal"] + ":", "calc.py:"]
    assert quoted[2:2 + len(FIXED.splitlines()) + 2] == ["```", *FIXED.splitlines(), "```"]
    assert '"files"' not in md and "\\n" not in md


def test_the_bench_challenges_the_problem_and_the_tests_integrity_and_says_what_it_did_not(wf):
    """E10c (FE §12; TS §43): the bench ran the named tests; its refusal of an edited test is a structural protection and
    never a check (founder ruling R-1), and no regression run or small deliberate changes were tried, so those read not
    tested."""
    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    ws.run(wf, mid)
    closed = next(e["detail"] for e in reversed(wf.missions.events(mid)) if e["kind"] == "mission.closed")
    assert {name: status for _, name, status in closed["acceptance"]["surfaces"]} == {
        "original_bug_still_present": "passed", "regression_introduced": "not_tested", "test_weakened": "not_tested",
        "edge_case_missed": "not_tested"}  # the bench's refusal of an edited test is a protection, not a check (R-1)
    assert closed["acceptance"]["adjudicated_by"] == "g6.acceptance.evaluate"


def test_each_check_of_the_patch_keeps_one_failure_elicitation_record(wf):
    """FE §28 (E10c): one record per verify cycle, on the check's own close, with its cost and time."""
    from g6.workforce import acceptance as wa

    mid = _planned(wf)
    ws.run(wf, mid)
    ws.submit(wf, mid, "patch", {"files": {"calc.py": FIXED}})
    ws.run(wf, mid)
    closes = [e["detail"] for e in wf.missions.events(mid, kind="run.closed")]
    (record,) = [d["elicitation"] for d in closes if d["unit"] == "check-patch"]
    assert set(record) == set(wa.ELICITATION_FIELDS)
    assert (record["work_unit_id"], record["risk_tier"], record["verdict"]) == ("patch", "R1", "pass")
    assert record["known_failure_signatures_considered"] == list(wa.SURFACES["code"])
    assert record["elicitation_strategies"] == ["test_execution"]
    assert (record["probes_executed"], record["evidence_gaps"]) == (["test_execution"], [])
    assert (record["failures_confirmed"], record["counterexamples_found"]) == ([], 0)
    assert record["remaining_unchecked_surfaces"] == ["edge_case_missed", "regression_introduced", "test_weakened"]
    assert record["cost"]["currency"] == "AUD" and isinstance(record["latency_seconds"], int)
    assert [d.get("elicitation") for d in closes if d["unit"] == "patch"] == [None]  # a do step's close keeps none
