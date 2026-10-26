# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The runtime: one attempt at a time over a mission's work graph (workforce OS plan, P0 T6).

``step`` reconciles, picks a claimable unit (priority order; a unit whose attempts are spent is
never picked again), forms its worker under the mission's ceiling, opens a run (a new instance),
claims the unit, runs the rung, closes the run with its artifact and verdict, and moves the unit:
complete only when the attempt was not blocked and, for a verify unit, the verdict passed. When
nothing is left to try, the mission closes with the roll-up.

``roll_up`` is worst-wins: a do unit counts as its verdicts on ITS OWN latest artifact say (never as
its producer said), a degraded output is at most qualified-draft, a unit that did not complete or
whose ledger disagrees with the tracker is blocked-escalated, and the contract of what counts as done is
measured (E2c): an unmet or unchecked requirement holds the mission at qualified-draft.

The pull rung (V1b): a do unit the plan gave no code call is the owner's chat's to write. ``step``
never runs it; ``awaiting`` lists it; ``submit`` records what the chat wrote as one attempt by a pull
worker, a draft (at most qualified-draft) until the unit's verifier judges it.

A unit has at most one open attempt: ``step`` waits for it (until ``reconcile`` abandons it at its wall
clock) and never retries beside it, and a mission does not close while any attempt is open. A mission
a crash left in ``closing`` is finished by the next ``step``.

Write order across the two files (venture.db, units.db) is fixed and every step is resumable:
open run > claim > execute > close run (+ artifact + verdict, one transaction) > move the unit.
``reconcile`` finishes what a crash interrupted: an open run past its wall clock is abandoned (and
its unit failed, so a retry may claim it); a closed run whose unit never moved is settled from the
ledger.

One runner per mission (V2a): ``run`` and ``step`` hold the mission's runner lease and pass it to
every write, so a runner whose lease was taken over lands nothing. An attempt left open under an
older lease belongs to a runner that stopped, died or was fenced out, so ``reconcile`` abandons it at
once rather than at its wall clock. A cut-off attempt whose worker may have acted (an op not declared
read-only) is escalated, never retried: nothing yet records whether its call ran.

Library cut (audit 010 §11): the outbox, the drafting executor and its recovery, formal analysis and learned probes
are not ported; their branches are gone, not stubbed. A strict ("charged") budget is only ever what the caller asks for:
nothing derives it from a hosted owner.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import (
    acceptance,
    bench,
    classes,
    economics,
    reconfigure,
    reuse,
    rungs,
    scheduler,
)
from g6.workforce.calls import Door, venture_invoke
from g6.workforce.capabilities import Capabilities
from g6.workforce.contracts import (
    AUTHORITY_COPY,
    BLOCKED,
    NOT_INDEPENDENT,
    QUALIFIED,
    VERIFIED,
    VERIFIER_GRADE,
    Outcome,
    combine,
    rank,
)
from g6.workforce.decisions import HUMAN, Decisions
from g6.workforce.graph import SUPERVISOR, WorkGraph
from g6.workforce.missions import (
    RUNNING,
    Lease,
    LeaseLost,
    MissionStore,
    stamp,
)
from g6.workforce.plans import (
    ARTIFACT_PREFIX,
    MAX_DEPTH,
    Plan,
    PlanRejected,
    UnitSpec,
    _authority_problems,
    _gate_problems,
    parse_plan,
)
from g6.workforce.runs import MAX_ARTIFACT_BYTES, RunLedger, outcome_of
from g6.workforce.wakes import WaitStale, Wakes, Want
from g6.workforce.workers import WorkerSpec, form_worker

#: The template each unit kind is formed from (a gate's records a person's decision, V4c).
TEMPLATES = {"do": "doer", "verify": "verifier", "gate": "gatekeeper"}
#: Ops that run the owner's code in a child process (the test bench): cut off mid-call, that code may still run.
RUNS_CODE = frozenset({(bench.COMPONENT, "run_tests"), (bench.COMPONENT, bench.IN_PLACE)})
#: How much of an input's content a gate's question carries for the person to read (V3a); a larger artifact is
#: shown by its id and digest alone, and a send can then find no approved text in it (it refuses).
COVER_BYTES = 16 * 1024
#: What an approval is given for (Security Brief 3, SR3-2; review I4): the work behind its gate is one of these, least
#: to most.
EFFECT_CLASSES = ("read", "local_write", "shadow", "external", "irreversible")
# COPY: (Security Brief 3, SR3-2; review M7) the question a person is asked when an approval no longer covers the work
# behind it: neutral about the answer, and the plan's own goal (the chat's words) labelled as the plan's.
# COPY: What a person approving a code change is told was not compared (the probe's own reason follows).
APPROVE_UNCHECKED = " Before you approve, note that {why}"
REAPPROVAL = ("The approval of step {gate} no longer applies: {changed}. That work will not run until you decide again "
              "(approve or reject). The step's goal, as the plan states it: '{goal}'")
