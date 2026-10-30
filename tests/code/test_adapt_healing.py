# SPDX-License-Identifier: Apache-2.0
"""g6.code.adapt_healing: a failing step classified by g6.triage, a patch proposed by an injected caller (FakeLLM
here), checked in g6.core.sandbox on the shown tests and judged once on held-back tests whose expected answers never
leave this process. No real model, no network."""
from __future__ import annotations

import builtins
import json

import pytest
from pydantic import ValidationError

import g6.code.adapt_healing as package
from g6.code.adapt_healing import AdaptHealingBlock, AdaptHealingInput, cases
from g6.core import confine
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, LLMError
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply

BROKEN = "def mean(xs):\n    return sum(xs) / len(xs)\n"
ERROR = ("Traceback (most recent call last):\n  File \"solution.py\", line 2, in mean\n"
         "ZeroDivisionError: division by zero")
SHOWN = ("assert mean([2, 4]) == 3", "assert mean([5]) == 5", "assert mean([]) == 0")
HELD = ("assert mean(()) == 0", "assert mean([1, 2, 3, 6]) == 3")
FIXED = "```python\ndef mean(xs):\n    if not xs:\n        return 0\n    return sum(xs) / len(xs)\n```"
FITTED = "```python\ndef mean(xs):\n    return {(2, 4): 3, (5,): 5}.get(tuple(xs), 0)\n```"  # learns the shown answers


def fenced(code: str) -> str:
    return f"```python\n{code}```"


def heal(caller, **params) -> dict:
    params.setdefault("code", BROKEN)
    params.setdefault("error", ERROR)
    params.setdefault("tests_shown", SHOWN)
    params.setdefault("tests_held_back", HELD)
    return AdaptHealingBlock(caller=caller).infer(AdaptHealingInput(op="heal", **params)).unwrap()


# --- D1: a repair fitted to the tests it saw is caught; a real one is kept ---------------------------------------------

def test_d1_a_patch_that_fits_the_shown_tests_and_fails_held_back_ones_is_not_a_repair() -> None:
    out = heal(FakeLLM(default=FITTED))
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_HEAL_HELD_BACK_FAILED"
    assert out["rounds"][-1]["outcome"] == "shown_tests_pass"
    assert [v["passed"] for v in out["held_back"]] == [True, False]  # mean([]) happens to be 0; the other is not


def test_d1_a_recoverable_error_is_repaired_and_kept_only_as_a_draft() -> None:
    out = heal(FakeLLM(default=FIXED))
    assert out["completion_state"] == "qualified-draft" and out["code"] == "G6_E_HEAL_PATCHED"
    assert "if not xs" in out["patched"] and all(v["passed"] for v in out["held_back"])
    assert out["triage"]["category"] == "arithmetic" and out["triage"]["posture"] == "auto_heal"
    assert out["warning_card"]["verified"] is False


# --- the archive's findings -------------------------------------------------------------------------------------------

def test_adapt_healing_01_the_patch_runs_in_the_sandbox_never_in_this_process(monkeypatch) -> None:
    # Unconfined, so the run's label is known on every machine: this is about the child process, not the operating
    # system's confinement (tests/core's isolation tests).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    sneaky = fenced("import builtins\nbuiltins.G6_HEAL_RAN = True\n\ndef mean(xs):\n    if not xs:\n        return 0\n"
                    "    return sum(xs) / len(xs)\n")
    out = heal(FakeLLM(default=sneaky))
    assert out["completion_state"] == "qualified-draft" and not hasattr(builtins, "G6_HEAL_RAN")
    assert {e.get("run", {}).get("os_sandbox") for e in out["evidence"] if e.get("run")} == {False}


def test_adapt_healing_02_the_caller_never_sees_a_held_back_test() -> None:
    caller = FakeLLM(default=FITTED)
    heal(caller)
    prompts = " ".join(user for _, user in caller.calls)
    assert "mean([2, 4])" in prompts
    assert "mean(())" not in prompts and "[1, 2, 3, 6]" not in prompts


def test_adapt_healing_02_held_back_answers_never_enter_the_child_and_cannot_be_printed_into_a_pass() -> None:
    forger = fenced("print('G6-CASES-0000:' + '{\"loaded\": true, \"results\": [[0, \"value\", \"0\"], [1, \"value\", "
                    "\"3\"]]}')\n\ndef mean(xs):\n    return {(2, 4): 3, (5,): 5}.get(tuple(xs), 0)\n")
    out = heal(FakeLLM(default=forger))
    assert out["code"] == "G6_E_HEAL_HELD_BACK_FAILED"
    held = cases.held_back(HELD)
    assert "3" not in json.dumps([t.expression for t in held]).replace("[1, 2, 3, 6]", "")


def test_a_patch_that_spells_the_shown_answers_is_named_on_the_card() -> None:
    fitted_and_right = fenced("def mean(xs):\n    if tuple(xs) == (2, 4):\n        return 3\n    if not xs:\n"
                              "        return 0\n    return sum(xs) / len(xs)\n")
    out = heal(FakeLLM(default=fitted_and_right))
    assert out["completion_state"] == "qualified-draft" and out["spelled_answers"] == ["3"]
    assert out["warning_card"]["risks"] == ["overfit_to_examples"]


