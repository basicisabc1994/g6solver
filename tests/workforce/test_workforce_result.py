# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A mission's return, answer first (Phase E Part 2, E8a; Design §5, §9-§13, §23-§25; Doctrine §18).

Pinned: a mission's result says its answer first, then its trust state with the checks that ran in plain words, what
remains uncertain or failed (each stop's what, why, how material, what resolves it), disagreement as two
interpretations until a person decides, the evidence with its provenance, and what the work took; the default
return is that result plus what the chat needs to drive the mission, within its token budget, and the technical
fields wait for detail="full"; a mission finished before this part renders from its stored outcome. Offline.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from g6.workforce import result_view as rv
from g6.workforce import store
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import service as ws
from g6.workforce import view as wview

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
        "expect_field": "total", "side_effect": "read", "check": "input_model", "model_free": True}),
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True}),
    ("fake_tests", "run"): wcap.ParamContract.from_dict({
        "component": "fake_tests", "op": "run", "required": {"total": "number"}, "probe_params": {"total": 1},
        "expect_field": "passed", "side_effect": "read", "check": "input_model", "model_free": True}),
}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver", "fake_tests"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def _world(component: str, op: str, params: dict):
    if component == "fake_ledger" and params["numbers"] == []:  # a tool that says it could not, and returns nothing
        return {"ok": True, "completion_state": "blocked-escalated", "result": None}
    if component == "fake_ledger" and params["numbers"] == [0]:  # a tool that fails with its own error
        return {"ok": False, "error": {"code": "LEDGER_LOCKED", "message": "the ledger is locked"}}
    if component == "fake_ledger":
        return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
    if component == "fake_tests":
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"passed": params["total"] == 6, "tests": {"t::total": params["total"] == 6,
                                                                     "t::shape": True}}}
    return {"ok": True, "completion_state": "qualified-draft",
            "result": {"satisfiable": params["total"] != params["expected"]}}


SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
CHECK = {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is the ledger's 6",
         "capabilities": [["fake_solver", "check"]],
         "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                    "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}
SEVEN = {**CHECK, "key": "check-seven", "goal": "The total is 7", "oracle": {**CHECK["oracle"], "params": {"expected": 7}}}
TESTED = {**CHECK, "key": "test-sum", "goal": "The ledger's tests pass", "capabilities": [["fake_tests", "run"]],
          "oracle": {"capability": ["fake_tests", "run"], "params": {}, "bind": {"total": "sum.total"},
                     "expect": {"field": "passed", "equals": True}, "kind": "test"}}
PULLED = {"key": "sum", "kind": "do", "goal": "Total the ledger by hand", "output_schema": "total", "max_attempts": 2}


@pytest.fixture
def wf(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    env = {ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: "alice"}
    bound = ws.bind(env, registry=FakeRegistry(), invoke=_world, contracts=CONTRACTS, authority=SCRIPTED)
    for pair in CONTRACTS:
        assert ws.probe(bound, *pair)["level"] == "probe_passed"
    return bound


def _ran(wf, units: list, contract: dict | None = None) -> str:
    mid = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid, contract)
    ws.plan(wf, mid, {"units": units, "edges": []})
    ws.run(wf, mid)
    return mid


def _md(wf, mid: str) -> str:
    return ws.status(wf, mid)["result"]["markdown"]


def test_the_answer_comes_first_and_what_it_took_is_said(wf):
    mid = _ran(wf, [SUM, CHECK])
    found = ws.status(wf, mid)["result"]
    md = found["markdown"]
    assert (found["state"], found["label"], found["glyph"]) == (wc.VERIFIED, "Verified", "✓")
    assert md.splitlines()[0] == "**Task:** Total the ledger, checked"
    assert md.splitlines()[1] == "> Total the ledger:" and '"total": 6' in md
    assert md.index('"total": 6') < md.index("**✓ Verified**") < md.index("**Evidence**")
    assert ("**What it took:** 2 attempts · cost: not known (0.00 AUD known; 2 costs not measured) · "
            "your time: nothing was asked of you (your time in the chat is not measured)") in md


