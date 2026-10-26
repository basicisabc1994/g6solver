# SPDX-License-Identifier: Apache-2.0
"""Explicit checks on outputs; plain-language criteria remain unchecked until mapped."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from g6.intent_engine.schema import IntentContract


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["contains", "absent", "regex", "word_count"]
    value: str = Field(default="", max_length=256)
    min_words: int = Field(default=0, ge=0, le=100_000)
    max_words: int = Field(default=100_000, ge=0, le=100_000)

    @model_validator(mode="after")
    def valid(self):
        if self.kind != "word_count" and not self.value:
            raise ValueError("literal or regex check needs a nonempty value")
        if self.min_words > self.max_words:
            raise ValueError("word count minimum exceeds maximum")
        return self


def _bounded_regex(pattern: str):
    # Only fixed-width atoms, fixed repetition {n}, character classes and anchors.
    # No branching, groups, backreferences, lookaround or variable repetition.
    i, in_class = 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 1
            if i == len(pattern) or pattern[i].isalnum() and pattern[i] not in "dDsSwWbB":
                raise ValueError("unsupported regex escape")
        elif ch == "[":
            if in_class:
                raise ValueError("nested class")
            in_class = True
        elif ch == "]":
            in_class = False
        elif not in_class:
            if ch in "()*+?|}":
                raise ValueError("unbounded or branching regex")
            if ch == "{":
                repeat = re.match(r"\{([0-9]{1,2})\}", pattern[i:])
                if not repeat or not 1 <= int(repeat[1]) <= 64:
                    raise ValueError("only fixed repetition from 1 to 64 is supported")
                i += len(repeat[0]) - 1
        i += 1
    return re.compile(pattern)


def _evaluate(check: Check, text: str):
    if len(text) > 100_000:
        raise ValueError("output exceeds 100000 characters")
    if check.kind == "word_count":
        count = len(text.split())
        return check.min_words <= count <= check.max_words, (0, len(text))
    if check.kind == "regex":
        match = _bounded_regex(check.value).search(text)
    else:
        # Case sensitive, so offsets refer to original text without Unicode folding changes.
        match = re.search(re.escape(check.value), text)
    found = match is not None
    return (not found if check.kind == "absent" else found), (match.span() if match else None)


def check_output(contract: IntentContract, text: str) -> list[dict]:
    """Each declared criterion is pass/fail/unchecked; matching failure criteria fail.

    No criterion is inferred from prose. Mappings are human-authored contract data,
    not proof that a person approved an action. Freeze them with the existing harness.
    """
    rows = []
    for kind in ("success", "failure"):
        criteria = getattr(contract, kind + "_criteria")
        checks = getattr(contract, kind + "_checks")
        for index, criterion in enumerate(criteria):
            row = {"criterion": criterion, "kind": kind, "index": index, "status": "unchecked", "span": None,
                   "code": "G6_E_CRITERIA_UNVERIFIABLE"}
            check = checks.get(criterion)
            if check is not None:
                try:
                    check = Check.model_validate(check)
                    satisfied, span = _evaluate(check, text)
                    row.update(status="pass" if satisfied == (kind == "success") else "fail", span=span, code="")
                except Exception:
                    row["code"] = "G6_E_CRITERION_CHECK_FAILED"
            rows.append(row)
    return rows


@dataclass(frozen=True)
class _CriterionRunner:
    contract_json: str
    kind: str
    index: int

    def __call__(self, text: str) -> str:
        from g6.intent_engine.schema import IntentContract
        rows = check_output(IntentContract.model_validate_json(self.contract_json), text)
        row = next(r for r in rows if r["kind"] == self.kind and r["index"] == self.index)
        return "unknown" if row["status"] == "unchecked" else row["status"]


def harness_checks(contract: IntentContract) -> tuple:
    """Bind the typed checks to the existing harness and its recorded value_report.

    The callable's frozen representation includes the full contract, so changing
    a literal or word bound changes the harness's check hash. No new store. A
    failure criterion's predicate reads "avoids: <criterion>", so its pass says the
    unacceptable outcome did not occur.
    """
    from g6.harness import Check as HarnessCheck
    frozen = contract.model_dump_json()
    return tuple(HarnessCheck(criterion if kind == "success" else f"avoids: {criterion}",
                              _CriterionRunner(frozen, kind, i), check_id=f"{kind}-{i}")
                 for kind in ("success", "failure")
                 for i, criterion in enumerate(getattr(contract, kind + "_criteria")))
