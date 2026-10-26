# SPDX-License-Identifier: Apache-2.0
"""g6.operator — a simulation-first outer loop for a venture: budgeted ticks, and an approval queue a person decides.

Capability label: ``implemented``.

    from g6.harness import Check
    from g6.operator import ActionQueue, ExecutionResult, OperatorStore, SimulatedExecutor, Task, tick
    scope = OperatorStore().for_owner("dana")
    venture = scope.create_venture("newsletter", caps={"model": 5.0, "cash": 0.0})
    queue = ActionQueue(scope)
    executor = SimulatedExecutor({"t1": ExecutionResult("Welcome, and thanks for joining.",
                                                        proposals=({"type": "post", "channel": "blog",
                                                                    "body": "Welcome!", "verification_contract": vc},))})
    task = Task("t1", "Write the welcome post", reserve=0.5, checks=(Check("says welcome", lambda o: "Welcome" in o),))
    reply = tick(queue, venture["venture_id"], task, executor)
    queue.approve(venture["venture_id"], reply["queued"][0], reviewer="dana")   # a person, never the executor

Each tick reserves the task's cost against the month's budget in the same transaction as the stop check, runs the
task on an executor, judges the output with the task's checks from the operator's own record, and sends every
outward draft through ``g6.action_gating.evaluate_action`` and the deterministic draft checks (``route``) into the
queue as ``proposed`` (see ``g6.operator.loop``). State lives in ``$G6_HOME/operator.db``: owner-scoped ventures,
append-only evidence, a monthly ledger and the queue's append-only decisions.

What it does not do (audit 011): it sends, posts, publishes, pays and deploys nothing, and opens no outward channel.
Optional task proposals use an explicitly injected model caller. The archive's Claude Code and local executors and its MCP approve tools are not ported (VEN-001, VEN-002,
VEN-003); the only executor here is ``SimulatedExecutor``. The queue holds declared drafts only: a real executor the
caller plugs in can do anything its own process can, so run it in a sandbox the caller controls. The queue refuses a
decision by the drafting actor or an ``auto:`` reviewer, but it cannot tell a person from a program that passes a
person's name. Its checks are deterministic text checks and the caller's own, so nothing it returns is ``verified``.
There is no auto-approval, no per-type autonomy tier and no learning layer that writes guards (VEN-012, VEN-013).
"""
from g6.operator.gates import ACCOUNTS, spend_refusal, stop_reason
from g6.operator.queue import (
    GUARD_CANDIDATE, OUTWARD_TYPES, ActionQueue, Estimate, QueueError, TaskProposal, propose_tasks, rank,
)
from g6.operator.router import Context, Finding, Guard, Routed, route
from g6.operator.store import BudgetRefused, OperatorStore, OwnerScope
from g6.operator.loop import ExecutionResult, Executor, SimulatedExecutor, Task, gate, tick

__g6_mitigates__: tuple[str, ...] = ("low_value_first",)
__g6_introduces__: tuple[str, ...] = ()
__g6_sensitive_ops__: tuple[str, ...] = ("propose_tasks",)

__all__ = [
    "ACCOUNTS", "GUARD_CANDIDATE", "OUTWARD_TYPES", "ActionQueue", "BudgetRefused", "Context", "ExecutionResult",
    "Executor", "Finding", "Guard", "OperatorStore", "OwnerScope", "QueueError", "Routed", "SimulatedExecutor", "Task",
    "Estimate", "TaskProposal", "propose_tasks", "rank",
    "gate", "route", "spend_refusal", "stop_reason", "tick",
]