def test_the_checks_are_said_in_plain_words(wf):
    md = _md(wf, _ran(wf, [SUM, TESTED]))
    assert "- ✓ Tests were run on the result: 2 of 2 passed (Total the ledger)" in md
    # C5-1: a passing test is evidence, listed above on its own; it is not a reviewer, so nothing was compared
    assert "Evidence coverage: **Strong** · Reviewer comparison: **Not evaluated**" in md
    for word in ("oracle", "formal", "verdict", "artifact", "rung"):
        assert word not in md.lower(), word
    report = rv.render_trust_report(wview.build(wf, _ran(wf, [SUM, TESTED])))
    assert "Tests: 2 of 2 passed" in report and "Verdict: ✓ Verified" in report


def test_evidence_carries_its_provenance(wf):
    mid = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid, None)
    ws.plan(wf, mid, {"units": [PULLED, CHECK], "edges": []})
    ws.run(wf, mid)
    ws.submit(wf, mid, "sum", {"total": 7})  # the chat's draft is wrong; its check sends it back
    ws.run(wf, mid)
    wf.runtime.correct(mid, "sum", {"total": 6}, reason="recounted", actor="human")
    ws.run(wf, mid)
    md = _md(wf, mid)
    assert "- Source: Total the ledger by hand: written by you (supplied by you)" in md
    assert "your time: you stepped in 1 time (time not measured)" in md
    computed = _md(wf, _ran(wf, [SUM, CHECK]))
    assert "- Calculation: Total the ledger: computed by a tool, with no model (supplied by G6)" in computed


def test_a_stopped_step_says_what_failed_and_what_resolves_it(wf):
    mid = _ran(wf, [{**SUM, "call": {**SUM["call"], "params": {"numbers": [1, 2, 4]}}}, CHECK])
    md = _md(wf, mid)
    assert "**⛔ Could not verify**" in md and "**What failed**" in md
    line = next(x for x in md.splitlines() if x.startswith("- This step stopped"))
    assert "To resolve: correct the result so that it passes the independent check." in line
    assert line.endswith("(code G6_E_RESULT_NOT_VERIFIED)")
    assert "Evidence coverage: **Weak** · Reviewer comparison: **Not evaluated**" in md  # C5-1: a failed check is no reviewer
    assert "oracle" not in md.lower()  # the step's own reason is said plainly; the raw answer waits for detail="full"


#: Wire values and machinery a person should never read in a reason (Design §19, §23; the lead's P2.21 ruling).
WIRE = re.compile(r"blocked[-_]escalated|qualified[-_]draft|verified[-_]ready|'verified'|\bdeclare|oracle|"
                  r"draft until it is judged", re.IGNORECASE)


def _said(wf, numbers: list) -> tuple[str, str]:
    """(the mission, its stopped step's line), every item line of its result checked for wire words."""
    mid = _ran(wf, [{**SUM, "call": {**SUM["call"], "params": {"numbers": numbers}}}, CHECK])
    md = _md(wf, mid)
    for line in md.splitlines():
        assert not (line.startswith("- ") and WIRE.search(line)), line
    return mid, next(x for x in md.splitlines() if x.startswith("- This step stopped"))


def test_a_stopped_steps_reasons_are_said_in_plain_words(wf):
    """The lead's P2.21 ruling: a step's stored reasons ("the reply declares blocked-escalated; declared
    blocked-escalated; the component did not answer: no result: empty; a produced output is a draft until it is
    judged") are wire values said twice; the view says what happened once, in plain words. The stored reasons are
    unchanged and stay in the technical details."""
    mid, line = _said(wf, [])
    assert line.startswith("- This step stopped after 1 attempt (Total the ledger): the tool said it could not do "
                           "this; the tool returned no result. "), line
    assert line.endswith("(code G6_E_CAPABILITY_UNAVAILABLE)")
    stored = wf.ledger.outcome(wf.ledger.runs(mid)[0]).reasons  # the record keeps its own words
    assert "the reply declares blocked-escalated" in stored and "a produced output is a draft until it is judged" in stored
    _, failed = _said(wf, [0])
    assert ": the tool reported a failure: LEDGER_LOCKED the ledger is locked. " in failed, failed
    _, checked = _said(wf, [1, 2, 4])
    assert ": the check failed: its answer had satisfiable = True where False was expected. " in checked, checked


