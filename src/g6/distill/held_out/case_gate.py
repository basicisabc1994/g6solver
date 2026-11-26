# SPDX-License-Identifier: Apache-2.0
"""Small case adapter until the records slice is integrated. No corpus discovery.

All inputs are explicit. Sources refer only to the design's development/selection
archive. This module neither opens those sources nor spends the final cohort.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from g6.core.ledger import digest
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256 = r"^[0-9a-f]{64}$"
Split = Literal["tuning", "acceptance", "monitor", "reserve", "unseen"]
SPLIT_RULE = "sha256-canonical-[seed,unit_id]-percentile:50/20/15/15;selection=unseen;v1"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(Record):
    source: Literal["round3", "reviewer_domain", "maintainer", "operator", "k3_pilot", "hard_negative"]
    cohort: Literal["development", "selection"]
    source_ref: str = Field(min_length=1, max_length=1024)
    source_sha256: str = Field(pattern=SHA256)
    amended: Literal[False] = False

    @field_validator("source_ref")
    @classmethod
    def permitted_reference(cls, value: str) -> str:
        normal = value.replace("\\", "/").lower()
        if any(token in normal for token in ("test_weakening_se005", "swebench", "swe-bench", "g6-held-sources",
                                             "failure_corpus", "/v7/", "final-", "/.g6/", ".env")):
            raise ValueError("excluded source")
        return value


def python_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute() or ".." in path.parts
            or path.as_posix() != value or path.suffix != ".py"
            or any(part.startswith(".") for part in path.parts) or path.name == "conftest.py"):
        raise ValueError("expected a relative, ordinary Python source path")
    return value


class CaseRecord(Record):
    unit_id: str = Field(min_length=1, max_length=256)
    provenance: Provenance
    files: dict[str, str]
    target: str
    changed_lines: tuple[int, ...] = Field(min_length=1, max_length=4096)
    before_tests: str = Field(min_length=1, max_length=131072)
    after_tests: str = Field(min_length=1, max_length=131072)
    known_wrong: tuple[str, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def bounded_program(self):
        python_path(self.target)
        if (not self.files or len(self.files) > 128 or self.target not in self.files
                or any(line < 1 for line in self.changed_lines)
                or len(set(self.changed_lines)) != len(self.changed_lines)):
            raise ValueError("invalid program or changed lines")
        for path, source in self.files.items():
            python_path(path)
            if any(parent.as_posix() in self.files for parent in PurePosixPath(path).parents):
                raise ValueError("file and directory paths conflict")
            if path == "tests/test_case.py" or path.startswith("tests/test_case.py/"):
                raise ValueError("reserved oracle test path")
            if len(source.encode("utf-8")) > 131072:
                raise ValueError("source too large")
        if sum(len(s.encode("utf-8")) for s in self.files.values()) > 1048576:
            raise ValueError("program too large")
        if any(len(s.encode("utf-8")) > 131072 for s in self.known_wrong):
            raise ValueError("wrong implementation too large")
        return self

    @property
    def case_id(self) -> str:
        return digest(self.model_dump(mode="json"))


class CaseRef(Record):
    case_id: str = Field(pattern=SHA256)
    unit_id: str = Field(min_length=1)
    cohort: Literal["development", "selection"]
    split: Split


def unit_split(unit_id: str, cohort: str, seed: str) -> Split:
    if not seed or not unit_id or cohort not in ("development", "selection"):
        raise ValueError("a seed, unit and allowed cohort are required")
    if cohort == "selection":
        return "unseen"
    # Integer thresholds avoid floating point and retain the complete hash.
    percentile = int(digest([seed, unit_id]), 16) * 100 // (1 << 256)
    return "tuning" if percentile < 50 else "acceptance" if percentile < 70 else (
        "monitor" if percentile < 85 else "reserve")


def cut(cases: list[CaseRecord], *, seed: str) -> dict[str, Split]:
    if not cases or not seed:
        raise ValueError("cases and seed are required")
    result, cohorts = {}, {}
    for original in cases:
        # Revalidate nested mutable mappings before they cross the boundary.
        case = CaseRecord.model_validate(original.model_dump(mode="json"))
        if case.case_id in result:
            raise ValueError("duplicate case")
        if cohorts.setdefault(case.unit_id, case.provenance.cohort) != case.provenance.cohort:
            raise ValueError("one unit cannot have conflicting cohorts")
        result[case.case_id] = unit_split(case.unit_id, case.provenance.cohort, seed)
    return dict(sorted(result.items()))


def training_view(cases: list[CaseRecord], *, seed: str) -> tuple[CaseRecord, ...]:
    assignments = cut(cases, seed=seed)
    return tuple(CaseRecord.model_validate(c.model_dump(mode="json"))
                 for c in sorted(cases, key=lambda c: c.case_id) if assignments[c.case_id] == "tuning")
