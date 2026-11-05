# SPDX-License-Identifier: Apache-2.0
"""A typed algorithm-construction IR; claimed bounds never constitute established proof."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from g6.core.ledger import digest


@dataclass(frozen=True)
class DesignProblem:
    """A problem in declarative terms: representation(s), wanted output, and established preconditions.

    Inputs are caller-supplied assertions, not independent observations.
    """
    representations: tuple[str, ...]
    target: str
    established_properties: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    source: str = "caller"

    def __post_init__(self) -> None:
        if not self.representations or not self.target or not self.source:
            raise ValueError("problem needs representations, a target, and provenance")


@dataclass(frozen=True)
class AlgorithmPrimitive:
    id: str
    consumes: str
    produces: str
    preconditions: tuple[str, ...]
    postcondition: str
    complexity_claim: str
    proof_obligations: tuple[str, ...]
    technique: str
    reference: str = "classical algorithmic technique"

    def __post_init__(self) -> None:
        if not self.id or not self.consumes or not self.produces or not self.postcondition:
            raise ValueError("primitive needs id, input/output representations, and postcondition")


@dataclass(frozen=True)
class DesignCandidate:
    id: str
    steps: tuple[str, ...]
    target: str
    produced: tuple[str, ...]
    complexity_claims: tuple[str, ...]
    proof_obligations: tuple[str, ...]
    provenance: str = "catalog_proposal"
    status: Literal["candidate_only"] = "candidate_only"


@dataclass(frozen=True)
class DesignSearch:
    candidates: tuple[DesignCandidate, ...]
    expansions: int
    truncated: bool
    rejected: tuple[str, ...]
    status: str = "proposal_only"

    @property
    def fingerprint(self) -> str:
        return digest({"candidates": [c.id for c in self.candidates],
                       "expansions": self.expansions, "truncated": self.truncated,
                       "rejected": self.rejected})
