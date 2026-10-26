# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Objective contract types for action-logic decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObjectiveContract:
    """Explicit objective required by G6_Action_Logic.md G6-INV-01."""

    desired_outcome: str
    protected_constraints: tuple[str, ...] = ()
    priorities: tuple[str, ...] = ()
    success_indicators: tuple[str, ...] = ()
    failure_indicators: tuple[str, ...] = ()
    time_horizons: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    authority: str = ""

    def is_adequate(self) -> bool:
        return bool(
            self.desired_outcome.strip()
            and self.success_indicators
            and self.stop_conditions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired_outcome": self.desired_outcome,
            "protected_constraints": list(self.protected_constraints),
            "priorities": list(self.priorities),
            "success_indicators": list(self.success_indicators),
            "failure_indicators": list(self.failure_indicators),
            "time_horizons": list(self.time_horizons),
            "stop_conditions": list(self.stop_conditions),
            "authority": self.authority,
        }


__all__ = ["ObjectiveContract"]
