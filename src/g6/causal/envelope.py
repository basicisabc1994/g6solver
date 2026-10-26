# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.causal and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused before anything ran, DoWhy missing, out of time, the two engines disagreeing, a refuter or G6's
  own recomputation contradicting the estimate) is ``blocked-escalated``. The result keeps no answer and no estimate;
  a refuted estimate keeps its numbers in ``refutations``, ``recomputation`` and the evidence;
* a graph answer is ``qualified-draft``: ``G6_E_CAUSAL_GRAPH_ANSWER`` when DoWhy ran too and agreed,
  ``G6_E_CAUSAL_ONE_ENGINE`` when only the pure-Python algorithms ran, with how to add the second;
* an estimate is ``qualified-draft`` only when the engines agreed on the identification, every refuter that tested it
  passed and, for linear regression, G6's own recomputation matched: ``G6_E_CAUSAL_ESTIMATE``;
  ``G6_E_CAUSAL_REFUTATION_INCOMPLETE`` when a refuter did not test it (DoWhy could not run it on this estimate, or
  its change never reached the estimator), naming which; ``G6_E_CAUSAL_REFUTATION_OFF`` when the caller turned
  refutation off.

Every card says the answer rests on the causal graph, which data cannot confirm. Any warning DoWhy raised, other than
the two it raises on every call (pandas' copy-keyword deprecation and DoWhy's note on unobserved nodes), is listed on
the card. It never gives ``verified``: the result is re-validated through :class:`~g6.causal.schema.CausalResult`,
which has no such state.
"""
from __future__ import annotations

import math
from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.causal.schema import CausalResult

GRAPH_ANSWER = "G6_E_CAUSAL_GRAPH_ANSWER"
ONE_ENGINE = "G6_E_CAUSAL_ONE_ENGINE"
ESTIMATE = "G6_E_CAUSAL_ESTIMATE"
REFUTATION_OFF = "G6_E_CAUSAL_REFUTATION_OFF"
REFUTATION_INCOMPLETE = "G6_E_CAUSAL_REFUTATION_INCOMPLETE"
GRAPH_INVALID = "G6_E_CAUSAL_GRAPH_INVALID"
GRAPH_CYCLE = "G6_E_CAUSAL_GRAPH_CYCLE"
SELF_LOOP = "G6_E_CAUSAL_SELF_LOOP"
UNKNOWN_NODE = "G6_E_CAUSAL_UNKNOWN_NODE"
INPUT_INVALID = "G6_E_CAUSAL_INPUT_INVALID"
INPUT_TOO_LARGE = "G6_E_CAUSAL_INPUT_TOO_LARGE"
TIME_LIMIT_TOO_LONG = "G6_E_CAUSAL_TIME_LIMIT_TOO_LONG"
TIME_LIMIT = "G6_E_CAUSAL_TIME_LIMIT"
NOT_IDENTIFIABLE = "G6_E_CAUSAL_NOT_IDENTIFIABLE"
METHOD_NOT_APPLICABLE = "G6_E_CAUSAL_METHOD_NOT_APPLICABLE"
ENGINE_UNAVAILABLE = "G6_E_CAUSAL_ENGINE_UNAVAILABLE"
ENGINE_ERROR = "G6_E_CAUSAL_ENGINE_ERROR"
ENGINES_DISAGREE = "G6_E_CAUSAL_ENGINES_DISAGREE"
REFUTED = "G6_E_CAUSAL_REFUTED"
CERTIFICATE_REFUTED = "G6_E_CAUSAL_CERTIFICATE_REFUTED"

INSTALL = "pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'"
_ACTIONS = {
    GRAPH_INVALID: "Give the graph as JSON ({\"nodes\": [...], \"edges\": [[\"A\", \"B\"], ...], \"unobserved\": "
                   "[...]}) or as DOT (digraph { A -> B; U [observed=no]; }), correcting what the reason names.",
    GRAPH_CYCLE: "A causal graph has no cycles. Remove one edge of the cycle the reason names, or split the variable "
                 "into its value before and after.",
    SELF_LOOP: "Remove the edge from the node to itself: a variable is not its own cause in a causal graph.",
    UNKNOWN_NODE: "Use the node names the graph declares (check the spelling), or add the node to the graph.",
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Reduce the problem below the limit the reason names, or split it.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit within the cap the reason names.",
    TIME_LIMIT: "The work did not finish within the time limit. Raise time_limit, lower simulations, or use fewer "
                "rows.",
    NOT_IDENTIFIABLE: "The graph does not let data answer this question by the backdoor, frontdoor or "
                      "instrumental-variable criterion. Measure a variable that blocks the open path the reason "
                      "names, or find an instrument, and add it to the graph and the data.",
    METHOD_NOT_APPLICABLE: "Use a method that matches how the graph identifies the effect, as the reason says.",
    ENGINE_UNAVAILABLE: f"Install DoWhy: {INSTALL}. The graph ops (d_separated, backdoor_paths, adjustment_sets, "
                        "instruments, identify) answer without it.",
    ENGINE_ERROR: "The engine failed before answering; inspect the reason, and report it with the input if it recurs.",
    ENGINES_DISAGREE: "Do not use the answer: G6's pure-Python algorithms and DoWhy disagree. The evidence names "
                      "where; report it with the graph.",
    REFUTED: "Do not rely on the estimate: a refuter contradicts it. The evidence keeps the estimate and every "
             "refuter's numbers; look for the artefact (a few rows driving the effect, a model that does not fit).",
    CERTIFICATE_REFUTED: "Do not use the estimate: G6's own least-squares recomputation contradicts DoWhy's. The "
                         "evidence keeps both numbers; report it with the data.",
}
_GRAPH_RESTS = ("It holds only if the graph is right: which variables cause which, and which are unobserved. Data "
                "cannot confirm a causal graph, so the answer is not verified.")
_ESTIMATE_RESTS = ("The estimate rests on the graph's assumptions (which variables cause which, and that no "
                   "unobserved variable confounds what the identification needs), which data cannot confirm, so it "
                   "is not verified.")


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and math.isfinite(value) else None


def finish(operation: str, *, engines: list[dict[str, Any]] | None = None, code: str = "", reason: str = "",
           answer: dict[str, Any] | None = None, estimate: Any = None, confidence_interval: list[float] | None = None,
           refutations: list[dict[str, Any]] | None = None, recomputation: dict[str, Any] | None = None,
           agreement: dict[str, Any] | None = None, graph: dict[str, Any] | None = None,
           data: dict[str, Any] | None = None, method: str = "", refute: bool | None = None,
           tolerance: dict[str, Any] | None = None, one_engine_why: str = "",
           warnings_seen: list[dict[str, Any]] | None = None, dowhy_log: list[dict[str, Any]] | None = None,
           steps: list[dict[str, Any]] | None = None, elapsed_s: float = 0.0, request_id: str = "",
           run_id: str = "") -> CausalResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    engines = engines or []
    ran = [e["name"] for e in engines]
    refutations = refutations or []
    recomputation = recomputation or {}
    estimate = _finite(estimate)
    estimating = operation == "estimate_effect"
    if not code:
        failed = [r for r in refutations if r.get("tested") and not r.get("passed")]
        if agreement is not None and not agreement.get("agreed"):
            code = ENGINES_DISAGREE
            reason = ("G6's pure-Python algorithms and DoWhy disagree: "
                      + "; ".join(agreement.get("differences", [])[:3]))
        elif recomputation and not recomputation.get("matched"):
            code = CERTIFICATE_REFUTED
            reason = (f"G6's least-squares recomputation gives {recomputation.get('g6'):.6g}, DoWhy gave "
                      f"{recomputation.get('dowhy'):.6g}, a difference of {recomputation.get('difference'):.3g}, "
                      f"beyond the tolerance of {recomputation.get('tolerance'):.3g}")
        elif failed:
            code = REFUTED
            reason = "a refuter contradicts the estimate: " + "; ".join(r["verdict"] for r in failed)
        elif estimating and estimate is None:
            code, reason = ENGINE_ERROR, "DoWhy gave no finite estimate"
    refuted_estimate = None
    if code and estimate is not None:
        refuted_estimate = {"value": estimate, "confidence_interval": confidence_interval}
    if code:
        answer, estimate, confidence_interval = {}, None, None
    kind = ("causal_refusal" if code and not ran else "causal_check" if code
            else "causal_estimate" if estimating else "causal_answer")
    evidence: dict[str, Any] = {
        "kind": kind,
        "operation": operation,
        "engines": engines,
        "dowhy_ran": "dowhy" in ran,
        "graph": graph or {},
        "agreement": agreement or {},
        "elapsed_s": elapsed_s,
    }
    if estimating:
        evidence |= {"method": method, "data": data or {}, "refute": refute, "refutations": refutations,
                     "recomputation": recomputation, "tolerance": tolerance or {}, "steps": steps or []}
    if warnings_seen:
        evidence["warnings"] = warnings_seen
    if dowhy_log:
        evidence["dowhy_log"] = _deduplicated(dowhy_log)
    if refuted_estimate is not None:
        evidence["refuted_estimate"] = refuted_estimate
    if code:
        evidence |= {"code": code, "reason": reason}
    risks = [f"DoWhy raised {w['category']}: {w['message']}" for w in warnings_seen or []]
    if code:
        card = WarningCard(code=code, message=reason, severity="blocked", risks=risks,
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    elif estimating:
        card = _estimate_card(engines, method, refute, refutations, recomputation, risks)
    else:
        card = _graph_card(engines, agreement or {}, one_engine_why, risks)
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return CausalResult.model_validate({
        "operation": operation, "engines": ran, "answer": answer or {}, "estimate": estimate,
        "confidence_interval": confidence_interval, "refutations": refutations, "recomputation": recomputation,
        "agreement": agreement or {}, "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _deduplicated(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DoWhy's log warnings, each once with how often it was logged (a refuter logs the same note on every run)."""
    counted: dict[tuple[str, str, str], int] = {}
    for r in records:
        key = (r.get("level", ""), r.get("logger", ""), r.get("message", ""))
        counted[key] = counted.get(key, 0) + 1
    return [{"level": lv, "logger": lg, "message": m, "count": n} for (lv, lg, m), n in counted.items()][:50]


