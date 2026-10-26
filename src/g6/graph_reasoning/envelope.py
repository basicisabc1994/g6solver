# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.graph_reasoning and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused before evaluating, a conflict, out of time, too large, PyReason asked for and missing, the two
  engines disagreeing, refuted by G6's own check) is ``blocked-escalated``, and the result keeps no interpretation,
  answer or explanation. A conflict keeps its derivation, which is what the caller needs to resolve it;
* an interpretation is ``qualified-draft`` only when G6's check of it passed and, where PyReason ran, both engines gave
  every interval within the tolerance. Its card says which engines ran: ``G6_E_GRAPH_REASONING_ENGINE_ANSWER`` when
  both did, ``G6_E_GRAPH_REASONING_ONE_ENGINE`` when only the pure-Python engine did, and why;
* a model checked and not evaluated (the ``check`` op) is ``qualified-draft`` with
  ``G6_E_GRAPH_REASONING_NOT_EVALUATED``.

It never gives ``verified``: the result is re-validated through
:class:`~g6.graph_reasoning.schema.GraphReasoningResult`, which has no such state, because the interpretation of a
caller's graph and rules is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.graph_reasoning.schema import GraphReasoningResult

_P = "G6_E_GRAPH_REASONING_"
ENGINE_ANSWER = _P + "ENGINE_ANSWER"
ONE_ENGINE = _P + "ONE_ENGINE"
NOT_EVALUATED = _P + "NOT_EVALUATED"
PARSE_ERROR = _P + "PARSE_ERROR"
ARITY_CLASH = _P + "ARITY_CLASH"
UNKNOWN_LABEL = _P + "UNKNOWN_LABEL"
UNKNOWN_FUNCTION = _P + "UNKNOWN_FUNCTION"
UNKNOWN_NODE = _P + "UNKNOWN_NODE"
UNKNOWN_EDGE = _P + "UNKNOWN_EDGE"
BAD_INTERVAL = _P + "BAD_INTERVAL"
VACUOUS_CLAUSE = _P + "VACUOUS_CLAUSE"
UNSAFE_RULE = _P + "UNSAFE_RULE"
NOT_STRATIFIABLE = _P + "NOT_STRATIFIABLE"
CONFLICT = _P + "CONFLICT"
INPUT_INVALID = _P + "INPUT_INVALID"
INPUT_TOO_LARGE = _P + "INPUT_TOO_LARGE"
TOO_MANY_GROUNDINGS = _P + "TOO_MANY_GROUNDINGS"
ANSWER_TOO_LARGE = _P + "ANSWER_TOO_LARGE"
TIME_LIMIT_TOO_LONG = _P + "TIME_LIMIT_TOO_LONG"
TIME_LIMIT = _P + "TIME_LIMIT"
ENGINE_UNAVAILABLE = _P + "ENGINE_UNAVAILABLE"
NOT_COMPARABLE = _P + "NOT_COMPARABLE"
PYREASON_ERROR = _P + "PYREASON_ERROR"
ENGINE_ERROR = _P + "ENGINE_ERROR"
ENGINES_DISAGREE = _P + "ENGINES_DISAGREE"
CERTIFICATE_REFUTED = _P + "CERTIFICATE_REFUTED"
NOT_CHECKED = _P + "NOT_CHECKED"

#: How far PyReason's float intervals may sit from G6's exact ones and still agree.
TOLERANCE = 1e-9