@pytest.mark.parametrize("stored", [
    "the reply declares blocked-escalated", "declared blocked_escalated: the ledger is locked",
    "the component did not answer: component reported blocked-escalated",
    "the component's own 'verified' is capped at qualified-draft: only an independent verdict promotes",
    "verification not measured (formal): the oracle's answer carries no 'satisfiable'",
    "a pass that cannot verify: not executed; the verifier asked a model (2 calls)",
    "the oracle's op is not declared model-free", "refused at the door: no ParamContract declares fake.op",
    "refused at the door: fake.op declares its cost in USD and the budget is in AUD",
    "the component did not answer: degraded: verified_ready was claimed", "reported a failure: ok is false",
    "refused at the door: fake.op is sensitive: the generic door denies it",
    "no worker can be formed: a gate unit needs the human rung (V1)",
    "unit 'sum' names no call, and only the deterministic rung runs in P0 (local, pull and human rungs are V1)",
])
def test_every_stored_reason_shape_is_said_without_wire_words(stored):
    """Each reason shape the workforce stores (contracts, rungs, runs, the door) reads without a wire state or the
    machinery's own words once the view says it; a raw tool answer stays in the technical details. G6's own internal
    words (door, rung) are said plainly here, in G6's text, since the renderer no longer filters content by them."""
    said = wview._why([stored, "a produced output is a draft until it is judged", 'the oracle answered: {"x": 1}'])
    assert (said or "capped" in stored) and not WIRE.search(said), said  # a policy note alone says nothing
    assert not rv.INTERNAL.search(said), said


def test_checks_that_disagree_are_shown_as_two_interpretations_until_a_person_decides(wf):
    """Review Focus 5."""
    mid = _ran(wf, [SUM, CHECK, SEVEN])
    waiting = ws.status(wf, mid)
    md = waiting["result"]["markdown"]
    assert waiting["result"]["state"] == "" and "**Not finished yet**" in md and "Verified" not in md
    assert "**Two interpretations remain**" in md
    assert "- The result holds: the check(s) check-sum passed Total the ledger" in md
    assert "- The result is wrong: the check check-seven failed it" in md
    assert (f"Why G6 could not resolve this: the checks disagree and none can be tried again, so a person decides: "
            f"python -P -m g6.workforce.decide uphold|set-aside {mid} check-seven") in md
    wf.runtime.decide(mid, "check-seven", "uphold", reason="the export says 7", actor="human")
    ws.run(wf, mid)
    assert "**⛔ Could not verify**" in _md(wf, mid)


def test_the_default_return_is_the_summary(wf):
    mid = _ran(wf, [SUM, CHECK])
    short = ws.summary(wf, mid)
    assert set(short) <= {*ws.SUMMARY, "more"} and "result" in short
    assert not {"units", "runs", "verdicts", "tree", "outcome", "decisions"} & set(short)
    assert short["more"].startswith('mission_status with detail="full" adds: ')
    assert rv.wire_tokens(short["result"]) <= rv.RESULT_BUDGET
    assert ws.trim(ws.status(wf, mid)) == short


def test_a_mission_finished_before_this_part_renders_from_its_stored_outcome(wf, monkeypatch):
    """Review Focus 1: a mission closed with no acceptance evaluation on its close reads its state from the stored
    outcome and says its coverage was not recorded."""
    real = wf.missions.close
    monkeypatch.setattr(wf.missions, "close", lambda mission_id, outcome, *, actor, lease=None, detail=None:
                        real(mission_id, outcome, actor=actor, lease=lease))
    mid = _ran(wf, [SUM, CHECK])
    found = ws.status(wf, mid)["result"]
    assert found["state"] == wc.VERIFIED and "Evidence coverage: **not recorded**" in found["markdown"]
    assert "- ✓ A solver checked the result against its inputs (Total the ledger)" in found["markdown"]


