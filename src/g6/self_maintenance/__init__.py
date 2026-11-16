# SPDX-License-Identifier: Apache-2.0
"""g6.self_maintenance — names the constraint of a measured workflow and plans the next maintenance ticket within a token budget, running nothing.

The theory of constraints over G6's own work: ``identify_constraint`` names the stage of a measured serial workflow
with the least effective capacity, only when every stage was independently observed; ``focus_plan`` ranks the tickets
for that stage within a token budget (``BudgetBook`` and ``BudgetPolicy``, an append-only token ledger). Over MCP both
are read-only plans that reserve nothing.

``controller.run_one_cycle`` is the host's Python API for one maintenance cycle on a disposable copy. Nothing runs
without a person's approval in the operator's queue (``ActionQueue.request_run`` and ``run_approver``) bound to exactly
that ticket, provider and day, and the report names who approved. The live agent path (``run_existing_agent``, Codex
or Claude Code with writes) refuses in this release (``G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF``) before any runtime is
built; it waits for the mission container of a later phase.

``pacing`` (a paced week per provider, on the operator's ledger) and ``envelope`` (the constraint log, the stop file,
the heartbeat and the failed-cycle streak) know nothing of what the work is. The tick that uses them needs the
meta-harness, so it lives there, in ``g6.metaharness.loop``: the layer rule lets that package import this one, and
never the reverse.
"""
from g6.self_maintenance.block import MaintenanceInput, SelfMaintenanceBlock
from g6.self_maintenance.codes import SelfMaintenanceRefused

__g6_component__ = True
__g6_block__ = "SelfMaintenanceBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("action_gating", "agent_claude", "agent_codex", "agents", "operator",
                                     "safety_engine", "workspace")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a measured
# constraint that is not what limits the people G6 serves).
__g6_mitigates__: tuple[str, ...] = ("low_value_first", "unchecked_self_change")
__g6_introduces__: tuple[str, ...] = ("wrong_problem",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["MaintenanceInput", "SelfMaintenanceBlock", "SelfMaintenanceRefused"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_LLM_HOST_FAILED": "person",
    "G6_E_LLM_RATE_LIMITED": "retry",
    "G6_E_LOOP_ENROLMENT_CHANGED": "none",
    "G6_E_LOOP_HOST_UNKNOWN": "person",
    "G6_E_LOOP_INPUT_CHANGED": "person",
    "G6_E_LOOP_STOPPED": "person",
    "G6_E_LOOP_STOP_CANONICAL": "none",
    "G6_E_LOOP_STOP_CANONICAL_FAILED": "none",
    "G6_E_LOOP_STOP_COUNT_FAILED": "none",
    "G6_E_LOOP_STOP_READ_FAILED": "none",
    "G6_E_LOOP_STOP_REASON_ESCAPED": "none",
    "G6_E_LOOP_STOP_REASON_FAILED": "none",
    "G6_E_LOOP_STOP_STATE_UNREADABLE": "none",
    "G6_E_LOOP_STOP_UNREADABLE": "none",
    "G6_E_LOOP_STOP_WRITE_FAILED": "none",
    "G6_E_LOOP_TICK_BUDGET_SPENT": "person",
    "G6_E_LOOP_UNNAMED_REQUEST": "none",
    "G6_E_LOOP_UNOBSERVABLE_HOST": "person",
    "G6_E_LOOP_UNWATCHED_HOST": "person",
    "G6_E_MODEL_AT_CAPACITY": "person",
    "G6_E_MODEL_REFUSED": "person",
    "G6_E_SELF_MAINTENANCE_CONSTRAINT": "none",
    "G6_E_SELF_MAINTENANCE_INPUT": "input",
    "G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF": "none",
    "G6_E_SELF_MAINTENANCE_PLAN": "none",
    "G6_E_OPERATOR_APPROVAL_MISMATCH": "person",
}
