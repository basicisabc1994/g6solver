# SPDX-License-Identifier: Apache-2.0
"""``agent_to_smt`` and ``check_z3``: does an LTL property hold on every path of an agent's model? (optional)

audit 012, CSF-001. The archive's ``agent_to_smt`` never read its ``property`` argument: it encoded the phi_A union
bound as a QF_LRA recurrence, labelled the answer with the caller's LTL formula, and an explicit model could then
become ``completion_state="verified"`` for a formula nobody checked (``G(!s1)`` on a model that reaches ``s1`` at step
1 came back verified). Here the property is the encoding, or there is no check:

* the question is: does the formula hold, read by :func:`g6.csf.ltl.evaluate_ltl`, on every path of the model with
  positive probability over steps 0..N? Z3 looks for a path on which it fails;
* an atom names a state of the agent and is true exactly when the agent is in that state. A formula with an atom that
  names no state is refused with ``G6_E_CSF_PROPERTY_NOT_ENCODABLE``, and one that does not parse with
  ``G6_E_CSF_LTL_PARSE_ERROR``: it is never replaced by another property;
* every successor with positive probability is a branch (CSF-009: the archive's SMV and Lean encodings kept only the
  most probable successor, which drops exactly the rare transition into an unsafe state a safety check is for);
* a counterexample Z3 returns is re-read: it must be a path of the model on which ``evaluate_ltl`` finds the formula
  false, or the result is ``G6_E_CSF_ENCODING_MISMATCH``.

The phi_A union bound is not re-encoded here: :func:`g6.csf.reachability_bound` computes it exactly, and the archive's
Z3 copy of it added no information.

Results: ``holds=True`` is ``qualified-draft`` (a solver decision on the caller's model, not a proof a person
inspected, and not a measurement of the agent); a counterexample, a refusal or no decision is ``blocked-escalated``.
Z3 runs through ``g6.formal.check_sat`` and is imported only then; without it the result is ``blocked-escalated`` with
``G6_E_FORMAL_SOLVER_UNAVAILABLE``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from g6.csf.ltl import (
    LTLAlways,
    LTLAnd,
    LTLAtom,
    LTLEventually,
    LTLFormula,
    LTLNext,
    LTLNot,
    LTLOr,
    LTLRelease,
    LTLUntil,
    _nodes,
    _past_end,
    evaluate_ltl,
    ltl_to_str,
    parse_ltl,
)
from g6.csf.schema import MODEL_INVALID, BoundedAgent, CSFError, check_id, labelled
from g6.formal import check_sat

PROPERTY_NOT_ENCODABLE = "G6_E_CSF_PROPERTY_NOT_ENCODABLE"
LTL_PARSE_ERROR = "G6_E_CSF_LTL_PARSE_ERROR"
PROPERTY_VIOLATED = "G6_E_CSF_PROPERTY_VIOLATED"
SOLVER_DECISION = "G6_E_CSF_SOLVER_DECISION"
ENCODING_MISMATCH = "G6_E_CSF_ENCODING_MISMATCH"

#: What a decision depends on; carried in the evidence of every check.
ASSUMPTIONS = (
    "The model's states and transitions are the caller's; nothing in g6.csf measured them.",
    "The property is read on finite traces of steps 0..N, as g6.csf.ltl.evaluate_ltl reads it.",
    "An atom names a state of the agent and is true exactly when the agent is in that state.",
    "Every successor with positive probability is a possible branch; probabilities are not otherwise used.",
    "Holding on the model says nothing about the agent outside the model or beyond N steps.",
)


@dataclass(frozen=True)
class SMTEncoding:
    """The property and the model as Z3 terms: ``goal`` is satisfiable exactly when some path of the model violates
    ``property``. ``x_t`` is the index (in ``states``) of the state at step t; ``f<k>_<t>`` is subformula k at t."""

    property: str
    formula: LTLFormula
    horizon: int
    states: tuple[str, ...]
    variables: dict[str, str]
    assertions: tuple[str, ...]
    goal: str


def _formula(prop: Any) -> LTLFormula:
    if isinstance(prop, str):
        try:
            return parse_ltl(prop)
        except ValueError as exc:
            raise CSFError(LTL_PARSE_ERROR, f"the property does not parse: {exc}") from None
    try:
        _nodes(prop)
    except TypeError:
        raise CSFError(PROPERTY_NOT_ENCODABLE, f"the property must be an LTL formula or its text, not "
                                               f"{type(prop).__name__}") from None
    return prop


def _support(agent: BoundedAgent, state: str) -> tuple[str, ...]:
    """Every successor of ``state`` with positive probability (audit 012, CSF-009: not only the most probable)."""
    return tuple(outcome.next_state for outcome in agent.kernel[state])


def _either(terms: list[str]) -> str:
    return terms[0] if len(terms) == 1 else "(or " + " ".join(terms) + ")"


def agent_to_smt(agent: BoundedAgent, property: str | LTLFormula) -> SMTEncoding:
    """Encode ``property`` (an LTL formula or its text) against ``agent``'s model over steps 0..N, or raise
    :class:`~g6.csf.schema.CSFError` (``G6_E_CSF_LTL_PARSE_ERROR``, ``G6_E_CSF_PROPERTY_NOT_ENCODABLE``)."""
    if not isinstance(agent, BoundedAgent):
        raise CSFError(MODEL_INVALID, f"agent must be a BoundedAgent, got {type(agent).__name__}")
    formula = _formula(property)
    order = _nodes(formula)
    atoms = {node.predicate for node in order if isinstance(node, LTLAtom)}
    unknown = sorted(atoms - set(agent.states))
    if unknown:
        # audit 012, CSF-001: a property that cannot be encoded is refused, never swapped for another one.
        raise CSFError(PROPERTY_NOT_ENCODABLE, f"atom(s) {', '.join(map(repr, unknown))} name no state of the agent; "
                                               "an atom is true exactly when the agent is in the state it names")
    index = {state: i for i, state in enumerate(agent.states)}
    positions = agent.resources.N + 1
    variables = {f"x_{t}": "Int" for t in range(positions)}
    assertions = [f"(= x_0 {index[agent.initial_state]})"]
    for t in range(positions - 1):
        for state in agent.states:
            successors = [f"(= x_{t + 1} {index[nxt]})" for nxt in _support(agent, state)]
            assertions.append(f"(=> (= x_{t} {index[state]}) {_either(successors)})")
    ids = {node: k for k, node in enumerate(order)}
    past = _past_end(order)

    def ref(node: LTLFormula, t: int) -> str:
        if t < positions:
            return f"f{ids[node]}_{t}"
        return "true" if past[node] else "false"

    for node in order:
        for t in range(positions):
            variables[f"f{ids[node]}_{t}"] = "Bool"
            assertions.append(f"(= f{ids[node]}_{t} {_term(node, t, ref, index)})")
    return SMTEncoding(property=ltl_to_str(formula), formula=formula, horizon=agent.resources.N, states=agent.states,
                       variables=variables, assertions=tuple(assertions), goal=f"(not {ref(formula, 0)})")


def _term(node: LTLFormula, t: int, ref: Callable[[LTLFormula, int], str], index: dict[str, int]) -> str:
    """Subformula ``node`` at step ``t``, by the same recursion :func:`g6.csf.ltl.evaluate_ltl` uses."""
    if isinstance(node, LTLAtom):
        return f"(= x_{t} {index[node.predicate]})"
    if isinstance(node, LTLNot):
        return f"(not {ref(node.inner, t)})"
    if isinstance(node, LTLAnd):
        return f"(and {ref(node.left, t)} {ref(node.right, t)})"
    if isinstance(node, LTLOr):
        return f"(or {ref(node.left, t)} {ref(node.right, t)})"
    if isinstance(node, LTLNext):
        return ref(node.inner, t + 1)
    if isinstance(node, LTLAlways):
        return f"(and {ref(node.inner, t)} {ref(node, t + 1)})"
    if isinstance(node, LTLEventually):
        return f"(or {ref(node.inner, t)} {ref(node, t + 1)})"
    if isinstance(node, LTLUntil):
        return f"(or {ref(node.right, t)} (and {ref(node.left, t)} {ref(node, t + 1)}))"
    if isinstance(node, LTLRelease):
        return f"(and {ref(node.right, t)} (or {ref(node.left, t)} {ref(node, t + 1)}))"
    raise TypeError(f"not an LTL formula: {type(node).__name__}")


@dataclass(frozen=True)
class SMTCheck:
    """Whether ``property`` holds on every path of the model over ``horizon`` steps.

    ``holds`` is ``None`` when there was no decision (``code`` and ``reason`` say why). When it is ``False``,
    ``counterexample`` is a path of the model on which the property fails, with its probability under the model.
    """

    property: str
    horizon: int
    holds: bool | None
    counterexample: tuple[str, ...] = ()
    counterexample_probability: float | None = None
    code: str = ""
    reason: str = ""
    model: dict[str, Any] = field(default_factory=dict)
    solver_evidence: tuple[dict[str, Any], ...] = ()
    request_id: str = ""
    run_id: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.holds is True and not self.code else "blocked-escalated"

    def to_envelope(self) -> dict[str, Any]:
        """The result as a library envelope with a ``G6_E_*`` warning card."""
        if self.completion_state == "qualified-draft":
            code, action = SOLVER_DECISION, "Check that the model's transitions are the agent's before relying on it."
            message = (f"Z3 found no path of the model, over steps 0..{self.horizon}, on which {self.property} fails. "
                       "A solver decision on the model supplied, not a proof a person inspected and not a measurement "
                       "of the agent, so it is not verified.")
        elif self.holds is False:
            code, action = self.code, "Do not rely on the property: change the plan or the model, or escalate."
            message = self.reason
        else:
            code, action = self.code, "Correct the input or install what is missing, then check again."
            message = self.reason
        evidence = {"kind": "csf_bounded_ltl_check", "property": self.property, "horizon": self.horizon,
                    "holds": self.holds, "counterexample": list(self.counterexample),
                    "counterexample_probability": self.counterexample_probability, "model": self.model,
                    "assumptions": list(ASSUMPTIONS), "solver": list(self.solver_evidence)}
        return labelled(self, state=self.completion_state, code=code, message=message, action=action,
                        evidence=[evidence])


def _path(model: dict[str, Any], encoding: SMTEncoding) -> tuple[str, ...] | None:
    path = []
    for t in range(encoding.horizon + 1):
        i = model.get(f"x_{t}")
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(encoding.states):
            return None
        path.append(encoding.states[i])
    return tuple(path)


def _path_probability(agent: BoundedAgent, path: tuple[str, ...]) -> float | None:
    """The path's probability under the model, or ``None`` when it is not a path of the model."""
    if not path or path[0] != agent.initial_state:
        return None
    probability = 1.0
    for here, nxt in zip(path, path[1:]):
        step = next((o.probability for o in agent.kernel[here] if o.next_state == nxt), None)
        if step is None:
            return None
        probability *= step
    return probability


