# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.optimisation and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused before solving, OR-Tools missing, infeasible, unbounded, out of time, refuted by G6's own check)
  is ``blocked-escalated``, and the result keeps no objective and no solution;
* an answer is ``qualified-draft`` only when G6's check of it passed. Its card says what G6 checked and what still
  rests on the solver: ``G6_E_OPTIM_SOLVER_ANSWER`` for an optimum (or a solution, when there is nothing to optimise),
  ``G6_E_OPTIM_NOT_PROVEN_OPTIMAL`` for a feasible answer the solver did not prove optimal, naming the gap;
* a search's answer (``minimise``, ``global_search``: ``search`` given) is never proven optimal, and its card says how
  far the search got: ``G6_E_OPTIM_LOCAL_OPTIMUM`` for a local method that converged with the first-order condition
  holding and no lower point found around it (a local minimum, called local; on an expression that is not smooth, the
  card says SciPy's success flag is the only basis), ``G6_E_OPTIM_BEST_FOUND`` for a global search that converged by
  its own test (the best point found), ``G6_E_OPTIM_NOT_CONVERGED`` for a point a search stopped at before either, or
  one with a lower point next to it (a maximum or a saddle, say).

It never gives ``verified``: the result is re-validated through :class:`~g6.optimisation.schema.OptimResult`, which has
no such state, because a solver's answer on a caller's model is not a proof a person has inspected.
"""
from __future__ import annotations

import math
from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.optimisation.schema import OptimResult

SOLVER_ANSWER = "G6_E_OPTIM_SOLVER_ANSWER"
NOT_PROVEN_OPTIMAL = "G6_E_OPTIM_NOT_PROVEN_OPTIMAL"
SOLVER_UNAVAILABLE = "G6_E_OPTIM_SOLVER_UNAVAILABLE"
INPUT_INVALID = "G6_E_OPTIM_INPUT_INVALID"
INPUT_TOO_LARGE = "G6_E_OPTIM_INPUT_TOO_LARGE"
TIME_LIMIT_TOO_LONG = "G6_E_OPTIM_TIME_LIMIT_TOO_LONG"
INFEASIBLE = "G6_E_OPTIM_INFEASIBLE"
UNBOUNDED = "G6_E_OPTIM_UNBOUNDED"
INFEASIBLE_OR_UNBOUNDED = "G6_E_OPTIM_INFEASIBLE_OR_UNBOUNDED"
TIME_LIMIT = "G6_E_OPTIM_TIME_LIMIT"
NO_SOLUTION = "G6_E_OPTIM_NO_SOLUTION"
MODEL_INVALID = "G6_E_OPTIM_MODEL_INVALID"
SOLVER_ERROR = "G6_E_OPTIM_SOLVER_ERROR"
CERTIFICATE_REFUTED = "G6_E_OPTIM_CERTIFICATE_REFUTED"
NOT_CHECKED = "G6_E_OPTIM_NOT_CHECKED"
SCIPY_UNAVAILABLE = "G6_E_OPTIM_SCIPY_UNAVAILABLE"
LOCAL_OPTIMUM = "G6_E_OPTIM_LOCAL_OPTIMUM"
BEST_FOUND = "G6_E_OPTIM_BEST_FOUND"
NOT_CONVERGED = "G6_E_OPTIM_NOT_CONVERGED"

_ACTIONS = {
    SOLVER_UNAVAILABLE: "Install OR-Tools: pip install 'g6[optim] @ git+https://github.com/basicisabc1994/g6solver'.",
    INPUT_INVALID: "Correct the model as the reason says and run it again.",
    INPUT_TOO_LARGE: "Split the problem or reduce it below the limit the reason names.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit of at most 60 seconds, or split the problem.",
    INFEASIBLE: "No answer meets every constraint. Relax or correct the constraints that conflict, then run it again.",
    UNBOUNDED: "The objective improves without end. Add the bound or constraint the model is missing.",
    INFEASIBLE_OR_UNBOUNDED: "The solver could not tell whether no answer meets the constraints or the objective "
                             "improves without end. Check both: add missing bounds and relax conflicting constraints.",
    TIME_LIMIT: "No answer was found within the time limit. Raise time_limit (at most 60 seconds) or simplify the "
                "model.",
    NO_SOLUTION: "The search ended without an answer, which does not prove there is none. Relax the constraints or "
                 "try local_search='guided' with a longer time_limit.",
    MODEL_INVALID: "The solver rejected the model; correct what the reason names (often numbers too large).",
    SOLVER_ERROR: "The solver failed before answering; inspect the reason, and report it if it recurs.",
    CERTIFICATE_REFUTED: "Do not use the answer: G6's own check contradicts it. The evidence keeps the refuted answer "
                         "and the failures; report it with them.",
    NOT_CHECKED: "Build results with g6.optimisation's functions; this one carries no check by G6.",
    SCIPY_UNAVAILABLE: "Install SciPy: pip install 'g6[optim-nonlinear] @ git+https://github.com/basicisabc1994/g6solver'.",
}
_CERTIFICATES = {
    "duals": "the duals (dual feasible, with no duality gap)",
    "min_cut": "a cut whose capacity equals the flow",
    "no_negative_cycle": "the residual network, which has no negative cycle",
}
_NOT_VERIFIED = "A solver's answer on your model is not a proof a person has inspected, so it is not verified."


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and math.isfinite(value) else None


def finish(operation: str, *, engine: str, engine_version: str = "", status: str = "", code: str = "",
           reason: str = "", optimal: bool | None = None, objective: Any = None, bound: Any = None,
           solution: dict[str, Any] | None = None, certificate: dict[str, Any] | None = None, check: Any = None,
           problem: dict[str, Any] | None = None, elapsed_s: float = 0.0, request_id: str = "",
           run_id: str = "", search: dict[str, Any] | None = None) -> OptimResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``. ``search`` is how
    far a search got (``minimise``, ``global_search``); it is kept in the evidence and decides the draft's card."""
    checked = check.as_dict() if check is not None else {}
    objective, bound = _finite(objective), _finite(bound)
    if not code and check is None:
        code, reason = NOT_CHECKED, "the answer carries no check by G6, so it is not reported"
    elif not code and not check.passed:
        code = CERTIFICATE_REFUTED
        reason = "G6's check contradicts the solver's answer: " + "; ".join(check.failures[:3])
    refuted = None
    if code in (CERTIFICATE_REFUTED, NOT_CHECKED) and (solution or objective is not None):
        refuted = {"objective": objective, "bound": bound, "solution": solution, "certificate": certificate}
        certificate = None
    if code:
        optimal, objective, bound, solution = (None if code != CERTIFICATE_REFUTED else False), None, None, {}
    gap = abs(objective - bound) if objective is not None and bound is not None else None
    relative_gap = gap / max(1.0, abs(objective)) if gap is not None else None
    ran = bool(engine_version)
    evidence: dict[str, Any] = {
        "kind": "optimisation_answer" if not code else "optimisation_check" if ran else "optimisation_refusal",
        "operation": operation,
        "engine": engine,
        "engine_version": engine_version,
        "external_solver_ran": ran,
        "status": status,
        "optimal": optimal,
        "objective": objective,
        "bound": bound,
        "gap": gap,
        "relative_gap": relative_gap,
        "check": checked,
        "certificate": certificate or {},
        "problem": problem or {},
        "elapsed_s": elapsed_s,
    }
    if code:
        evidence |= {"code": code, "reason": reason}
    if refuted is not None:
        evidence["refuted_answer"] = refuted
    if search is not None:
        evidence["search"] = search
    card = (_blocked_card(code, reason) if code
            else _search_card(engine, engine_version, check, search) if search is not None
            else _answer_card(engine, engine_version, check, optimal, bound, gap, relative_gap))
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return OptimResult.model_validate({
        "operation": operation, "status": status, "optimal": optimal, "objective": objective, "bound": bound,
        "gap": gap, "relative_gap": relative_gap, "solution": solution or {}, "certificate": certificate or {},
        "check": checked, "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _blocked_card(code: str, reason: str) -> dict[str, Any]:
    return WarningCard(code=code, message=reason, severity="blocked",
                       suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()


def _answer_card(engine: str, version: str, check: Any, optimal: bool | None, bound: float | None,
                 gap: float | None, relative_gap: float | None) -> dict[str, Any]:
    who = f"{engine} {version}".strip()
    what = "; ".join(check.checked)
    if optimal is False:
        how_far = (f"the solver's best bound is {bound:g}, a gap of {gap:g} ({relative_gap:.2%}) to this answer"
                   if bound is not None and gap is not None
                   else "the solver gives no bound, so how far this is from the best answer is unknown")
        return WarningCard(
            code=NOT_PROVEN_OPTIMAL, severity="amber",
            message=f"{who} found a feasible answer, and G6 checked it in pure Python ({what}). It is not proven "
                    f"optimal: {how_far}. {_NOT_VERIFIED}",
            suggested_action="Use it knowing a better answer may exist, or raise time_limit (at most 60 seconds) or "
                             "simplify the model and run it again.",
        ).model_dump()
    if optimal is None:
        basis = "There is no objective, so any answer that meets every constraint will do."
    elif check.certificate in _CERTIFICATES:
        basis = f"Its optimality is shown by {_CERTIFICATES[check.certificate]}, which G6 checked."
    else:
        basis = (f"Its optimality rests on the solver's own bound ({bound:g}), which G6 did not re-derive."
                 if bound is not None else "Its optimality rests on the solver's own proof, which G6 did not "
                                           "re-derive.")
    return WarningCard(
        code=SOLVER_ANSWER, severity="amber",
        message=f"{who} found this answer, and G6 checked it in pure Python ({what}). {basis} {_NOT_VERIFIED}",
        suggested_action="Check that the variables, constraints and objective say what you mean before relying on "
                         "the answer.",
    ).model_dump()


def _search_card(engine: str, version: str, check: Any, search: dict[str, Any]) -> dict[str, Any]:
    who, what = f"{engine} {version}".strip(), "; ".join(check.checked)
    evaluations, lower = search.get("evaluations", 0), search.get("lower_point")
    if not search.get("converged") or search.get("first_order") is False or lower:
        why = (f"it stopped at {search['stopped']}" if search.get("stopped")
               else "the projected gradient at the point is not near zero" if search.get("first_order") is False
               else f"G6 found a point next to it where the objective is better ({lower['objective']:g}; it is in the "
                    "evidence), as at a maximum or a saddle" if lower
               else "its own convergence test did not pass")
        action = ("Run it again from the better point in the evidence (x0), or with more starts."
                  if lower and not search.get("stopped") and search.get("first_order") is not False
                  else "Raise max_evaluations or time_limit (at most 60 seconds), or simplify the expression, and run "
                       "it again.")
        return WarningCard(
            code=NOT_CONVERGED, severity="amber",
            message=f"{who} returned the best point it found in {evaluations} evaluation(s), but {why}, so it is not "
                    f"shown to be a minimum, local or global. G6 checked the point ({what}). {_NOT_VERIFIED}",
            suggested_action=action,
        ).model_dump()
    if search.get("kind") == "local":
        starts, minima = search.get("starts", 1), search.get("distinct_minima", 1)
        spread = (f"the starts found {minima} different minima, and this is the lowest" if minima > 1
                  else "one start cannot show there is no better minimum elsewhere" if starts == 1
                  else f"all {starts} starts that converged found this one")
        tried = search.get("neighbours_tried", 0)
        basis = (f"None of the {tried} points G6 tried around it is lower." if search.get("smooth", True)
                 else f"The expression is not smooth (abs, min, max or a table), so no gradient was checked: SciPy's "
                      f"own success flag is the only basis for calling it a minimum. None of the {tried} points G6 "
                      "tried around it is lower, which could have refuted it but does not show it.")
        return WarningCard(
            code=LOCAL_OPTIMUM, severity="amber",
            message=f"{who} converged to a local minimum from {search.get('converged_starts', 0)} of {starts} "
                    f"start(s); {spread}. G6 checked it ({what}). {basis} A local method cannot show it is the best: "
                    f"nothing bounds the objective. {_NOT_VERIFIED}",
            suggested_action="Before calling it the best, run global_search, or more starts, on the same expression.",
        ).model_dump()
    return WarningCard(
        code=BEST_FOUND, severity="amber",
        message=f"{who} converged by its own test after {evaluations} evaluation(s), and G6 checked the point ({what}). "
                f"It is the best point the search found, not a proven global minimum: nothing bounds the objective. "
                f"{_NOT_VERIFIED}",
        suggested_action="Run it again with another seed; if the answers differ, treat neither as the best.",
    ).model_dump()


__all__ = ["BEST_FOUND", "CERTIFICATE_REFUTED", "INFEASIBLE", "INFEASIBLE_OR_UNBOUNDED", "INPUT_INVALID",
           "INPUT_TOO_LARGE", "LOCAL_OPTIMUM", "MODEL_INVALID", "NOT_CHECKED", "NOT_CONVERGED", "NOT_PROVEN_OPTIMAL",
           "NO_SOLUTION", "Refusal", "SCIPY_UNAVAILABLE", "SOLVER_ANSWER", "SOLVER_ERROR", "SOLVER_UNAVAILABLE",
           "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "UNBOUNDED", "finish"]
