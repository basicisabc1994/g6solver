# SPDX-License-Identifier: Apache-2.0
"""Exclusive, digest-pinned registrations. No proposals or corpus loaders here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from g6.core import confine, ledger
from g6.core.ledger import canonical, digest
from g6.workforce import mutation_check, repo_tests
from pydantic import Field, model_validator

from . import case_gate, oracle
from .case_gate import CaseRecord, CaseRef, Record, SHA256, SPLIT_RULE, cut, unit_split


def study_metadata(study: dict) -> dict:
    """Owner's 2026-11-18 grant, consumed when freezing a study registration."""
    budget = {'allowed': 42, 'total': 84}
    if 'sealed_bug_budget' in study and study['sealed_bug_budget'] != budget:
        raise ValueError('sealed bug budget differs from owner grant (42 of 84)')
    return dict(study, sealed_bug_budget=budget)


def strict_json(data: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON constant")

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def oracle_digest() -> str:
    """Pin executable judging dependencies as well as the label function."""
    modules = (case_gate, oracle, mutation_check, repo_tests, confine, ledger)
    # Source-checkout callers use validation.distill; study entry points put
    # validation on sys.path and use distill. These name the same pinned files.
    files = {m.__name__.removeprefix("validation."): Path(m.__file__) for m in modules}
    files.update({"sealed_gate": Path(__file__).with_name("sealed_gate.py"),
                  "registration_gate": Path(__file__), "operators": Path(__file__).parents[1] / "operators.py"})
    return digest({name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()})


class Registration(Record):
    version: int = Field(default=1, ge=1, le=1)
    seed: str = Field(min_length=1)
    split_rule: str = SPLIT_RULE
    cases: tuple[CaseRef, ...] = Field(min_length=1)
    manifest_sha256: str = Field(pattern=SHA256)
    operator_digests: dict[str, str]
    oracle_sha256: str = Field(pattern=SHA256)
    policy: oracle.OraclePolicy
    policy_sha256: str = Field(pattern=SHA256)
    kill_rule: str = oracle.KILL_RULE
    exclusion_rule: str = oracle.EXCLUSION_RULE
    study: dict[str, Any]

    @model_validator(mode="after")
    def intact(self):
        if self.split_rule != SPLIT_RULE or self.kill_rule != oracle.KILL_RULE or self.exclusion_rule != oracle.EXCLUSION_RULE:
            raise ValueError("unrecognised frozen rules")
        if set(self.operator_digests) != {"training", "legitimate", "held_out"}:
            raise ValueError("all three operator digests are required")
        import re
        if any(not isinstance(value, str) or not re.fullmatch(SHA256, value)
               for value in self.operator_digests.values()):
            raise ValueError("invalid operator digest")
        if self.manifest_sha256 != digest([c.model_dump(mode="json") for c in self.cases]):
            raise ValueError("manifest digest differs")
        if self.policy_sha256 != digest(self.policy.model_dump(mode="json")):
            raise ValueError("policy digest differs")
        units, ids = {}, set()
        for case in self.cases:
            if case.case_id in ids:
                raise ValueError("duplicate case")
            ids.add(case.case_id)
            expected = unit_split(case.unit_id, case.cohort, self.seed)
            if case.split != expected or units.setdefault(case.unit_id, (case.cohort, case.split)) != (case.cohort, case.split):
                raise ValueError("cut differs from frozen seed or unit")
        if list(self.cases) != sorted(self.cases, key=lambda c: c.case_id):
            raise ValueError("manifest is not canonically ordered")
        canonical(self.study)  # reject arbitrary Python values and non-finite numbers
        return self

    @property
    def registration_id(self) -> str:
        return digest(self.model_dump(mode="json"))


def freeze(path: Path, cases: list[CaseRecord], *, seed: str, operator_digests: dict[str, str],
           policy: dict, study: dict) -> Registration:
    """Create once. The owner must commit this before any Concept exists.

The expected digest must be kept independently by the acceptance/study gate.
An editable digest adjacent to a file is not a trust root.
"""
    path = Path(path)
    if path.exists():
        raise FileExistsError("a frozen registration cannot be replaced")
    if path.is_symlink() or path.is_junction():
        raise ValueError("linked registration refused")
    assignments = cut(cases, seed=seed)
    refs = tuple(CaseRef(case_id=c.case_id, unit_id=c.unit_id, cohort=c.provenance.cohort, split=assignments[c.case_id])
                 for c in sorted(cases, key=lambda c: c.case_id))
    policy_ = oracle.OraclePolicy.model_validate(policy)
    registration = Registration(seed=seed, cases=refs, manifest_sha256=digest([c.model_dump(mode="json") for c in refs]),
                                operator_digests=operator_digests, oracle_sha256=oracle_digest(), policy=policy_,
                                policy_sha256=digest(policy_.model_dump(mode="json")), study=study_metadata(study))
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(registration.model_dump(mode="json")) + "\n")
    return registration


def read_registration(path: Path, *, expected_digest: str) -> Registration:
    path = Path(path)
    if path.is_symlink() or path.is_junction() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("linked or oversized registration refused")
    data = strict_json(path.read_bytes())
    if digest(data) != expected_digest:
        raise ValueError("registration digest differs from frozen digest")
    registration = Registration.model_validate(data)
    if registration.oracle_sha256 != oracle_digest():
        raise ValueError("oracle code digest changed")
    return registration
