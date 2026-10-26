# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.agents' runtime, and what a person does about each (:func:`action`).

A run that finished is ``qualified-draft`` with :data:`SELF_REPORTED` (or :data:`DRY_RUN`); every other code on an
outcome is ``blocked-escalated``. The gate's codes are on each ``ToolDecision``: :data:`TOOL_ALLOWED` on a ``run``
verdict, :data:`DRY_RUN` on a ``dry_run`` verdict, and one of the refusal codes on a ``refused`` verdict.
"""
from __future__ import annotations

from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.resources import RESOURCE_LIMIT
from g6.core.stop import STOPPED

_P = "G6_E_AGENT_"
# how a run ended
SELF_REPORTED = _P + "SELF_REPORTED"
DRY_RUN = _P + "DRY_RUN"
NOT_A_RUNTIME = _P + "NOT_A_RUNTIME"
FAILED = _P + "FAILED"
BAD_EVENT = _P + "BAD_EVENT"
NO_OUTCOME = _P + "NO_OUTCOME"
STEP_LIMIT = _P + "STEP_LIMIT"
DEADLINE = _P + "DEADLINE"
CIRCUIT_OPEN = _P + "CIRCUIT_OPEN"
UNGATED_TOOL = _P + "UNGATED_TOOL"
WORKSPACE = _P + "WORKSPACE"
OUTPUT_TOO_LARGE = _P + "OUTPUT_TOO_LARGE"
CLEANUP_UNCONFIRMED = _P + "CLEANUP_UNCONFIRMED"
START_FAILED = _P + "START_FAILED"
EXIT_FAILED = _P + "EXIT_FAILED"
NOT_VERSIONED = _P + "NOT_VERSIONED"
# what the gate decided about one call
TOOL_ALLOWED = _P + "TOOL_ALLOWED"
TOOL_UNDECLARED = _P + "TOOL_UNDECLARED"
PERMISSION = _P + "PERMISSION"
PATHS_REQUIRED = _P + "PATHS_REQUIRED"
OUTSIDE_WORKSPACE = _P + "OUTSIDE_WORKSPACE"
TOOL_LIMIT = _P + "TOOL_LIMIT"
POLICY_BLOCKED = _P + "POLICY_BLOCKED"
GATE_BLOCKED = _P + "GATE_BLOCKED"
GATE_ERROR = _P + "GATE_ERROR"
CALL_REUSED = _P + "CALL_REUSED"
# what a gated tool call came to (GatedCalls)
TOOL_FAILED = _P + "TOOL_FAILED"
SELF_CALL = _P + "SELF_CALL"
# the agent_runtime block's own answers
TASK_CHECKED = _P + "TASK_CHECKED"
DECIDED = _P + "DECIDED"
TASK_REQUIRED = _P + "TASK_REQUIRED"
CALL_REQUIRED = _P + "CALL_REQUIRED"

_ACTIONS = {
    SELF_REPORTED: "Check the output before relying on it: g6.harness.run with checks of your own, frozen before the "
                   "run, or, for a change to code, g6.workforce.submit_patch.",
    DRY_RUN: "Read the decisions: they say what would run. To carry it out, run again with dry_run=false.",
    NOT_A_RUNTIME: "Pass an agent adapter: an object with a name, available() and run(task, *, gate, usage, stop).",
    FAILED: "The adapter raised an error. Read the reason, fix the adapter or the program it drives, and run again.",
    BAD_EVENT: "The adapter reported something that is not an AgentEvent. Fix the adapter.",
    NO_OUTCOME: "The adapter stopped without finishing. Read its events, then run again.",
    STEP_LIMIT: "Raise max_steps, within the policy's ceiling, or split the task.",
    DEADLINE: "Raise the time limit, within the policy's ceiling, or narrow the task.",
    STOPPED: "Whoever engaged G6's stop releases it; then run again.",
    RESOURCE_LIMIT: "A resource limit you set was reached. Raise it, or wait for its window to pass.",
    CIRCUIT_OPEN: "The adapter failed several times in a row. Wait for the cooldown, and fix what made it fail.",
    UNGATED_TOOL: "Do not use this run: the adapter reported a tool it ran without the gate's leave. Fix the adapter "
                  "so it asks the gate before each call.",
    WORKSPACE: "Name the folder the agent works in by its full path: an existing project folder, not a drive's root, "
               "your home folder or a folder that holds it, and not G6's own state folder or installed code.",
    OUTPUT_TOO_LARGE: "The agent's output passed its cap, so it was stopped and its process tree ended. Narrow the task.",
    CLEANUP_UNCONFIRMED: "G6 could not confirm the agent's processes ended. Look for leftover processes before running "
                         "again.",
    START_FAILED: "The program could not be started. Check that it runs from a terminal as you.",
    EXIT_FAILED: "The program exited with an error. Read the end of its output in the evidence, fix the cause (often "
                 "its sign-in), and run again.",
    NOT_VERSIONED: "Put the folder under version control (git init, then a first commit) so you can see and undo what "
                   "the agent changes, or run read-only.",
    OUTSIDE_PROGRAM_MISSING: "Install the program yourself as the reason says; G6 never installs it.",
    TOOL_ALLOWED: "Run the tool, then report its result with this call's id.",
    TOOL_UNDECLARED: "Declare the tool in the task, with what it does, or do not call it.",
    PERMISSION: "Grant the permission in the task's permissions if you mean the agent to have it, or do not call the "
                "tool.",
    PATHS_REQUIRED: "Name the files or folders the call touches in its paths.",
    OUTSIDE_WORKSPACE: "Keep the call inside the workspace: nothing outside it is the agent's to touch.",
    TOOL_LIMIT: "The run reached a ceiling of the trusted policy. Raise the limit, within the ceiling, or split the "
                "task.",
    POLICY_BLOCKED: "The trusted policy refused the call. Read the reason.",
    GATE_BLOCKED: "g6.action_gating did not allow the action. A person reads its message and decides, outside G6.",
    GATE_ERROR: "The gate could not decide, so the call was refused. Fix what the reason names.",
    CALL_REUSED: "Give each call its own call_id.",
    TOOL_FAILED: "The tool raised an error or is missing; its result says which. Fix the tool, or pass it in.",
    SELF_CALL: "An agent may not start another run of the component that runs it.",
    TASK_CHECKED: "Run the task through an adapter from Python (run_agent), and decide each call with decide first.",
    DECIDED: "Act only on a run verdict: dry_run and refused both mean do not run the tool.",
    TASK_REQUIRED: "Give a task: its goal, and the tools, permissions and limits the agent may use.",
    CALL_REQUIRED: "Give the call to decide: its tool, arguments and paths.",
}
_DEFAULT = "Read the reason, correct what it names, and run again."


def action(code: str) -> str:
    """What a person does about ``code``."""
    return _ACTIONS.get(code, _DEFAULT)


__all__ = ["BAD_EVENT", "CALL_REQUIRED", "CALL_REUSED", "CIRCUIT_OPEN", "CLEANUP_UNCONFIRMED", "DEADLINE", "DECIDED",
           "DRY_RUN", "EXIT_FAILED", "FAILED", "GATE_BLOCKED", "GATE_ERROR", "NOT_A_RUNTIME", "NOT_VERSIONED", "NO_OUTCOME",
           "OUTPUT_TOO_LARGE", "OUTSIDE_PROGRAM_MISSING", "OUTSIDE_WORKSPACE", "PATHS_REQUIRED", "PERMISSION",
           "POLICY_BLOCKED", "RESOURCE_LIMIT", "SELF_CALL", "SELF_REPORTED", "START_FAILED", "STEP_LIMIT", "STOPPED",
           "TASK_CHECKED", "TASK_REQUIRED", "TOOL_ALLOWED", "TOOL_FAILED", "TOOL_LIMIT", "TOOL_UNDECLARED",
           "UNGATED_TOOL",
           "WORKSPACE", "action"]
