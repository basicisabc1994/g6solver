# SPDX-License-Identifier: Apache-2.0
"""``recheck``: run an artifact's queries again (audit 017 FM-001, the artifact part).

The archive accepted an artifact when an unkeyed SHA-256 recomputed over the artifact's own fields matched the hash
stored next to them. Anyone who edited the artifact could recompute the hash, so "validated" meant only "consistent
with itself". Here an artifact binds the statement to its queries (rebuilt from ``sorts``, ``assumptions`` and
``goal``) and each query to the solver's answer, and ``recheck`` runs every query again on the installed solver. A
recheck that passes still leaves a result at ``qualified-draft``: it repeats a solver decision; it is not a proof a
person has inspected.
"""
from __future__ import annotations

from collections.abc import Callable

from g6.formal import propositional, z3_bridge
from g6.formal.envelope import Refusal
from g6.formal.schema import ProofArtifact


def recheck(artifact: ProofArtifact, *, timeout: float = z3_bridge.DEFAULT_TIMEOUT_S) -> tuple[bool, str]:
    """``(True, how)`` when every recorded query is the one the statement gives and the solver answers it the same
    way again; ``(False, why)`` otherwise. Never raises for a malformed or tampered artifact."""
    backends: dict[str, tuple[Callable, Callable[[str], str]]] = {
        "z3": (z3_bridge.artifact_queries, lambda q: z3_bridge.run_query(q, timeout)[0]),
        "propositional": (propositional.artifact_queries, propositional.decide),
    }
    if artifact.solver not in backends:
        return False, f"g6.formal cannot re-run solver {artifact.solver!r}"
    rebuild, decide = backends[artifact.solver]
    try:
        statement, expected = rebuild(artifact)
    except Refusal as refusal:
        return False, f"the artifact cannot be rebuilt: {refusal.reason}"
    recorded = tuple(check.query for check in artifact.checks)
    if artifact.statement != statement:
        return False, "the statement is not the one the artifact's assumptions and goal give"
    if not recorded or recorded != expected[:len(recorded)]:
        return False, "the recorded queries are not the ones the artifact's statement gives"
    if artifact.question == "valid" and artifact.checks[0].result == "unsat" and len(recorded) < 2:
        return False, "an unsat validity query is recorded without the check that the premises are consistent"
    note = ""
    if artifact.solver == "z3":
        try:
            version = z3_bridge.z3_version()
        except Refusal as refusal:
            return False, refusal.reason
        if version != artifact.solver_version:
            note = f" (recorded with z3 {artifact.solver_version}, re-run with z3 {version})"
    for i, check in enumerate(artifact.checks, 1):
        try:
            answer = decide(check.query)
        except Refusal as refusal:
            return False, f"query {i} could not be re-run: {refusal.code}: {refusal.reason}"
        if answer != check.result:
            return False, f"query {i}: the artifact records {check.result!r}, the solver now answers {answer!r}"
    count = len(artifact.checks)
    return True, f"{count} recorded quer{'y' if count == 1 else 'ies'} re-run with the same answer{note}"


__all__ = ["recheck"]
