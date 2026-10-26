# SPDX-License-Identifier: Apache-2.0
"""``apply_envelope``: the one place a g6.formal result is labelled (audit 017 §4 item 1, kept; FM-001, FM-002, FM-015).

Every public function returns through it. It decides the label from the result alone:

* a result with a ``code`` (refused, unavailable, unreadable, undecided, vacuous, circular, refuted by its own
  certificate, no optimum, an unfit modal frame) is ``blocked-escalated``, and so is a four-valued claim that comes out
  false (refuted) or both (a conflict);
* a recorded decision is ``qualified-draft``, with a ``G6_E_FORMAL_SOLVER_DECISION`` card saying it is a solver
  decision on the caller's encoding.

It never gives ``verified``. The archive's envelope gave ``verified`` to a Lean file that elaborated and carried a
self-consistent hash (FM-001); R3 ``verified`` needs a proof explained to and inspected by a person, which this package
does not do. Every result leaves with ``completion_state``, ``warning_card``, ``evidence``, ``request_id`` and
``run_id`` (FM-015: the archive's skill MCP tools returned none of them).
"""
from __future__ import annotations

from typing import Any, TypeVar

from g6.core.reliability_envelope import WarningCard
from g6.formal.schema import FormalResult, FourValuedResult, ProofArtifact, ValidityResult, _Labelled

#: The only backends this package runs, and what each one is. Any other name is refused (FM-002), and the pure-Python
#: paths say so in their evidence rather than passing for an engine (FM-013).
BACKENDS = {
    "z3": "Z3 SMT solver (the z3-solver package)",
    "propositional": "pure-Python DPLL in g6.formal.propositional (no external solver)",
    "four_valued": "pure-Python four-valued (Belnap) logic in g6.formal.four_valued (no external solver)",
}
_PURE_PYTHON = ("propositional", "four_valued")

SOLVER_DECISION = "G6_E_FORMAL_SOLVER_DECISION"
BACKEND_REFUSED = "G6_E_FORMAL_BACKEND_REFUSED"
SOLVER_UNAVAILABLE = "G6_E_FORMAL_SOLVER_UNAVAILABLE"
PARSE_ERROR = "G6_E_FORMAL_PARSE_ERROR"
UNKNOWN_SORT = "G6_E_FORMAL_UNKNOWN_SORT"
INPUT_INVALID = "G6_E_FORMAL_INPUT_INVALID"
INPUT_TOO_LARGE = "G6_E_FORMAL_INPUT_TOO_LARGE"
SOLVER_TIMEOUT = "G6_E_FORMAL_SOLVER_TIMEOUT"
SOLVER_UNKNOWN = "G6_E_FORMAL_SOLVER_UNKNOWN"
SOLVER_ERROR = "G6_E_FORMAL_SOLVER_ERROR"
VACUOUS_PREMISES = "G6_E_FORMAL_VACUOUS_PREMISES"
VACUOUS_PROPERTY = "G6_E_FORMAL_VACUOUS_PROPERTY"
CONCLUSION_ASSUMED = "G6_E_FORMAL_CONCLUSION_ASSUMED"
NO_DECISION = "G6_E_FORMAL_NO_DECISION"
CERTIFICATE_REFUTED = "G6_E_FORMAL_CERTIFICATE_REFUTED"
NO_OPTIMUM = "G6_E_FORMAL_NO_OPTIMUM"
CLAIM_REFUTED = "G6_E_FORMAL_CLAIM_REFUTED"
CLAIM_CONFLICT = "G6_E_FORMAL_CLAIM_CONFLICT"
CLAIM_UNKNOWN = "G6_E_FORMAL_CLAIM_UNKNOWN"
FRAME_UNFIT = "G6_E_FORMAL_FRAME_UNFIT"

