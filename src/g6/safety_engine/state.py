# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""state.py -- the mutable RunState for a single safety-governed run.

``RunState`` carries the cumulative ceiling counters, the run-scoped kill
switch, and the trusted :class:`RuntimePolicy`. The policy reference is set once
at construction from trusted config and is never reassigned from planner output
(SELF_CORRECTION.md §9). The projection helpers (``*_after``) let the policy
engine check a ceiling *before* an action runs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from g6.safety_engine.kill_switch import KillSwitch
from g6.safety_engine.schema import ActionRequest, RunStatus, RuntimePolicy


@dataclass
class RunState:
    policy: RuntimePolicy
    run_id: str = ""
    kill_switch: KillSwitch = field(default_factory=KillSwitch)

    # Cumulative counters (consumed ceilings).
    steps: int = 0
    tool_calls: int = 0
    shell_commands: int = 0
    lines_changed: int = 0
    retries: int = 0
    cost_cents: int = 0
    files_modified: set[str] = field(default_factory=set)
    #: Whether the run's latest test run passed, as trusted runtime code recorded it (``record_tests``).
    tests_passed: bool = False

    status: RunStatus = "RUNNING"
    done: bool = False

    # Injectable monotonic clock (seconds) for deterministic runtime-ceiling
    # tests; defaults to the real clock. Stamped at first elapsed() read.
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _started_at: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Anchor the runtime clock at construction so max_runtime_seconds is
        # measured from the run's start.
        self._started_at = self._clock()

    # -- runtime ----------------------------------------------------------
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            self._started_at = self._clock()
        return max(0.0, self._clock() - self._started_at)

    # -- ceiling projections (read-only; no mutation) ---------------------
    def steps_after(self) -> int:
        return self.steps + 1

    def tool_calls_after(self) -> int:
        return self.tool_calls + 1

    def shell_commands_after(self, action: ActionRequest) -> int:
        return self.shell_commands + (1 if action.is_shell_command else 0)

    def files_modified_count_after(self, action: ActionRequest) -> int:
        return len(self.files_modified.union(action.files_touched))

    def lines_changed_after(self, action: ActionRequest) -> int:
        return self.lines_changed + max(0, int(action.estimated_lines_changed))

    def cost_after(self, action: ActionRequest) -> int:
        """What the run would have been charged once ``action`` cost the most it may (a negative ceiling is 0)."""
        return self.cost_cents + max(0, int(action.cost_ceiling_cents))

    # -- commit an executed action (called only after authorization) ------
    def record_execution(self, action: ActionRequest) -> None:
        self.tool_calls += 1
        if action.is_shell_command:
            self.shell_commands += 1
        self.files_modified.update(action.files_touched)
        self.lines_changed += max(0, int(action.estimated_lines_changed))

    def record_step(self) -> None:
        """One decision asked of the policy engine (it calls this itself, allowed or refused)."""
        self.steps += 1

    def record_retry(self) -> None:
        self.retries += 1

    def record_tests(self, passed: bool) -> None:
        """Record the outcome of the run's latest test run; only a boolean ``True`` is a pass.

        The code that ran the tests calls this. A planner saying the tests pass is not a test run."""
        self.tests_passed = passed is True

    def record_cost(self, cents: int) -> None:
        """Charge what an executed action cost: its actual, or its ceiling when no actual is reported (E1c)."""
        self.cost_cents += max(0, int(cents))
