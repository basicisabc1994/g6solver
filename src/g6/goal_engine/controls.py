# SPDX-License-Identifier: Apache-2.0
"""``evaluate_controls``: every control in force at one plan node, read against one executor ``Observation``.

The controls in force at a node are those declared on it and on every node above it, root first (audit 013, GE-007).
For each declaring node: its ``resource_bounds`` (reported as the guardrail ``resource_bounds``), its guardrails, its
checkpoints and its active breakpoints. Each guardrail and checkpoint gets ``pass``, ``fail`` or ``unknown``; each
active breakpoint gets a ``PauseRequest`` (GE-004: the archive's breakpoint callback was a no-op).

The report decides nothing for anyone. ``may_proceed`` is False when a guardrail fails or is unknown, or while a pause
is pending; checkpoints never block. The executor must obey it and must release each pause with ``release_pause``,
which accepts only an approval recorded in a ``g6.core.approval_authority.ApprovalAuthority`` for that pause's scope.
Evaluation changes no state: built-in predicates are pure, and a registered one should be.

States: ``blocked-escalated`` when a guardrail fails or is unknown, a pause is pending, the node is not in the spec,
or the input is not a spec and an observation; ``qualified-draft`` otherwise, because the observation is the
executor's and nothing here checks it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.approval_authority import ApprovalAuthority, ApprovalRecord
from g6.goal_engine.codes import (
    APPROVAL_REQUIRED,
    CHECKPOINT_FAILED,
    CHECKPOINT_UNKNOWN,
    GUARDRAIL_FAILED,
    GUARDRAIL_UNKNOWN,
    INPUT_INVALID,
    NODE_UNKNOWN,
    OBSERVATION_UNCHECKED,
    CompletionState,
    card,
    evidence,
)
from g6.goal_engine.predicates import ControlError, Verdict, resolve, run, usage_within
from g6.goal_engine.schema import RESOURCE_BOUNDS, CheckpointSpec, GoalSpec, GuardrailSpec, Observation

_SOURCE = "evaluate_controls"
_CODES = {("guardrail", "fail"): GUARDRAIL_FAILED, ("guardrail", "unknown"): GUARDRAIL_UNKNOWN,
          ("checkpoint", "fail"): CHECKPOINT_FAILED, ("checkpoint", "unknown"): CHECKPOINT_UNKNOWN}


class ControlResult(BaseModel):
    """One guardrail or checkpoint read against the observation; ``message`` is the spec's own message or
    description, ``reason`` is what the predicate found."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["guardrail", "checkpoint"]
    name: str
    declared_by: str
    predicate: str
    verdict: Verdict
    reason: str
    code: str = ""
    message: str = ""


class PauseRequest(BaseModel):
    """An active breakpoint in force at ``node_id``: that node's work must not start until an approval recorded for
    ``scope`` is presented to ``release_pause``. The scope names the exact spec (its digest), the node, the node that
    declared the breakpoint and its name, so an approval releases this pause and no other."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    breakpoint: str
    declared_by: str
    description: str
    scope: str
    run_id: str = ""


class ControlReport(BaseModel):
    """What the controls in force at one node say about one observation, and its envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    spec_digest: str
    results: tuple[ControlResult, ...] = ()
    pauses: tuple[PauseRequest, ...] = ()
    blocked: bool
    may_proceed: bool
    completion_state: CompletionState
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def _scope_chain(spec: GoalSpec, node_id: str) -> list[tuple[str, GoalSpec]] | None:
    """The nodes from the root down to ``node_id``, or None when the spec has no such node."""
    parts = node_id.split(".")
    if parts[0] != "root":
        return None
    chain = [("root", spec)]
    for part in parts[1:]:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return None
        index, current = int(part), chain[-1][1]
        if index >= len(current.subtasks):
            return None
        chain.append((f"{chain[-1][0]}.{index}", current.subtasks[index]))
    return chain


def _read(kind: Literal["guardrail", "checkpoint"], spec: GuardrailSpec | CheckpointSpec, declared_by: str,
          observation: Observation) -> ControlResult:
    try:
        verdict, reason, code = run(resolve(spec.predicate, spec.params), observation)
    except ControlError as exc:
        verdict, reason, code = "unknown", exc.reason, exc.code
    return ControlResult(kind=kind, name=spec.name, declared_by=declared_by, predicate=spec.predicate,
                         verdict=verdict, reason=reason, code=code or _CODES.get((kind, verdict), ""),
                         message=spec.message if isinstance(spec, GuardrailSpec) else spec.description)


def _results(chain: list[tuple[str, GoalSpec]], observation: Observation) -> list[ControlResult]:
    found: list[ControlResult] = []
    for node_id, node in chain:
        limits = node.resource_bounds.limits() if node.resource_bounds is not None else {}
        if limits:
            verdict, reason, code = run(usage_within(limits), observation)
            found.append(ControlResult(kind="guardrail", name=RESOURCE_BOUNDS, declared_by=node_id,
                                       predicate="resource_limit", verdict=verdict, reason=reason,
                                       code=code or _CODES.get(("guardrail", verdict), "")))
        found.extend(_read("guardrail", g, node_id, observation) for g in node.guardrails)
        found.extend(_read("checkpoint", c, node_id, observation) for c in node.checkpoints)
    return found


