# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ceilings.py -- hard runtime ceiling enforcement.

``exceeds_ceiling`` projects each ceiling axis *before* an action runs and
returns the first breached ceiling. The policy engine calls it on every action;
a breach yields a BLOCK decision whose reason contains the word "ceiling"
(SELF_CORRECTION.md §9 / §14.4). Every value compared against comes from the
trusted :class:`RuntimePolicy` — never from planner output — so a planner that
"states the ceiling is waived" changes nothing.
"""
from __future__ import annotations

from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState

#: How a cost-axis breach begins (the workforce door reads it to record ``refused:budget``, Phase E E1c).
COST_CEILING = "cost ceiling would be exceeded"
#: How a wall-clock breach begins.
RUNTIME_CEILING = "runtime ceiling exceeded"


def exceeds_ceiling(
    action: ActionRequest, state: RunState, policy: RuntimePolicy
) -> tuple[bool, str]:
    """Return ``(exceeded, reason)`` for *action* against the trusted *policy*.

    Checked axes (SELF_CORRECTION.md §9): tool calls, steps (decisions asked
    for), shell commands, files modified, lines changed, retries, wall-clock
    runtime, privilege level, and
    (Phase E E1c) cost: what the run was charged so far plus the most this
    action may cost, against ``max_cost_cents``. The reason string always
    contains "ceiling" so the run's terminal reason is self-describing.
    """
    if state.tool_calls_after() > policy.max_tool_calls:
        return True, (
            f"tool-call ceiling would be exceeded "
            f"({state.tool_calls_after()} > {policy.max_tool_calls})"
        )

    if state.steps_after() > policy.max_steps:
        return True, (
            f"step ceiling would be exceeded "
            f"({state.steps_after()} > {policy.max_steps})"
        )

    if state.shell_commands_after(action) > policy.max_shell_commands:
        return True, (
            f"shell-command ceiling would be exceeded "
            f"({state.shell_commands_after(action)} > {policy.max_shell_commands})"
        )

    if state.files_modified_count_after(action) > policy.max_files_modified:
        return True, (
            f"files-modified ceiling would be exceeded "
            f"({state.files_modified_count_after(action)} > {policy.max_files_modified})"
        )

    if state.lines_changed_after(action) > policy.max_lines_changed:
        return True, (
            f"lines-changed ceiling would be exceeded "
            f"({state.lines_changed_after(action)} > {policy.max_lines_changed})"
        )

    if state.retries > policy.max_retries:
        return True, (
            f"retry ceiling exceeded ({state.retries} > {policy.max_retries})"
        )

    if state.elapsed_seconds() > policy.max_runtime_seconds:
        return True, (
            f"{RUNTIME_CEILING} "
            f"({state.elapsed_seconds():.1f}s > {policy.max_runtime_seconds}s)"
        )

    if int(action.privilege_level) > int(policy.max_privilege_level):
        return True, (
            f"privilege ceiling exceeded "
            f"({action.privilege_level} > {policy.max_privilege_level})"
        )

    if state.cost_after(action) > policy.max_cost_cents:
        return True, (
            f"{COST_CEILING} "
            f"({state.cost_after(action)} > {policy.max_cost_cents} cents)"
        )

    return False, ""
