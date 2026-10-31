# SPDX-License-Identifier: Apache-2.0
"""Coding as a domain of the self-improvement loop: a reviewer reads recorded patches, and replays say if it reads them right.

Each case is one finished patch an agent wrote, with the issue it answers. Its label comes from the maintainers' tests, run by an
independent check neither the agent nor the reviewer ever saw. A profile is how the reviewer reads: its system prompt. Its reply ends in
``VERDICT: RIGHT`` or ``VERDICT: WRONG``, and the judge compares that with the label. No patch runs, so a case is one model call and no
container, which is what gives the gate enough cases on work where a live run is costly (design 6.2).

**Bugs are the unit of evidence.** Patches of one bug share its issue, so a case carries its bug as ``meta["group"]``, and ``cut_pool``
keeps a bug on one side of every cut: a proposer that read one patch of a bug is never scored on another. For the same reason a bug on
both sides of the seal is refused. The gate still counts cases, not bugs, so a report on this domain says how many bugs it rests on.

What this measures is reading, not fixing: a better reader gives a truer "done", and no more than that.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from g6.core.vault import Vault
from g6.harness import Check
from g6.metaharness.domain import Execution, FailureRecord, Judge, SealedSplit, reason_coded
from g6.metaharness.pool import EditSpace
from g6.metaharness.propose import ProposerView
from g6.metaharness.recovery import WRONG_ANSWER
from g6.metaharness.schema import Model, SuiteCase

RIGHT, WRONG = "right", "wrong"
ACCEPTED_DEFECT, FALSE_ALARM = "accepted_defect", "false_alarm"
_VERDICT = re.compile(r"^VERDICT:\s*(RIGHT|WRONG)\s*$", re.M | re.I)
READ_AS_WRITTEN = ("You review a candidate bug fix written by someone else. State the rule the issue implies, read the patch against it, and "
                   "end with one line: VERDICT: RIGHT if it fully meets the issue, or VERDICT: WRONG if it does not.")
TASK = "Review this candidate fix.\n\n{input}"


def verdict_of(reply: str) -> str | None:
    """The reviewer's own last verdict line; a patch's text may hold the word, so nothing else counts."""
    found = _VERDICT.findall(reply or "")
    return found[-1].lower() if found else None


def right_verdict(output: str, expected: Any) -> bool:
    """The judge: only the verdict on the maintainers' label counts, and no verdict is never right."""
    return verdict_of(output) == expected


def _says_a_verdict(reply: str) -> bool:
    return verdict_of(reply) is not None


def load_cases(path: str | Path) -> tuple[SuiteCase, ...]:
    """Cases from a JSON-lines file of ``id``, ``bug``, ``input`` and ``label``. A case missing any of them is refused, not skipped."""
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("label") not in (RIGHT, WRONG) or not row.get("bug") or not row.get("input") or not row.get("id"):
            raise ValueError(f"a case needs an id, a bug, an input and a label of right or wrong: {str(row.get('id'))!r}")
        cases.append(SuiteCase(row["id"], row["input"], row["label"], meta={"group": row["bug"]}))
    return tuple(cases)


class CodingReplay:
    """``cases``: the development pool. ``sealed``: a file alone in its folder, which the loop holds only as a vault. ``caller``: the
    reviewer's model, declared unpaid: the loop never spends API credits."""

    name = "coding-replay"

    def __init__(self, cases: str | Path, sealed: str | Path | None, caller: Any, *, prompt: str = READ_AS_WRITTEN) -> None:
        self._cases, self.llm, self._prompt = load_cases(cases), caller, prompt
        self._labels = {c.case_id: c.expected for c in self._cases}
        self._sealed = None
        if sealed is not None:
            held = load_cases(sealed)
            shared = {c.meta["group"] for c in held} & {c.meta["group"] for c in self._cases}
            if shared:
                raise ValueError(f"these bugs are on both sides of the seal: {sorted(shared)[:5]}")
            self._sealed = SealedSplit(Vault.seal([Path(sealed).parent], ledger=None, vault_id=self.name), tuple(c.ref() for c in held))

    def development(self) -> tuple[SuiteCase, ...]:
        return self._cases

    def sealed(self) -> SealedSplit | None:
        return self._sealed

    def execution(self) -> Execution:
        check = Check("the reply ends in a verdict", _says_a_verdict, check_id="ends-in-verdict", known_bad=("", "no verdict here"),
                      known_good="VERDICT: RIGHT")
        return Execution(task=TASK, checks=(check,), models={"reviewer": Model(self.llm)}, space=EditSpace({"retries": [0, 1]}),
                         initial={"routing": {"answer": "reviewer"}, "prompt": self._prompt})

    def judge(self) -> Judge | None:
        return Judge(right_verdict, answers_by="independent")

    def failures(self, view: ProposerView) -> tuple[FailureRecord, ...]:
        """A wrong reading is one of two causes, which pull a prompt in opposite directions: a defect let through, or a false alarm."""
        found = []
        for record in reason_coded(view):
            cause = record.cause
            if cause == WRONG_ANSWER:
                cause = ACCEPTED_DEFECT if self._labels.get(record.case_id) == WRONG else FALSE_ALARM
            found.append(FailureRecord(record.case_id, cause, record.changeable, record.note, record.independently_observed))
        return tuple(found)


__all__ = ["ACCEPTED_DEFECT", "FALSE_ALARM", "READ_AS_WRITTEN", "CodingReplay", "load_cases", "right_verdict", "verdict_of"]
