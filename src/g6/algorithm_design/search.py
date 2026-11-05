# SPDX-License-Identifier: Apache-2.0
"""Deterministic bottom-up primitive composition under explicit state/expansion budgets."""
from __future__ import annotations

from collections import deque

from g6.algorithm_design.catalog import primitives
from g6.algorithm_design.ir import AlgorithmPrimitive, DesignCandidate, DesignProblem, DesignSearch
from g6.core.ledger import digest


def search_algorithms(problem: DesignProblem, *, catalog: tuple[AlgorithmPrimitive, ...] | None = None,
                      max_depth: int = 3, max_expansions: int = 128, max_candidates: int = 16) -> DesignSearch:
    """Enumerate type-correct primitive paths, never silently inventing prerequisites.

    The search is a design proposal and does not run generated code or establish any claimed
    running-time bound. Composition is only output-representation-to-input-representation;
    semantic compatibility remains a proof obligation.
    """
    if not 1 <= max_depth <= 8 or not 1 <= max_expansions <= 10_000 or not 1 <= max_candidates <= 500:
        raise ValueError("invalid bounded search budgets")
    rules = tuple(sorted(catalog if catalog is not None else primitives(), key=lambda x: x.id))
    if len({r.id for r in rules}) != len(rules):
        raise ValueError("catalog primitive identifiers must be unique")
    initial = tuple(sorted(set(problem.representations)))
    known = set(problem.established_properties)
    work = deque((rep, ()) for rep in initial)
    visited = set((rep, ()) for rep in initial)
    found: list[DesignCandidate] = []
    refused: set[str] = set()
    expansions = 0
    truncated = False
    while work:
        rep, path = work.popleft()
        if len(path) >= max_depth:
            continue
        for rule in rules:
            if rule.consumes != rep:
                continue
            missing = set(rule.preconditions) - known
            if missing:
                refused.add(f"{rule.id}:missing_preconditions:{','.join(sorted(missing))}")
                continue
            if expansions >= max_expansions:
                truncated = True
                break
            expansions += 1
            new_path = (*path, rule)
            if rule.produces == problem.target:
                names = tuple(r.id for r in new_path)
                obligations = tuple(sorted(set(
                    ["composition preserves each intermediate representation", "validate on independent examples",
                     "establish applicability of each primitive on the real input"]
                    + [o for r in new_path for o in r.proof_obligations]
                    + list(problem.constraints)
                )))
                claims = tuple(r.complexity_claim for r in new_path)
                ident = digest({"problem": problem.__dict__, "steps": names})
                found.append(DesignCandidate(ident, names, problem.target,
                                             tuple([*initial, *(r.produces for r in new_path)]),
                                             claims, obligations))
                if len(found) >= max_candidates:
                    truncated = bool(work) or any(r.consumes == rep for r in rules)
                    break
            else:
                # For representation-only composition no cycles or identical state revisits.
                state = (rule.produces, tuple(r.id for r in new_path))
                if rule.produces not in [*initial, *(r.produces for r in path)] and state not in visited:
                    visited.add(state)
                    work.append((rule.produces, new_path))
        if truncated or len(found) >= max_candidates:
            break
    return DesignSearch(tuple(found), expansions, truncated, tuple(sorted(refused)))
