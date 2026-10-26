# SPDX-License-Identifier: Apache-2.0
"""Contracts aggregate G6's own property checks, never caller truth labels."""
from __future__ import annotations

import math
from time import monotonic

from g6.formal.envelope import INPUT_INVALID, SOLVER_TIMEOUT, VACUOUS_PROPERTY, Refusal, apply_envelope
from g6.formal.four_valued import B, F, N, T
from g6.formal.schema import FourValuedResult
from g6.formal.z3_bridge import MAX_TIMEOUT_S, check_sat, check_validity

#: With less than this left (per query), no further solver query starts: Z3's timeout is in whole milliseconds.
_MIN_QUERY_S = 0.001


class ContractResult(FourValuedResult):
    def note(self) -> str:
        return "Each property was checked by G6 with Z3; the aggregate is a Belnap conjunction."


def check_contract(properties, *, premises=(), sorts=None, require_property: bool = True,
                   timeout: float = 10.0, request_id: str = "", run_id: str = "") -> ContractResult:
    """Check up to 32 SMT properties, mandatory non-vacuity, and joint consistency, within one deadline.

    T requires all property checks to support the contract. A counterexample is F;
    an unavailable or vacuous check is N. With premises, a property that holds is
    checked again without them, and one that holds alone is N
    (``G6_E_FORMAL_VACUOUS_PROPERTY``): it says nothing about the premises.
    Jointly inconsistent property formulas are surfaced as B (a specification
    conflict), distinct from one false claim. ``timeout`` bounds the whole call:
    each check gets the time that remains (a validity check, which can run two
    queries, half of it), and running out is N with ``G6_E_FORMAL_SOLVER_TIMEOUT``.
    No input supplies a proof, solver answer, confidence or truth label.
    """
    pieces, alone = [], {}
    consistency = None
    fields = {"value": "N", "computed": False}
    try:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_TIMEOUT_S):
            raise Refusal(INPUT_INVALID, f"timeout must be a number of seconds in (0, {MAX_TIMEOUT_S:g}]")
        deadline = monotonic() + timeout

        def left(queries: int = 1) -> float:
            rest = deadline - monotonic()
            if rest < _MIN_QUERY_S * queries:
                raise Refusal(SOLVER_TIMEOUT, f"the contract's deadline of {timeout:g} s ran out after {len(pieces)} "
                                              "property checks; unknown is never a decision")
            return rest / queries

        if not isinstance(properties, (list, tuple)) or len(properties) > 32:
            raise ValueError("properties must be a list of at most 32 SMT terms")
        if not properties:
            fields.update(code="G6_E_FORMAL_NO_PROPERTY" if require_property else "G6_E_FORMAL_CLAIM_UNKNOWN",
                          reason="no property supplied; no contract was checked")
        elif not all(isinstance(p, str) and p.strip() and len(p) <= 200000 for p in properties):
            raise ValueError("every property must be a non-empty SMT term of at most 200000 characters")
        else:
            listed = []
            fields["inputs"] = {"properties": listed}
            for i, prop in enumerate(properties):
                pieces.append(check_validity(premises, prop, sorts=sorts, timeout=left(2)))
                listed.append({"formula": prop, "result": pieces[-1].model_dump(mode="json")})
                if premises and pieces[-1].valid and not pieces[-1].code:
                    alone[i] = check_validity((), prop, sorts=sorts, timeout=left(2))
                    listed[-1]["without_premises"] = alone[i].model_dump(mode="json")
            failed = next((p for p in [*pieces, *alone.values()] if p.code), None)
            if failed:
                fields.update(code=failed.code, reason=failed.reason)
            vacuous = [properties[i] for i, check in alone.items() if check.valid and not check.code]

            def decision(i, piece):
                if piece.code or not (piece.valid or piece.counterexample is not None):
                    return N
                if not piece.valid:
                    return F
                if i in alone:
                    return T if not alone[i].code and alone[i].counterexample is not None else N
                return T

            value = T
            for i, piece in enumerate(pieces):
                value = value & decision(i, piece)
            if any(p.vacuous for p in pieces) or vacuous:
                fields["vacuous"] = True
            if any(p.vacuous for p in pieces):
                value = N
            if not failed and len(properties) > 1:
                # Check property consistency without premises: a false implication alone is not a conflict.
                consistency = check_sat("(and " + " ".join(properties) + ")", sorts, timeout=left())
                fields["inputs"]["consistency"] = consistency.model_dump(mode="json")
                if consistency.code or consistency.satisfiable is None:
                    fields.update(code=consistency.code or "G6_E_FORMAL_CLAIM_UNKNOWN", reason=consistency.reason)
                    value = N
                elif consistency.satisfiable is False:
                    value = B
            if vacuous and "code" not in fields and value.state.value == "N":
                fields.update(code=VACUOUS_PROPERTY, reason="these properties hold without the premises, so they say "
                                                            "nothing about them: " + "; ".join(vacuous)[:400])
            fields.update(value=value.state.value, computed=True)
    except Refusal as refusal:
        fields.update(value="N", computed=False, code=refusal.code, reason=refusal.reason)
    except Exception as exc:  # noqa: BLE001 -- no solver/aggregation failure becomes a pass
        fields.update(value="N", computed=False, code="G6_E_FORMAL_CONTRACT_FAILED",
                      reason=f"{type(exc).__name__}: {exc}"[:500])
    result = apply_envelope(ContractResult(operation="check_contract", backend="four_valued", **fields),
                            backend="four_valued", operation="check_contract", request_id=request_id, run_id=run_id)
    checks = [*pieces, *alone.values(), *([consistency] if consistency is not None else [])]
    # The aggregation is pure Python; its entry says whether Z3 ran for the checks it aggregates (listed after it).
    z3_ran = any(e.get("external_solver_ran") for check in checks for e in check.evidence)
    evidence = [*({**e, "external_solver_ran": z3_ran} for e in result.evidence),
                *(e for check in checks for e in check.evidence)]
    card = result.warning_card
    if result.completion_state == "qualified-draft":
        card = {**card, "message": "G6 checked the properties with Z3 (and joint consistency for multiple properties), then aggregated "
                "the decisions with Belnap logic. This is a solver decision on your encoding, not verified proof."}
    return result.model_copy(update={"evidence": evidence, "warning_card": card})
