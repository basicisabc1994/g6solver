# SPDX-License-Identifier: Apache-2.0

"""Action-gating audit logger.

In-memory by default; when constructed with a durable ``store``
(``ActionGateAuditStore``) it ALSO persists every record to a tamper-evident
SQLite log that survives process restart. A durable-write failure raises
``RuntimeError`` exactly like an unavailable/failing logger, so the caller's
fail-closed audit path blocks the action rather than proceeding unaudited.
``evaluate_action`` uses the durable default trail (``ActionGateAuditStore.default()``)
unless it is given a logger (audit 002, AG-009).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from g6.action_gating.audit_store import ActionGateAuditStore
from g6.action_gating.capabilities import discover_capabilities
from g6.action_gating.provenance import provenance_for_decision
from g6.action_gating.schema import (
    ActionCapsule,
    ActionGateAuditRecord,
    GateDecision,
)


def audit_required(capsule: ActionCapsule, decision: GateDecision | None = None) -> bool:
    risk = decision.risk.aggregate_risk if decision else "unknown"
    return (
        capsule.external_side_effect
        or capsule.real_world_effect
        or capsule.irreversible_or_costly_to_reverse
        or risk in {"medium", "high", "critical", "unknown"}
    )


@dataclass
class ActionGateAuditLogger:
    available: bool = True
    fail_writes: bool = False
    records: list[ActionGateAuditRecord] = field(default_factory=list)
    store: ActionGateAuditStore | None = None

    def write(self, capsule: ActionCapsule, decision: GateDecision) -> ActionGateAuditRecord:
        if not self.available:
            raise RuntimeError("audit_logger unavailable")
        if self.fail_writes:
            raise RuntimeError("audit_logger write failed")
        record = ActionGateAuditRecord(
            audit_id=decision.audit_id,
            action_id=capsule.action_id,
            timestamp=datetime.now(UTC).isoformat(),
            action_capsule=capsule,
            capabilities_snapshot=discover_capabilities(audit_available=self.available),
            degradation=decision.degradation,
            risk_assessment=decision.risk,
            scenario_model=decision.scenario_model,
            rumsfeld_matrix=decision.rumsfeld_matrix,
            mitigation_plan=decision.mitigation_plan,
            user_mental_model=decision.user_mental_model,
            safety_qa=decision.safety_qa,
            decision=decision.decision,
            reason_codes=decision.reason_codes,
            evidence=decision.evidence,
            user_message=decision.required_user_message,
            confirmation_required=decision.required_confirmation_phrase is not None,
            confirmation_phrase=decision.required_confirmation_phrase,
            gate_token_issued=decision.gate_token is not None,
            gate_token_id=decision.gate_token,
            # PA-15: tag the durable entry with its evidence-provenance class so the
            # inspectable trail distinguishes what backed the decision.
            provenance_class=provenance_for_decision(capsule, decision).value,
        )
        self.records.append(record)
        if self.store is not None:
            # Durable, tamper-evident persistence. A backing-store failure is an
            # audit failure: re-raise as RuntimeError so the caller's fail-closed
            # path blocks the action rather than proceeding with a lost record.
            try:
                self.store.append(record)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"audit_logger durable write failed: {exc}") from exc
        return record