_ACTIONS = {
    PARSE_ERROR: "Correct the program at the line and column given. Statements end with '.', a fact is label(node) or "
                 "label(node, node) with an optional : [lower, upper], a rule is head <- clause, clause., and rule "
                 "arguments are variables.",
    ARITY_CLASH: "Give each label one number of arguments everywhere: one for a node label, two for an edge label.",
    UNKNOWN_LABEL: "Name a label the model has (check the spelling), or add facts or a rule that give it.",
    UNKNOWN_FUNCTION: "Use one of the annotation functions the reason lists, or give the head a fixed interval.",
    UNKNOWN_NODE: "Name a node of the graph (check the spelling), or add it with node name.",
    UNKNOWN_EDGE: "Name an edge of the graph, written as label(source, target), or add it with edge source -> target.",
    BAD_INTERVAL: "Give an interval [lower, upper] with 0 <= lower <= upper <= 1.",
    VACUOUS_CLAUSE: "Drop the clause, or give it an interval narrower than [0, 1]: every atom, known or not, is within "
                    "[0, 1], so the clause says nothing.",
    UNSAFE_RULE: "Use every head variable in a clause of the body, so each head atom the rule labels is bound to a "
                 "node.",
    NOT_STRATIFIABLE: "Break the cycle the reason names: an annotation function, and the other clauses of a rule with "
                      "a percentage threshold, may only read labels that do not depend on the rule's own head.",
    CONFLICT: "Resolve the conflict the evidence derives: the two intervals for this atom have nothing in common. "
              "Correct the facts or rules on either side; G6 does not choose between them.",
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Split the model or reduce it below the limit the reason names.",
    TOO_MANY_GROUNDINGS: "The rules have more groundings than G6 evaluates in one call. Connect each rule's variables "
                         "through edge clauses, narrow the graph, or split the problem.",
    ANSWER_TOO_LARGE: "Ask for less: name the labels you need, or query one atom.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit of at most 60 seconds, or split the model.",
    TIME_LIMIT: "The reasoning and its checks did not finish within the time limit. Raise time_limit (at most 60 "
                "seconds) or narrow the model.",
    ENGINE_UNAVAILABLE: "Install a PyReason that imports here, or leave engine at 'auto' to run the pure-Python engine "
                        "alone; the reason says why PyReason did not import.",
    NOT_COMPARABLE: "Leave engine at 'auto' to run the pure-Python engine alone: the reason names the rules PyReason "
                    "evaluates differently, so its answer could not confirm G6's.",
    PYREASON_ERROR: "PyReason failed before answering; inspect the reason, and report it with the model if it recurs.",
    ENGINE_ERROR: "The pure-Python engine failed before answering; report it with the model and the reason.",
    ENGINES_DISAGREE: "Do not use the answer: the two engines gave different intervals. The evidence names the atoms "
                      "they disagree on; report it with the model.",
    CERTIFICATE_REFUTED: "Do not use the answer: G6's own check contradicts it. The evidence names the failures; report "
                         "it with the model.",
    NOT_CHECKED: "Build results with g6.graph_reasoning's functions; this one carries no check by G6.",
}
_CHECKED = ("G6 checked the interpretation in pure Python: closure (applying every rule once more tightens nothing), "
            "support (every interval comes from a fact or from a rule whose body held, at intervals derived "
            "before it) and soundness (every interval within [0, 1] and not empty).")
