# SPDX-License-Identifier: Apache-2.0
"""Frozen, typed CIR records; all fingerprints use G6's existing canonical digest."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from g6.core.ledger import digest

Authority = Literal["stated", "repository_stated", "person_confirmed",
                    "model_hypothesis", "adopted_unconfirmed"]
Essentiality = Literal["essential", "accidental_useful", "accidental_avoidable", "unknown"]
KNOWN_AUTHORITIES = frozenset(("stated", "repository_stated", "person_confirmed"))
VALID_AUTHORITIES = KNOWN_AUTHORITIES | {"model_hypothesis", "adopted_unconfirmed"}


@dataclass(frozen=True)
class Snapshot:
    tree_digest: str
    environment_digest: str
    isolation: str = "unknown"

    def __post_init__(self) -> None:
        if not self.tree_digest or not self.environment_digest:
            raise ValueError("snapshot requires exact tree and environment digests")


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    authority: Authority
    source: str
    kind: Literal["satisfy", "preserve", "forbid"] = "satisfy"

    def __post_init__(self) -> None:
        if not self.id or not self.text.strip() or not self.source.strip():
            raise ValueError("requirement id, text and source are required")
        if self.authority not in VALID_AUTHORITIES:
            raise ValueError("invalid authority")
        if self.kind not in ("satisfy", "preserve", "forbid"):
            raise ValueError("invalid requirement kind")


@dataclass(frozen=True)
class Obligation:
    id: str
    requirement_id: str
    expectation: str
    oracle_source: str
    normative: bool = False

    def __post_init__(self) -> None:
        if not all((self.id, self.requirement_id, self.expectation, self.oracle_source)):
            raise ValueError("obligation must specify its requirement, oracle and expectation")


@dataclass(frozen=True)
class FrozenIntent:
    requirements: tuple[Requirement, ...]
    obligations: tuple[Obligation, ...]

    def __post_init__(self) -> None:
        mapping = {r.id: r for r in self.requirements}
        if len(mapping) != len(self.requirements) or len({o.id for o in self.obligations}) != len(self.obligations):
            raise ValueError("duplicate frozen intent identifier")
        for o in self.obligations:
            r = mapping.get(o.requirement_id)
            if r is None:
                raise ValueError("obligation references missing requirement")
            # An obligation's bit cannot elevate its requirement's authority.
            if o.normative and r.authority not in KNOWN_AUTHORITIES:
                raise ValueError("model/unconfirmed obligation cannot be normative")

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def normative(self, obligation_id: str) -> bool:
        return any(o.id == obligation_id and o.normative for o in self.obligations)


@dataclass(frozen=True)
class Dependency:
    source: str
    target: str
    kind: str
    file: str
    line: int
    provenance: str = "static"


@dataclass(frozen=True)
class StateVariable:
    id: str
    owner: str
    mutable: bool
    essentiality: Essentiality = "unknown"
    derivation: str = ""
    justification: str = ""


@dataclass(frozen=True)
class Effect:
    operation: str
    resource: str
    kind: Literal["read", "write", "create", "delete", "unknown"]
    provenance: str
    file: str
    line: int


@dataclass(frozen=True)
class ComplexityFinding:
    id: str
    root_cause: str
    location: str
    essentiality: Essentiality = "unknown"
    evidence: str = ""
    proposed_simplification: str = ""


@dataclass(frozen=True)
class Hypothesis:
    id: str
    claim: str
    severity: Literal["low", "medium", "high"] = "medium"
    falsifier: str = ""
    state: Literal["open", "refuted", "supported_in_scope", "ambiguous"] = "open"


@dataclass(frozen=True)
class CIR:
    snapshot: Snapshot
    intent: FrozenIntent
    components: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    state: tuple[StateVariable, ...]
    effects: tuple[Effect, ...]
    findings: tuple[ComplexityFinding, ...] = ()
    unknowns: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def vector(self, touched: tuple[str, ...] = ()) -> dict:
        owners = {s.owner for s in self.state if s.mutable and
                  (not touched or any(s.owner == t or s.owner.startswith(t + ".") for t in touched))}
        return {
            "mutable_state_owners_touched": len(owners),
            "may_read_write_effect_edges": sum(e.kind in ("read", "write", "create", "delete") for e in self.effects),
            "required_ordering_constraints": None,  # no ordering analysis yet: unknown, not zero
            "unmodelled_dynamic_effects": len(self.unknowns),
            "cross_boundary_dependency_edges": sum(d.source.split(".")[0] != d.target.split(".")[0]
                                                   for d in self.dependencies),
            "transitive_impacted_components": None,  # supplied only by impact_cone
            "duplicated_or_dead_code_candidates": None,
            "uncovered_essential_obligations": sum(o.normative for o in self.intent.obligations),
            "open_hypotheses_by_severity": None,
            "incomplete": bool(self.unknowns),
        }