def _as_before_this_part(db: Path, mid: str) -> None:
    """The mission's rows as the code before this part wrote them (pre dc8895a108): no Ledger §10 records on
    ``run.closed``, no evaluation on ``mission.closed``, no acceptance contract or its digest, no intervention rows."""
    import sqlite3

    conn = sqlite3.connect(db)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
        conn.execute(f'DROP TRIGGER "{name}"')
    for kind, path in (("run.closed", "$.verification"), ("mission.closed", "$.acceptance"),
                       ("mission.status", "$.acceptance_sha256")):
        conn.execute("UPDATE evidence SET detail_json = json_remove(detail_json, ?) WHERE kind = ? AND ref = ?",
                     (path, kind, mid))
    conn.execute("DELETE FROM evidence WHERE kind = 'intervention' AND ref = ?", (mid,))
    conn.execute("UPDATE missions SET acceptance_json = '' WHERE mission_id = ?", (mid,))
    conn.commit()
    conn.close()


def test_a_mission_finished_verified_before_this_part_says_which_checks_ran(wf, tmp_path):
    """Fix pass I1 (Review Focus 1): a mission that closed verified before this part, its rows in the old shape, says
    the checks its verdicts record. C5-1: its checks are tests and solvers, not reviewers, so what was compared is not
    recorded; it never reads "Not evaluated" (a claim that none ran) beside "Verified"."""
    mid = _ran(wf, [SUM, CHECK])
    _as_before_this_part(tmp_path / "venture.db", mid)
    found = ws.status(wf, mid)["result"]
    md = found["markdown"]
    assert found["state"] == wc.VERIFIED and "None ran" not in md and "Not evaluated" not in md
    assert "Evidence coverage: **not recorded** · Reviewer comparison: **not recorded**" in md
    assert "- ✓ A solver checked the result against its inputs (Total the ledger)" in md
    stopped = _ran(wf, [{**SUM, "call": {**SUM["call"], "params": {"numbers": []}}}, CHECK])  # no check ever ran
    _as_before_this_part(tmp_path / "venture.db", stopped)
    assert "Reviewer comparison: **not recorded**" in _md(wf, stopped)  # what was not recorded is said so


def _rec(mechanism: str, result: str, **over) -> dict:
    return {"mechanism": mechanism, "executed": True, "independent": True, "result": result, **over}


def test_the_reviewer_comparison_reads_reviewers_only_never_tests_or_solvers():
    """C5-1 (the independent review, 2026-10-09): one passing test is evidence, not reviewers being consistent."""
    agreement = wview._agreement
    tests_only = (_rec("test_execution", "pass"), _rec("formal_proof_check", "pass"), _rec("deterministic_computation", "pass"))
    assert agreement(tests_only, recorded=True) == "None ran"  # shown "Not evaluated"
    assert agreement((_rec("test_execution", "fail"),), recorded=True) == "None ran"  # a failed test is no reviewer
    assert agreement(tests_only, recorded=False) == ""  # closed before records were kept: "not recorded"
    assert agreement((*tests_only, _rec("independent_model_review", "pass")), recorded=True) == "Agreement"
    assert agreement((*tests_only, _rec("independent_model_review", "fail")), recorded=True) == "Disagreement"
    assert agreement((_rec("independent_model_review", "pass"), _rec("human_review", "fail")), recorded=True) == "Disagreement"
    assert agreement((_rec("independent_model_review", "pass", executed=False),), recorded=True) == "None ran"  # never ran


def test_a_person_setting_aside_a_failed_check_makes_the_comparison_mixed():
    agreement = wview._agreement
    person = _rec("human_review", "set_aside", kind="human")
    assert agreement((_rec("test_execution", "fail", set_aside=True), person), recorded=True) == "Mixed"
    assert agreement((_rec("independent_model_review", "fail", set_aside=True), _rec("independent_model_review", "pass")),
                     recorded=True) == "Mixed"
    # a set-aside of a failed test with no person record is not a reviewer's word
    assert agreement((_rec("test_execution", "fail", set_aside=True),), recorded=True) == "None ran"


