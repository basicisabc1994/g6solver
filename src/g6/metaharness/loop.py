# SPDX-License-Identifier: Apache-2.0
"""The self-improvement loop's tick, in shadow: it works on any domain, with no person in it.

One tick is one cycle of design 6.1. **Measure** the head on the tuning cases. **Identify** the constraint: the cause
a profile can change that costs the most cases, through ``self_maintenance.constraints``, and none at all when the
failure records were not independently observed. **Exploit**, by proposing within the meta-harness's editable schema:
``method.tune`` runs its usual proposers, with ``FailureTraceStrategy`` shown only the constraint's failures, and races
them on the selection split. **Gate**: the champion goes to the held-out split and the unchanged gate under
``EvidencePolicy``. **Record** the cycle in the local constraint log, and **adopt** what the gate recommends in the
loop's own workspace (``g6.metaharness.shadow``), so the next tick starts from it.

The only person in it is the one who enrols a domain: they confirm what judges the work, once, and the loop runs from
there. Someone has to say what "better" means, and it cannot be the thing being judged.

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been
established.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import digest
from g6.core.llm import DEFAULT_MAX_TOKENS
from g6.metaharness.api import _blocked, _envelope, judging_sha256
from g6.metaharness.domain import (
    Cut,
    Domain,
    FailureRecord,
    Judge,
    cut_pool,
    domain_problems,
    judge_source,
    sealed_closed,
)
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.method import Method, tune
from g6.metaharness.monitor import MIN_PAIRS
from g6.metaharness.propose import DevRunner, ProposerView
from g6.metaharness.schema import APPROVAL_REQUIRED, EvidencePolicy, Suite, Workflow
from g6.metaharness.shadow import SCOPE, adapt_shadow
from g6.operator.store import OperatorStore
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import ENDS_A_STREAK, MAX_FAILED_CYCLES, CycleLog, beat, stop_reason
from g6.self_maintenance.pacing import AccountReading, Admission, BudgetMeter, ProviderWindow
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

NOT_ENROLLED = "G6_E_LOOP_NOT_ENROLLED"
POOL_TOO_SMALL = "G6_E_LOOP_POOL_TOO_SMALL"
EVALUATOR_IN_REACH = "G6_E_LOOP_EVALUATOR_IN_REACH"
STOPPED = "G6_E_LOOP_STOPPED"
PAUSED = "G6_E_LOOP_PAUSED"
RESUMED = "G6_E_LOOP_RESUMED"
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"
HELD_OUT_SPENT = "G6_E_LOOP_HELD_OUT_SPENT"
SEALED_BREACH = "G6_E_LOOP_SEALED_BREACH"
PERSON_REQUIRED = "G6_E_LOOP_PERSON_REQUIRED"
NO_PATH_OUT = "G6_E_LOOP_NO_PATH_OUT"
ENROLLED = "G6_E_LOOP_ENROLLED"
STATUS = "G6_E_LOOP_STATUS"
IDLE = "G6_E_LOOP_IDLE"
CYCLE = "G6_E_LOOP_CYCLE"
CYCLE_FAILED = "G6_E_LOOP_CYCLE_FAILED"
NO_CONSTRAINT = "G6_E_LOOP_NO_CONSTRAINT"
BUDGET_WAIT = "G6_E_LOOP_BUDGET_WAIT"
NO_BUDGET = "G6_E_LOOP_NO_BUDGET"
BUSY = "G6_E_LOOP_BUSY"
SPLIT_REUSED = "G6_E_LOOP_SPLIT_REUSED"
UNKNOWN = "unknown"
STATUS_LINE = ("Early alpha. Individual capabilities have been tested; general application-level improvements have not "
               "yet been established.")


@dataclass(frozen=True)
class LoopSettings:
    windows: tuple[ProviderWindow, ...]
    method: Method = field(default_factory=Method)
    seed: int = 20261031             # cuts the development pool
    audit_per_tick: int = 10         # monitor cases per tick
    eval_calls: int = 2000           # the most one held-out evaluation may call; the gate's thresholds are not settings
    # THE OWNER'S RULING, NOT THE LOOP'S: may a passed gate stand in for a person outside the loop's own workspace?
    # Off until they say otherwise. No profile can reach it, and in this phase turning it on still moves nothing.
    person_free_adoption_outside_shadow: bool = False


@dataclass(frozen=True)
class Constraint:
    name: str
    basis: str
    observations: tuple[tuple[str, float], ...] = ()
    case_ids: tuple[str, ...] = ()
    reason: str = ""


def name_constraint(records: Sequence[FailureRecord], cases: int) -> Constraint:
    """The cause a profile can change that costs the most cases. Each cause is a stage of the work, and a stage's
    capacity is the share of cases it lets through, so the bottleneck is the costliest cause."""
    costs = Counter(r.cause for r in records if r.changeable)
    if not costs:
        return Constraint(UNKNOWN, "no_changeable_cause", reason="only a person can change what is failing here: "
                          + ", ".join(sorted({r.cause for r in records})))
    independent = all(r.independently_observed for r in records)
    found = identify_constraint(tuple(StageMeasurement(cause, cases, max(0, cases - cost), 1.0, independent)
                                      for cause, cost in costs.most_common(40)))
    if found.bottleneck == UNKNOWN:
        return Constraint(UNKNOWN, found.evidence, reason="no constraint is named: not every failure record was "
                                                          "independently observed")
    return Constraint(found.bottleneck, found.evidence, found.observations,
                      tuple(r.case_id for r in records if r.cause == found.bottleneck))


class _OnConstraint:
    """A proposer shown only the constraint's failures: all other work waits behind it (design 6.1, step 4)."""

    def __init__(self, inner: Any, case_ids: Sequence[str]) -> None:
        self.inner, self.name, self.case_ids = inner, inner.name, frozenset(case_ids)

    def propose(self, view: ProposerView, runner: DevRunner) -> list[Any]:
        shown = tuple(f for f in view.failures if f.case_id in self.case_ids)
        return self.inner.propose(replace(view, failures=shown), runner)


