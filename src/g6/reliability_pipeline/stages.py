# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Stage primitives for the reliability state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from g6.core.evidence import EvidenceBundle


class Stage(StrEnum):
    INTAKE = "stage_0_intake"
    PREMORTEM = "stage_1_premortem"
    RISK_TIERING = "stage_2_risk_tiering"
    EPISTEMIC_CONTRACT = "stage_3_epistemic_contract"
    PLAN_BUDGET = "stage_4_plan_budget"
    # UI.md C5 (2026-06-12): advisory agent_router consultation — recorded
    # between plan/budget and execution; it never blocks or dispatches.
    ROUTING = "stage_4b_routing"
    EXECUTION = "stage_5_execution"
    VERIFICATION = "stage_6_verification"
    HANDOFF = "stage_7_handoff"
    LEARNING = "stage_8_learning"


class StageState(StrEnum):
    ADVANCE = "advance"
    DOWNGRADE = "downgrade"
    BLOCK = "block"


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    state: StageState
    reason: str = ""
    safe_next_action: str = ""
    evidence: EvidenceBundle | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def advance(cls, stage: Stage, **metadata) -> "StageResult":
        return cls(stage=stage, state=StageState.ADVANCE, metadata=metadata)

    @classmethod
    def downgrade(cls, stage: Stage, reason: str, **metadata) -> "StageResult":
        return cls(stage=stage, state=StageState.DOWNGRADE, reason=reason, metadata=metadata)

    @classmethod
    def block(cls, stage: Stage, reason: str, safe_next_action: str, **metadata) -> "StageResult":
        return cls(
            stage=stage,
            state=StageState.BLOCK,
            reason=reason,
            safe_next_action=safe_next_action,
            metadata=metadata,
        )


__all__ = ["Stage", "StageResult", "StageState"]
