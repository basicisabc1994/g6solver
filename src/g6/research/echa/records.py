# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The kernel's typed records (ECHA spec 4.1, 4.3, 4.6): a standard, a piece of evidence and a claim.

Pure value objects. Each is validated when it is built, hashed as canonical JSON (the idiom ``Harness.hash`` uses), and
carries what the claim rule (spec 2.2) requires: predicate, scope, provenance, assumptions, limits and versions.
``unknown`` is its own result and is never a pass; evidence in the category ``NO_DISCRIMINATING_EVIDENCE`` can be
neither a pass nor a fail; a claim has no result field because its result is computed from its evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

SCHEMA = "echa.v1"
#: The fixture_version of evidence taken against nothing sealed: it is protected by no access boundary and can never be
#: independent.
UNSEALED = "unsealed"
ORACLE_CATEGORIES = ("FORMAL_CHECK", "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER", "HEURISTIC_REVIEW",
                     "NO_DISCRIMINATING_EVIDENCE")
RESULTS = ("pass", "fail", "unknown", "not_applicable")
PURPOSES = ("check", "negative_control", "positive_control")
BREADTHS = ("covered_cases", "general")
ESTIMANDS = ("oracle_scoped", "true_correctness")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def text_of(name: str, value: Any, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")  # noqa: TRY004 - every refusal is one ValueError to a caller
    if required and not value.strip():
        raise ValueError(f"{name} is required")
    return value


def texts_of(name: str, value: Any, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{name} must be a list of text")  # noqa: TRY004
    found = tuple(text_of(name, item) for item in value)
    if required and not found:
        raise ValueError(f"{name} is required (at least one)")
    return found


def hex64_of(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(f"{name} must be a sha256 hex digest")
    return value


def _set(obj: Any, **values: Any) -> None:
    for key, value in values.items():
        object.__setattr__(obj, key, value)


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class StandardRef:
    """A frozen task standard (the Task Standard Contract's text and the hash every record that cites it must carry)."""

    standard_id: str
    text: str
    source_provenance: str
    mandatory_predicates: tuple[str, ...]
    standard_sha256: str
    schema_version: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA:
            raise ValueError(f"schema_version must be {SCHEMA}")
        text_of("standard_id", self.standard_id)
        text_of("text", self.text)
        text_of("source_provenance", self.source_provenance)
        _set(self, mandatory_predicates=texts_of("mandatory_predicates", self.mandatory_predicates))
        if hex64_of("standard_sha256", self.standard_sha256) != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("standard_sha256 is not the hash of the standard's text")

    @classmethod
    def from_text(cls, standard_id: str, text: str, source_provenance: str,
                  mandatory_predicates: tuple[str, ...] | list[str]) -> StandardRef:
        return cls(standard_id, text, source_provenance, tuple(mandatory_predicates),
                   hashlib.sha256(text.encode("utf-8")).hexdigest())

    def to_dict(self) -> dict:
        return {f.name: _plain(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, body: Mapping) -> StandardRef:
        return cls(**dict(body))


@dataclass(frozen=True)
class EvidenceRecord:
    """One result of one oracle on one predicate (spec 4.3), with what makes it usable: its scope, its independence and the
    negative control that shows the check can fail."""

    evidence_id: str
    predicate_id: str
    oracle_category: str
    oracle_version: str
    fixture_version: str
    independent: bool
    independence_group: str
    independence_rationale: str
    result: str
    scope: str
    known_limitations: tuple[str, ...]
    source_ref: str
    standard_sha256: str
    negative_control_ref: str = ""
    purpose: str = "check"
    oracle_error_bound: float | None = None
    schema_version: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA:
            raise ValueError(f"schema_version must be {SCHEMA}")
        text_of("evidence_id", self.evidence_id)
        text_of("predicate_id", self.predicate_id)
        text_of("oracle_version", self.oracle_version)
        if self.fixture_version != UNSEALED:  # a sealed fixture is named by its vault's digest, never by a free-text label
            hex64_of("fixture_version", self.fixture_version)
        hex64_of("standard_sha256", self.standard_sha256)
        if self.result not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        if self.oracle_category not in ORACLE_CATEGORIES:
            raise ValueError(f"oracle_category must be one of {ORACLE_CATEGORIES}")
        if self.purpose not in PURPOSES:
            raise ValueError(f"purpose must be one of {PURPOSES}")
        text_of("scope", self.scope)
        text_of("source_ref", self.source_ref)
        text_of("negative_control_ref", self.negative_control_ref, required=False)
        _set(self, known_limitations=texts_of("known_limitations", self.known_limitations))
        if self.oracle_category == "NO_DISCRIMINATING_EVIDENCE" and self.result in ("pass", "fail"):
            raise ValueError("NO_DISCRIMINATING_EVIDENCE can only read unknown or not_applicable, never pass or fail")
        if not isinstance(self.independent, bool):
            raise ValueError("independent must be true or false")  # noqa: TRY004
        text_of("independence_group", self.independence_group, required=False)
        text_of("independence_rationale", self.independence_rationale, required=False)
        if self.independent:
            if self.fixture_version == UNSEALED:
                raise ValueError("an independent record must name the sealed fixture it was taken against (fixture_version)")
            text_of("independence_rationale", self.independence_rationale)
            text_of("independence_group", self.independence_group)
        if self.oracle_category == "INDEPENDENT_EXECUTABLE" and not self.independent:
            raise ValueError("INDEPENDENT_EXECUTABLE must be independent (an author-controlled check is a PARTIAL_FALSIFIER)")
        bound = self.oracle_error_bound
        if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float)) or not 0 <= bound <= 1):
            raise ValueError("oracle_error_bound must be a number from 0 to 1, or None when it is not bounded")

    def to_dict(self) -> dict:
        return {f.name: _plain(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, body: Mapping) -> EvidenceRecord:
        return cls(**dict(body))

    @property
    def sha256(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class ClaimRecord:
    """What a report says was established (spec 4.6). It has no result: ``evaluate_claim`` computes one from the evidence."""

    claim_id: str
    subject: str
    predicate_id: str
    scope: str
    breadth: str
    estimand: str
    population: str
    sample_unit: str
    provenance: str
    assumptions: tuple[str, ...]
    known_limitations: tuple[str, ...]
    versions: Mapping[str, str]
    evidence_ids: tuple[str, ...]
    standard_sha256: str
    requires_independent_groups: int = 1
    schema_version: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA:
            raise ValueError(f"schema_version must be {SCHEMA}")
        for name in ("claim_id", "subject", "predicate_id", "scope", "population", "sample_unit", "provenance"):
            text_of(name, getattr(self, name))
        if self.breadth not in BREADTHS:
            raise ValueError(f"breadth must be one of {BREADTHS}")
        if self.estimand not in ESTIMANDS:
            raise ValueError(f"estimand must be one of {ESTIMANDS}")
        hex64_of("standard_sha256", self.standard_sha256)
        _set(self, assumptions=texts_of("assumptions", self.assumptions, required=True),
             known_limitations=texts_of("known_limitations", self.known_limitations, required=True),
             evidence_ids=texts_of("evidence_ids", self.evidence_ids, required=True))
        if not isinstance(self.versions, Mapping) or not self.versions:
            raise ValueError("versions is required (the standard, harness, oracle and component versions in force)")
        _set(self, versions={text_of("versions", k): text_of("versions", v) for k, v in self.versions.items()})
        if (isinstance(self.requires_independent_groups, bool) or not isinstance(self.requires_independent_groups, int)
                or self.requires_independent_groups < 1):
            raise ValueError("requires_independent_groups must be a whole number of at least 1")

    def to_dict(self) -> dict:
        return {f.name: _plain(dict(getattr(self, f.name)) if f.name == "versions" else getattr(self, f.name))
                for f in fields(self)}

    @classmethod
    def from_dict(cls, body: Mapping) -> ClaimRecord:
        return cls(**dict(body))
