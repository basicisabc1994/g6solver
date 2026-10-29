# SPDX-License-Identifier: Apache-2.0
"""Read the user-authored ontology as metadata, not as executable code or a trusted oracle."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogMatch:
    algorithm_id: str
    problem_ids: tuple[str, ...]
    time: str
    qualification: str
    model: str
    proof_obligations: tuple[str, ...]
    missing_features: tuple[str, ...]
    assumptions: tuple[str, ...]
    implementation_status: str
    status: str


def retrieve_ontology(ontology: dict, *, problem_id: str, established_features: tuple[str, ...],
                      limit: int = 20) -> tuple[CatalogMatch, ...]:
    """Strict symbolic feature recognition: unknown != true; no guessed equivalences."""
    if not 1 <= limit <= 100:
        raise ValueError("invalid retrieval limit")
    if not isinstance(ontology, dict) or not isinstance(ontology.get("algorithms"), list):
        raise ValueError("ontology must have an algorithms array")
    features = set(established_features)
    results = []
    for row in ontology["algorithms"]:
        if not isinstance(row, dict) or problem_id not in row.get("problem_ids", ()):
            continue
        required = tuple(str(x) for x in row.get("recognition_rules", ()))
        missing = tuple(sorted(set(required) - features))
        bound = row.get("complexity", {})
        results.append(CatalogMatch(
            str(row["id"]), tuple(row.get("problem_ids", ())), str(bound.get("time", "unknown")),
            str(bound.get("qualification", "not established")),
            str(bound.get("model", "unknown")), tuple(row.get("proof_obligations", ())),
            missing, tuple(row.get("assumptions", ())),
            str(row.get("implementation_status", "unknown")),
            "needs_preconditions" if missing else "eligible_as_proposal",
        ))
    return tuple(sorted(results, key=lambda x: (bool(x.missing_features), len(x.missing_features), x.algorithm_id))[:limit])


def read_ontology(path: str | Path) -> dict:
    """Load explicit local metadata, never execute it. Caller chooses a trusted local path."""
    p = Path(path)
    if p.stat().st_size > 4_000_000:
        raise ValueError("ontology exceeds size budget")
    result = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("ontology must be a mapping")
    return result
