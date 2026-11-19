# SPDX-License-Identifier: Apache-2.0
"""Seeded maintenance sampling for admitted replacements on decided scope."""
from __future__ import annotations

from pydantic import TypeAdapter

from g6.core.ledger import digest

from .graph_records import ExecutionRecord
from .records import Model, Sha256, Verdict

SHADOW_RATE = 0.05


def shadow_selected(case_id: str, *, seed: int) -> bool:
    TypeAdapter(Sha256).validate_python(case_id)
    if type(seed) is not int:
        raise ValueError("integer seed required")
    return int(digest(["shadow-audit.v1", seed, case_id]), 16) * 20 < 2**256


class ShadowRecord(Model):
    case_id: Sha256
    seed: int
    program_verdict: Verdict
    teacher_verdict: Verdict
    disagrees: bool
    attempt_id: Sha256


class ShadowAudit:
    def __init__(self, *, seed: int, ledger):
        if type(seed) is not int:
            raise ValueError("integer seed required")
        self.seed, self.ledger = seed, ledger
        self.records: list[ShadowRecord] = []

    @property
    def disagreements(self):
        return [row for row in self.records if row.disagrees]

    def run(self, case_id, *, program_verdict, admitted_replacement: bool, in_scope: bool, teacher):
        """The host supplies admission/scope and an injected teacher runner.

        A runner may return a verdict or ExecutionRecord; either way it must use
        the model send hook. Failed audits remain costed attempts, not verdicts.
        """
        if (admitted_replacement is not True or in_scope is not True
                or program_verdict not in {"concern", "no_concern"}
                or not shadow_selected(case_id, seed=self.seed)):
            return None
        with self.ledger.measure("shadow:" + case_id, category="maintenance") as meter:
            result = teacher()
            if isinstance(result, ExecutionRecord):
                result = ExecutionRecord.model_validate_json(result.model_dump_json())
                meter.cost = result.cost
                if result.terminal not in {"ok", "abstain"}:
                    meter.outcome = "failed"
                    return None
                verdict = result.root_output
            else:
                verdict = result
            TypeAdapter(Verdict).validate_python(verdict)
            meter.outcome = "screened"
        row = ShadowRecord(case_id=case_id, seed=self.seed, program_verdict=program_verdict,
            teacher_verdict=verdict, disagrees=program_verdict != verdict, attempt_id=meter.record.attempt_id)
        self.records.append(row)
        return row
