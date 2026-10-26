# SPDX-License-Identifier: Apache-2.0
"""``solve`` (one typed satisfiability query to either backend) and ``solver_support`` (what works here).

``solve`` is the archive's legacy ``FormalMethodsBlock`` dispatch reduced to its two decision procedures (audit 017
§11). The archive reached SWI-Prolog and Lean through ``solver=``, ``backend=`` and legacy ``op=`` names, round its
own deny-list (FM-002); here any field naming another engine is refused as ``G6_E_FORMAL_BACKEND_REFUSED`` before
anything runs.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from g6 import __version__
from g6.formal.envelope import BACKEND_REFUSED, BACKENDS, INPUT_INVALID, Refusal, apply_envelope
from g6.formal.propositional import check_propositional
from g6.formal.schema import FormalResult, LogicalQuery
from g6.formal.z3_bridge import check_sat, run_query, z3_version

#: Fields whose rejection means another engine or operation was asked for, not a typo.
_ENGINE_FIELDS = ("backend", "engine", "solver", "operation", "op")


def solve(query: LogicalQuery | Mapping[str, Any]) -> FormalResult:
    """Answer one :class:`LogicalQuery` (or a mapping of its fields) with ``z3`` or the ``propositional`` DPLL."""
    if not isinstance(query, LogicalQuery):
        raw = dict(query) if isinstance(query, Mapping) else {}
        try:
            if not isinstance(query, Mapping):
                raise TypeError(f"a query is a LogicalQuery or a mapping, not {type(query).__name__}")
            query = LogicalQuery.model_validate(raw)
        except (TypeError, ValidationError) as exc:
            return _refused(raw, exc)
    if query.solver == "propositional":
        return check_propositional(query.formula, query.variables, query.assertions, request_id=query.request_id,
                                   run_id=query.run_id)
    return check_sat(query.formula, query.variables, query.assertions, timeout=query.timeout_sec,
                     request_id=query.request_id, run_id=query.run_id)


def _refused(raw: dict[str, Any], exc: Exception) -> FormalResult:
    fields = [str((e.get("loc") or ("",))[0]) for e in exc.errors()] if isinstance(exc, ValidationError) else []
    engine = [f for f in _ENGINE_FIELDS if f in fields]
    if engine:
        code = BACKEND_REFUSED
        reason = ", ".join(f"{f}={raw.get(f)!r}" for f in engine) + (
            " is refused: g6.formal runs only z3 and the propositional DPLL, and no code-executing solver")
    else:
        code = INPUT_INVALID
        reason = "; ".join(f"{f or 'input'}: {e['msg']}" for f, e in zip(fields, exc.errors(), strict=True)) \
            if isinstance(exc, ValidationError) else str(exc)
    # The evidence names the engine that was asked for; apply_envelope refuses any name outside BACKENDS.
    named = [raw[f] for f in ("backend", "engine", "solver") if isinstance(raw.get(f), str) and raw[f]]
    backend = next((n for n in named if n not in BACKENDS), named[0] if named else "z3")
    ids = {k: raw[k] if isinstance(raw.get(k), str) else "" for k in ("request_id", "run_id")}
    return apply_envelope(FormalResult(operation="solve", backend=backend, code=code, reason=reason),
                          backend=backend, operation="solve", **ids)


def solver_support() -> list[dict[str, Any]]:
    """Each backend, what it is, and whether it works here.

    Z3 is probed for real: imported and asked one trivial query. The DPLL is pure Python, always present, and labelled
    as such, never as an external engine (audit 017 FM-013: the archive reported stand-ins as backend execution).
    """
    z3_row: dict[str, Any] = {"backend": "z3", "engine": BACKENDS["z3"], "kind": "smt_solver", "pure_python": False,
                              "probe": "import z3, then decide (assert true)"}
    try:
        answer = run_query("(assert true)\n(check-sat)", 5.0)[0]
        z3_row |= {"available": answer == "sat", "version": z3_version(),
                   "reason": "" if answer == "sat" else f"z3 answered {answer!r} on a trivial query"}
    except Refusal as refusal:
        z3_row |= {"available": False, "version": "", "reason": refusal.reason}
    dpll_row = {"backend": "propositional", "engine": BACKENDS["propositional"], "kind": "sat_solver",
                "pure_python": True, "probe": "none: it is part of g6.formal", "available": True,
                "version": f"g6 {__version__}", "reason": ""}
    belnap_row = {**dpll_row, "backend": "four_valued", "engine": BACKENDS["four_valued"], "kind": "evaluator"}
    return [z3_row, dpll_row, belnap_row]


__all__ = ["solve", "solver_support"]
