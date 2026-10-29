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
"""
from g6.self_maintenance.block import MaintenanceInput, SelfMaintenanceBlock
from g6.self_maintenance.codes import SelfMaintenanceRefused

__g6_component__ = True
__g6_block__ = "SelfMaintenanceBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("action_gating", "agent_claude", "agent_codex", "agents", "operator",
                                     "safety_engine")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a measured
# constraint that is not what limits the people G6 serves).
__g6_mitigates__: tuple[str, ...] = ("low_value_first", "unchecked_self_change")
__g6_introduces__: tuple[str, ...] = ("wrong_problem",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["MaintenanceInput", "SelfMaintenanceBlock", "SelfMaintenanceRefused"]