_ACTIONS = {
    BACKEND_REFUSED: "Use backend 'z3', 'propositional' or 'four_valued'. Code-executing solvers (Prolog, Lean, TLA+, "
                     "run_code) are not part of g6.formal.",
    SOLVER_UNAVAILABLE: "Install z3-solver (pip install 'g6[formal]'), or use solver='propositional' for Boolean "
                        "formulas.",
    PARSE_ERROR: "Write each formula, premise and conclusion as one SMT-LIB term; declare every symbol with its sort.",
    UNKNOWN_SORT: "Give each symbol the sort Int, Real, Bool, String, (_ BitVec n) or (Array S T).",
    INPUT_INVALID: "Correct the input and run the check again.",
    INPUT_TOO_LARGE: "Split or simplify the formula.",
    SOLVER_TIMEOUT: "Raise the timeout or simplify the query. Unknown is never read as a decision.",
    SOLVER_UNKNOWN: "Simplify the query (for example, avoid non-linear arithmetic). Unknown is never a decision.",
    SOLVER_ERROR: "Inspect the reason; the solver failed before deciding.",
    VACUOUS_PREMISES: "Fix the premises so they are consistent: inconsistent premises entail every conclusion.",
    VACUOUS_PROPERTY: "State a property that depends on the premises: one that holds without them says nothing about "
                      "them.",
    CONCLUSION_ASSUMED: "Take the conclusion out of the premises: assuming a claim does not support it.",
    NO_DECISION: "Build results with g6.formal's functions; this one records no solver decision.",
    CERTIFICATE_REFUTED: "Do not use the answer: its own check on a fresh solver contradicts it. Report it with the "
                         "evidence; the counterexample shows where it fails.",
    NO_OPTIMUM: "Bound the objective (add constraints or bounds), or use a non-strict inequality: an unbounded or "
                "unattained objective has no optimum to report.",
    CLAIM_REFUTED: "The claim comes out false on the values given; revise it or the evidence behind them.",
    CLAIM_CONFLICT: "The claim has evidence both for and against it; trace the conflict to its cause before using it.",
    CLAIM_UNKNOWN: "Nothing supports or refutes the claim yet; gather evidence for the atoms it rests on before using it.",
    FRAME_UNFIT: "Fix the frame (every world needs the access the modal reading assumes) or choose the reading it "
                 "fits; a necessity at a world that sees no other holds vacuously.",
}
#: A four-valued claim that is not supported is not usable as it stands. The archive's qa_logic blocked F and B and let
#: N through as a draft; here N blocks too, so a claim with no evidence is never read as an answer (2026-10-20).
_CLAIM_CODES = {
    "F": (CLAIM_REFUTED, "the claim comes out F (false): the values given refute it"),
    "B": (CLAIM_CONFLICT, "the claim comes out B (both): there is evidence for and against it, an unresolved conflict"),
    "N": (CLAIM_UNKNOWN, "the claim comes out N (neither): no evidence supports or refutes it yet"),
}


