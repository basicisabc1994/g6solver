# SPDX-License-Identifier: Apache-2.0
"""``g6.lean.search`` (the archive's ``lean_prover``, rewritten): ``prove`` tries the core tactics in order, then proofs
an injected model proposes, and takes a candidate only when ``g6.lean.check_proof`` passed it against the user's own
statement; ``sketch`` is a model's draft, labelled as one. Nothing here says proved: the search's label is
``check_proof``'s, at best ``qualified-draft``.

Most tests need no Lean: the screen, the declaration check and the limits run before Lean is looked for, and a test
double stands in for the checker, built with ``g6.lean.envelope.finish`` (the one place a check is labelled) so it
returns what ``check_proof`` returns when Lean reports a theorem. Two tests run Lean itself and are skipped, with the
reason, when it is absent."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.lean
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, NoModel
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.lean import check_proof, search
from g6.lean.envelope import (
    CODE_EXECUTION,
    ELABORATION_FAILED,
    IMPORT_REFUSED,
    LEAN_ENV,
    LEAN_MISSING,
    PROOF_CHECKED,
    SORRY,
    STATEMENT_MISMATCH,
    STATEMENT_REFUSED,
    TIME_LIMIT,
    finish,
)
from g6.lean.lean_block import LeanBlock, LeanInput
from g6.lean.schema import LeanResult
from g6.lean.search import (
    CORE_TACTICS,
    GOAL,
    MODEL_FAILED,
    NO_MODEL,
    PROOF_FOUND,
    PROPOSAL_REFUSED,
    SEARCH_EXHAUSTED,
    SKETCH_UNCHECKED,
    SearchResult,
)
from g6.mcp import invoke_reply


def _resolved() -> str | None:
    named = os.environ.get(LEAN_ENV, "").strip()
    return (shutil.which(named) or (named if Path(named).is_file() else None)) if named else shutil.which("lean")


needs_lean = pytest.mark.skipif(_resolved() is None, reason=f"Lean 4 is not installed: install elan, Lean's "
                                                            f"toolchain manager, so lean is on PATH, or set {LEAN_ENV}")


@pytest.fixture
def no_lean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEAN_ENV, str(tmp_path / "no-lean-here"))


class Checker:
    """A stand-in for ``check_proof``: Lean "accepts" exactly the proofs in ``accepts`` for the statement given, and
    reports an elaboration error for anything else. Every call is recorded."""

    def __init__(self, *accepts: str, statement_printed: str | None = None) -> None:
        self.accepts, self.printed, self.calls = set(accepts), statement_printed, []

    def __call__(self, source: str, theorem: str, *, expected_statement: str = "", **_limits) -> LeanResult:
        self.calls.append(source)
        proof = source.rsplit(":= ", 1)[-1].strip()
        printed = self.printed if self.printed is not None else expected_statement
        if printed != expected_statement:
            return finish("check_proof", lean_ran=True, theorem=theorem, kind="theorem", statement=printed, axioms=[],
                          expected_statement=expected_statement, statement_matched=False, code=STATEMENT_MISMATCH,
                          reason=f"Lean prints the statement as {printed}")
        if proof in self.accepts:
            return finish("check_proof", lean_ran=True, theorem=theorem, kind="theorem", statement=printed, axioms=[],
                          expected_statement=expected_statement, statement_matched=True)
        return finish("check_proof", lean_ran=True, theorem=theorem, code=ELABORATION_FAILED,
                      reason="Lean reported 1 error(s) in the source: line 1: unsolved goals",
                      messages=[{"severity": "error", "line": 1, "column": 0, "text": "unsolved goals"}])


def _blocked(result: SearchResult, code: str) -> None:
    assert (result.code, result.completion_state) == (code, "blocked-escalated"), (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump(mode="json")) == []


# --- D1: a maths claim is never accepted without the check passing it ----------------------------------------------

def test_d1_a_proposal_lean_rejects_is_never_taken_however_sure_the_model_is() -> None:
    # Replacing _prove's `if _passed(result, statement)` with `if True` makes this fail (the first candidate is taken).
    model = FakeLLM(default="by decide  -- this is certainly correct; the proof is complete")
    result = search._prove("2 + 2 = 5", check=Checker(), llm=model, budget=6)
    _blocked(result, SEARCH_EXHAUSTED)
    assert result.proof == "" and len(result.candidates) == 6 and len(model.calls) == 2
    assert {c["origin"] for c in result.candidates} == {"core tactic", "model"}
    assert all(c["code"] for c in result.candidates)


def test_d1_the_first_candidate_check_proof_passes_is_the_answer_and_its_label_is_check_proof_s() -> None:
    # Removing the copy of the passing check into SearchResult.check makes this fail.
    checker = Checker("by decide")
    result = search._prove("2 + 2 = 4", check=checker, budget=8)
    assert result.completion_state == "qualified-draft" and result.code == "", result.reason
    assert result.proof == "by decide" and [c["proof"] for c in result.candidates] == ["by rfl", "by decide"]
    assert result.check["warning_card"]["code"] == PROOF_CHECKED and result.warning_card["code"] == PROOF_FOUND
    assert result.source == checker.calls[-1] and f"theorem {GOAL} : 2 + 2 = 4 := by decide" in result.source
    assert check_envelope(result.model_dump(mode="json")) == []


# --- LEAN_PROVER-01: the archive's pass-through of a model's own declaration, and a lying checker ------------------

@pytest.mark.parametrize("reply", ["theorem g6_goal : True := trivial", "lemma easy : 1 = 1 := rfl",
                                   "```lean\ntheorem g6_goal : 2 + 2 = 5 := by decide\n```", "", "   "])
def test_p0_01_a_reply_that_declares_its_own_theorem_is_refused_before_any_check(reply: str) -> None:
    # Removing _proof_body's declaration refusal makes this fail (the archive passed such text through as the proof).
    checker = Checker("theorem g6_goal : True := trivial", "trivial")
    result = search._prove("2 + 2 = 5", check=checker, llm=FakeLLM(default=reply), tactics=(), budget=2)
    _blocked(result, SEARCH_EXHAUSTED)
    assert checker.calls == [] and all(c["code"] == PROPOSAL_REFUSED for c in result.candidates)


@pytest.mark.parametrize("lie", [{"theorem": "other"}, {"expected_statement": "True"}, {"statement_matched": False},
                                 {"kind": "def"}, {"axioms": ["sorryAx"]}, {"lean_ran": False}])
def test_p0_01_a_check_that_is_not_about_this_goal_and_statement_is_not_a_pass(lie: dict) -> None:
    # Removing a condition from _passed makes its case fail.
    def lying(source: str, theorem: str, *, expected_statement: str = "", **_limits) -> LeanResult:
        fields = {"lean_ran": True, "theorem": theorem, "kind": "theorem", "statement": expected_statement,
                  "axioms": [], "expected_statement": expected_statement, "statement_matched": True} | lie
        good = finish("check_proof", **{k: v for k, v in fields.items()})
        return good.model_copy(update={"completion_state": "qualified-draft", "code": "",
                                       "warning_card": {**(good.warning_card or {}), "code": PROOF_CHECKED}})

    _blocked(search._prove("2 + 2 = 5", check=lying, budget=1), SEARCH_EXHAUSTED)


# --- LEAN_PROVER-02: caller and model text never reaches Lean unscreened --------------------------------------------

@pytest.mark.parametrize(("proposal", "code"), [
    ('by\n  #eval IO.Process.run { cmd := "calc" }\n  rfl', CODE_EXECUTION),
    ("by sorry", SORRY),
    ("by\n  native_decide", CODE_EXECUTION),
])
def test_p0_02_a_proposal_that_runs_code_or_leaves_a_gap_is_screened_out_before_lean(
        proposal: str, code: str, no_lean, monkeypatch: pytest.MonkeyPatch) -> None:
    # Removing check_proof's screen (screened=False) makes this fail: Lean is looked for and LEAN_MISSING stops it.
    def refuse(*args, **kwargs):
        raise AssertionError("a program was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    result = search._prove("2 + 2 = 4", check=check_proof, llm=FakeLLM(default=proposal), tactics=(), budget=1)
    _blocked(result, SEARCH_EXHAUSTED)
    assert result.candidates[0]["code"] == code and result.candidates[0]["lean_ran"] is False


def test_p0_02_a_preamble_that_imports_or_declares_the_goal_is_refused_before_anything_runs(no_lean) -> None:
    # Removing _prove's preamble screen makes this fail (each candidate would carry the refused preamble).
    checker = Checker("by rfl")
    _blocked(search._prove("2 + 2 = 4", check=checker, preamble="import Mathlib\n"), IMPORT_REFUSED)
    _blocked(search._prove("2 + 2 = 4", check=checker, preamble=f"theorem {GOAL} : True := trivial\n"),
             "G6_E_LEAN_INPUT_INVALID")
    _blocked(search._prove("True) := trivial #exit example : (True", check=checker), STATEMENT_REFUSED)
    assert checker.calls == []


# --- LEAN_PROVER-03, -04, -05: no download, no fake stepping, models only through the injected caller ---------------

def test_p1_03_and_p1_04_no_op_downloads_or_reports_a_step_it_did_not_take() -> None:
    # Adding a prove_step, lakefile or mathlib op or field to LeanInput makes this fail.
    for op, params in (("prove_step", {"tactic": "simp"}), ("prove", {"statement": "True", "lakefile": "x"}),
                       ("prove", {"statement": "True", "mathlib": True}), ("sketch", {"statement": "True",
                                                                                     "model": "claude"})):
        refused = get_registry().invoke("lean", op, params)
        assert str(refused.failure()).startswith("[G6_E_INPUT_VALIDATION]"), op


def test_p1_05_without_a_model_the_search_uses_the_core_tactics_and_says_no_model_was_given() -> None:
    # Removing _prove's model-failure catch makes the NoModel case raise.
    alone = search._prove("2 + 2 = 5", check=Checker(), budget=8)
    assert len(alone.candidates) == len(CORE_TACTICS) and alone.evidence[0]["model"] == "none given"
    failing = search._prove("2 + 2 = 5", check=Checker(), llm=NoModel(), budget=8)
    _blocked(failing, SEARCH_EXHAUSTED)
    assert failing.evidence[0]["model"].startswith(MODEL_FAILED) and len(failing.candidates) == len(CORE_TACTICS)


def test_the_model_sees_lean_s_errors_from_the_last_failure() -> None:
    # Removing the last failure's messages from _ask's prompt makes this fail.
    model = FakeLLM(default="by omega")
    search._prove("2 + 2 = 5", check=Checker(), llm=model, tactics=("rfl",), budget=2)
    assert "unsolved goals" in model.calls[0][1] and "by rfl" in model.calls[0][1]


def test_a_statement_lean_prints_otherwise_stops_the_search_at_once() -> None:
    # Removing STATEMENT_MISMATCH from search's stop codes makes this fail (every candidate is tried in vain).
    checker = Checker("by rfl", statement_printed="2 + 2 = 4")
    result = search._prove("2+2=4", check=checker, budget=8)
    _blocked(result, STATEMENT_MISMATCH)
    assert len(checker.calls) == 1 and "2 + 2 = 4" in result.reason


@pytest.mark.parametrize("budget", [0, 33, True, 2.5])
def test_the_budget_is_bounded(budget) -> None:
    _blocked(search._prove("2 + 2 = 4", check=Checker("by rfl"), budget=budget), "G6_E_LEAN_INPUT_INVALID")


def test_m1_lean_missing_says_how_to_install_lean(no_lean) -> None:
    # Removing _card's use of g6.lean.envelope's own actions makes this fail (a generic action, no install steps).
    result = search._prove("2 + 2 = 4", check=check_proof)
    _blocked(result, LEAN_MISSING)
    action = result.warning_card["suggested_action"]
    assert "elan" in action and LEAN_ENV in action and "never installs" in action


def test_m2_the_search_has_one_deadline_and_each_check_gets_what_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    # Removing _prove's deadline makes this fail (each check gets the full time limit and the search runs on).
    now, given = [0.0], []

    def slow(source: str, theorem: str, *, expected_statement: str = "", time_limit: float = 0.0, **_limits):
        given.append(time_limit)
        now[0] += 6.0
        return Checker()(source, theorem, expected_statement=expected_statement)

    monkeypatch.setattr(search, "_clock", lambda: now[0])
    result = search._prove("2 + 2 = 5", check=slow, budget=8, time_limit=60, deadline_s=10)
    _blocked(result, TIME_LIMIT)
    assert given == [10.0, 4.0] and len(result.candidates) == 2
    for deadline in (0, 3601, True):
        _blocked(search._prove("2 + 2 = 4", check=Checker("by rfl"), deadline_s=deadline), "G6_E_LEAN_INPUT_INVALID")


def test_review3_m1_a_deadline_that_passes_in_the_last_check_is_the_timeout_refusal(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Removing the out_of_time() call before _prove's exhausted return makes this fail (SEARCH_EXHAUSTED).
    now, calls = [0.0], []

    def timing_out(source: str, theorem: str, *, expected_statement: str = "", time_limit: float = 0.0, **_limits):
        calls.append(time_limit)
        now[0] += 3.0 if len(calls) < 4 else 5.0
        if len(calls) == 4:
            return finish("check_proof", lean_ran=True, theorem=theorem, code=TIME_LIMIT,
                          reason="Lean did not finish within the time left")
        return Checker()(source, theorem, expected_statement=expected_statement)

    monkeypatch.setattr(search, "_clock", lambda: now[0])
    result = search._prove("2 + 2 = 5", check=timing_out, budget=4, deadline_s=10)
    _blocked(result, TIME_LIMIT)
    assert len(calls) == 4


def test_review3_m1_the_time_left_is_read_once_so_a_check_never_gets_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reading the clock a second time in attempt (the check's time_limit) makes this fail (INPUT_INVALID stops it).
    now, given = [0.0], []

    def clock() -> float:
        now[0] += 0.4
        return now[0]

    def strict(source: str, theorem: str, *, expected_statement: str = "", time_limit: float = 0.0, **_limits):
        given.append(time_limit)
        if time_limit <= 0:   # what check_proof itself does with a time limit of 0 or less
            return finish("check_proof", code="G6_E_LEAN_INPUT_INVALID", reason="time_limit must be positive")
        return Checker()(source, theorem, expected_statement=expected_statement)

    monkeypatch.setattr(search, "_clock", clock)
    result = search._prove("2 + 2 = 5", check=strict, budget=4, deadline_s=0.6)
    _blocked(result, TIME_LIMIT)
    assert given and all(t > 0 for t in given)


# --- sketch ----------------------------------------------------------------------------------------------------------

def test_sketch_is_a_model_draft_labelled_as_one() -> None:
    # Removing sketch's SKETCH_UNCHECKED card makes this fail.
    result = search.sketch("∀ (n : Nat), n + 0 = n", llm=FakeLLM(default="Induct on n; the base case is rfl. Verified."))
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == SKETCH_UNCHECKED
    assert "checked nothing" in result.warning_card["message"] and result.draft.startswith("Induct on n")
    assert result.proof == "" and result.check is None
    _blocked(search.sketch("True"), NO_MODEL)
    _blocked(search.sketch("True", llm=NoModel()), MODEL_FAILED)


def test_g1_no_search_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        SearchResult(operation="prove", completion_state="verified")


# --- the block, MCP and declarations -------------------------------------------------------------------------------

def test_g5_prove_runs_lean_so_it_is_sensitive_and_sketch_is_not(no_lean) -> None:
    assert "prove" in g6.lean.__g6_sensitive_ops__ and "sketch" not in g6.lean.__g6_sensitive_ops__
    refused = invoke_reply("lean", "prove", json.dumps({"statement": "2 + 2 = 4"}))
    assert refused["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    sketched = invoke_reply("lean", "sketch", json.dumps({"statement": "2 + 2 = 4"}))
    assert not sketched["ok"] and sketched["result"]["code"] == NO_MODEL and check_envelope(sketched) == []
    payload = get_registry().invoke("lean", "prove", {"statement": "2 + 2 = 4"}).unwrap()
    assert payload["code"] == LEAN_MISSING and payload["op"] == "prove"


def test_the_block_passes_its_injected_model_to_sketch() -> None:
    # Removing LeanBlock's llm hand-over to sketch makes this fail (NO_MODEL).
    payload = LeanBlock(llm=FakeLLM(default="By rfl.")).infer(LeanInput(op="sketch", statement="True")).unwrap()
    assert payload["draft"] == "By rfl." and payload["warning_card"]["code"] == SKETCH_UNCHECKED


def test_d2_the_failure_mode_is_declared_by_g6_lean_and_no_longer_planned() -> None:
    assert "unchecked_maths_claim" in g6.lean.__g6_mitigates__
    assert "g6.lean.search" not in FailureModeRegistry.planned()


# --- Lean itself ---------------------------------------------------------------------------------------------------

@needs_lean
def test_with_lean_two_and_two_is_found_by_a_core_tactic() -> None:
    result = search.prove("2 + 2 = 4")
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.proof in {f"by {t}" for t in CORE_TACTICS} and result.check["statement_matched"] is True


@needs_lean
def test_with_lean_a_false_statement_exhausts_the_core_tactics() -> None:
    result = search.prove("2 + 2 = 5")
    _blocked(result, SEARCH_EXHAUSTED)
    assert [c["code"] for c in result.candidates] == [ELABORATION_FAILED] * len(CORE_TACTICS)