_NOT_VERIFIED = ("It is what your graph and rules say, not a proof a person has inspected, so it is not verified; if a "
                 "rule or an interval does not say what you mean, neither does the answer.")


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def finish(operation: str, *, engines: list[dict[str, Any]] | None = None, code: str = "", reason: str = "",
           problems: list[dict[str, Any]] | None = None, interpretation: list[dict[str, Any]] | None = None,
           answer: dict[str, Any] | None = None, explanation: dict[str, Any] | None = None,
           report: dict[str, Any] | None = None, check: Any = None, agreement: dict[str, Any] | None = None,
           model: dict[str, Any] | None = None, solution: dict[str, Any] | None = None,
           conflict: dict[str, Any] | None = None, one_engine_why: str = "", outside: list[str] | None = None,
           cycle: list[str] | None = None, pyreason_warnings: list[dict[str, Any]] | None = None,
           elapsed_s: float = 0.0, request_id: str = "", run_id: str = "") -> GraphReasoningResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    engines = engines or []
    ran = [e["name"] for e in engines]
    checked = check.as_dict() if check is not None else {}
    evaluates = operation != "check"
    if code == CONFLICT and check is not None and not check.passed:
        code = CERTIFICATE_REFUTED
        reason = "G6's check contradicts the conflict the engine reported: " + "; ".join(check.failures[:3])
    if not code and evaluates:
        if agreement is not None and not agreement.get("agreed"):
            code = ENGINES_DISAGREE
            reason = ("the pure-Python engine and PyReason gave different intervals: "
                      + "; ".join(agreement.get("differences", [])[:3]))
        elif check is None:
            code, reason = NOT_CHECKED, "the interpretation carries no check by G6, so it is not reported"
        elif not check.passed:
            code = CERTIFICATE_REFUTED
            reason = "G6's check contradicts the interpretation: " + "; ".join(check.failures[:3])
    if code:
        interpretation, answer, explanation = [], {}, {}
    kind = ("graph_reasoning_refusal" if code and not ran else "graph_reasoning_check" if code
            else "graph_reasoning_interpretation" if evaluates else "graph_reasoning_model_check")
    evidence: dict[str, Any] = {
        "kind": kind,
        "operation": operation,
        "engines": engines,
        "pyreason_ran": "pyreason" in ran,
        "model": model or {},
        "solution": solution or {},
        "check": checked,
        "agreement": agreement or {},
        "tolerance": TOLERANCE,
        "elapsed_s": elapsed_s,
    }
    if outside:
        evidence["outside_pyreason_fragment"] = outside
    if pyreason_warnings:
        evidence["pyreason_warnings"] = pyreason_warnings
    if problems:
        evidence["problems"] = problems
    if cycle:
        evidence["cycle"] = cycle
    if conflict:
        evidence["conflict"] = conflict
    if code:
        evidence |= {"code": code, "reason": reason}
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    elif not evaluates:
        card = _model_card()
    else:
        card = _answer_card(engines, one_engine_why)
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return GraphReasoningResult.model_validate({
        "operation": operation, "engines": ran, "interpretation": interpretation or [], "answer": answer or {},
        "explanation": explanation or {}, "report": report or {}, "check": checked, "conflict": conflict or {},
        "problems": problems or [], "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _answer_card(engines: list[dict[str, Any]], one_engine_why: str) -> dict[str, Any]:
    pyreason = next((e for e in engines if e["name"] == "pyreason"), None)
    if pyreason is not None:
        return WarningCard(
            code=ENGINE_ANSWER, severity="amber",
            message=f"The pure-Python engine and PyReason {pyreason.get('version', '')} gave every interval within "
                    f"{TOLERANCE:g} of each other, and {_CHECKED} {_NOT_VERIFIED}",
            suggested_action="Check that the graph, the facts and the rules say what you mean before relying on the "
                             "answer.",
        ).model_dump()
    return WarningCard(
        code=ONE_ENGINE, severity="amber",
        message=f"Only the pure-Python engine ran ({one_engine_why or 'PyReason did not run'}), so no second engine "
                f"confirmed the interpretation; {_CHECKED} {_NOT_VERIFIED}",
        suggested_action="Use the answer knowing one engine derived it and G6's own check confirmed it; the evidence "
                         "says why PyReason did not run.",
    ).model_dump()


def _model_card() -> dict[str, Any]:
    return WarningCard(
        code=NOT_EVALUATED, severity="amber",
        message="The model parses, every interval is within [0, 1], each label has one arity, every rule is safe and "
                "the rules are stratified. Nothing was evaluated, so this says nothing about what the rules derive.",
        suggested_action="Run reason, query or explain to evaluate it.",
    ).model_dump()


__all__ = ["ANSWER_TOO_LARGE", "ARITY_CLASH", "BAD_INTERVAL", "CERTIFICATE_REFUTED", "CONFLICT", "ENGINES_DISAGREE",
           "ENGINE_ANSWER", "ENGINE_ERROR", "ENGINE_UNAVAILABLE", "INPUT_INVALID", "INPUT_TOO_LARGE", "NOT_CHECKED",
           "NOT_COMPARABLE", "NOT_EVALUATED", "NOT_STRATIFIABLE", "ONE_ENGINE", "PARSE_ERROR", "PYREASON_ERROR",
           "Refusal", "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "TOLERANCE", "TOO_MANY_GROUNDINGS", "UNKNOWN_EDGE",
           "UNKNOWN_FUNCTION", "UNKNOWN_LABEL", "UNKNOWN_NODE", "UNSAFE_RULE", "VACUOUS_CLAUSE", "finish"]