class _Counted:
    """A model caller the host counts. Every call of a tick passes through one, so what a tick is charged is what the
    host saw, and nothing a run reports about itself."""

    def __init__(self, inner: Any, counts: list[int]) -> None:
        self.inner, self.counts = inner, counts

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        self.counts[0] += 1
        return self.inner.complete(system, user, max_tokens=max_tokens)


def _workflow(domain: Domain, cut: Cut, settings: LoopSettings, counts: list[int]) -> Workflow:
    run, judge = domain.execution(), domain.judge()
    models = {name: dataclasses.replace(model, caller=_Counted(model.caller, counts))
              for name, model in run.models.items()}
    suite = Suite(dev=cut.tune, selection=cut.selection, held_out=cut.held_out,
                  provenance={"dev": "caller", "held_out": judge.answers_by})
    return Workflow(name=domain.name, task=run.task, checks=run.checks, oracle=judge.verdict, suite=suite,
                    models=models, policy=EvidencePolicy(max_eval_model_calls=settings.eval_calls),
                    optional_checks=run.optional_checks, pipeline_steps=run.pipeline_steps, feedback=run.feedback)


def _pool_problems(cut: Cut, policy: EvidencePolicy) -> list[tuple[str, str]]:
    needs = {"tuning": (cut.tune, 1), "selection": (cut.selection, 1), "held-out": (cut.held_out, policy.min_cases),
             "monitor": (cut.monitor, MIN_PAIRS)}
    return [(POOL_TOO_SMALL, f"the {name} split has {len(found)} cases and needs {least}")
            for name, (found, least) in needs.items() if len(found) < least]