# COPY: (Security Brief 3, SR3-2) what changed since a person approved a gate, in the question above.
APPROVAL_CHANGED = {
    "effect": "what the work it covers may do changed after the approval, from \"{was}\" to \"{now}\"",
    "contract": "a tool it covers was redefined after the approval ({ops})",
    "unrecorded": "it was given before G6 recorded what an approval covers",
}
# COPY: (Security Brief 3, SR3-2; review I4) what the work behind a gate may do, in a person's words.
EFFECT_WORDS = {
    "read": "reads only; changes nothing",
    "local_write": "changes files or records on this computer; sends nothing out",
    "shadow": "shadow sends that deliver nothing",
    "external": "actions outside G6",
    "irreversible": "actions outside G6 that cannot be undone",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _effect_words(effect: Any) -> str:
    """An approval's recorded effect as a person reads it: its class, and the steps and ops that have it."""
    effect = effect if isinstance(effect, dict) else {}
    ops = [str(op) for op in effect.get("ops") or []]
    return EFFECT_WORDS.get(str(effect.get("class")), EFFECT_WORDS["external"]) + (f" ({', '.join(ops)})" if ops else "")


def _pulled(spec: UnitSpec) -> bool:
    """A do unit the plan gave no code call: the owner's chat writes it (the pull rung), G6 never runs it."""
    return spec.kind == "do" and not spec.call


def _waits_for(want: Want) -> str:
    return {"timer": f"its start time {want.at}", "file": f"the file {want.name!r}",
            "submission": "the owner's chat", "approval": "a person's decision (the owner, at the local command line)",
            "retry": "an explicit mission_run retry_token after repairing the zero-inference refusal"
            }[want.kind]


def ceiling_for(mission: dict, plan: Plan | None = None, spec: UnitSpec | None = None) -> RuntimePolicy:
    """The mission's trusted ceiling: no shell, no direct workspace writes, no privilege, its own budget, read fail-closed (E1c:
    a budget that cannot be read is 0, so nothing that may cost money runs). For a delegated unit (V4a) its budget
    is lowered to each ancestor's in turn (never raised), so a child is never formed wider than its parent:
    ``form_worker`` refuses it a second time, after the plan's own check. Native bench/draft internals may create
    scratch copies; drafting separately records/enforces exact frozen-file authorization for its copy only.
    These zero direct-action counters do not describe or authorize those internal scratch effects."""
    hard, _readable = economics.hard_limit(mission.get("budget_cents"))
    ceiling = RuntimePolicy(max_steps=25, max_tool_calls=50, max_shell_commands=0, max_files_modified=0,
                            max_lines_changed=0, max_retries=3, max_runtime_seconds=900,
                            max_cost_cents=hard, max_privilege_level=0)
    if plan is not None and spec is not None:
        for ancestor in reversed(_ancestors(plan, spec)):
            ceiling = dataclasses.replace(ceiling, max_cost_cents=min(ceiling.max_cost_cents, ancestor.budget_cents))
    return ceiling


def _ancestors(plan: Plan, spec: UnitSpec) -> list[UnitSpec]:
    """The units that delegated ``spec``, nearest first (a plan's parent links are acyclic and shallow)."""
    out, at = [], spec
    while at.parent and len(out) <= MAX_DEPTH:
        at = plan.unit(at.parent)
        out.append(at)
    return out


def judged_latest(verdicts: list[dict], verifier: str, artifact_id: str) -> bool:
    """Whether ``verifier`` passed the output ``artifact_id`` (V4b: a pass on an older output does not count)."""
    return any(v["unit_key"] == verifier and v["artifact_id"] == artifact_id and v["result"] == "pass"
               for v in verdicts)


def own_budget(plan: Plan, spec: UnitSpec) -> int:
    """What a unit keeps for its own worker: its budget less what it carved out for its children (V4a)."""
    return spec.budget_cents - sum(u.budget_cents for u in plan.units if u.parent == spec.key)


def _context(spec: UnitSpec, inputs: dict[str, dict | None], produced: dict | None, artifact: dict | None) -> dict:
    """Ledger §18 for one attempt: the bytes it read (its inputs, and for a check the output it judged) and wrote;
    tokens are not reported by any counted door, so they stay unknown (plan ruling R9)."""
    read = [found for found in (*inputs.values(), produced) if found is not None]
    return economics.context_record(
        read_bytes=sum(economics.size_of(found.get("content")) for found in read),
        output_bytes=economics.size_of(artifact.get("content")) if artifact else 0,
        reused_inputs=sum(1 for key in spec.inputs if key.startswith(ARTIFACT_PREFIX)))


def _provenance(calls: tuple, inputs: dict[str, dict | None]) -> dict:
    """How an output was made (E2b): the call that made it, with its contract's digest, and the digest of every input
    it read (a reused one also names the mission and the verdicts behind it)."""
    made = next((c for c in calls if isinstance(c, dict) and c.get("refused") is False), None)
    return {"call": ({"component": made["component"], "op": made["op"], "contract_sha": made.get("contract_sha", "")}
                     if made else None),
            "inputs": {key: {"artifact_id": found["artifact_id"], "sha256": found.get("sha256", ""),
                             **({"reused": found["reused"]} if "reused" in found else {})}
                       for key, found in inputs.items() if found is not None}}


def _keepable(spec: UnitSpec, result: rungs.RungResult) -> rungs.RungResult:
    """The attempt as the ledger can keep it (fix I1 of the Part 1 review): an output larger than one step's result may
    be is not kept, and the attempt stops saying what failed and why, its calls and their costs recorded as they
    happened. The same call would answer the same way, so it is not tried again (``output_too_large``)."""
    if result.artifact is None or result.outcome.state == BLOCKED:
        return result
    size = economics.size_of(result.artifact.get("content"))
    if size <= MAX_ARTIFACT_BYTES:
        return result
    made = ".".join(spec.call["capability"]) if spec.call else f"step {spec.key}"
    why = f"{made} answered with {size} bytes, more than the {MAX_ARTIFACT_BYTES} bytes G6 keeps as one step's result"
    return dataclasses.replace(result, outcome=Outcome(BLOCKED, reasons=(why,), source="ledger"), artifact=None,
                               failure_code="output_too_large")


class Runtime:
    def __init__(self, missions: MissionStore, graph: WorkGraph, ledger: RunLedger, capabilities: Capabilities, *,
                 invoke: Callable[[str, str, dict], Any] | None = None,
                 now: Callable[[], datetime] | None = None, holder: str | None = None,
                 alive: Callable[[str], bool | None] | None = None, inbox: str | Path | None = None,
                 charged: bool = False, stop_requested: Callable[[], bool] | None = None,
                 certificates: bool = False, readiness: bool = False) -> None:
        self._missions, self._graph, self._ledger, self._caps = missions, graph, ledger, capabilities
        #: Whether a coding mission's certificate decides its state at close (RVC R6, founder ruling R6-P).
        self.certificates = certificates
        self.readiness = readiness
        self._invoke = invoke
        self._stop_requested = stop_requested
        self._now = now or (lambda: datetime.now(UTC))
        #: Who this runner is on a lease; ``alive`` says whether another lease holder is still running.
        self.holder = holder or f"runner:{uuid.uuid4().hex[:12]}"
        self._alive = alive
        #: The questions only a person answers (V4c: gates and disagreements between verifiers).
        self.decisions = Decisions(missions)
        #: What waiting missions wait for (``inbox``: where awaited files are dropped; None: no file arrives).
        self.wakes = Wakes(missions, inbox, decided=lambda mission_id, key: any(
            d["unit_key"] == key for d in self.decisions.all(mission_id)))
        #: A strict budget, only when the caller asks for one (the archive derived it from a hosted owner; the library
        #: never does): a call whose cost cannot be bounded is refused, and an unknown spend counts as all that was left.
        self.charged = bool(charged)

    # -- the lease ------------------------------------------------------------------------------------
    def lease(self, mission_id: str) -> Lease:
        """Take this runner's lease on the mission (``LeaseHeld`` while another live runner has it)."""
        return self._missions.take_lease(mission_id, holder=self.holder, now=self._now(), alive=self._alive)

    def renew(self, lease: Lease) -> Lease:
        """Keep the lease by this runner's clock (``LeaseLost`` once it is no longer the mission's)."""
        return self._missions.renew_lease(lease, now=self._now())

    # -- the budget (E1c, Ledger §20) ----------------------------------------------------------------------
    def budget(self, mission_id: str) -> economics.BudgetPolicy:
        """The mission's budget policy, its ceiling read fail-closed, under its resource mode (E2d, Ledger §25)."""
        policy = economics.budget_policy(self._missions.get(mission_id), charged=self.charged)
        return scheduler.budget_for(policy, self._missions.mode(mission_id))

    def _spent(self, mission_id: str, unit_key: str) -> tuple[int, bool]:
        """The unit's binding costs plus outstanding call reservations: the known amount in the budget's currency,
        and whether any amount is unknown (under a strict budget an unknown amount consumes the rest of it).
        The ledger checks again atomically before each invocation; this snapshot alone grants no admission."""
        spent = self._ledger.committed_total(mission_id, unit_key=unit_key)
        return spent.known_minor_units, spent.amount_minor_units is None

    def _budget_pressure(self, mission_id: str) -> None:
        """Ledger §20 at the soft limit: ``SOFT_BUDGET`` recorded once, with the spend that crossed it. Re-evaluating
        the strategy and preferring cheaper routes, which the soft limit also calls for, is the scheduler's (E2d)."""
        policy = self.budget(mission_id)
        spent = self._ledger.cost_total(mission_id, binding_only=True)
        if policy.pressure(spent):
            self._missions.record_event(mission_id, "budget", actor="code", detail={
                "code": "SOFT_BUDGET", "spent_minor_units": spent.known_minor_units,
                "unknown_entries": spent.unknown_entries, "soft_limit": policy.soft_machine_cost_limit,
                "hard_limit": policy.hard_machine_cost_limit, "currency": policy.currency}, idem_key="budget:soft")

    def _leased(self, mission_id: str, lease: Lease | None, work: Callable[[Lease], Any], idle: Any) -> Any:
        """``work`` under ``lease``, or under a lease taken for it and given back after; ``idle`` when the
        mission is not running (a finished or unplanned mission has no runner)."""
        if lease is not None:
            return work(lease)
        if self.idle(mission_id):
            return idle
        taken = self.lease(mission_id)
        try:
            return work(taken)
        finally:
            self._missions.release_lease(taken)

    def idle(self, mission_id: str) -> bool:
        """Nothing for a runner to do: the mission is not running, or it waits and nothing it waits for has
        happened (looking costs no lease, no event and no call)."""
        mission = self._missions.get(mission_id)
        if mission["status"] not in RUNNING:
            return True
        return mission["status"] == "waiting" and not self.wakes.due(mission_id, self._now())

    # -- driving --------------------------------------------------------------------------------------
    def run(self, mission_id: str, *, max_steps: int = 200) -> dict:
        """Step the mission under one lease, renewed after each step, until nothing is left to do."""
        if self.idle(mission_id):
            return self._missions.get(mission_id)
        lease = self.lease(mission_id)
        try:
            for _ in range(max_steps):
                done = self.step(mission_id, lease=lease)
                if done is None or "closed" in done:
                    break
                lease = self.renew(lease)
        finally:
            self._missions.release_lease(lease)
        return self._missions.get(mission_id)

    def step(self, mission_id: str, *, lease: Lease | None = None) -> dict | None:
        """One attempt, or the mission's close; None when there is nothing to do. A unit with an open
        attempt is waited for (until reconcile abandons it), never attempted again beside it; a mission a
        crash left in ``closing`` is finished."""
        return self._leased(mission_id, lease, lambda held: self._step(mission_id, held), None)

    def _check_coding_snapshot(self, mission_id: str) -> None:
        from g6.workforce import coding

        problem = coding.snapshot_problem(self._missions.acceptance(mission_id).metadata.get("coding"),
                                          self._missions.get(mission_id)["venture_id"])
        if problem:
            raise ValueError(problem)

    def _step(self, mission_id: str, lease: Lease) -> dict | None:
        mission = self._missions.get(mission_id)
        self._check_coding_snapshot(mission_id)
        if mission["status"] == "closing":
            return self._close(mission_id, lease)
        if mission["status"] == "waiting":  # looking costs nothing; only an event that happened wakes it
            due = self.wakes.due(mission_id, self._now())
            if not due or not self.wakes.fire(mission_id, due, now=self._now(), lease=lease):
                return None
            mission = self._missions.get(mission_id)
        if mission["status"] != "active":
            return None
        self.reconcile(mission_id, lease=lease)
        plan = self._plan(mission)
        seen = self._ledger.runs(mission_id)
        running = {r["unit_key"] for r in seen if r["state"] == "open"}
        wants = []
        for key in self._graph.claimable(mission_id):
            spec = plan.unit(key)
            if key in running or self._tries(mission_id, spec) >= spec.max_attempts:
                continue
            held = self._held(mission, spec)
            if held is not None:
                if held.kind == "approval" and spec.kind == "gate":  # ask a person (once), showing what they approve
                    unchecked = self.unchecked(mission_id)  # 2026-10-08: and what no check could compare
                    self.decisions.request(
                        mission_id, key, "gate",
                        question=spec.goal + (APPROVE_UNCHECKED.format(why=" ".join(unchecked)) if unchecked else ""),
                        detail={**self._covers(mission_id, spec), **({"unchecked": list(unchecked)} if unchecked else {})},
                        binding=self._binding(plan, key))  # review I3: what it asks about
                wants.append(held)
                continue
            return self._attempt(mission, spec, lease)
        for key, ask in self._disputes(mission_id, plan):  # verifiers disagree and no retry is left: a person decides
            found = self.decisions.get(mission_id, key, "adjudication")
            if found is None:
                self.decisions.request(mission_id, key, "adjudication", question=ask["question"], detail=ask["detail"])
                wants.append(Want("approval", key))
            elif found["decision"] == "set_aside":
                return self._set_aside(mission_id, key, found, lease)
        if not any(w.kind == "approval" for w in wants) and self._settled(mission_id, plan):
            self._missions.transition(mission_id, "closing", reason="no unit is left to try", actor="code",
                                      lease=lease)
            return self._close(mission_id, lease)
        if wants and not running:
            why = "; ".join(f"{w.unit_key} waits for {_waits_for(w)}" for w in wants)
            try:
                self.wakes.wait(mission_id, wants, reason=why, lease=lease, now=self._now(), runs_seen=len(seen))
            except WaitStale:
                return {"rechecked": mission_id}  # an attempt landed meanwhile (the chat's): look again
            return {"waiting": [w.unit_key for w in wants]}
        return None

    def _covers(self, mission_id: str, spec: UnitSpec) -> dict:
        """What a gate's person approves (V3a): for each input that is a unit, its latest artifact's id, digest and
        content (bounded); a send behind the gate sends only text found there. Nothing when it has no such input."""
        covers = []
        for key in (k for k in spec.inputs if not k.startswith(ARTIFACT_PREFIX)):
            found = self._ledger.last_artifact(mission_id, key)
            if found is None:
                covers.append({"unit": key, "artifact_id": "", "sha256": "", "content": None})
                continue
            size = len(json.dumps(found.get("content"), sort_keys=True, default=str).encode("utf-8"))
            covers.append({"unit": key, "artifact_id": found["artifact_id"], "sha256": found.get("sha256", ""),
                           "content": found.get("content") if size <= COVER_BYTES else None})
        return {"covers": covers} if covers else {}

    def _held(self, mission: dict, spec: UnitSpec) -> Want | None:
        """What holds a ready unit back, if anything: its start time, its file, or the owner's chat."""
        if spec.not_before and spec.not_before > stamp(self._now()):
            return Want("timer", spec.key, at=spec.not_before)
        if spec.await_file and self.wakes.dropped(mission, spec.await_file) is None:
            return Want("file", spec.key, name=spec.await_file)
        if spec.kind == "gate" and self.decisions.get(mission["mission_id"], spec.key, "gate") is None:
            return Want("approval", spec.key)
        asked = self._reapproval_pending(mission, spec) if spec.kind != "gate" else ""
        if asked:  # a gate upstream is asked again (Security Brief 3, SR3-2): nothing behind it runs meanwhile
            return Want("approval", asked)
        if self._pulled_now(mission["mission_id"], spec):
            return Want("submission", spec.key)
        return None

    # -- the pull rung: units the owner's chat writes ------------------------------------------------
    def awaiting(self, mission_id: str) -> list[UnitSpec]:
        """The pull units G6 is waiting for the owner's chat to submit (ready, not held by a time or a
        file, attempts left, none open), on an active mission or one waiting for them."""
        mission = self._missions.get(mission_id)
        if mission["status"] not in ("active", "waiting"):
            return []
        plan = self._plan(mission)
        running = {r["unit_key"] for r in self._ledger.open_runs(mission_id)}
        out = []
        for key in self._graph.claimable(mission_id):
            spec = plan.unit(key)
            held = self._held(mission, spec)
            if held is not None and held.kind == "submission" and key not in running \
                    and self._tries(mission_id, spec) < spec.max_attempts:
                out.append(spec)
        return out

    def submit(self, mission_id: str, unit_key: str, content: dict) -> dict:
        """Record what the owner's chat wrote for a pull unit G6 is waiting for: one attempt by a pull worker, closed
        with the content as its artifact, a draft (at most qualified-draft) until the unit's verifier judges it.
        Anything G6 is not waiting for is refused and changes nothing."""
        written = Outcome(QUALIFIED, reasons=("written by the owner's chat model: a draft until it is judged",),
                          source="pull")
        # At least one model call wrote it (the chat's own; its cost is the owner's session's: unknown, E1a).
        return self._written(mission_id, unit_key, content, written=written, actor="chat", model_calls=1,
                             costs=(economics.owner_session_cost(),), what="This step cannot be submitted")

    def correct(self, mission_id: str, unit_key: str, content: dict, *, reason: str, actor: str,
                decided_by: str = "", actor_type: str = "user") -> dict:
        """A person's own output for a unit G6 is waiting for the owner's chat to write (Ledger §6 ``correction``;
        plan ruling P2.6). It is one attempt like the chat's, a draft until the unit's check judges it (a person's
        word is not an oracle, invariant 16), costing no machine, and its intervention is written in that attempt's
        own close. Only a person corrects, and a correction gives its reason."""
        if actor != HUMAN:
            raise ValueError(f"only a person corrects a unit; {actor!r} may not (the chat submits instead)")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("a correction gives its reason")
        person = economics.HumanIntervention(kind="correction", actor_type=actor_type,
                                             decided_by=decided_by or self._missions.owner, required_by_policy=False,
                                             reason=reason[:2000], outcome="submitted", work_unit_id=unit_key)
        written = Outcome(QUALIFIED, reasons=(f"written by a person ({reason[:200]}): a draft until it is judged",),
                          source="human")

        def recorded(run_id: str) -> None:  # inside the attempt's close
            self.decisions.intervene_within(mission_id, dataclasses.replace(person, metadata={"run_id": run_id}),
                                            idem=f"intervention:correction:{run_id}")

        return self._written(mission_id, unit_key, content, written=written, actor=HUMAN, model_calls=0, costs=(),
                             what="This correction cannot be recorded", within=recorded)

    def _written(self, mission_id: str, unit_key: str, content: dict, *, written: Outcome, actor: str,
                 model_calls: int, costs: tuple, what: str, within: Callable[[str], None] | None = None) -> dict:
        """One attempt at a pull unit G6 is waiting for, written by ``actor`` (the chat, or a person), closed with
        ``content`` as its artifact; ``within(run_id)`` writes inside the close. Anything G6 is not waiting for is
        refused and changes nothing."""
        if not isinstance(content, dict):
            raise ValueError("a submission is a JSON object (the unit's output)")  # noqa: TRY004 - one refusal type
        size = len(json.dumps(content, sort_keys=True, default=str).encode("utf-8"))
        if size > MAX_ARTIFACT_BYTES:  # refused before any attempt opens: a refusal spends nothing
            raise ValueError(f"the submission is too large to store ({size} bytes; at most {MAX_ARTIFACT_BYTES})")
        mission = self._missions.get(mission_id)
        if mission["status"] not in ("active", "waiting"):
            raise ValueError(f"mission {mission_id} is {mission['status']}, not active: nothing is waiting")
        plan = self._plan(mission)
        try:
            spec = plan.unit(unit_key)
        except KeyError:
            raise ValueError(f"the plan has no unit {unit_key!r}") from None
        if spec.key not in {u.key for u in self.awaiting(mission_id)}:
            raise ValueError(f"unit {unit_key!r} is not waiting for the chat (it is code's to run, not ready, "
                             "taken, or out of attempts)")
        standard = self._missions.acceptance(mission_id).metadata.get("coding")
        if standard and spec.key == "patch":
            from g6.workforce import coding

            self._check_coding_snapshot(mission_id)
            coding.check_submission(standard, content)
        worker = self._formed(mission, plan, spec, "puller", allowed=())
        inputs, stale = self._inputs(mission, spec)
        if stale:  # refused before any attempt opens: a refusal spends nothing (E2b)
            raise ValueError(classes.explain("stale_reuse", stale, what=what, material="Nothing was recorded."))
        run = self._open_submission(mission_id, unit_key, worker, retry=self._retry_record(mission_id, spec),
                                    actor=actor)
        try:
            self._graph.claim(mission_id, unit_key, agent=f"worker:{run.instance_id}")
        except (ValueError, LookupError) as exc:
            self._ledger.abandon(run.run_id, f"the claim was refused: {exc}", code="abandoned:claim")
            raise ValueError(f"unit {unit_key!r} could not be taken: {exc}") from None
        self._ledger.close(run.run_id, written,
                           artifact={"schema": spec.output_schema, "content": content,
                                     "provenance": _provenance((), inputs)},
                           model_calls=model_calls, costs=costs,
                           context=_context(spec, inputs, None, {"content": content}),
                           within=(lambda: within(run.run_id)) if within is not None else None)
        self._settle(mission_id, spec, self._ledger.run(mission_id, run.run_id))
        return {"unit": unit_key, "run_id": run.run_id}

    def _formed(self, mission: dict, plan: Plan, spec: UnitSpec, template: str,
                allowed: tuple[tuple[str, str], ...] | None = None) -> WorkerSpec:
        """The unit's worker: the template narrowed under the mission's ceiling (a delegated unit's: its
        ancestors' budgets), with the budget the unit kept after carving out its children's."""
        return form_worker(template, ceiling=ceiling_for(mission, plan, spec), contracts=self._caps.contract,
                           unit=dataclasses.replace(spec, budget_cents=own_budget(plan, spec)), allowed=allowed)

    # -- reuse of verified results (E2b) ----------------------------------------------------------------------
    def _inputs(self, mission: dict, spec: UnitSpec) -> tuple[dict[str, dict | None], str]:
        """Each input's artifact: a unit's latest output, or a saved verified result (``artifact:<id>``) that may
        still be reused, with its provenance under ``reused``; and why a saved one may not be ("" when all may)."""
        out: dict[str, dict | None] = {}
        refused = []
        for key in spec.inputs:
            if not key.startswith(ARTIFACT_PREFIX):
                out[key] = self._ledger.last_artifact(mission["mission_id"], key)
                continue
            try:
                found = reuse.check(self._ledger, key.removeprefix(ARTIFACT_PREFIX), contract=self._caps.contract,
                                    now=self._now(), risk_tier=mission["risk_tier"], plan=self._stored_plan)
            except reuse.ReuseRefused as why:
                out[key] = None
                refused.append(f"the saved result {key} cannot be reused: {why}")
                continue
            out[key] = {**found.artifact, "reused": found.provenance()}
        return out, "; ".join(refused)

    def reusable(self, artifact_id: str, risk_tier: str) -> str | None:
        """None when a mission of ``risk_tier`` may reuse the saved result now, else what to tell the planner (Design
        §23). ``parse_plan`` asks this of every ``artifact:`` input."""
        try:
            reuse.check(self._ledger, artifact_id, contract=self._caps.contract, now=self._now(), risk_tier=risk_tier,
                        plan=self._stored_plan)
        except reuse.ReuseRefused as why:
            return classes.explain("stale_reuse", str(why),
                                   what=f"The saved result {ARTIFACT_PREFIX}{artifact_id} cannot be reused",
                                   material="Nothing was built on it.", when="plan")
        return None

    def _stored_plan(self, mission_id: str) -> Plan:
        """A mission's stored plan (reuse reads what each check that verified a saved result read, Gate W review gw2)."""
        return self._missions.plan(mission_id)[1]

    def brief_inputs(self, mission_id: str, spec: UnitSpec) -> tuple[dict[str, Any], str]:
        """A pull unit's inputs as its brief shows them (their contents), and, when a saved result it names may no
        longer be reused, what to do about it (Design §23); "" when every input can be read."""
        inputs, stale = self._inputs(self._missions.get(mission_id), spec)
        shown = {key: None if found is None else found.get("content") for key, found in inputs.items()}
        return shown, (classes.explain("stale_reuse", stale, what="An input of this step cannot be read",
                                       material="Nothing should be built on it.") if stale else "")

    # -- delegation (V4a): the owner's chat hands part of a unit to children ---------------------------
    def delegate(self, mission_id: str, unit_key: str, children: list, *, actor: str = "chat") -> dict:
        """Hand part of a unit G6 is waiting for the chat to write to ``children`` (units in the plan format):
        the next plan revision is the current one with the children appended (each delegated by the unit) and
        the unit consuming its child do units, so it waits for their verifiers. ``parse_plan`` judges it with
        the broker, so nothing widens what the unit had; it is stored by a compare-and-set, in one transaction
        with its event and the wake of a waiting mission; its units are then created (``reconcile`` finishes an
        interrupted creation). A refusal changes nothing."""
        if not isinstance(children, list) or not children or not all(isinstance(c, dict) for c in children):
            raise ValueError("a delegation is a non-empty list of units (JSON objects in the plan format)")
        mission = self._missions.get(mission_id)
        if mission["status"] not in ("active", "waiting"):
            raise ValueError(f"mission {mission_id} is {mission['status']}, not active: nothing is waiting")
        revision, plan = self._missions.plan(mission_id, mission["plan_revision"])
        try:
            spec = plan.unit(unit_key)
        except KeyError:
            raise ValueError(f"the plan has no unit {unit_key!r}") from None
        if spec.key not in {u.key for u in self.awaiting(mission_id)}:
            raise ValueError(f"unit {unit_key!r} is not waiting for the chat: only a unit G6 is waiting for the "
                             "chat to write may delegate")
        if not self._formed(mission, plan, spec, "puller", allowed=()).may_delegate:
            raise ValueError(f"unit {unit_key!r}'s worker may not delegate")
        named = [c.get("parent") for c in children if c.get("parent") not in (None, "", unit_key)]
        if named:
            raise PlanRejected([f"a child of this delegation is delegated by {unit_key!r}, not {p!r}" for p in named])
        kids = [{**c, "parent": unit_key} for c in children]
        body = json.loads(plan.to_json())
        made = [c["key"] for c in kids if c.get("kind") == "do" and isinstance(c.get("key"), str)]
        for unit in body["units"]:
            if unit["key"] == unit_key:
                unit["inputs"] = list(dict.fromkeys([*unit["inputs"], *made]))
        body["units"] += kids
        amended = parse_plan(self._missions.normalized(mission_id, body, self._caps.side_effect),  # E10b: S9
                             risk_tier=mission["risk_tier"], budget_cents=mission["budget_cents"],
                             ready=self._caps.ready, effects=self._caps.side_effect, reusable=self.reusable,
                             contract=self._caps.contract, authority=self._caps.authority)
        keys = [c.get("key") for c in kids]
        seen = len(self._ledger.runs(mission_id, unit_key))

        def wake(new: int) -> None:  # inside the amendment's transaction: raising rolls the amendment back
            runs = self._ledger.runs(mission_id, unit_key)
            if len(runs) != seen or any(r["state"] == "open" for r in runs):  # review of V4: the chat submitted it
                raise ValueError(f"unit {unit_key!r} changed while it was being delegated (the chat submitted it): "
                                 "look again")
            if self._missions.get(mission_id)["status"] == "waiting":
                self.wakes.end(mission_id, reason=f"unit {unit_key} delegated part of its work", now=self._now(),
                               key=f"plan:{new}", actor=actor)

        new = self._missions.amend_plan(mission_id, amended, expect_revision=revision, actor=actor,
                                        detail={"delegation": {"parent": unit_key, "children": keys}}, within=wake)
        self._graph.extend(mission_id)
        return {"unit": unit_key, "revision": new, "children": keys}

    def _open_submission(self, mission_id: str, unit_key: str, worker, retry: dict | None = None,
                         actor: str = "chat") -> Any:
        """The chat's attempt at a unit the mission waits for. The submission is the event a waiting mission
        waits on, so it wakes it first; if a runner moved the mission to waiting while the chat was submitting,
        it wakes it then and opens once more (Codex V2: never a failed submission for a race)."""
        for last_try in (False, True):
            if self._missions.get(mission_id)["status"] == "waiting":
                self.wakes.fire_submission(mission_id, unit_key, now=self._now())
            try:
                return self._ledger.open(mission_id, unit_key, worker, actor=actor, retry=retry)
            except (ValueError, sqlite3.IntegrityError):
                if last_try or self._missions.get(mission_id)["status"] != "waiting":
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _close(self, mission_id: str, lease: Lease) -> dict:
        """``closing`` -> ``done`` with the roll-up, measured against what counts as done (E2c), the evaluation kept on
        the close (re-run as is after a crash between the two)."""
        self._budget_pressure(mission_id)
        outcome, evaluation = self.assess(mission_id)
        detail = {"acceptance": evaluation.as_dict()}
        if self.certificates and self._missions.acceptance(mission_id).metadata.get("coding"):
            # RVC R6.5 (founder ruling R6-P): a code change's certificate decides its state; kept on the close
            from g6.workforce import coding_certificate

            outcome, found = coding_certificate.decided(self._missions, self._ledger, mission_id, outcome,
                                                        unchecked=self.unchecked(mission_id))
            detail.update(found)
        gap = self._missions.acceptance(mission_id).metadata.get("coding", {}).get("cannot_check")
        if gap:
            # COPY: Frozen unsupported guarantees cap closure even when a test certificate verifies.
            outcome = outcome.demote(QUALIFIED, f"Cannot check: {gap['requirement']} ({gap['why']}).")
        self._missions.close(mission_id, outcome, actor="code", lease=lease, detail=detail)
        return {"closed": outcome.state}

    def _receipt_digests(self, mission_id: str) -> dict[str, str] | None:
        """With certificates on, a code change's frozen test file digests, for its test checks' receipts (RVC R6.4)."""
        standard = self._missions.acceptance(mission_id).metadata.get("coding") if self.certificates else None
        return dict(standard["test_digests"]) if standard else None

    def _attempt(self, mission: dict, spec: UnitSpec, lease: Lease) -> dict:
        mission_id = mission["mission_id"]
        plan = self._plan(mission)
        # Gate W (review M1): the unit is judged again now, with the store's own rule and words, so an op whose class
        # fell after its plan was accepted (a later census reading, a changed contract) is escalated, never dispatched;
        # and (review gw2) so is one whose contract now says it acts, with no gate a person approves upstream of it.
        refused = [*_authority_problems({spec.key: spec}, self._caps.authority, self._caps.side_effect),
                   *_gate_problems(plan, self._caps.side_effect, keys=(spec.key,))]
        # Security Brief 3 (SR3-2): and so is a unit behind a gate a person refused when asked again; a unit behind one
        # asked again, or one that acts whose approval no longer covers it (its contract or effect changed), waits.
        held, why = self._unbound_approval(mission_id, plan, spec) if not refused else ("", "")
        if held == "rejected":
            refused.append(why)
        if refused:
            self._graph.escalate(mission_id, spec.key, "; ".join(refused))
            return {"unit": spec.key, "escalated": "; ".join(refused)}
        if held == "asked":
            return {"unit": spec.key, "approval": why}
        template = TEMPLATES.get(spec.kind)
        try:
            if template is None:
                raise ValueError(f"a {spec.kind} unit needs the human rung (V1)")
            worker = self._formed(mission, plan, spec, template)
        except ValueError as exc:
            self._graph.escalate(mission_id, spec.key, f"no worker can be formed: {exc}")
            return {"unit": spec.key, "escalated": str(exc)}
        run = self._ledger.open(mission_id, spec.key, worker, lease=lease, retry=self._retry_record(mission_id, spec))
        try:
            self._graph.claim(mission_id, spec.key, agent=f"worker:{run.instance_id}")
        except (ValueError, LookupError) as exc:
            self._ledger.abandon(run.run_id, f"the claim was refused: {exc}", code="abandoned:claim", lease=lease)
            return {"unit": spec.key, "abandoned": str(exc)}
        spent, unknown = self._spent(mission_id, spec.key)
        door = Door(worker, contracts=self._caps.contract, run_id=run.run_id, charged=self.charged,
                    stop_requested=self._stop_requested,
                    spent_cents=spent, spent_unknown=unknown,
                    record=lambda index, cost: self._ledger.record_cost(run.run_id, index, cost),
                    reserve=lambda index, contract: self._ledger.reserve_call(
                        run.run_id, index, contract, charged=self.charged, lease=lease),
                    invoke=venture_invoke(mission["venture_id"], invoke=self._invoke))
        inputs, stale = self._inputs(mission, spec)
        produced = self._ledger.last_artifact(mission_id, spec.verifies) if spec.kind == "verify" else None
        if stale:  # E2b: a saved result this unit reads lapsed after the plan was accepted; it is not read
            result = rungs.RungResult(Outcome(BLOCKED, reasons=(stale,)), failure_code="stale_reuse")
        elif spec.kind == "gate":
            result = self._gate(mission_id, spec)
        elif spec.kind == "do":
            result = rungs.run_do(spec, door, inputs)
        else:
            capability = tuple(spec.oracle["capability"]) if spec.oracle else ("", "")
            if (self._missions.acceptance(mission_id).metadata.get("coding")
                    and "regression" in (spec.oracle or {}).get("params", {})):
                from g6.workforce import coding

                params = spec.oracle["params"]
                spec = dataclasses.replace(spec, oracle={**spec.oracle, "params": {**params, "regression": {
                    **params["regression"], "replay": coding.regression_replays(self._missions, mission_id)}}})
            result = rungs.run_verify(spec, door, instance_id=run.instance_id, produced=produced, inputs=inputs,
                                      contract=self._caps.contract(*capability),
                                      authority=self._caps.authority(*capability),
                                      test_provenance=("local_planner" if any(
                                          e["detail"].get("origin") == "local_planner"
                                          for e in self._missions.events(mission_id, kind="plan.revision"))
                                      else (self._missions.acceptance(mission_id).metadata.get("coding") or {})
                                      .get("test_provenance", "")), ledger=self._ledger,
                                      disclosed=self._disclosed(mission_id, spec, produced),
                                      receipts=self._receipt_digests(mission_id))
        result = _keepable(spec, result)
        # Retain calls/costs from an executed attempt, but never accept its
        # artifact or verdict after the frozen checking snapshot changed.
        from g6.workforce import coding

        standard = self._missions.acceptance(mission_id).metadata.get("coding")
        integrity = coding.snapshot_problem(standard, mission["venture_id"])
        if integrity:
            result = dataclasses.replace(result, artifact=None, verdict=None, mechanisms=(),
                failure_code="coding_snapshot_changed", outcome=Outcome(BLOCKED, reasons=(integrity,), source="coding"))
        context = _context(spec, inputs, produced, result.artifact)
        refused_output = None
        standard = self._missions.acceptance(mission_id).metadata.get("coding")
        if standard and spec.key == "patch" and result.artifact is not None:
            from g6.workforce import coding

            content = result.artifact.get("content")
            try:
                coding.check_submission(standard, content)
            except ValueError as exc:
                # Preserve the bounded JSON projection as rejected evidence,
                # never as an artifact a check/consumer may execute. Match the
                # existing ledger's serialization for non-JSON component replies.
                rejected = json.loads(json.dumps(content, sort_keys=True, default=str))
                refused_output = {"run_id": run.run_id, "unit": spec.key, "content": rejected,
                                  "sha256": _digest(rejected), "reason": str(exc),
                                  "provenance": _provenance(result.calls, inputs)}
                result = dataclasses.replace(result, artifact=None, failure_code="coding_scope",
                    outcome=Outcome(BLOCKED, reasons=(str(exc),), source="coding"))
        artifact = ({**result.artifact, "provenance": _provenance(result.calls, inputs)}
                    if result.artifact is not None else None)
        elicitation = None
        if spec.kind == "verify" and produced is not None:  # FE §28 (E10c): one record per verify cycle
            judged = plan.unit(spec.verifies)
            elicitation = acceptance.elicitation_record(
                unit=spec.verifies, artifact_id=str(produced.get("artifact_id", "")), risk_tier=judged.risk,
                classes=acceptance.unit_classes(plan, judged, self._caps.side_effect), records=result.mechanisms,
                verdict=result.verdict.verdict.result if result.verdict is not None else "unmeasured")
        try:
            self._ledger.close(run.run_id, result.outcome, artifact=artifact, calls=result.calls,
                               model_calls=result.model_calls, failure_code=result.failure_code,
                               verdict=result.verdict, lease=lease, costs=result.costs, context=context,
                               mechanisms=result.mechanisms, elicitation=elicitation,
                               within=(lambda: self._missions.append_event(
                                   mission_id, "coding.output_refused", "code", refused_output,
                                   f"coding.output_refused:{run.run_id}")) if refused_output is not None else None)
        except (LeaseLost, sqlite3.OperationalError):
            raise  # this runner may write nothing more, or the store was busy: reconcile finishes the attempt
        except Exception as exc:  # noqa: BLE001 - any other refusal of the record would refuse the same attempt again
            why = f"G6 could not record this step's result ({type(exc).__name__}: {exc})"[:300]
            self._ledger.close(run.run_id, Outcome(BLOCKED, reasons=(why,), source="ledger"), calls=result.calls,
                               model_calls=result.model_calls, failure_code="unrecorded", lease=lease,
                               costs=result.costs, context=context, mechanisms=())  # no finding kept: no record
        self._budget_pressure(mission_id)
        self._settle(mission_id, spec, self._ledger.run(mission_id, run.run_id), lease)
        return {"unit": spec.key, "run_id": run.run_id, "state": result.outcome.state}

    def _settle(self, mission_id: str, spec: UnitSpec, row: dict, lease: Lease | None = None) -> None:
        """Move the unit as its closed run says (also what reconcile does for a run that closed). units.db
        cannot share the lease's transaction, so the move is refused unless the runner still holds the lease
        and the unit is still held by this run's own attempt (Codex V2: a stale runner's late move).

        Recovery by class (E2a, Ledger §15): a failure the runtime may try again by itself (one that may not recur,
        or a failed check V4b sends back) fails the unit so it is claimed again; any other stops it at once,
        escalated with what failed, why, how material it is and what would resolve it (Design §23)."""
        self._missions.check_lease(lease)
        agent = f"worker:{row['instance_id']}"
        standard = self._missions.acceptance(mission_id).metadata.get("coding")
        coding = spec.kind == "verify" and bool(standard)
        moved = reconfigure.on_settle(self, mission_id, spec, row, lease) if standard else None
        self._missions.check_lease(lease)  # a re-probe may outlast the lease: a runner that lost it moves nothing
        if moved is not None:  # E2E Build 6: a change of course inside the frozen standard (reconfigure.py)
            if moved[0] == "fail":
                self._graph.fail(mission_id, spec.key, moved[1][:300], agent=agent)
            else:
                self._graph.escalate(mission_id, spec.key, moved[1], agent=agent)
        elif self._passed(mission_id, spec, row) or self._advisory_unmeasured(mission_id, spec, row):
            # an advisory check that could not decide completes unmeasured, never as a pass (2026-10-08)
            self._graph.complete(mission_id, spec.key, agent=agent)
        elif spec.kind == "gate":  # a person's rejection is final: escalated, with their reason (V4c)
            self._graph.escalate(mission_id, spec.key, "; ".join(outcome_of(row).reasons)[:300] or "not approved",
                                 agent=agent)
        else:
            if spec.kind == "verify" and not coding:
                self._send_back(mission_id, spec, row)  # before the verifier's own move: reconcile resumes it
            reasons, code = "; ".join(outcome_of(row).reasons), row["failure_code"]
            if classes.retried_by_runtime(code):
                self._graph.fail(mission_id, spec.key, reasons[:300] or "the attempt failed", agent=agent)
            else:
                if classes.classify(code) == "BUDGET_EXHAUSTED":  # Ledger §20: the hard limit stopped this step
                    self._missions.record_event(mission_id, "budget", actor="code", detail={
                        "code": "BUDGET_EXHAUSTED", "unit": spec.key, "run_id": row["run_id"], "failure_code": code},
                        idem_key=f"budget:hard:{row['run_id']}")
                if classes.classify(code) == "CAPABILITY_UNAVAILABLE":  # E7d: a need this suite cannot meet here
                    step = spec.call or spec.oracle or {}
                    self._missions.record_unmet(mission_id, kind="not_available",
                                                subject=".".join(step.get("capability") or [spec.key]),
                                                why=reasons[:300], unit=spec.key)
                self._graph.escalate(mission_id, spec.key, classes.explain(code, reasons), agent=agent)
        if coding:
            self._coding_send_back(mission_id, self._plan(self._missions.get(mission_id)))

    def _coding_send_back(self, mission_id: str, plan: Plan) -> None:
        # Every check settles first; reconciliation resumes this idempotent decision after an interruption.
        units = {u["key"]: u["status"] for u in self._graph.units(mission_id)}
        if units.get("patch") != "complete":
            return
        latest = self._ledger.last_artifact(mission_id, "patch")
        checks = {u.key for u in plan.units if u.kind == "verify" and u.verifies == "patch"}
        judged = [v for v in self._ledger.verdicts(mission_id, "patch") if v["artifact_id"] == latest["artifact_id"]]
        if checks <= {v["unit_key"] for v in judged}:
            failed = next((v for v in judged if v["executed"] and v["result"] == "fail"), None)
            advisory = self._advisory(mission_id)  # an advisory check that could not decide does not hold a repair back
            if failed and all(v["executed"] or v["unit_key"] in advisory for v in judged):
                self._send_back(mission_id, plan.unit(failed["unit_key"]),
                                self._ledger.run(mission_id, failed["run_id"]))

    # -- a person's decisions (V4c): gates and disagreements between verifiers -----------------------------
    def decide(self, mission_id: str, unit_key: str, decision: str, *, reason: str, actor: str,
               decided_by: str = "", timing: tuple[datetime, datetime] | None = None, minutes: Any = None,
               principal: dict | None = None) -> dict:
        """A person's answer to a question G6 asked (the local command line calls this), recorded as theirs with its
        Ledger §6 intervention, then the waiting mission woken on it. Only ``human`` decides (``Decisions.record``
        refuses any other actor). ``minutes``: how long the person says it took (R5), kept with the answer.
        ``principal``: who answered and through which channel (``decisions.principal``, audit 010 WF-013)."""
        asked = [q for q in self.decisions.requested(mission_id) if q["unit"] == unit_key]
        kind = asked[0]["kind"] if asked else "gate"
        # Security Brief 3 (SR3-2; review I3): a gate's answer is given for the work its question described when asked
        binding = self.decisions.asked_binding(mission_id, unit_key) if asked and kind == "gate" else None
        found = self.decisions.record(mission_id, unit_key, kind, decision, reason=reason, actor=actor,
                                      decided_by=decided_by, timing=timing,
                                      required_by_policy=self._required(mission_id, unit_key, kind), binding=binding,
                                      minutes=minutes, principal=principal)
        wanted = [w for w in self.wakes.armed(mission_id) if w["kind"] == "approval" and w["unit_key"] == unit_key]
        if wanted:
            self.wakes.fire(mission_id, wanted, now=self._now(), actor="human")
        return found

    def confirm_acceptance(self, mission_id: str, *, reason: str, actor: str, decided_by: str = "",
                           timing: tuple[datetime, datetime] | None = None, principal: dict | None = None) -> Any:
        """A person confirms the mission's contract of what counts as done (E7d), after the chat accepted it and before
        a plan is stored, or again when its standard was raised while it ran (E10b; S15). Required for R2 and R3 (plan
        ruling P2.9); an incomplete spec cannot be confirmed. ``principal``: who confirmed and through which channel
        (``decisions.principal``, audit 010 WF-013)."""
        mission = self._missions.get(mission_id)
        contract = self._missions.acceptance(mission_id)
        before_plan = mission["status"] == "awaiting_plan" and not mission["plan_revision"]
        raised = mission["status"] in ("active", "waiting") and contract.revision > 1  # S15 (E10b)
        if not (before_plan or raised):
            # COPY: when a person may confirm what counts as done.
            raise ValueError(f"what counts as done is confirmed after the chat accepts the mission and before its plan, "
                             f"or when it was raised while the mission ran (it is {mission['status']})")
        if not contract.metadata.get("spec_complete"):
            asked = " ".join(str(q[1]) for q in contract.metadata.get("missing") or [])
            raise ValueError("What counts as done cannot be confirmed yet: the spec is not complete. Nothing was "
                             f"recorded. To resolve: answer these in the chat first: {asked} (code G6_E_MISSING_PARAM)")
        return self.decisions.confirm(mission_id, acceptance_sha=contract.sha256, reason=reason, actor=actor,
                                      decided_by=decided_by, timing=timing, principal=principal,
                                      required_by_policy=mission["risk_tier"] in acceptance.CONFIRM_TIERS)

    def confirmation_pending(self, mission_id: str) -> list[dict]:
        """The confirmation an R2 or R3 mission waits for, as a question for the person (E7d); [] otherwise. A mission
        accepted before contracts were kept waits for none: it plans as before (fix pass I2)."""
        mission = self._missions.get(mission_id)
        if (mission["risk_tier"] not in acceptance.CONFIRM_TIERS or mission["acceptance"] is None
                or self._missions.confirmed(mission_id)):
            return []
        contract = self._missions.acceptance(mission_id)
        if mission["status"] == "awaiting_plan":
            ask = "Confirm what counts as done before the plan"
        elif mission["status"] in ("active", "waiting") and contract.revision > 1:  # S15 (E10b)
            # COPY: the question when a standard was raised while the mission ran.
            ask = "What counts as done was raised while the mission ran; confirm it"
        else:
            return []
        from g6.workforce import coding

        standard = contract.metadata.get("coding", {})
        scope = standard.get("scope", {}).get("selected")
        # COPY: The person confirms the frozen function as well as the acceptance standard.
        scope_line = f"Scope: {scope['module']} / {scope['function']}. " if scope else ""
        return [{"unit": "", "kind": "acceptance",
                 "question": scope_line + coding.access_reason(standard)
                             + f"{ask}: python -m g6.workforce.decide confirm {mission_id} --reason \"...\"",
                 "detail": {"what_counts_as_done": acceptance.describe(contract)}}]

    def _required(self, mission_id: str, unit_key: str, kind: str) -> bool:
        """Ledger §6 ``required_by_policy`` (plan ruling P2.5). An adjudication always is: checks disagree and none
        may retry (V4c). A gate is when an external or irreversible op waits behind it (V3a), or when the mission is
        high-consequence (R3)."""
        if kind != "gate":
            return True
        mission = self._missions.get(mission_id)
        if mission["risk_tier"] == "R3":
            return True
        plan = self._plan(mission)
        unit_key = self.decisions.gate_of(mission_id, unit_key)  # a fresh approval is its gate's (SR3-2)
        behind = [u for u in plan.units if unit_key in plan.upstream(u.key)]
        return any(self._caps.side_effect(component, op) in ("external", "irreversible")
                   for unit in behind for component, op in unit.capabilities)

    def _gate(self, mission_id: str, spec: UnitSpec) -> rungs.RungResult:
        """A gate's one attempt: the person's recorded decision, as theirs (an approval passes the gate; a
        rejection blocks it with their reason). Nothing here decides."""
        found = self.decisions.get(mission_id, spec.key, "gate")
        if found is None:
            return rungs.RungResult(Outcome(BLOCKED, reasons=(f"gate {spec.key} has no person's decision",)),
                                    failure_code="undecided")
        if found["decision"] == "approve":
            return rungs.RungResult(Outcome(VERIFIED, reasons=(f"approved by a human: {found['reason']}",),
                                            evidence=(found["decision_id"],), source="human decision"))
        return rungs.RungResult(Outcome(BLOCKED, reasons=(f"rejected by a human: {found['reason']}",),
                                        evidence=(found["decision_id"],), source="human decision"),
                                failure_code="rejected")

    # -- an approval binds what it was given for (Security Brief 3, SR3-2; the founder's ruling of 2026-10-06) ---------
    def _acts(self, spec: UnitSpec) -> bool:
        """The unit names an op not declared read-only (an op with no declared side effect counts as acting)."""
        return any(self._caps.side_effect(component, op) != "read" for component, op in spec.capabilities)

    def _effect_class(self, component: str, op: str) -> str:
        """What an op may do, as an approval binds it (review I4): read only, write on this computer, act outside G6, or
        act outside G6 past undoing. An op that declares no effect counts as acting outside G6, as the planner counts
        it. (The archive's shadow class belonged to the outbox, which is not ported.)"""
        effect = self._caps.side_effect(component, op)
        if effect in ("read", "local_write", "irreversible"):
            return effect
        return "external"

    def _binding(self, plan: Plan, gate: str) -> dict:
        """What an approval of ``gate`` is given for: the effect digest (the strongest effect class behind it, and each
        unit that acts there with its ops) and the contract digest (each of those ops' contract)."""
        contracts: dict[str, str] = {}
        ops, effect_class = [], EFFECT_CLASSES[0]
        for unit in plan.units:
            if gate not in plan.upstream(unit.key) or not self._acts(unit):
                continue
            for component, op in unit.capabilities:
                found = self._caps.contract(component, op)
                contracts[f"{component}.{op}"] = found.sha256 if found is not None else ""
                ops.append(f"{unit.key}: {component}.{op}")
                effect_class = max(effect_class, self._effect_class(component, op), key=EFFECT_CLASSES.index)
        effect = {"class": effect_class, "ops": sorted(ops)}
        return {"contract_digest": _digest(contracts), "effect_digest": _digest(effect), "contracts": contracts,
                "effect": effect}

    @staticmethod
    def _approval_changed(was: dict | None, now: dict) -> str:
        """What changed since an approval was given, as the fresh question says it ("" when nothing did)."""
        if not isinstance(was, dict) or not was.get("contract_digest") or not was.get("effect_digest"):
            return APPROVAL_CHANGED["unrecorded"]
        if was["effect_digest"] != now["effect_digest"]:
            return APPROVAL_CHANGED["effect"].format(was=_effect_words(was.get("effect")),
                                                     now=_effect_words(now["effect"]))
        if was["contract_digest"] != now["contract_digest"]:
            old = was.get("contracts") if isinstance(was.get("contracts"), dict) else {}
            changed = sorted(op for op in {**old, **now["contracts"]} if old.get(op) != now["contracts"].get(op))
            return APPROVAL_CHANGED["contract"].format(ops=", ".join(changed))
        return ""

    def _reapproval_pending(self, mission: dict, spec: UnitSpec) -> str:
        """The fresh approval a gate upstream of ``spec`` waits for, by its key ("" when none does)."""
        if not any(q["detail"].get("reapproves") for q in self.decisions.requested(mission["mission_id"])):
            return ""  # no gate of this mission was ever asked again: nothing to read
        plan = self._plan(mission)
        kinds = {u.key: u.kind for u in plan.units}
        for gate in sorted(k for k in plan.upstream(spec.key) if kinds.get(k) == "gate"):
            asked, found = self.decisions.effective(mission["mission_id"], gate)
            if found is None and asked is not None and asked["unit"] != gate:
                return asked["unit"]
        return ""

    def _unbound_approval(self, mission_id: str, plan: Plan, spec: UnitSpec) -> tuple[str, str]:
        """Whether the approvals upstream of ``spec`` still cover it: ("", "") when each gate's latest answer is an
        approval and, for a unit that acts, was given for the contracts and effects its gate's work has now;
        ("rejected", why) when a person turned a fresh approval down; ("asked", key) when a fresh approval waits for a
        person. One is asked here before a unit that acts, when its gate's approval no longer applies (a digest changed,
        or none was recorded): that approval is superseded, recorded so on the fresh question, and never honoured."""
        kinds = {u.key: u.kind for u in plan.units}
        acts = self._acts(spec)
        for gate in sorted(k for k in plan.upstream(spec.key) if kinds.get(k) == "gate"):
            asked, found = self.decisions.effective(mission_id, gate)
            if found is None:
                if asked is not None and asked["unit"] != gate:
                    return "asked", asked["unit"]
                continue  # the gate itself is undecided: it holds what follows it
            if found["decision"] != "approve":
                return "rejected", f"rejected by a human: {found['reason']}"
            if not acts:
                continue
            now = self._binding(plan, gate)
            changed = self._approval_changed(self.decisions.binding(mission_id, found["decision_id"]), now)
            if changed:
                ask = plan.unit(gate)
                return "asked", self.decisions.reask(
                    mission_id, gate, supersedes=found["decision_id"], detail=self._covers(mission_id, ask),
                    question=REAPPROVAL.format(gate=gate, changed=changed, goal=ask.goal), binding=now)
        return "", ""

    def _disputes(self, mission_id: str, plan: Plan) -> list[tuple[str, dict]]:
        """Verify units whose executed fail of an output stands with no retry left, while another verifier passed
        that same output: the question a person adjudicates (uphold the fail, or set it aside)."""
        if (self._missions.acceptance(mission_id).metadata.get("coding")
                and any(m["refs"].get("repair_stop") for m in self._missions.messages(mission_id))):
            return []  # the frozen coding loop stopped; its failed verdict still blocks the roll-up
        status = {u["key"]: u["status"] for u in self._graph.units(mission_id)}
        out = []
        for spec in plan.units:
            if spec.kind != "verify" or status.get(spec.key) != "failed" or status.get(spec.verifies) != "complete" \
                    or self._tries(mission_id, spec) < spec.max_attempts:
                continue
            latest = self._ledger.last_artifact(mission_id, spec.verifies)
            if latest is None:
                continue
            on_it = [v for v in self._ledger.verdicts(mission_id, spec.verifies)
                     if v["artifact_id"] == latest["artifact_id"] and v["executed"]]
            failed = [v for v in on_it if v["unit_key"] == spec.key and v["result"] == "fail"]
            passed = sorted({v["unit_key"] for v in on_it if v["unit_key"] != spec.key and v["result"] == "pass"})
            if failed and passed:
                reasons = json.loads(failed[-1]["reasons_json"] or "[]")
                question = (f"Verifiers disagree on {spec.verifies}'s output: {', '.join(passed)} passed it and "
                            f"{spec.key} failed it ({'; '.join(reasons)[:600]}). Uphold the fail, or set it aside "
                            "with your reason (the result is then at most qualified-draft)?")
                out.append((spec.key, {"question": question, "detail": {
                    "verifies": spec.verifies, "artifact_id": latest["artifact_id"], "passed": passed,
                    "failed": spec.key, "reasons": reasons}}))
        return out

    def _set_aside(self, mission_id: str, key: str, found: dict, lease: Lease) -> dict:
        """A person set a verifier's fail aside: its unit completes on their decision, so what waits on it may run;
        the roll-up holds it at most qualified-draft, with their reason."""
        self._missions.check_lease(lease)
        self._graph.claim(mission_id, key, agent="human")
        self._graph.complete(mission_id, key, agent="human")
        return {"unit": key, "set_aside": found["decision_id"]}

    # -- the supervisor (V4b): a failed verdict goes back to the unit that can try again -----------------
    def _send_back(self, mission_id: str, spec: UnitSpec, row: dict) -> str | None:
        """An oracle that EXECUTED and failed the latest output of a unit the owner's chat writes, with attempts
        left: its reasons go to that unit as a message, and the unit is re-opened for the chat, after every
        other verifier of it that passed (each judges the new output). A unit whose oracle never ran is not retried.
        A code unit is never run again (the same inputs give the same answer): with attempts left, the scheduler decides
        whether the owner's chat writes it instead (E2d, Ledger §13 step 8; ``_escalate``), and only then is it sent
        back. Idempotent: one message per failing run, one decision per failed check, and re-opening an open unit
        changes nothing, so reconcile finishes an interrupted send-back."""
        found = [v for v in self._ledger.verdicts(mission_id, spec.verifies) if v["run_id"] == row["run_id"]]
        if not found or found[0]["result"] != "fail" or not found[0]["executed"]:
            return
        verdict, mission = found[0], self._missions.get(mission_id)
        plan = self._plan(mission)
        producer = plan.unit(spec.verifies)
        latest = self._ledger.last_artifact(mission_id, producer.key)
        if latest is None or latest["artifact_id"] != verdict["artifact_id"]:
            return
        focused, turn = None, {}
        if self._missions.acceptance(mission_id).metadata.get("coding"):
            from g6.workforce import coding

            turn = coding.repair_turn(self._missions, self._ledger, mission_id, producer)
            stop = turn.get("stop", "")
            if stop:
                self._missions.record_event(mission_id, "message", actor="code", detail={
                    "from": spec.key, "to": producer.key, "text": stop,
                    "refs": {"repair_stop": True, "run_id": row["run_id"], "artifact_id": verdict["artifact_id"]}},
                    idem_key=f"repair-stop:{verdict['artifact_id']}")
                return stop
            focused = coding.focused(self._missions, self._ledger, mission_id, producer.key, verdict["artifact_id"],
                                     strategy=turn.get("strategy"))
        if self._tries(mission_id, producer) >= producer.max_attempts:
            return
        if not self._pulled_now(mission_id, producer) and not self._escalate(mission, producer, verdict):
            return
        text = focused["text"] if focused else "; ".join(json.loads(verdict["reasons_json"] or "[]"))[:2000]
        self._missions.record_event(mission_id, "message", actor=f"worker:{row['instance_id']}",
                                    detail={"from": spec.key, "to": producer.key, "text": text,
                                            "refs": {"run_id": row["run_id"], "verdict_id": verdict["verdict_id"],
                                                     "artifact_id": verdict["artifact_id"],
                                                     **(focused["refs"] if focused else {})}},
                                    idem_key=f"message:{row['run_id']}")
        if turn.get("change"):  # review M-2: the repair ladder moves only once its send-back went out
            reconfigure.decide(self._missions, turn["change"], idem=turn["idem"])
        status = {u["key"]: u["status"] for u in self._graph.units(mission_id)}
        verdicts = self._ledger.verdicts(mission_id, producer.key)
        why = f"{spec.key} failed its latest output; the owner's chat tries again"
        for other in plan.units:  # while the unit is still complete, so a verifier may still be claimed
            # Only one that really passed this output: a "complete" with no pass behind it is a disagreement the
            # roll-up refuses, never healed here.
            if other.kind == "verify" and other.verifies == producer.key and other.key != spec.key \
                    and status.get(other.key) == "complete" \
                    and judged_latest(verdicts, other.key, verdict["artifact_id"]):
                self._graph.reopen(mission_id, other.key, f"the output it judged is being replaced ({why})")
        self._graph.reopen(mission_id, producer.key, why)

    def _disclosed(self, mission_id: str, spec: UnitSpec, produced: dict | None) -> bool:
        """#182 (lead ruling S-2): whether a send-back carried this check's own answer, in a way its pass on ``produced``
        could copy (``rungs.disclosed``); read from the mission's recorded messages and verdicts."""
        if produced is None or not spec.oracle:
            return False
        return rungs.disclosed(spec.key, spec.oracle, spec.verifies, produced,
                               self._missions.messages(mission_id, to=spec.verifies),
                               self._ledger.verdicts(mission_id, spec.verifies))

    def _escalated(self, mission_id: str) -> frozenset[str]:
        """The do units a scheduler decision handed to the owner's chat (E2d): each a code step whose result failed its
        check; the chat writes it from then on."""
        return frozenset(e["detail"]["work_unit_id"] for e in self._missions.events(mission_id, kind="escalation")
                         if e["detail"]["selected_option"]["strategy_type"] == "worker")

    def _pulled_now(self, mission_id: str, spec: UnitSpec) -> bool:
        """The owner's chat writes this unit: the plan named no code step for it (``_pulled``), or an escalation handed
        it to the chat (E2d)."""
        return _pulled(spec) or (spec.kind == "do" and spec.key in self._escalated(mission_id))

    def _escalate(self, mission: dict, producer: UnitSpec, verdict: dict) -> bool:
        """E2d (Ledger §12, §13 step 8): a code step whose latest result an executed check failed, with attempts left.
        The scheduler's decision is recorded either way, once per failed check; True when the recorded decision hands
        the step to the owner's chat, False when it stops for a person as before."""
        mission_id = mission["mission_id"]
        policy = self.budget(mission_id)
        spent = self._ledger.cost_total(mission_id, binding_only=True)
        contract = self._missions.acceptance(mission_id)
        now = stamp(self._now())
        planned, _ = scheduler.planned_option(producer, self._caps.contract, self._caps.side_effect)
        decision = scheduler.escalation(
            mission_id, producer, planned=planned, verdict=verdict, mode=self._missions.mode(mission_id),
            pressure=policy.pressure(spent),
            budget_before=economics.Money(max(0, policy.hard_machine_cost_limit - spent.known_minor_units),
                                          policy.currency),
            acceptance_reference=f"acceptance:{contract.sha256}", now=now,
            deadline_passed=bool(contract.deadline and contract.deadline < now))
        self._missions.record_event(mission_id, "escalation", actor="code", detail=decision.as_dict(),
                                    idem_key=f"escalation:{producer.key}:{verdict['verdict_id']}")
        return producer.key in self._escalated(mission_id)  # the recorded decision, not this call's, decides

    def _tries(self, mission_id: str, spec: UnitSpec) -> int:
        """The attempts that count against a unit's ``max_attempts``: every attempt at a do unit; for a verify
        unit, those on its do unit's latest output (V4b: each new output is judged afresh)."""
        if spec.kind == "verify":
            latest = self._ledger.last_artifact(mission_id, spec.verifies)
            if latest is not None:
                return self._ledger.attempts(mission_id, spec.key, after_run=latest["run_id"])
        return self._ledger.attempts(mission_id, spec.key)

    def _retry_record(self, mission_id: str, spec: UnitSpec) -> dict | None:
        """Ledger §15: what another attempt at a unit records (None for its first): the strategy the last attempt used,
        its failure code and class, why trying again could succeed, what one more attempt may cost (its call's
        declared ceiling; None when unknown). The ledger adds the attempt and the retry count."""
        runs = self._ledger.runs(mission_id, spec.key)
        if not runs:
            return None
        last = runs[-1]
        code = last["failure_code"]
        calls = [c for c in json.loads(last["calls_json"] or "[]") if isinstance(c, dict)]
        step = spec.call or spec.oracle or {}
        contract = self._caps.contract(*step["capability"]) if step.get("capability") else None
        ceiling = contract.cost_ceiling if contract is not None else None
        sent_back = bool(self._missions.messages(mission_id, to=spec.key))
        return {"previous_run": last["run_id"],
                "previous_strategy": {"rung": last["rung"],
                                      "calls": [f"{c.get('component')}.{c.get('op')}" for c in calls]},
                "failure_code": code, "failure_class": classes.classify(code), "why_retry": classes.why_retry(code),
                "expected_cost": ceiling.as_dict() if ceiling is not None else None,
                "reason_codes": list(scheduler.retry_codes(code, sent_back=sent_back))}  # Ledger §12 (E2d, P3.11)

    def _passed(self, mission_id: str, spec: UnitSpec, row: dict) -> bool:
        if row["state"] != "closed" or outcome_of(row).state == BLOCKED:
            return False
        if spec.kind != "verify":
            return True
        return any(v["run_id"] == row["run_id"] and v["result"] == "pass" for v in self._ledger.verdicts(mission_id))

    def _settled(self, mission_id: str, plan: Plan) -> bool:
        """Nothing running (no unit in progress, no attempt open) and nothing left that may be tried."""
        units = {u["key"]: u for u in self._graph.units(mission_id)}
        if any(u["status"] == "in_progress" for u in units.values()) or self._ledger.open_runs(mission_id):
            return False
        return not any(self._tries(mission_id, plan.unit(key)) < plan.unit(key).max_attempts
                       for key in self._graph.claimable(mission_id))

    # -- crash recovery -----------------------------------------------------------------------------
    def reconcile(self, mission_id: str, *, lease: Lease | None = None) -> list[str]:
        """Finish what a crash interrupted; returns what it did."""
        return self._leased(mission_id, lease, lambda held: self._reconcile(mission_id, held), [])

    def _reconcile(self, mission_id: str, lease: Lease) -> list[str]:
        notes = []
        if self._graph.extend(mission_id):  # a delegation whose units a crash left uncreated (V4a)
            notes.append("created the units of the latest plan revision")
        plan = self._plan(self._missions.get(mission_id))
        for row in self._ledger.open_runs(mission_id):
            if 0 < row["fence"] < lease.fence:  # opened under an earlier lease: nothing will ever close it
                why, code = "its runner lost the mission's lease (it stopped, died or was taken over)", "abandoned:lease"
            else:
                spec_json = json.loads(self._ledger.instance(mission_id, row["instance_id"])["spec_json"])
                limit = timedelta(seconds=int(spec_json["policy"]["max_runtime_seconds"]))
                if self._now() - datetime.fromisoformat(row["started_at"]) <= limit:
                    continue  # still within its wall clock: it may be running
                why, code = "its wall clock ran out while it was open", "abandoned:clock"
            self._ledger.abandon(row["run_id"], why, code=code, lease=lease)
            notes.append(f"abandoned {row['run_id']} ({row['unit_key']})")
        for unit in self._graph.units(mission_id):
            if unit["status"] != "in_progress":
                continue
            if unit["assigned_agent"] == "human":  # a person's set-aside a crash interrupted (V4c)
                self._missions.check_lease(lease)
                self._graph.complete(mission_id, unit["key"], agent="human")
                notes.append(f"completed {unit['key']} on a person's set-aside")
                continue
            if unit["assigned_agent"] == SUPERVISOR:  # a re-open a crash interrupted mid-way (review of V4)
                self._missions.check_lease(lease)
                self._graph.reopen(mission_id, unit["key"], "finishing a re-open a crash interrupted")
                notes.append(f"finished re-opening {unit['key']}")
                continue
            runs = self._ledger.runs(mission_id, unit["key"])
            last = runs[-1] if runs else None
            if last is not None and last["state"] == "open":
                continue
            if last is None:
                self._missions.check_lease(lease)
                self._graph.fail(mission_id, unit["key"], "claimed with no run in the ledger")
            elif last["state"] == "abandoned" and self._may_have_acted(mission_id, last):
                self._missions.check_lease(lease)
                self._graph.escalate(mission_id, unit["key"],
                                     "its attempt was cut off mid-call and may have acted (an op not declared "
                                     "read-only, or the owner's code it was running, which may still run): G6 "
                                     "never retries an action it cannot see", agent=f"worker:{last['instance_id']}")
            else:
                self._settle(mission_id, plan.unit(unit["key"]), last, lease)
            notes.append(f"settled {unit['key']} from the ledger")
        notes += self._stale(mission_id, plan, lease)
        if self._missions.acceptance(mission_id).metadata.get("coding"):
            self._coding_send_back(mission_id, plan)
        return notes

    def _stale(self, mission_id: str, plan: Plan, lease: Lease) -> list[str]:
        """A complete verifier whose pass judged an older output of a unit that has produced again is re-opened
        (V4b), before anything that waits on it can run: a pass counts only on the output it judged. One with no
        pass at all is a ledger/tracker disagreement, left for the roll-up to refuse (never healed)."""
        units = {u["key"]: u["status"] for u in self._graph.units(mission_id)}
        advisory = self._advisory_keys(plan)
        notes = []
        for spec in plan.units:
            if spec.kind != "verify" or units.get(spec.key) != "complete" or units.get(spec.verifies) != "complete":
                continue
            latest = self._ledger.last_artifact(mission_id, spec.verifies)
            verdicts = self._ledger.verdicts(mission_id, spec.verifies)
            passed_before = any(v["unit_key"] == spec.key and v["result"] == "pass" for v in verdicts)
            # 2026-10-08: an advisory check that could not decide an older output has decided nothing about this one
            undecided = latest is not None and spec.key in advisory and not any(
                v["unit_key"] == spec.key and v["artifact_id"] == latest["artifact_id"] for v in verdicts) and any(
                v["unit_key"] == spec.key and v["result"] == "unmeasured" for v in verdicts)
            if latest is not None and passed_before and not judged_latest(verdicts, spec.key,
                                                                          latest["artifact_id"]):
                self._missions.check_lease(lease)
                self._graph.reopen(mission_id, spec.key, f"its pass judged an older output of {spec.verifies}")
                notes.append(f"re-opened {spec.key}: {spec.verifies} has a newer output")
            elif undecided:
                self._missions.check_lease(lease)
                self._graph.reopen(mission_id, spec.key, f"its answer was about an older output of {spec.verifies}")
                notes.append(f"re-opened {spec.key}: {spec.verifies} has a newer output")
        return notes

    def _may_have_acted(self, mission_id: str, row: dict) -> bool:
        """Whether a cut-off attempt's worker could have changed something: anything but a read-only worker
        whose every allowed op is declared ``read`` (an op with no contract counts as acting). An op that runs
        the owner's code in a child process acts too, whatever the worker: the child outlives a killed runner,
        and nothing here can see it stop (Codex V2)."""
        spec = json.loads(self._ledger.instance(mission_id, row["instance_id"])["spec_json"])
        if any((component, op) in RUNS_CODE for component, op in spec.get("allowed", [])):
            return True
        if spec.get("read_only"):
            return False
        for component, op in spec.get("allowed", []):
            contract = self._caps.contract(component, op)
            if contract is None or contract.side_effect != "read":
                return True
        return False

    # -- roll-up --------------------------------------------------------------------------------------
    def roll_up(self, mission_id: str) -> Outcome:
        return self.assess(mission_id)[0]

    def assess(self, mission_id: str) -> tuple[Outcome, acceptance.Evaluation]:
        """The worst-wins roll-up, then measured against the mission's acceptance contract and its plan's unit checks
        (E2c): an unmet or unchecked requirement holds it at qualified-draft; below its required state it is
        blocked-escalated unless only an allowed degradation keeps it there; never promoted."""
        mission = self._missions.get(mission_id)
        plan = self._plan(mission)
        units = {u["key"]: u for u in self._graph.units(mission_id)}
        found = [self._unit_outcome(mission_id, spec, units.get(spec.key, {}).get("status"),
                                    units.get(spec.key, {}).get("reason", "")) for spec in plan.units]
        outcome = combine(*found)
        if not any(spec.kind == "verify" for spec in plan.units):  # review of V4: an approval checks nothing
            outcome = outcome.demote(QUALIFIED, "no output was checked by an executed oracle (a person's approval "
                                                "checks nothing)")
        events = self._missions.events(mission_id)
        closed = [e["detail"] for e in events if e["kind"] == "run.closed"]
        facts = acceptance.gather(
            plan=plan, runs=self._ledger.runs(mission_id),
            artifacts={e["evidence_id"]: e["detail"] for e in events if e["kind"] == "artifact"},
            closed=closed, decisions=self.decisions.all(mission_id),
            receipts=[],  # no outbox in the library: no send receipts
            verdicts=self._ledger.verdicts(mission_id),
            created_at=mission["created_at"], finished_at=stamp(self._now()),
            questions=self.decisions.requested(mission_id),
            effects=self._caps.side_effect)
        return acceptance.evaluate(self._missions.acceptance(mission_id), facts, outcome,
                                   extra=(*acceptance.unit_requirements(plan),
                                          *self._missions.unconfirmed_standard(mission_id)))  # S15 (E10b)

    def _unit_outcome(self, mission_id: str, spec: UnitSpec, status: str | None, why: str = "") -> Outcome:
        key = spec.key
        closed = [r for r in self._ledger.runs(mission_id, key) if r["state"] == "closed"]
        aside = self._set_aside_by_a_person(mission_id).get(key)
        if aside is not None and status == "complete":  # its fail stands, set aside by a person's judgement (V4c)
            return Outcome(QUALIFIED, reasons=(f"verifier {key}'s fail was set aside by a human: {aside['reason']}",),
                           evidence=(aside["decision_id"],), source="human decision")
        if status == "complete" and closed and key in self._advisory(mission_id):
            # Founder, 2026-10-08: an advisory check that could not decide leaves the result qualified-draft, in words
            # that name what was not compared; it is never a stop and never a pass.
            return outcome_of(closed[-1])
        if status != "complete":
            tries = self._ledger.attempts(mission_id, key)
            escalated = (f"unit {key} was escalated: {why}",) if status == "blocked" and why else ()
            if status == "failed" and self._tries(mission_id, spec) >= spec.max_attempts:
                escalated = self._exhausted(mission_id, key, tries)
            return Outcome(BLOCKED, reasons=(f"unit {key} did not complete ({status}; {tries} attempt(s))",
                                             *escalated))
        if not closed or not self._passed(mission_id, spec, closed[-1]):
            why = f"unit {key} is complete in the tracker but its ledger holds no passing run"
            return Outcome(BLOCKED, reasons=(why,))
        last = outcome_of(closed[-1])
        if spec.kind == "verify":
            latest = self._ledger.last_artifact(mission_id, spec.verifies)
            if latest is None or not judged_latest(self._ledger.verdicts(mission_id, spec.verifies), key,
                                                   latest["artifact_id"]):
                why = (f"verifier {key} has not passed unit {spec.verifies}'s latest output (a pass counts only on "
                       "the output it judged)")
                return Outcome(BLOCKED, reasons=(why,))
        if spec.kind != "do":
            return last
        artifact_id = closed[-1]["artifact_id"]
        aside = self._set_aside_by_a_person(mission_id)  # those verifiers' units count instead, at most qualified-draft
        judged = [v for v in self._ledger.verdicts(mission_id, key)
                  if v["artifact_id"] == artifact_id and v["unit_key"] not in aside]
        if not judged:
            return Outcome(BLOCKED, reasons=(f"unit {key}'s output was never judged",))
        worst = min((v["state"] for v in judged), key=rank)
        reasons = tuple(r for v in judged for r in json.loads(v["reasons_json"] or "[]"))
        judged_outcome = Outcome(worst, degraded=last.degraded, reasons=reasons, source=f"verdicts on {key}")
        # Gate W: a verified verdict counts only from a verifier-grade (A4) check, however the row was written.
        if any(v["state"] == VERIFIED and v.get("authority") != VERIFIER_GRADE for v in judged):
            judged_outcome = judged_outcome.demote(QUALIFIED, AUTHORITY_COPY["verdict_capped"])
        for reason in judged_outcome.reasons:
            if reason == AUTHORITY_COPY["verdict_capped"] or reason in NOT_INDEPENDENT:
                judged_outcome = judged_outcome.demote(
                    judged_outcome.state, AUTHORITY_COPY["rollup_capped"].format(unit=key, reason=reason))
        if last.degraded:  # with what degraded it (V3a: "shadow: nothing was sent ...")
            why = "; ".join(r.removeprefix("degraded: ") for r in last.reasons if r.startswith("degraded: "))
            judged_outcome = judged_outcome.demote(QUALIFIED, f"unit {key}'s output was degraded"
                                                   + (f": {why}" if why else ""))
        return judged_outcome

    def _exhausted(self, mission_id: str, key: str, tries: int) -> tuple[str, ...]:
        """A unit whose attempts are spent, failed in a way the runtime retried: what its last attempt failed on, why,
        how material it is, what resolves it and the code (Design §23; fix I2 of the Part 1 review), read from the
        ledger. Nothing when the last attempt recorded no failure."""
        runs = self._ledger.runs(mission_id, key)
        last = runs[-1] if runs else None
        if last is None or last["state"] == "open" or not last["failure_code"]:
            return ()
        what = f"This step stopped after {tries} attempt{'' if tries == 1 else 's'}"
        return (f"unit {key}: " + classes.explain(last["failure_code"], "; ".join(outcome_of(last).reasons),
                                                  what=what),)

    def _advisory_unmeasured(self, mission_id: str, spec: UnitSpec, row: dict) -> bool:
        """Founder, 2026-10-08: a code change's search for hidden failures (the probe's check: authority A0, it advises)
        answered that it could not compare part of the change. Its unit completes unmeasured instead of stopping the
        mission; the result is at most qualified-draft and the probe's reason names what nothing checked. A runner that
        never started is not this: it settles as an unavailable tool (``reconfigure.on_settle``) before this is asked."""
        if not (spec.kind == "verify" and self._missions.acceptance(mission_id).metadata.get("coding")
                and "regression" in ((spec.oracle or {}).get("params") or {})):
            return False
        if row["state"] != "closed" or row["failure_code"] != "verify_unmeasured":
            return False
        from g6.workforce import patch_checks

        return any(patch_checks.could_not_compare(record.get("probe") or {})
                   for event in self._missions.events(mission_id, kind="run.closed")
                   if event["detail"].get("run_id") == row["run_id"]
                   for record in event["detail"].get("verification") or [] if record.get("check") == spec.key)

    @staticmethod
    def _advisory_keys(plan: Plan) -> set[str]:
        """The units of a code change's plan that only advise: its searches for hidden failures (the oracle's params
        carry ``regression``)."""
        return {u.key for u in plan.units if u.kind == "verify" and u.verifies == "patch"
                and "regression" in ((u.oracle or {}).get("params") or {})}

    def unchecked(self, mission_id: str) -> tuple[str, ...]:
        """Founder, 2026-10-08: the words of each advisory check on the patch's latest output that a person should read:
        what it could not compare (its probe's reason, which names what nothing checked) and what changed in a function
        the objective asks to change (its probe's disclosure); () when neither."""
        found = self._advisory(mission_id, None)
        words = []
        for event in self._missions.events(mission_id, kind="run.closed"):
            for record in event["detail"].get("verification") or []:
                verdict = found.get(record.get("check"))
                if verdict is None or event["detail"].get("run_id") != verdict["run_id"]:
                    continue
                probe = record.get("probe") or {}
                words += [probe.get("reason", "") if verdict["result"] == "unmeasured" else "", probe.get("disclosed", "")]
        words += self._suite_words(mission_id)
        gap = self._missions.acceptance(mission_id).metadata.get("coding", {}).get("cannot_check")
        if gap:
            # COPY: The existing result caveats disclose the selected guarantee and missing checker.
            words.append(f"Cannot check: {gap['requirement']} ({gap['why']}); best result qualified-draft.")
        return tuple(dict.fromkeys(word for word in words if word))

    def _suite_words(self, mission_id: str) -> list[str]:
        """ECHA Stage A: what the repository's whole suite did not cover for this change, in a person's words: the tests
        the frozen standard could not count, a suite that could not be run to freeze it, and a patch that could not be
        compared with it (the test check's own record on the patch's latest output)."""
        standard = self._missions.acceptance(mission_id).metadata.get("coding")
        if not standard:
            return []
        from g6.workforce import coding

        words = coding.suite_words(standard)
        latest = self._ledger.last_artifact(mission_id, "patch")
        suites = [record["suite"] for event in self._missions.events(mission_id, kind="run.closed")
                  for record in event["detail"].get("verification") or []
                  if latest is not None and isinstance(record.get("suite"), dict)
                  and record.get("artifact_id") == latest["artifact_id"]]
        if any(suite.get("status") == "not_run" for suite in suites):
            words.append(coding.PATCH_NOT_COMPARED)
        unimported = max((suite.get("not_collected", 0) for suite in suites), default=0)
        if unimported:
            words.append(coding.NOT_IMPORTED.format(count=unimported))
        return words

    def _advisory(self, mission_id: str, results: tuple | None = ("unmeasured",)) -> dict[str, dict]:
        """The completed advisory checks whose verdict on the patch's latest output is one of ``results`` (unmeasured
        by default; every result when None), by unit key; {} for a mission that is not a code change
        (``_advisory_unmeasured``)."""
        if not self._missions.acceptance(mission_id).metadata.get("coding"):
            return {}
        latest = self._ledger.last_artifact(mission_id, "patch")
        if latest is None:
            return {}
        status = {u["key"]: u["status"] for u in self._graph.units(mission_id)}
        keys = self._advisory_keys(self._plan(self._missions.get(mission_id)))
        return {v["unit_key"]: v for v in self._ledger.verdicts(mission_id, "patch")
                if v["unit_key"] in keys and status.get(v["unit_key"]) == "complete"
                and v["artifact_id"] == latest["artifact_id"] and (results is None or v["result"] in results)}

    def _set_aside_by_a_person(self, mission_id: str) -> dict[str, dict]:
        """Verifier unit -> the person's decision that set its fail aside (V4c)."""
        return {d["unit_key"]: d for d in self.decisions.all(mission_id)
                if d["kind"] == "adjudication" and d["decision"] == "set_aside"}

    def _plan(self, mission: dict) -> Plan:
        """The mission's plan as its mode runs it: each step's attempts capped by the mode (E2d; the stored plan and its
        digest unchanged)."""
        plan = self._missions.plan(mission["mission_id"], mission["plan_revision"] or None)[1]
        return scheduler.limited(plan, scheduler.settings(self._missions.mode(mission["mission_id"]))
                                 .max_attempts_per_work_unit)
