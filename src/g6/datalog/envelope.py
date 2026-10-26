# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.datalog and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused before evaluating, Z3 asked for and missing, out of time, too large, the two engines disagreeing,
  refuted by G6's own check) is ``blocked-escalated``, and the result keeps no relations, answers or explanation;
* a model is ``qualified-draft`` only when G6's check of it passed and, where Z3 ran, both engines derived the same
  relations tuple for tuple. Its card says which engines ran: ``G6_E_DATALOG_ENGINE_ANSWER`` when both did,
  ``G6_E_DATALOG_ONE_ENGINE`` when only the pure-Python engine did, with how to add the second;
* a program checked and not evaluated (the ``check`` op) is ``qualified-draft`` with ``G6_E_DATALOG_NOT_EVALUATED``.

It never gives ``verified``: the result is re-validated through :class:`~g6.datalog.schema.DatalogResult`, which has no
such state, because the model of a caller's program is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.datalog.schema import DatalogResult

ENGINE_ANSWER = "G6_E_DATALOG_ENGINE_ANSWER"
ONE_ENGINE = "G6_E_DATALOG_ONE_ENGINE"
NOT_EVALUATED = "G6_E_DATALOG_NOT_EVALUATED"
PARSE_ERROR = "G6_E_DATALOG_PARSE_ERROR"
UNSAFE_RULE = "G6_E_DATALOG_UNSAFE_RULE"
NOT_STRATIFIABLE = "G6_E_DATALOG_NOT_STRATIFIABLE"
ARITY_CLASH = "G6_E_DATALOG_ARITY_CLASH"
UNKNOWN_RELATION = "G6_E_DATALOG_UNKNOWN_RELATION"
INPUT_INVALID = "G6_E_DATALOG_INPUT_INVALID"
INPUT_TOO_LARGE = "G6_E_DATALOG_INPUT_TOO_LARGE"
TOO_MANY_TUPLES = "G6_E_DATALOG_TOO_MANY_TUPLES"
ANSWER_TOO_LARGE = "G6_E_DATALOG_ANSWER_TOO_LARGE"
TIME_LIMIT_TOO_LONG = "G6_E_DATALOG_TIME_LIMIT_TOO_LONG"
TIME_LIMIT = "G6_E_DATALOG_TIME_LIMIT"
SOLVER_UNAVAILABLE = "G6_E_DATALOG_SOLVER_UNAVAILABLE"
SOLVER_ERROR = "G6_E_DATALOG_SOLVER_ERROR"
ENGINE_ERROR = "G6_E_DATALOG_ENGINE_ERROR"
ENGINES_DISAGREE = "G6_E_DATALOG_ENGINES_DISAGREE"
CERTIFICATE_REFUTED = "G6_E_DATALOG_CERTIFICATE_REFUTED"
NOT_CHECKED = "G6_E_DATALOG_NOT_CHECKED"

INSTALL_Z3 = "pip install 'g6[formal]'"
_ACTIONS = {
    PARSE_ERROR: "Correct the program at the line and column given. Facts end with '.', rules are head :- body., "
                 "queries end with '?', variables start upper-case or with '_', and strings use double quotes.",
    UNSAFE_RULE: "Bind every variable of the head, of each negated atom and of each comparison in a positive body "
                 "atom of the same rule, write facts with constants only, and give an anonymous variable in a negated "
                 "atom its own rule (has_q(X) :- q(X, _). then not has_q(X)).",
    NOT_STRATIFIABLE: "Break the cycle the reason names: no relation may depend on its own negation, directly or "
                      "through other rules.",
    ARITY_CLASH: "Give each relation one number of arguments everywhere it is used, or rename one of the uses.",
    UNKNOWN_RELATION: "Name a relation the program has (check the spelling), or add facts or rules for it.",
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Split the program or reduce it below the limit the reason names.",
    TOO_MANY_TUPLES: "The program derives more tuples than G6 evaluates in one call. Narrow the rules or the facts, or "
                     "split the problem.",
    ANSWER_TOO_LARGE: "Ask for less: name the relations you need, or use query with constants in the goal.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit of at most 60 seconds, or split the program.",
    TIME_LIMIT: "The evaluation and its checks did not finish within the time limit. Raise time_limit (at most 60 "
                "seconds) or narrow the program.",
    SOLVER_UNAVAILABLE: f"Install Z3: {INSTALL_Z3}, or leave engine at 'auto' to run the pure-Python engine alone.",
    SOLVER_ERROR: "Z3 failed before answering; inspect the reason, and report it with the program if it recurs.",
    ENGINE_ERROR: "The pure-Python engine failed before answering; report it with the program and the reason.",
    ENGINES_DISAGREE: "Do not use the answer: the two engines derived different relations. The evidence names the "
                      "tuples they disagree on; report it with the program.",
    CERTIFICATE_REFUTED: "Do not use the answer: G6's own check contradicts it. The evidence names the failures; report "
                         "it with the program.",
    NOT_CHECKED: "Build results with g6.datalog's functions; this one carries no check by G6.",
}
_CHECKED = ("G6 checked the model in pure Python: closure (applying every rule once more adds nothing), support "
            "(every tuple has a derivation from the facts) and negation (each negated atom is absent from the "
            "completed lower strata).")
