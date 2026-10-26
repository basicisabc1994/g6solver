# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""policy_engine.py -- the pre-action decision.

``PolicyEngine.authorize_action`` checks, in this order:

    kill switch -> ceiling -> guard BLOCK (trips kill switch) -> guard ESCALATE
    -> quorum -> allow-list -> forbidden path -> tests before a final action -> ALLOW

Every decision asked for past the ceilings is one step of the run (``RunState.steps``), allowed or refused, so
``max_steps`` bounds a planner that keeps proposing refused actions as well as one that keeps acting.
The kill-switch check reads the run's own switch and the process-wide stop (``g6.core.stop``; audit 007 SE-010). It
returns a decision only. It mints no token: the code that runs the action must obey the decision, and that code is
the authority boundary (in the library, the workforce's call door). A planner emits only data and never reaches it.
"""
from __future__ import annotations

import posixpath
from collections.abc import Sequence

from g6.core import stop
from g6.safety_engine.ceilings import exceeds_ceiling
from g6.safety_engine.quorum import quorum_satisfied
from g6.safety_engine.schema import (
    ActionRequest,
    AuthorizationDecision,
    GuardVerdict,
    RuntimePolicy,
    ValidatorVote,
)
from g6.safety_engine.state import RunState


class PolicyEngine:
    """Authorizes (or refuses) one action at a time against trusted policy."""

    def __init__(self) -> None:
        # Instrumentation for path-coverage assertions.
        self.was_called = False

    def authorize_action(
        self,
        action: ActionRequest,
        state: RunState,
        policy: RuntimePolicy,
        guard_verdicts: Sequence[GuardVerdict] = (),
        validator_votes: Sequence[ValidatorVote] = (),
    ) -> AuthorizationDecision:
        self.was_called = True
        guard_verdicts = tuple(guard_verdicts)
        validator_votes = tuple(validator_votes)
        checks: list[str] = []

        def block(reason: str) -> AuthorizationDecision:
            return AuthorizationDecision(
                decision="BLOCK",
                reason=reason,
                permitted_action=None,
                policy_checks=tuple(checks),
                guard_verdicts=guard_verdicts,
                validator_votes=validator_votes,
            )

        def escalate(reason: str) -> AuthorizationDecision:
            return AuthorizationDecision(
                decision="ESCALATE",
                reason=reason,
                permitted_action=None,
                policy_checks=tuple(checks),
                guard_verdicts=guard_verdicts,
                validator_votes=validator_votes,
            )

        # 1. Kill switch outranks everything below it.
        checks.append("kill_switch")
        if state.kill_switch.tripped:
            return block(f"kill switch tripped: {state.kill_switch.reason}")
        stopped = stop.engaged()
        if stopped is not None:
            return block(f"G6 is stopped: {stopped}")

        # 2. Hard runtime ceilings (trusted policy, never planner output).
        checks.append("ceiling")
        exceeded, ceiling_reason = exceeds_ceiling(action, state, policy)
        if exceeded:
            return block(f"runtime ceiling would be exceeded: {ceiling_reason}")
        state.record_step()  # this decision is one of the run's max_steps, whatever it decides

        # 3. Pattern-guard BLOCK trips the kill switch (a hard injection signal).
        checks.append("guard_block")
        if any(v.decision == "BLOCK" for v in guard_verdicts):
            reasons = "; ".join(
                v.reason for v in guard_verdicts if v.decision == "BLOCK"
            )
            state.kill_switch.trip(f"guard block verdict: {reasons}")
            return block(f"blocked by pattern guard: {reasons}")

        # 4. Pattern-guard ESCALATE requires trusted approval before the executor.
        checks.append("guard_escalate")
        if any(v.decision == "ESCALATE" for v in guard_verdicts):
            reasons = "; ".join(
                v.reason for v in guard_verdicts if v.decision == "ESCALATE"
            )
            return escalate(f"guard requested escalation: {reasons}")

        # 5. Validator quorum (pass-through unless require_quorum is configured).
        checks.append("quorum")
        if policy.require_quorum and not quorum_satisfied(validator_votes, policy):
            return block("validator quorum not reached")

        # 6. Tool allow-list. Default-deny once a non-empty list exists (§7);
        # an empty allowed_tools means "allow any" — enforcement is derived from
        # non-emptiness, so a populated list can never silently fail open.
        checks.append("allowed_tools")
        if policy.allowed_tools and action.tool_name not in policy.allowed_tools:
            return block(f"tool not allowed: {action.tool_name!r}")

        # 7. Forbidden paths.
        checks.append("forbidden_paths")
        if _touches_forbidden_path(action, policy):
            return block("forbidden path touched")

        # 8. A final answer waits for a passing test run the run itself recorded.
        checks.append("tests_before_final")
        if action.is_final and policy.require_tests_before_final and not state.tests_passed:
            return block("final action before the run recorded a passing test run")

        checks.append("allow")
        return AuthorizationDecision(
            decision="ALLOW",
            reason="authorized",
            permitted_action=action,
            policy_checks=tuple(checks),
            guard_verdicts=guard_verdicts,
            validator_votes=validator_votes,
        )


def _normal(path: str) -> str:
    """One spelling per path: forward slashes, ``.`` and ``..`` folded, lower case (audit 007 SE-008)."""
    return posixpath.normpath(path.replace("\\", "/")).lower()


def _touches_forbidden_path(action: ActionRequest, policy: RuntimePolicy) -> bool:
    # Conservative substring match on normalised spellings: a forbidden entry that appears anywhere in a
    # touched path blocks the action. This intentionally errs toward blocking (an over-broad match denies;
    # it never lets a forbidden path through), the safe direction for a deny-list.
    forbidden = [_normal(f) for f in policy.forbidden_paths if f]
    return any(f in _normal(touched) for touched in action.files_touched for f in forbidden)