def _overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _in_reach(root: Path, domain: Domain) -> list[str]:
    """What judges that lies in or around the loop's folder, which is the only place it writes."""
    judge, sealed = domain.judge(), domain.sealed()
    source = judge_source(judge.verdict) if judge is not None else None
    guarded = {"G6's own source": str(Path(g6.__file__).resolve().parent),
               **({"the judge's folder": str(source.resolve().parent)} if source is not None else {}),
               **{f"sealed root {i}": r for i, r in enumerate(sealed.vault.roots if sealed is not None else ())}}
    where = os.path.normcase(str(root))
    return [name for name, path in guarded.items() if _overlap(where, os.path.normcase(path))]


def _judge_digest(judge: Judge | None) -> str:
    source = judge_source(judge.verdict) if judge is not None else None
    return hashlib.sha256(source.read_bytes()).hexdigest() if source is not None else ""


class ShadowLoop:
    def __init__(self, domain: Domain, root: str | Path, settings: LoopSettings, *, proposer: Any,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None) -> None:
        self.domain, self.settings, self._now = domain, settings, now
        self.counts = [0]
        self._proposer = _Counted(proposer, self.counts)
        self.root = Path(root).resolve()
        reach = _in_reach(self.root, domain)
        if reach:                                # before anything is written: this folder is all the loop ever writes
            raise ValueError(f"[{EVALUATOR_IN_REACH}] the loop's folder overlaps {', '.join(reach)}: it never writes "
                             "what judges it")
        self.log = CycleLog(self.root / "records" / "constraint_log.jsonl")
        self.meter = BudgetMeter(settings.windows, store=OperatorStore(self.root / "operator.db"),
                                 read_account=read_account)
        self.problems = domain_problems(domain)
        if not settings.windows:
            self.problems.append((NO_BUDGET, "no provider window: an unknown budget stops the loop"))
        self.cut, self.workflow, self.mh = None, None, None
        if not self.problems:
            self.cut = cut_pool(domain.development(), settings.seed)
            self.workflow = _workflow(domain, self.cut, settings, self.counts)
            self.problems = _pool_problems(self.cut, self.workflow.policy) + self.workflow.problems()
        if not self.problems:
            self.mh = adapt_shadow(self.workflow, domain.execution().factory, workspace=self.root / "workspace",
                                   clock=clock)

    def worst_case(self) -> int:
        """The most one tick can call, from the limits it runs under: a measurement, the audits, each proposer and race
        of each cycle, and one evaluation. Each limit is checked before a run, so each can be passed by one run."""
        method, per_run = self.settings.method, max(m.max_calls_per_run for m in self.workflow.models.values())
        proposers = 1 + method.use_recovery + method.use_reflection + method.use_edits   # failure traces always run
        proposing = proposers * (method.propose_budget + per_run + method.reflect_calls + 1)
        racing = method.select_budget + per_run
        return (method.propose_budget + per_run + 2 * self.settings.audit_per_tick * per_run
                + method.cycles * (proposing + racing) + self.settings.eval_calls + per_run * (per_run + 2))

    def _frozen(self) -> dict[str, str]:
        """What must not change, read fresh from the domain: the code that judges, every frozen case, and the sealed
        split. The judge's whole file is digested, since a helper it calls judges too. No profile is in it."""
        run, sealed = self.domain.execution(), self.domain.sealed()
        return {"evaluator": digest({"judging": judging_sha256(), "judge": _judge_digest(self.domain.judge()),
                                     "task": run.task, "checks": [c.code_sha() for c in (*run.checks,
                                                                                         *run.optional_checks.values())]}),
                "cases": cut_pool(self.domain.development(), self.settings.seed).digest(),
                "sealed": digest({"vault": sealed.vault.digest if sealed is not None else "",
                                  "refs": [dict(r) for r in sealed.refs] if sealed is not None else []})}

    def _changed(self) -> list[str]:
        """Which of the frozen things differ from what a person enrolled. The vault's digest is of the files as sealed,
        so the sealed bytes are compared as well."""
        recorded, now = self.log.enrolled()["frozen"], self._frozen()
        changed = {name for name, value in now.items() if value != recorded.get(name)}
        sealed = self.domain.sealed()
        if sealed is not None and sealed.vault.verify_unchanged():
            changed.add("sealed")
        return sorted(changed)

    def _invalid(self) -> dict[str, Any] | None:
        if self.problems:
            return _blocked(self.problems[0][0], "; ".join(why for _, why in self.problems),
                            "Give this domain what it lacks; the loop will not run without it.")
        if self.log.enrolled() is None:
            return _blocked(NOT_ENROLLED, "no person has enrolled this domain", "loop.enrol('<your name>')")
        return None

    def _refused(self) -> dict[str, Any] | None:
        """Why this tick is not a cycle. Each is a wait or a refusal: none spends, and none is a failed cycle."""
        halted = stop_reason(self.root)
        if halted:
            return _blocked(STOPPED, halted, "Remove the STOP file to resume.")
        engaged = stop.engaged()
        if engaged is not None:
            return _blocked(stop.STOPPED, f"G6 is stopped: {engaged}", "Whoever engaged the stop releases it.")
        invalid = self._invalid()
        if invalid is not None:
            return invalid
        paused = self.log.paused()
        if paused:
            return _blocked(PAUSED, f"the loop is paused: {paused}", "A person reads why, then loop.resume('<name>').")
        changed = self._changed()
        if changed:
            return _blocked(FROZEN_CHANGED, f"changed since a person enrolled this domain: {', '.join(changed)}. Nothing "
                            "measured against it would mean what it did", "A person checks the change and enrols again.")
        if self.mh.held_out_left() <= 0:
            return _blocked(HELD_OUT_SPENT, "this held-out split has given all the evaluations it may: the gate needs a "
                            "fresh split, and only a person can confirm one", "A person freezes a fresh split and enrols.")
        return None

    def enrol(self, confirmed_by: str) -> dict[str, Any]:
        """A person confirms what judges this domain. It happens once, and it is the one step the loop cannot do."""
        if self.problems:
            return self._invalid()
        who = confirmed_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person enrols a domain (no empty or auto: name)")
        reused = self._reused()
        if reused:
            return _blocked(SPLIT_REUSED, f"{len(reused)} held-out cases were already seen by proposers or spent by an "
                            f"earlier split, for example {reused[:3]}: a gain measured on them proves nothing",
                            "Give the gate cases the loop has not used: a split changed by a few cases is not fresh.")
        confirmed = self.mh.confirm(who, initial=self.domain.execution().initial)
        if confirmed["completion_state"] == "blocked-escalated":
            return confirmed
        frozen = self._frozen()
        self.log.enrol(who, frozen, domain=self.domain.name, at=self._now().isoformat(),
                       seen=sorted(c.case_id for c in (*self.cut.tune, *self.cut.selection)),
                       held_out=sorted(c.case_id for c in self.cut.held_out),
                       held_out_digest=self.workflow.suite.digest("held_out"))
        return _envelope("qualified-draft", ENROLLED, f"{self.domain.name} is enrolled: the loop may run on it", **frozen,
                         next_action="loop.tick()")

    def _reused(self) -> list[str]:
        """Held-out cases that cannot be fresh evidence. The evaluation limit counts per split digest, so a split
        changed by one case would start again from nothing, and a re-cut could hand the gate what proposers tuned on.
        Against each earlier enrolment: nothing it let proposers see, and nothing from a split it spent."""
        current, seen = self.workflow.suite.digest("held_out"), set()
        evaluated = {r.get("split_digest") for r in self.mh.ledger.rows("evaluation")}
        for row in self.log.enrolments():
            seen |= set(row["seen"])
            if row["held_out_digest"] != current and row["held_out_digest"] in evaluated:
                seen |= set(row["held_out"])
        return sorted(c.case_id for c in self.cut.held_out if c.case_id in seen)

    def tick(self) -> dict[str, Any]:
        """One cycle. A refusal is a reply, never a raised error, and a heartbeat follows every tick, so someone
        watching can tell a loop that is waiting from one that has died. One tick at a time per folder: a restart
        can overlap a tick that is still running, and the lock is the operating system's, so it dies with its process."""
        try:
            with Coordination(self.root / ".tick").mutex():
                reply = self._refused() or self._run()
                self._beat(reply["completion_state"], reply["warning_card"]["code"])
                return reply
        except WorkspaceRefused:
            return _blocked(BUSY, "another tick is running on this folder; this one did nothing")

    def _beat(self, state: str, code: str) -> None:
        beat(self.root, domain=self.domain.name, at=self._now().isoformat(), tick=len(self.log.cycles()), state=state,
             code=code)

    def _reserve(self) -> tuple[Admission | None, dict[str, Any] | None]:
        """A provider with room for this tick's worst case, or the wait: the loop never spends anywhere else."""
        now, worst, waits = self._now(), self.worst_case(), []
        for provider in self.meter.windows:
            spend = self.meter.reserve(provider, now, worst)
            if spend.admitted:
                return spend, None
            waits.append(spend)
        until = [w.wait_until for w in waits if w.wait_until is not None]
        return None, _blocked(BUDGET_WAIT, "; ".join(f"{w.provider}: {w.reason}" for w in waits),
                              "The loop waits; it runs again when a ceiling or a reset allows.",
                              wait_until=min(until).isoformat() if until else "")

    def _run(self) -> dict[str, Any]:
        spend, waiting = self._reserve()
        if waiting is not None:
            return waiting
        self._beat("running", CYCLE)
        before, recommended = self.counts[0], []
        with sealed_closed(self.domain.sealed().vault) as breaches:
            try:
                row = self._cycle(recommended)
            except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
                row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                       "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]}
            finally:
                charged = self.meter.settle(spend, self.counts[0] - before)
        if breaches:                  # read only now: a strategy that swallowed the refusal adopts nothing either
            row |= {"outcome": "sealed_breach", "code": SEALED_BREACH,
                    "reason": f"{len(breaches)} attempt(s) to open the sealed split while developing: nothing was read "
                              "and nothing is adopted, and a person decides whether it still counts as sealed"}
        elif recommended:
            row |= self._adopt(recommended[-1]["decision"])
        seen = self.meter.reading(spend.provider, self._now())
        row["budget"] = {"provider": seen.provider, "day": seen.day, "charged": charged, "used": seen.used,
                         "target": seen.target, "ceiling": seen.ceiling, "estimated": seen.estimated}
        reply = self._reply(self.log.cycle(row))
        self._pause_if_failing(row)
        return reply

    def _adopt(self, decision: Any) -> dict[str, Any]:
        adopted = self.mh.adopt(decision)
        said = {"code": adopted["warning_card"]["code"], "reason": adopted["message"]}
        if adopted["completion_state"] == "blocked-escalated":
            return {**said, "outcome": "blocked"}
        return {**said, "outcome": "adopted", "head_version": adopted["head_version"]}

    def _pause_if_failing(self, row: dict[str, Any]) -> None:
        streak = self.log.failed_streak()
        if row["outcome"] == "sealed_breach":
            self.log.pause(row["reason"])
        elif streak >= MAX_FAILED_CYCLES:
            last = self.log.cycles()[-streak:]
            self.log.pause("three failed cycles in a row: " + "; ".join(
                f"tick {c['tick']} {c['outcome']} ({str(c.get('reason', ''))[:160]})" for c in last))

    def resume(self, accepted_by: str) -> dict[str, Any]:
        """A person lifts a pause, having read why. The loop cannot lift its own."""
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person lifts a pause (no empty or auto: name)")
        if self.log.paused():
            self.log.resume(who)
        return _envelope("qualified-draft", RESUMED, "the loop is not paused", next_action="loop.tick()")

    def _cycle(self, recommended: list[dict[str, Any]]) -> dict[str, Any]:
        version, head = self.mh._head()
        row: dict[str, Any] = {"at": self._now().isoformat(), "domain": self.domain.name, "head_version": version,
                               "head_profile": head.digest()}
        audited = self.mh.audit(self.cut.monitor, count=self.settings.audit_per_tick)
        row["audit"] = {k: audited[k] for k in ("audited", "pairs", "mean_difference", "threshold")}
        if audited["rolled_back"]:
            return {**row, "outcome": "rolled_back", "code": audited["warning_card"]["code"],
                    "reason": audited["message"], "undoes": audited["undoes"], "head_version": audited["head_version"]}
        measured = self.mh.measure(budget=self.settings.method.propose_budget)
        if "view" not in measured:
            return {**row, "outcome": "blocked", "code": measured["warning_card"]["code"], "reason": measured["message"]}
        view = measured["view"]
        row |= {"dev_success": view.dev_success, "dev_cases": len(view.dev)}
        if not view.failures:
            return {**row, "outcome": "idle", "code": IDLE, "reason": measured["message"]}
        constraint = name_constraint(self.domain.failures(view), len(view.dev))
        row |= {"constraint": constraint.name, "basis": constraint.basis,
                "observations": [list(o) for o in constraint.observations]}
        if constraint.name == UNKNOWN:
            return {**row, "outcome": "no_constraint", "code": NO_CONSTRAINT, "reason": constraint.reason}
        return {**row, **self._exploit(constraint, recommended)}

    def _exploit(self, constraint: Constraint, recommended: list[dict[str, Any]]) -> dict[str, Any]:
        """Propose and gate. A recommendation is only held here: it is adopted once the sealed split is known untouched."""

        def nobody(decided: dict[str, Any]) -> None:
            recommended.append(decided)              # no name is given: none stands in for a person's

        proposer = _OnConstraint(FailureTraceStrategy(self._proposer), constraint.case_ids)
        run = tune(self.mh, self.settings.method, approve=nobody, reflector=self._proposer,
                   space=self.domain.execution().space, extra=(proposer,))
        reasons = (run.decision or {}).get("reasons") or run.notes or ["no candidate beat the head"]
        return {"confirmation": run.confirmation, "decision": run.decision, "champion": run.champion,
                "outcome": run.outcome, "code": (run.decision or {}).get("code", CYCLE), "reason": "; ".join(reasons),
                "recovery": run.recovery}

    def leave_shadow(self) -> dict[str, Any]:
        """Take the head out of the loop's own workspace. Nothing in this phase can. Outside shadow a person accepts a
        change, and whether a passed gate may ever stand in for them is the owner's ruling, held in one setting."""
        refused = self._invalid()
        if refused is not None:
            return refused
        version, head = self.mh._head()
        if not self.settings.person_free_adoption_outside_shadow:
            return _blocked(PERSON_REQUIRED, f"version {version} holds only in the loop's own workspace: outside it a "
                            "person accepts a change", "Take this profile to an ordinary meta-harness as a candidate: "
                            "mh.candidate(profile, proposer=...), for its gate and a person.",
                            candidate=head.model_dump(mode="json"))
        return _blocked(NO_PATH_OUT, "the owner's setting allows it, but this phase has no way out of shadow: no "
                        "adoption reaches anything outside this workspace", "A later phase builds the path.",
                        candidate=head.model_dump(mode="json"))

    def _reply(self, row: dict[str, Any]) -> dict[str, Any]:
        state = "qualified-draft" if row["outcome"] in ENDS_A_STREAK else "blocked-escalated"
        fields = {k: v for k, v in row.items() if k not in ("code", "reason")}
        return _envelope(state, row["code"], row["reason"], **fields)

    def status(self) -> dict[str, Any]:
        """Where the loop stands; it runs nothing."""
        refused = self._invalid()
        if refused is not None:
            return refused
        version, head = self.mh._head()
        return _envelope("qualified-draft", STATUS, f"head version {version} of {self.domain.name}", head_version=version,
                         head_profile=head.digest(), store_version=self.mh.store.current_approved(), scope=SCOPE,
                         paused=self.log.paused(), failed_streak=self.log.failed_streak(),
                         cycles=len(self.log.cycles()), held_out_left=self.mh.held_out_left(), g6_status=STATUS_LINE)


__all__ = ["Constraint", "LoopSettings", "ShadowLoop", "name_constraint"]