def _warned(risks: list[str]) -> str:
    if not risks:
        return ""
    return f" DoWhy also raised {len(risks)} warning{'s' if len(risks) != 1 else ''}, listed under risks."


def _graph_card(engines: list[dict[str, Any]], agreement: dict[str, Any], one_engine_why: str,
                risks: list[str]) -> dict[str, Any]:
    dowhy = next((e for e in engines if e["name"] == "dowhy"), None)
    if dowhy is not None:
        compared = "; ".join(agreement.get("compared", [])) or "the answer"
        return WarningCard(
            code=GRAPH_ANSWER, severity="amber", risks=risks,
            message=f"G6's exact pure-Python graph algorithms and DoWhy {dowhy.get('version', '')} agree ({compared})."
                    f" {_GRAPH_RESTS}{_warned(risks)}",
            suggested_action="Check that the graph's edges and its unobserved nodes say what you believe about the "
                             "world before relying on the answer.",
        ).model_dump()
    return WarningCard(
        code=ONE_ENGINE, severity="amber", risks=risks,
        message=f"Only G6's exact pure-Python graph algorithms ran ({one_engine_why or 'DoWhy did not run'}), so no "
                f"second engine confirmed the answer. {_GRAPH_RESTS}",
        suggested_action=f"Add DoWhy as the second engine with {INSTALL} and run it again, or use the answer knowing "
                         "one engine gave it.",
    ).model_dump()