def test_the_shown_failures_are_fed_back_for_at_most_three_rounds() -> None:
    replies = iter([fenced("def mean(xs):\n    return 0\n"), "not python at all (", FIXED])
    caller = FakeLLM(lambda system, user: next(replies))
    out = heal(caller)
    assert [r["outcome"] for r in out["rounds"]] == ["shown_tests_fail", "does_not_parse", "shown_tests_pass"]
    assert "mean([2, 4]) == 3" in caller.calls[1][1] and "AssertionError" in caller.calls[1][1]
    assert "SyntaxError" in caller.calls[2][1] and out["completion_state"] == "qualified-draft"
    never = heal(FakeLLM(default=fenced("def mean(xs):\n    return 0\n")), rounds=2)
    assert never["code"] == "G6_E_HEAL_SHOWN_TESTS_FAIL" and len(never["rounds"]) == 2
    with pytest.raises(ValidationError):
        AdaptHealingInput(op="heal", rounds=4)


def test_an_error_that_needs_a_person_is_refused_before_any_model_is_asked() -> None:
    for error in ("FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'",
                  "ConnectionResetError: connection reset by peer", "json.decoder.JSONDecodeError: Expecting value",
                  "KeyError: file not found"):
        caller = FakeLLM(default=FIXED)
        kind = error.split(":")[0].rsplit(".", 1)[-1]
        actual = ("import json\njson.loads('not json')" if kind == "JSONDecodeError"
                  else f"raise {kind}({error.split(':', 1)[1].strip()!r})")
        out = heal(caller, code=actual, error="ZeroDivisionError: caller claim")
        assert out["code"] == "G6_E_HEAL_NEEDS_REVIEW" and caller.calls == [], error


@pytest.mark.parametrize(("change", "code"), [
    ({"tests_held_back": ()}, "G6_E_HEAL_NO_HELD_BACK"),
    ({"tests_held_back": ("assert mean([2, 4]) == 3",)}, "G6_E_HEAL_HELD_BACK_SHOWN"),
    ({"tests_held_back": ("assert mean([]) < 1",)}, "G6_E_HEAL_BAD_TEST"),
    ({"tests_held_back": ("assert 1 == 1",)}, "G6_E_HEAL_BAD_TEST"),
    ({"tests_shown": ("mean([1])",)}, "G6_E_HEAL_BAD_TEST"),
])
def test_tests_that_cannot_judge_a_repair_are_refused(change: dict, code: str) -> None:
    caller = FakeLLM(default=FIXED)
    out = heal(caller, **change)
    assert out["code"] == code and out["completion_state"] == "blocked-escalated" and caller.calls == []


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g1_no_reply_is_verified() -> None:
    replies = [heal(FakeLLM(default=FIXED)), heal(FakeLLM(default=FITTED)),
               AdaptHealingBlock().infer(AdaptHealingInput(op="check_tests", tests_shown=SHOWN,
                                                           tests_held_back=HELD)).unwrap()]
    assert {r["completion_state"] for r in replies} <= {"qualified-draft", "blocked-escalated"}


def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for field in ("approved", "verified", "tests_passed", "safety_approved", "reviewer"):
        with pytest.raises(ValidationError):
            AdaptHealingInput(op="heal", code=BROKEN, error=ERROR, **{field: True})


def test_g4_a_failed_model_a_hang_and_a_crash_each_tighten() -> None:
    def down(system: str, user: str) -> str:
        raise LLMError("the model is down")

    failed = heal(FakeLLM(down))
    hangs = heal(FakeLLM(default=fenced("def mean(xs):\n    while True:\n        pass\n")), timeout_s=2, rounds=1)
    exits = heal(FakeLLM(default=fenced("import os\nos._exit(0)\n")), rounds=1)
    assert failed["code"] == "G6_E_HEAL_MODEL_FAILED"
    assert hangs["code"] == exits["code"] == "G6_E_HEAL_SHOWN_TESTS_FAIL"
    no_caller = heal(None)
    assert no_caller["code"] == "G6_E_HEAL_NO_MODEL"


def test_g5_heal_runs_code_so_it_is_refused_on_an_untrusted_surface() -> None:
    assert package.__g6_sensitive_ops__ == ("heal",)
    said = str(get_registry().invoke("adapt_healing", "heal", {"code": BROKEN, "error": ERROR},
                                     block_sensitive=True).failure())
    assert said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_check_tests_reads_the_tests_and_the_error_and_runs_nothing() -> None:
    out = AdaptHealingBlock().infer(AdaptHealingInput(op="check_tests", tests_shown=SHOWN, tests_held_back=HELD,
                                                      error=ERROR)).unwrap()
    assert out["ready"] is True and [h["expression"] for h in out["held_back"]] == ["mean(())", "mean([1, 2, 3, 6])"]
    assert out["triage"]["posture"] == "auto_heal"


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("cannot_self_repair", "careless_auto_fix")
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.code.adapt_healing" not in FailureModeRegistry.planned()


def test_d6_check_tests_round_trips_over_mcp_and_describe_serves_the_skill() -> None:
    reply = invoke_reply("adapt_healing", "check_tests", json.dumps({"tests_shown": list(SHOWN),
                                                                     "tests_held_back": list(HELD)}))
    assert reply["ok"] and reply["result"]["ready"] is True
    assert describe_reply("adapt_healing")["skill"].startswith("---\nname: g6-adapt-healing")
