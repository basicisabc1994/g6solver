# SPDX-License-Identifier: Apache-2.0
"""The stable ``G6_E_SELF_MAINTENANCE_*`` codes, and the refusal the live agent path raises."""
from __future__ import annotations

INPUT = "G6_E_SELF_MAINTENANCE_INPUT"
CONSTRAINT = "G6_E_SELF_MAINTENANCE_CONSTRAINT"
PLAN = "G6_E_SELF_MAINTENANCE_PLAN"
LIVE_AGENT_OFF = "G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF"


class SelfMaintenanceRefused(Exception):
    """Something the maintenance loop will not do, with its stable code and the reason in words."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


__all__ = ["CONSTRAINT", "INPUT", "LIVE_AGENT_OFF", "PLAN", "SelfMaintenanceRefused"]