class Refusal(Exception):
    """A check that cannot produce a usable decision, with its stable code and the reason in words."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


R = TypeVar("R", bound=_Labelled)
_BASE_FIELDS = set(FormalResult.model_fields) | set(ValidityResult.model_fields)


def _dropped(result: _Labelled) -> dict[str, Any]:
    """The claim a refused result loses: its decision fields, and every field its type adds, back at their defaults."""
    base = ({"valid": False, "counterexample": None} if isinstance(result, ValidityResult)
            else {"satisfiable": None, "model": {}} if isinstance(result, FormalResult) else {})
    return base | {name: field.get_default(call_default_factory=True)
                   for name, field in type(result).model_fields.items() if name not in _BASE_FIELDS}


def apply_envelope(result: R, *, backend: str, operation: str, request_id: str = "", run_id: str = "") -> R:
    """``result`` labelled: its state, warning card, evidence and ids set from what it records. Never ``verified``."""
    update: dict[str, Any] = {"backend": backend, "operation": operation}
    code, reason = result.code, result.reason
    artifact = result.artifact
    if backend not in BACKENDS:
        # audit 017 FM-002: no backend name reaches a code-executing solver, and a decision claimed for one is dropped.
        if code != BACKEND_REFUSED or not reason:
            reason = (f"{backend!r} is not a g6.formal backend; only {', '.join(sorted(BACKENDS))} run here, and no "
                      "code-executing solver (Prolog, Lean, TLA+, run_code) is reachable")
        code = BACKEND_REFUSED
        artifact = None
        update |= _dropped(result)
    elif isinstance(result, FourValuedResult):
        # Only the four-valued evaluator's own label carries its computation (FM-013); an unsupported claim blocks.
        if not code and not (result.computed and backend == "four_valued"):
            code, reason = NO_DECISION, f"the result records no four-valued evaluation by g6.formal for {backend!r}"
        elif not code and result.value in _CLAIM_CODES:
            code, reason = _CLAIM_CODES[result.value]
    elif isinstance(result, ValidityResult) and result.vacuous:
        # audit 017 FM-014: unsatisfiable premises make a validity result vacuous, whoever built the result.
        update["valid"] = False
        code = code or VACUOUS_PREMISES
        reason = reason or "the premises are unsatisfiable, so they entail every conclusion: the result is vacuous"
    elif isinstance(result, ValidityResult) and result.conclusion_assumed and not code:
        code, reason = CONCLUSION_ASSUMED, "the conclusion is among the premises, so the check is circular"
    if not code and not isinstance(result, FourValuedResult):
        if artifact is None or (isinstance(result, FormalResult) and result.satisfiable is None):
            code, reason = NO_DECISION, "the result records no solver decision"
        elif artifact.solver != backend:
            # audit 017 FM-013: the label names the engine that decided, so a decision cannot be relabelled.
            code, reason = NO_DECISION, f"the recorded decision was made by {artifact.solver!r}, not {backend!r}"
    update |= {
        "code": code,
        "reason": reason,
        "artifact": artifact,
        "completion_state": "blocked-escalated" if code else "qualified-draft",
        "warning_card": _card(code, reason, backend, artifact, "" if code else result.note()),
        "evidence": [_evidence(result, backend, operation, artifact, code, reason)],
        "request_id": request_id or result.request_id,
        "run_id": run_id or result.run_id,
    }
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return type(result).model_validate({**result.model_dump(), **update})


def _card(code: str, reason: str, backend: str, artifact: ProofArtifact | None, note: str = "") -> dict[str, Any]:
    if code:
        return WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    if artifact is not None and artifact.solver == "z3":
        who = f"z3 {artifact.solver_version} decided this on the exact query in the evidence."
    elif backend == "four_valued":
        who = ("The pure-Python four-valued evaluator in g6.formal computed this from the values in the evidence; no "
               "external solver ran.")
    else:
        who = ("The pure-Python DPLL in g6.formal decided this on the exact query in the evidence; no external "
               "solver ran.")
    return WarningCard(
        code=SOLVER_DECISION, severity="amber",
        message=f"{who} {note + ' ' if note else ''}A solver decision on your encoding is not a proof a person has "
                "inspected, so it is not verified.",
        suggested_action="Check that the sorts, premises and conclusion say what you mean before relying on it.",
    ).model_dump()


def _evidence(result: _Labelled, backend: str, operation: str, artifact: ProofArtifact | None,
              code: str, reason: str) -> dict[str, Any]:
    recorded = artifact is not None or (isinstance(result, FourValuedResult) and result.computed
                                        and backend == "four_valued")
    entry: dict[str, Any] = {
        "kind": "formal_decision" if recorded and not code else "formal_refusal" if not recorded else "formal_check",
        "backend": backend,
        "operation": operation,
        "engine": BACKENDS.get(backend, "none: refused"),
        # What ran is read from the recorded decision, not from the label asked for (FM-013).
        "pure_python": (artifact.solver if artifact else backend) in _PURE_PYTHON,
        "external_solver_ran": artifact is not None and artifact.solver == "z3",
        "elapsed_s": result.elapsed_s,
    }
    if artifact is not None:
        entry |= {k: v for k, v in artifact.model_dump(mode="json").items() if k != "operation"}
    if isinstance(result, ValidityResult):
        entry |= {"vacuous": result.vacuous, "conclusion_assumed": result.conclusion_assumed}
    if recorded and (certificate := result.certificate()):
        entry["certificate"] = certificate
    if code:
        entry |= {"code": code, "reason": reason}
    return entry


__all__ = ["BACKENDS", "BACKEND_REFUSED", "CERTIFICATE_REFUTED", "CLAIM_CONFLICT", "CLAIM_REFUTED", "CLAIM_UNKNOWN",
           "CONCLUSION_ASSUMED",
           "FRAME_UNFIT", "INPUT_INVALID", "INPUT_TOO_LARGE", "NO_DECISION", "NO_OPTIMUM", "PARSE_ERROR", "Refusal",
           "SOLVER_DECISION", "SOLVER_ERROR", "SOLVER_TIMEOUT", "SOLVER_UNAVAILABLE", "SOLVER_UNKNOWN", "UNKNOWN_SORT",
           "VACUOUS_PREMISES", "VACUOUS_PROPERTY", "apply_envelope"]