def _pause_requests(chain: list[tuple[str, GoalSpec]], node_id: str, digest: str,
                    run_id: str) -> tuple[PauseRequest, ...]:
    return tuple(
        PauseRequest(node_id=node_id, breakpoint=bp.name, declared_by=declared_by, description=bp.description,
                     scope=f"goal_engine:pause:{digest}:{node_id}:{declared_by}:{bp.name}", run_id=run_id)
        for declared_by, node in chain for bp in node.breakpoints if bp.active)


def _refused(code: str, reason: str, node_id: str, digest: str, request_id: str, run_id: str) -> ControlReport:
    return ControlReport(node_id=node_id, spec_digest=digest, blocked=True, may_proceed=False,
                         completion_state="blocked-escalated",
                         warning_card=card("blocked-escalated", code, reason,
                                           "Do not start the node's work; correct the spec or the observation."),
                         evidence=[evidence(code, reason, _SOURCE)], request_id=request_id, run_id=run_id)


def evaluate_controls(spec: GoalSpec, observation: Observation, *, request_id: str = "",
                      run_id: str = "") -> ControlReport:
    """Read every control in force at ``observation.node`` against ``observation``; see the module docstring."""
    if not isinstance(spec, GoalSpec) or not isinstance(observation, Observation):
        return _refused(INPUT_INVALID, f"expected a GoalSpec and an Observation, got {type(spec).__name__} and "
                        f"{type(observation).__name__}", str(getattr(observation, "node", "")), "", request_id, run_id)
    digest = spec.digest()
    node_id = observation.node
    chain = _scope_chain(spec, node_id)
    if chain is None:
        return _refused(NODE_UNKNOWN, f"the spec has no node {node_id!r} (ids look like 'root', 'root.0', "
                        "'root.0.1')", node_id, digest, request_id, run_id)
    results = _results(chain, observation)
    pauses = _pause_requests(chain, node_id, digest, run_id)
    guards = [r for r in results if r.kind == "guardrail"]
    failed = [r for r in guards if r.verdict == "fail"]
    unknown = [r for r in guards if r.verdict == "unknown"]
    blocked = bool(failed or unknown)
    state: CompletionState = "blocked-escalated" if blocked or pauses else "qualified-draft"
    if failed:
        code, message = GUARDRAIL_FAILED, f"{len(failed)} guardrail(s) failed at {node_id}: " + "; ".join(
            f"{r.name} ({r.declared_by}): {r.reason}" for r in failed[:5])
        action = "Do not start this node's work; change the plan or the conditions and evaluate again."
    elif unknown:  # audit 013, GE-005: an unknown is never a pass
        code, message = GUARDRAIL_UNKNOWN, f"{len(unknown)} guardrail(s) could not be decided at {node_id}: " + "; ".join(
            f"{r.name} ({r.declared_by}): {r.reason}" for r in unknown[:5])
        action = "Do not start this node's work; report the missing usage, metric or action and evaluate again."
    elif pauses:
        code, message = APPROVAL_REQUIRED, f"{len(pauses)} breakpoint(s) at {node_id} need a recorded approval: " + (
            ", ".join(f"{p.breakpoint} ({p.declared_by})" for p in pauses))
        action = "Have a reviewer record an approval for each pause's scope, then release it with release_pause."
    else:
        code, message = OBSERVATION_UNCHECKED, (
            f"{len(guards)} guardrail(s) passed at {node_id} on the executor's observation, which nothing here checks")
        action = "Proceed only if the executor's usage, metrics and action are measured, not assumed."
    entries = [evidence(code, message, _SOURCE, node_id=node_id, guardrails=len(guards),
                        checkpoints=len(results) - len(guards), pauses=len(pauses))]
    entries += [evidence(r.code, f"{r.kind} {r.name} ({r.declared_by}): {r.verdict}: {r.reason}", _SOURCE)
                for r in results if r.verdict != "pass"]
    entries += [evidence(APPROVAL_REQUIRED, f"breakpoint {p.breakpoint} ({p.declared_by})", _SOURCE, scope=p.scope)
                for p in pauses]
    return ControlReport(node_id=node_id, spec_digest=digest, results=tuple(results), pauses=pauses, blocked=blocked,
                         may_proceed=not blocked and not pauses, completion_state=state,
                         warning_card=card(state, code, message, action), evidence=entries, request_id=request_id,
                         run_id=run_id)


def release_pause(pause: PauseRequest, approval_id: object, *, authority: ApprovalAuthority,
                  consume: bool = True) -> ApprovalRecord | None:
    """The approval record that releases ``pause``, or None.

    Only a live, human-issued approval that ``authority`` recorded for ``pause.scope`` (and, when the approval is
    pinned to a run, for ``pause.run_id``) releases it; a single-use approval is consumed here. An id the caller made
    up resolves to nothing. This function issues no approval.
    """
    if not isinstance(pause, PauseRequest):
        return None
    if not authority.check(approval_id, scope=pause.scope, run_id=pause.run_id or None, consume=consume,
                           require_human=True):
        return None
    return authority.resolve(approval_id)


__all__ = ["ApprovalRecord", "ControlReport", "ControlResult", "PauseRequest", "evaluate_controls", "release_pause"]