def _estimate_card(engines: list[dict[str, Any]], method: str, refute: bool | None,
                   refutations: list[dict[str, Any]], recomputation: dict[str, Any],
                   risks: list[str]) -> dict[str, Any]:
    dowhy = next((e for e in engines if e["name"] == "dowhy"), {})
    done = [f"DoWhy {dowhy.get('version', '')} estimated it by {method}, after G6's pure-Python identification and "
            "DoWhy's agreed"]
    if recomputation:
        done.append(f"G6's own least-squares fit matched DoWhy's estimate within {recomputation['tolerance']:.3g}")
    else:
        done.append("G6 did not recompute it: for this method the number rests on DoWhy's estimator")
    tested = [r["refuter"] for r in refutations if r.get("tested")]
    untested = [r for r in refutations if not r.get("tested")]
    if refute:
        if tested:
            done.append(f"the refuters that tested it ({', '.join(tested)}) passed G6's tolerance")
        code, action = ESTIMATE, ("Check that the graph says what you believe about the world, and that its "
                                  "unobserved nodes are all marked, before relying on the estimate.")
        if untested:
            names = ", ".join(r["refuter"] for r in untested)
            done.append(f"{'no refuter' if not tested else 'not every refuter'} tested it: {names} did not, as "
                        "the risks say")
            risks = [*risks, *(r["verdict"] for r in untested)]
            code, action = REFUTATION_INCOMPLETE, ("Rely on the estimate only as far as the refuters that tested it "
                                                   "support it, or estimate it by another method they can test.")
    else:
        done.append("refutation was turned off by the caller, so no refuter tried to knock the estimate down")
        code, action = REFUTATION_OFF, "Run it again with refute=true before relying on the estimate."
    return WarningCard(
        code=code, severity="amber", risks=risks,
        message="; ".join(done) + f". {_ESTIMATE_RESTS}{_warned(risks)}",
        suggested_action=action,
    ).model_dump()


__all__ = ["CERTIFICATE_REFUTED", "ENGINES_DISAGREE", "ENGINE_ERROR", "ENGINE_UNAVAILABLE", "ESTIMATE", "GRAPH_ANSWER",
           "GRAPH_CYCLE", "GRAPH_INVALID", "INPUT_INVALID", "INPUT_TOO_LARGE", "INSTALL", "METHOD_NOT_APPLICABLE",
           "NOT_IDENTIFIABLE", "ONE_ENGINE", "REFUTATION_INCOMPLETE", "REFUTATION_OFF", "REFUTED", "Refusal",
           "SELF_LOOP", "TIME_LIMIT",
           "TIME_LIMIT_TOO_LONG", "UNKNOWN_NODE", "finish"]