def test_a_long_answer_points_to_where_it_is_in_full(wf):
    """Fix pass I6: an answer cut to its budget says where the rest is, and that place has all of it."""
    mid = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid, None)
    ws.plan(wf, mid, {"units": [PULLED, CHECK], "edges": []})
    ws.run(wf, mid)
    notes = [f"line {i}: the ledger row {i} was reconciled against the bank feed SENTINEL{i}" for i in range(60)]
    ws.submit(wf, mid, "sum", {"total": 6, "notes": notes})
    ws.run(wf, mid)
    md = ws.summary(wf, mid)["result"]["markdown"]
    pointer = next(line for line in md.splitlines() if "not shown in full" in line)
    assert 'mission_status with detail="full"' in pointer and '"outputs"' in pointer, pointer
    assert "SENTINEL59" not in md
    (output,) = ws.status(wf, mid)["outputs"]  # what mission_status(detail="full") returns
    assert (output["step"], output["content"]["notes"][-1]) == ("sum", notes[-1])
    assert "outputs" not in ws.summary(wf, mid)


def test_a_result_that_could_not_be_verified_shows_no_answer_by_default(wf):
    """Fix pass I7 (lead ruling; founder to confirm): as the job surface does under D3, a blocked-escalated mission's
    default return shows no answer; what its checks rejected stays readable on request, marked as such."""
    mid = _ran(wf, [{**SUM, "call": {**SUM["call"], "params": {"numbers": [1, 2, 4]}}}, CHECK])
    found = ws.status(wf, mid)
    md = found["result"]["markdown"]
    assert found["result"]["state"] == wc.BLOCKED
    assert "_No answer is shown: see what failed below._" in md and '"total": 7' not in md
    assert found["outputs"] == [{"step": "sum", "goal": "Total the ledger", "content": {"total": 7},
                                 "note": "a draft the checks rejected"}]
    (passed,) = ws.status(wf, _ran(wf, [SUM, CHECK]))["outputs"]
    assert passed == {"step": "sum", "goal": "Total the ledger", "content": {"total": 6}}
    stopped = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    wf.missions.cancel(stopped, reason="the export was replaced", actor="chat")
    md = _md(wf, stopped)
    assert "_No answer is shown: see what failed below._" in md
    assert "- This mission was cancelled: the export was replaced." in md


def test_a_status_is_never_lost_to_its_own_summary(wf, monkeypatch):
    """Fix pass M9: a result view that cannot be built reads "Could not verify", and the status is still returned."""
    mid = _ran(wf, [SUM, CHECK])

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(wview, "build", broken)
    found = ws.status(wf, mid)
    assert (found["result"]["state"], found["result"]["label"]) == (wc.BLOCKED, "Could not verify")
    assert found["completion_state"] == wc.VERIFIED and found["runs"]  # every other field as it is
    assert ws.summary(wf, mid)["result"]["label"] == "Could not verify"


def test_a_code_result_says_its_standard_what_was_challenged_and_what_was_not(wf):
    """E10c (TS §28; FE §12): the mission's view carries the standard and each surface in plain words; general work
    names no surface yet, so its report keeps today's lines."""
    view = wview.build(wf, _ran(wf, [SUM, TESTED]))
    assert view.standard == "ordinary (as set for this mission)"
    assert [(s.what, s.status) for s in view.surfaces] == [
        ("its tests pass", "passed"), ("whether anything else broke", "not_tested"),
        ("whether its tests were weakened", "not_tested"),
        ("whether its tests catch small deliberate mistakes", "not_tested")]
    md = rv.render(view)["markdown"]
    assert "Failure challenges: ✓ its tests pass" in md
    assert ("- Not tested: whether anything else broke; whether its tests were weakened; whether its tests catch "
            "small deliberate mistakes") in md
    assert "Standard: ordinary (as set for this mission)" in rv.render_trust_report(view)
    plain = wview.build(wf, _ran(wf, [SUM, CHECK]))
    assert plain.surfaces == () and "Failure challenges" not in rv.render_trust_report(plain)