_NOT_VERIFIED = ("It is what your program says, not a proof a person has inspected, so it is not verified; if a rule "
                 "does not say what you mean, neither does the answer.")


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def finish(operation: str, *, engines: list[dict[str, Any]] | None = None, code: str = "", reason: str = "",
           problems: list[dict[str, Any]] | None = None, relations: dict[str, Any] | None = None,
           queries: list[dict[str, Any]] | None = None, explanation: dict[str, Any] | None = None,
           report: dict[str, Any] | None = None, check: Any = None, agreement: dict[str, Any] | None = None,
           program: dict[str, Any] | None = None, model: dict[str, Any] | None = None, one_engine_why: str = "",
           undefined: list[str] | None = None, cycle: list[str] | None = None, elapsed_s: float = 0.0,
           request_id: str = "", run_id: str = "") -> DatalogResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    engines = engines or []
    ran = [e["name"] for e in engines]
    checked = check.as_dict() if check is not None else {}
    evaluates = operation != "check"
    if not code and evaluates:
        if agreement is not None and not agreement.get("agreed"):
            code = ENGINES_DISAGREE
            reason = ("the pure-Python engine and Z3 derived different relations: "
                      + "; ".join(agreement.get("differences", [])[:3]))
        elif check is None:
            code, reason = NOT_CHECKED, "the model carries no check by G6, so it is not reported"
        elif not check.passed:
            code = CERTIFICATE_REFUTED
            reason = "G6's check contradicts the model: " + "; ".join(check.failures[:3])
    if code:
        relations, queries, explanation = {}, [], {}
    kind = ("datalog_refusal" if code and not ran else "datalog_check" if code
            else "datalog_model" if evaluates else "datalog_program_check")
    evidence: dict[str, Any] = {
        "kind": kind,
        "operation": operation,
        "engines": engines,
        "z3_ran": "z3" in ran,
        "program": program or {},
        "model": model or {},
        "check": checked,
        "agreement": agreement or {},
        "elapsed_s": elapsed_s,
    }
    if problems:
        evidence["problems"] = problems
    if cycle:
        evidence["cycle"] = cycle
    if code:
        evidence |= {"code": code, "reason": reason}
    if code:
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    elif not evaluates:
        card = _program_card(undefined or [])
    else:
        card = _answer_card(engines, one_engine_why, undefined or [])
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return DatalogResult.model_validate({
        "operation": operation, "engines": ran, "relations": relations or {}, "queries": queries or [],
        "explanation": explanation or {}, "report": report or {}, "check": checked, "problems": problems or [],
        "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _undefined_note(undefined: list[str]) -> str:
    if not undefined:
        return ""
    names = ", ".join(undefined)
    return (f" No fact or rule defines {names}, so {'it is' if len(undefined) == 1 else 'they are'} empty: check the "
            "spelling.")


def _answer_card(engines: list[dict[str, Any]], one_engine_why: str, undefined: list[str]) -> dict[str, Any]:
    z3 = next((e for e in engines if e["name"] == "z3"), None)
    if z3 is not None:
        return WarningCard(
            code=ENGINE_ANSWER, severity="amber",
            message=f"The pure-Python engine and Z3 {z3.get('version', '')}'s Datalog engine derived the same "
                    f"relations, tuple for tuple, and {_CHECKED}{_undefined_note(undefined)} {_NOT_VERIFIED}",
            suggested_action="Check that the facts and rules say what you mean before relying on the answer.",
        ).model_dump()
    return WarningCard(
        code=ONE_ENGINE, severity="amber",
        message=f"Only the pure-Python engine ran ({one_engine_why or 'Z3 did not run'}), so no second engine "
                f"confirmed the model; {_CHECKED}{_undefined_note(undefined)} {_NOT_VERIFIED}",
        suggested_action=f"Add the second engine with {INSTALL_Z3} and run it again, or use the answer knowing one "
                         "engine derived it.",
    ).model_dump()


def _program_card(undefined: list[str]) -> dict[str, Any]:
    return WarningCard(
        code=NOT_EVALUATED, severity="amber",
        message="The program parses, every rule is safe, its negation is stratified and each relation has one arity. "
                f"Nothing was evaluated, so this says nothing about what it derives.{_undefined_note(undefined)}",
        suggested_action="Run evaluate, query or explain to derive from it.",
    ).model_dump()


__all__ = ["ANSWER_TOO_LARGE", "ARITY_CLASH", "CERTIFICATE_REFUTED", "ENGINES_DISAGREE", "ENGINE_ANSWER",
           "ENGINE_ERROR", "INPUT_INVALID", "INPUT_TOO_LARGE", "INSTALL_Z3", "NOT_CHECKED", "NOT_EVALUATED",
           "NOT_STRATIFIABLE", "ONE_ENGINE", "PARSE_ERROR", "Refusal", "SOLVER_ERROR", "SOLVER_UNAVAILABLE",
           "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "TOO_MANY_TUPLES", "UNKNOWN_RELATION", "UNSAFE_RULE", "finish"]