def check_z3(agent: BoundedAgent, property: str | LTLFormula, *, timeout: float = 10.0, request_id: str = "",
             run_id: str = "") -> SMTCheck:
    """Ask Z3 for a path of ``agent``'s model, over steps 0..N, on which ``property`` fails. Never ``verified``."""
    ids = {"request_id": check_id(request_id, "request_id"), "run_id": check_id(run_id, "run_id")}
    model = agent.describe() if isinstance(agent, BoundedAgent) else {}
    horizon = agent.resources.N if isinstance(agent, BoundedAgent) else 0
    try:
        encoding = agent_to_smt(agent, property)
    except CSFError as refusal:
        return SMTCheck(property=_shown(property), horizon=horizon, holds=None, code=refusal.code,
                        reason=refusal.reason, model=model, **ids)
    decided = check_sat(encoding.goal, encoding.variables, encoding.assertions, timeout=timeout, **ids)
    common = {"property": encoding.property, "horizon": horizon, "model": model,
              "solver_evidence": tuple(decided.evidence), **ids}
    if decided.code:
        return SMTCheck(holds=None, code=decided.code, reason=decided.reason, **common)
    if decided.satisfiable is False:
        return SMTCheck(holds=True, **common)
    path = _path(decided.model, encoding)
    probability = _path_probability(agent, path) if path else None
    if path is None or probability is None or evaluate_ltl(encoding.formula, [{state: True} for state in path]):
        return SMTCheck(holds=None, code=ENCODING_MISMATCH,
                        reason="Z3 returned a model that is not a path of the agent on which the property fails; "
                               "the encoding and the evaluator disagree, so there is no decision", **common)
    return SMTCheck(holds=False, counterexample=path, counterexample_probability=probability, code=PROPERTY_VIOLATED,
                    reason=f"{encoding.property} fails on the path {' -> '.join(path)}, which has probability "
                           f"{probability:.6g} under the model", **common)


def _shown(prop: Any) -> str:
    """The property as given, for a result that could not encode it."""
    if isinstance(prop, str):
        return prop
    try:
        return ltl_to_str(prop)
    except (TypeError, RecursionError):
        return f"<{type(prop).__name__}>"


__all__ = ["ASSUMPTIONS", "ENCODING_MISMATCH", "LTL_PARSE_ERROR", "PROPERTY_NOT_ENCODABLE", "PROPERTY_VIOLATED",
           "SOLVER_DECISION", "SMTCheck", "SMTEncoding", "agent_to_smt", "check_z3"]