def test_a_surface_is_said_once_across_steps_at_its_worst():
    """Review of E10c, finding 5: two steps that fared differently on one surface do not each get a line that contradicts
    the other; the surface reads once, at its worst (failed, then not tested, then passed), in the order first met."""
    from types import SimpleNamespace

    steps = SimpleNamespace(surfaces=(
        ("a", "original_bug_still_present", "passed"), ("b", "original_bug_still_present", "failed"),
        ("c", "original_bug_still_present", "not_tested"), ("a", "test_weakened", "passed"),
        ("b", "test_weakened", "not_tested"), ("a", "edge_case_missed", "passed"), ("b", "edge_case_missed", "passed")))
    assert [(s.what, s.status) for s in wview._surfaces(steps)] == [
        ("its tests fail", "failed"), ("whether its tests were weakened", "not_tested"),
        ("its tests catch small deliberate mistakes", "passed")]


def test_a_stored_surface_outside_the_current_vocabulary_is_ignored_and_never_fatal():
    """Review of E10c, finding 6: a stored evaluation is read back whatever it holds; entries that do not fit the current
    vocabulary (a renamed surface, a status that is not one of three, a malformed row) are left out, the rest kept."""
    from types import SimpleNamespace

    stored = SimpleNamespace(surfaces=(("a", "made_up_surface", "passed"), ("a", "test_weakened", "maybe"),
                                       ("a", "edge_case_missed"), ("a", "edge_case_missed", "passed", "extra"),
                                       ("a", "original_bug_still_present", "passed")))
    assert [(s.what, s.status) for s in wview._surfaces(stored)] == [("its tests pass", "passed")]
    assert wview._surfaces(SimpleNamespace(surfaces=())) == () and wview._surfaces(None) == ()


def test_a_task_with_no_code_step_shows_no_code_coverage_but_still_owes_its_checks(wf):
    """Founder decision (2026-10-09): no code-specific coverage line where no step is code, and that is not an absence of
    verification obligations: a requirement no check measures still holds the result below verified."""
    checked = wview.build(wf, _ran(wf, [SUM, CHECK]))
    unchecked = wview.build(wf, _ran(wf, [SUM, CHECK], {"goal": "x", "success_criteria": ["the books feel right"]}))
    assert checked.surfaces == () and unchecked.surfaces == ()
    assert "Failure challenges" not in rv.render_trust_report(unchecked)
    assert (checked.state, unchecked.state) == (rv.VERIFIED, rv.QUALIFIED_DRAFT)


def test_render_the_review_samples(wf):
    """The Design §34 review's rendered returns: written to G6_DESIGN_REVIEW_DIR when it is set, always within budget."""
    samples = {"verified": _ran(wf, [SUM, TESTED]), "failed": _ran(wf, [{**SUM, "call": {
        **SUM["call"], "params": {"numbers": [1, 2, 4]}}}, CHECK]), "disputed": _ran(wf, [SUM, CHECK, SEVEN]),
        "uncertain": _ran(wf, [SUM, CHECK], {"goal": "x", "success_criteria": ["the books feel right"]})}
    out = os.environ.get("G6_DESIGN_REVIEW_DIR")
    for name, mid in samples.items():
        result = ws.summary(wf, mid)["result"]
        assert rv.wire_tokens(result) <= rv.RESULT_BUDGET, name
        assert not [line for line in result["markdown"].splitlines() if line.startswith("- ") and WIRE.search(line)]
        if out:
            (Path(out) / f"e8a-mission-{name}.md").write_text(result["markdown"], encoding="utf-8")
            (Path(out) / f"e10c-trust-report-{name}.md").write_text(rv.render_trust_report(wview.build(wf, mid)),
                                                                    encoding="utf-8")
