# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""schema.py -- shared types for the safety engine (proposed action -> guard -> policy decision).

These dataclasses follow the data model of the archive's safety specification (RuntimePolicy, GuardVerdict,
ActionRequest, AuthorizationDecision, ValidatorVote). An ``ActionRequest`` is **untrusted**: it is data only and carries
no authority until the policy engine decides on it.

Trust ordering:

    hard runtime controls > kill switch > policy engine > validator quorum > pattern guard > planner > model text

Nothing below the policy engine in that order may grant authority; in particular planner output can never raise a
ceiling or reset the kill switch. The decision is not a token: the caller that runs the action must obey it (audit 007
SE-001/002/004: the archive's single-use permit was minted and consumed by the same door, so it added nothing).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

# Decision / status vocabularies (kept as Literals so the exact strings are preserved verbatim across surfaces).
GuardDecision = Literal["ALLOW", "BLOCK", "ESCALATE"]
AuthDecision = Literal["ALLOW", "BLOCK", "ESCALATE"]
VoteDecision = Literal["APPROVE", "REJECT", "ABSTAIN"]
RunStatus = Literal[
    "RUNNING",
    "SUCCESS",
    "ABORTED",
    "ESCALATED",
    "NEEDS_CORRECTION",
    "SAFE_TERMINAL",
]


# ---------------------------------------------------------------------------
# Runtime policy — trusted configuration. Frozen: a run never mutates it,
# and (critically) it is NEVER constructed from planner output.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimePolicy:
    """Trusted, immutable per-run policy (ceilings + quorum config).

    Every field is a hard ceiling or a trusted gate parameter. The planner may
    *request* a higher ceiling; the runtime rejects the request — these values
    are loaded from trusted config before the run starts and never from planner output.
    """

    #: The most decisions the run may ask the policy engine for, allowed or refused (a step is one proposal).
    max_steps: int = 25
    max_tool_calls: int = 50
    max_shell_commands: int = 10
    max_files_modified: int = 25
    max_lines_changed: int = 2000
    max_retries: int = 3
    max_runtime_seconds: int = 900
    max_cost_cents: int = 5000
    max_privilege_level: int = 1
    # Tool allow-list. An EMPTY set means "allow any tool" (the ceiling/guard/
    # quorum layers still apply); a NON-EMPTY set switches on strict
    # allow-listing — only listed tools may run. Enforcement is derived from
    # non-emptiness (no separate opt-in flag), so populating this can never
    # silently fail open.
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    forbidden_paths: frozenset[str] = field(default_factory=frozenset)
    #: An action marked ``is_final`` is blocked until the run has recorded a passing test run (``RunState.record_tests``).
    require_tests_before_final: bool = True
    # Quorum config. The default is a pass-through (require_quorum False); when a
    # caller opts in it must satisfy n == 3f+1 and required_accepts == 2f+1
    # (validated by g6.safety_engine.quorum).
    require_quorum: bool = False
    quorum_n: int = 1
    quorum_f: int = 0
    quorum_required_accepts: int = 1


# ---------------------------------------------------------------------------
# Action request — untrusted. Not executable until authorized.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionRequest:
    """A single tool invocation a plan proposes. Untrusted data.

    ``estimated_lines_changed`` is used purely so ceilings can be *projected*
    before the action runs; it is advisory planner data and is never trusted to *raise* a ceiling.
    """

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    expected_effect: str = ""
    files_touched: tuple[str, ...] = ()
    privilege_level: int = 0
    is_shell_command: bool = False
    estimated_lines_changed: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: The most this action may cost, in the policy's minor units: a projection for the cost
    #: ceiling. It is planner data, never trusted to lower the projection: a negative value counts as 0.
    cost_ceiling_cents: int = 0
    #: This action delivers the run's final answer: under ``require_tests_before_final`` it waits for passing tests.
    #: Marking an action final can only hold it, never let it through.
    is_final: bool = False


@dataclass(frozen=True)
class GuardVerdict:
    decision: GuardDecision
    reason: str = ""
    matched_patterns: tuple[str, ...] = ()
    risk_score: float = 0.0
    evidence: tuple[str, ...] = ()
    surface: str = ""


@dataclass(frozen=True)
class ValidatorVote:
    validator_id: str
    #: Declared by the validator itself; nothing here checks it (audit 007 SE-009).
    fault_domain: str
    decision: VoteDecision
    reason: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthorizationDecision:
    decision: AuthDecision
    reason: str = ""
    permitted_action: ActionRequest | None = None
    policy_checks: tuple[str, ...] = ()
    guard_verdicts: tuple[GuardVerdict, ...] = ()
    validator_votes: tuple[ValidatorVote, ...] = ()
