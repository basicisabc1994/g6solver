# SPDX-License-Identifier: Apache-2.0
"""AdaptHealingBlock: the registry and MCP door (``g6_invoke``) to adapt_healing.

``heal`` asks the injected model caller for a patch and runs it, with the caller's tests, in ``g6.core.sandbox`` (a
bounded child process on this computer), so it is sensitive: callable from Python, and over MCP only when the person
who runs the server lists it in ``G6_MCP_SENSITIVE`` (and then with the model the person configured). ``check_tests``
reads
the tests and the error and runs nothing. A repair is ``qualified-draft`` at best, and only when every held-back test
passed.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.code._types import CodeRefused, answer, refuse
from g6.code.adapt_healing import cases
from g6.code.adapt_healing.heal import (HELD_BACK_FAILED, LEAKED, MAX_ROUNDS, MODEL_FAILED, NEEDS_REVIEW, NO_HELD_BACK,
                                        NO_MODEL, SHOWN_FAILED, error_parts, heal)
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.llm import CONFIGURE_MODEL
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.triage import classify

COMPONENT = "adapt_healing"
INPUT = "G6_E_HEAL_INPUT"
PATCHED = "G6_E_HEAL_PATCHED"
CHECKED = "G6_E_HEAL_TESTS_READ"
SENSITIVE_OPS: tuple[str, ...] = ("heal",)
_OPS: dict[str, str] = {
    "heal": "heal(code, error, tests_shown, tests_held_back, rounds, timeout_s): the error classified by g6.triage, "
            "a patch proposed by the model caller you give the block, checked in g6.core.sandbox on the shown tests "
            "and judged once on held-back tests the caller never sees; sensitive",
    "check_tests": "check_tests(tests_shown, tests_held_back, error): whether each test has the form heal needs, "
                   "whether a held-back test is also shown, and the error's triage; nothing runs",
}
_ACTIONS = {
    INPUT: "Give the fields the op needs (the help op lists them).",
    PATCHED: "Read the patch and the rounds; run your own suite on it before you keep it.",
    CHECKED: "Correct any test the reply names, then heal.",
    NEEDS_REVIEW: "A person looks at this error: it touches the world outside the code, or its class is not known.",
    NO_MODEL: f"To give G6 a model, {CONFIGURE_MODEL}. Or give the block a model caller from Python "
              "(AdaptHealingBlock(caller=...)).",
    NO_HELD_BACK: "Give tests_held_back: tests of the form assert <expression> == <literal> the caller never sees.",
    LEAKED: "Hold back tests whose expressions no shown test uses.",
    SHOWN_FAILED: "No proposal passed the shown tests: fix it by hand, or give more context in the error.",
    HELD_BACK_FAILED: "The patch fits the shown tests and fails held-back ones: it is not a repair. Fix it by hand.",
    MODEL_FAILED: "The model did not answer: try again, or with another caller.",
    cases.BAD_TEST: "Write shown tests as one assert each, and held-back tests as assert <expression> == <literal>.",
}


class AdaptHealingInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["heal", "check_tests", "ops", "help"] = "help"
    code: str = Field("", max_length=200_000)
    error: str = Field("", max_length=20_000)
    tests_shown: tuple[str, ...] = Field((), max_length=50)
    tests_held_back: tuple[str, ...] = Field((), max_length=50)
    rounds: int = Field(MAX_ROUNDS, ge=1, le=MAX_ROUNDS)
    timeout_s: float = Field(10.0, gt=0, le=60)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: AdaptHealingInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def heal_op(d: AdaptHealingInput, caller: Any) -> dict[str, Any]:
    if not d.code.strip() or not d.error.strip():
        raise CodeRefused(INPUT, "give code (the failing step's source), error (its error or traceback) and the tests")
    out = heal(d.code, d.error, d.tests_shown, d.tests_held_back, caller, rounds=d.rounds, timeout_s=d.timeout_s)
    said = (f"Repaired in {len(out['rounds'])} round(s): the patch passed the {len(d.tests_shown)} shown test(s) and all "
            f"{len(d.tests_held_back)} held-back test(s), which the caller never saw.")
    risks = []
    if out["spelled_answers"]:
        said += (f" It spells out {len(out['spelled_answers'])} answer(s) the shown tests expect: check it was not "
                 "fitted to them.")
        risks = ["overfit_to_examples"]
    return answer(COMPONENT, "heal", PATCHED, said, action=_ACTIONS[PATCHED], evidence=out["evidence"], risks=risks,
                  **_ids(d), patched=out["patched"], triage=out["triage"], rounds=out["rounds"],
                  held_back=out["held_back"], spelled_answers=out["spelled_answers"])


def check_tests(d: AdaptHealingInput) -> dict[str, Any]:
    shown = cases.shown(d.tests_shown)
    held = cases.held_back(d.tests_held_back)
    leaked = [t.index for t in held if t.shape in cases.shapes_shown(shown)]
    found: dict[str, Any] = {"shown": len(shown), "held_back": [{"index": t.index, "expression": t.expression}
                                                                  for t in held], "held_back_also_shown": leaked}
    if d.error.strip():
        kind, message = error_parts(d.error)
        triage = classify(kind, message, d.error)
        found["triage"] = {"source": "caller_claim", "category": triage.category, "posture": triage.posture, "reasons": list(triage.reasons),
                           "hint": triage.hint}
    ready = bool(held) and not leaked and found.get("triage", {}).get("posture", "auto_heal") == "auto_heal"
    said = (f"{len(shown)} shown and {len(held)} held-back test(s) read; nothing ran. "
            + ("heal can judge a repair on them." if ready else "heal would refuse: "
               + ("no held-back test" if not held else "a held-back test is also shown" if leaked
                  else "the error needs a person") + "."))
    return answer(COMPONENT, "check_tests", CHECKED, said, action=_ACTIONS[CHECKED],
                  evidence=[{"kind": "tests_read", "ran_code": False}], **_ids(d), ready=ready, **found)


@block_contract(
    purpose="repair one failing step of Python code without a careless automatic fix: the error classified first, a "
            "patch proposed by an injected model, checked on shown tests in g6.core.sandbox and kept only if it "
            "passes held-back tests the proposer never saw",
    mitigates_failure_modes=("cannot_self_repair", "careless_auto_fix"),
    verification_method="held-back tests the model never sees, their expressions computed in g6.core.sandbox and "
                        "compared with their literals in G6's own process; g6.triage refuses errors that need a person",
)
@component_maturity("beta", reason="g6.triage, g6.core.sandbox and held-back judging, tested with scripted patches "
                                   "that fit the shown tests and fail held-back ones")
class AdaptHealingBlock(AIBlock[AdaptHealingInput, dict, dict]):
    """Repair a failing step, judged on tests the proposer never saw. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, caller: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.caller = caller

    def infer(self, data: AdaptHealingInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            cases.check_bounds(data.tests_shown, data.tests_held_back)
            out = check_tests(data) if data.op == "check_tests" else heal_op(data, self.caller)
        except (RecursionError, MemoryError) as exc:
            out = refuse(COMPONENT, data.op, cases.BAD_TEST, f"input exceeds parsing limits: {type(exc).__name__}",
                         action=_ACTIONS[cases.BAD_TEST], **_ids(data))
        except CodeRefused as exc:
            out = refuse(COMPONENT, data.op, exc.code, exc.reason, action=_ACTIONS.get(
                exc.code, "Correct the request as the reason says."), evidence=exc.evidence, **_ids(data),
                **exc.fields)
        if data.op == "heal":
            out["warning_card"]["message"] += " " + cases.PROCESS_BLIND_SPOT
            out["evidence"].append({"kind": "blind_spot", "reason": cases.PROCESS_BLIND_SPOT})
        return Result.ok(out)


__all__ = ["COMPONENT", "SENSITIVE_OPS", "AdaptHealingBlock", "AdaptHealingInput"]
