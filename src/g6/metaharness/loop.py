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

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import digest
from g6.metaharness.api import _blocked, _envelope, judging_sha256
from g6.metaharness.domain import Cut, Domain, FailureRecord, cut_pool, domain_problems, judge_source
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.method import Method, tune
from g6.metaharness.monitor import MIN_PAIRS
from g6.metaharness.propose import DevRunner, ProposerView
from g6.metaharness.schema import APPROVAL_REQUIRED, EvidencePolicy, Suite, Workflow
from g6.metaharness.shadow import SCOPE, adapt_shadow
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import ENDS_A_STREAK, CycleLog, beat, stop_reason
from g6.self_maintenance.pacing import ProviderWindow

NOT_ENROLLED = "G6_E_LOOP_NOT_ENROLLED"
POOL_TOO_SMALL = "G6_E_LOOP_POOL_TOO_SMALL"
STOPPED = "G6_E_LOOP_STOPPED"
ENROLLED = "G6_E_LOOP_ENROLLED"
STATUS = "G6_E_LOOP_STATUS"
IDLE = "G6_E_LOOP_IDLE"
CYCLE = "G6_E_LOOP_CYCLE"
CYCLE_FAILED = "G6_E_LOOP_CYCLE_FAILED"
NO_CONSTRAINT = "G6_E_LOOP_NO_CONSTRAINT"
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


def _workflow(domain: Domain, cut: Cut, settings: LoopSettings) -> Workflow:
    run, judge = domain.execution(), domain.judge()
    suite = Suite(dev=cut.tune, selection=cut.selection, held_out=cut.held_out,
                  provenance={"dev": "caller", "held_out": judge.answers_by})
    return Workflow(name=domain.name, task=run.task, checks=run.checks, oracle=judge.verdict, suite=suite,
                    models=run.models, policy=EvidencePolicy(max_eval_model_calls=settings.eval_calls),
                    optional_checks=run.optional_checks, pipeline_steps=run.pipeline_steps, feedback=run.feedback)


def _pool_problems(cut: Cut, policy: EvidencePolicy) -> list[tuple[str, str]]:
    needs = {"tuning": (cut.tune, 1), "selection": (cut.selection, 1), "held-out": (cut.held_out, policy.min_cases),
             "monitor": (cut.monitor, MIN_PAIRS)}
    return [(POOL_TOO_SMALL, f"the {name} split has {len(found)} cases and needs {least}")
            for name, (found, least) in needs.items() if len(found) < least]


class ShadowLoop:
    def __init__(self, domain: Domain, root: str | Path, settings: LoopSettings, *, proposer: Any,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.domain, self.settings, self._proposer, self._now = domain, settings, proposer, now
        self.root = Path(root).resolve()
        self.log = CycleLog(self.root / "records" / "constraint_log.jsonl")
        self.problems = domain_problems(domain)
        self.cut, self.workflow, self.mh = None, None, None
        if not self.problems:
            self.cut = cut_pool(domain.development(), settings.seed)
            self.workflow = _workflow(domain, self.cut, settings)
            self.problems = _pool_problems(self.cut, self.workflow.policy) + self.workflow.problems()
        if not self.problems:
            self.mh = adapt_shadow(self.workflow, domain.execution().factory, workspace=self.root / "workspace",
                                   clock=clock)

    def _frozen(self) -> dict[str, str]:
        """What must not change: what judges, and every frozen case."""
        judge = self.domain.judge()
        return {"evaluator": digest({"judging": judging_sha256(), "workflow": self.workflow.digest(),
                                     "judge_source": judge_source(judge.verdict).read_bytes().hex()}),
                "cases": self.cut.digest(), "sealed": self.domain.sealed().vault.digest}

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
        return self._invalid()

    def enrol(self, confirmed_by: str) -> dict[str, Any]:
        """A person confirms what judges this domain. It happens once, and it is the one step the loop cannot do."""
        if self.problems:
            return self._invalid()
        who = confirmed_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person enrols a domain (no empty or auto: name)")
        confirmed = self.mh.confirm(who, initial=self.domain.execution().initial)
        if confirmed["completion_state"] == "blocked-escalated":
            return confirmed
        frozen = self._frozen()
        self.log.enrol(who, frozen, domain=self.domain.name, at=self._now().isoformat())
        return _envelope("qualified-draft", ENROLLED, f"{self.domain.name} is enrolled: the loop may run on it", **frozen,
                         next_action="loop.tick()")

    def tick(self) -> dict[str, Any]:
        """One cycle. A refusal is a reply, never a raised error, and a heartbeat follows every tick, so someone
        watching can tell a loop that is waiting from one that has died."""
        reply = self._refused() or self._run()
        self._beat(reply["completion_state"], reply["warning_card"]["code"])
        return reply

    def _beat(self, state: str, code: str) -> None:
        beat(self.root, domain=self.domain.name, at=self._now().isoformat(), tick=len(self.log.cycles()), state=state,
             code=code)

    def _run(self) -> dict[str, Any]:
        self._beat("running", CYCLE)
        try:
            row = self._cycle()
        except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
            row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error", "code": CYCLE_FAILED,
                   "reason": f"{type(exc).__name__}: {exc}"[:500]}
        return self._reply(self.log.cycle(row))

    def _cycle(self) -> dict[str, Any]:
        version, head = self.mh._head()
        row: dict[str, Any] = {"at": self._now().isoformat(), "domain": self.domain.name, "head_version": version,
                               "head_profile": head.digest()}
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
        return {**row, **self._exploit(constraint)}

    def _exploit(self, constraint: Constraint) -> dict[str, Any]:
        recommended: list[dict[str, Any]] = []

        def nobody(decided: dict[str, Any]) -> None:
            recommended.append(decided)              # no name is given: none stands in for a person's

        proposer = _OnConstraint(FailureTraceStrategy(self._proposer), constraint.case_ids)
        run = tune(self.mh, self.settings.method, approve=nobody, reflector=self._proposer,
                   space=self.domain.execution().space, extra=(proposer,))
        said = {"confirmation": run.confirmation, "decision": run.decision, "champion": run.champion}
        if not recommended:
            reasons = (run.decision or {}).get("reasons") or run.notes or ["no candidate beat the head"]
            return {**said, "outcome": run.outcome, "code": (run.decision or {}).get("code", CYCLE),
                    "reason": "; ".join(reasons), "recovery": run.recovery}
        adopted = self.mh.adopt(recommended[-1]["decision"])
        if adopted["completion_state"] == "blocked-escalated":
            return {**said, "outcome": "blocked", "code": adopted["warning_card"]["code"], "reason": adopted["message"]}
        return {**said, "outcome": "adopted", "code": adopted["warning_card"]["code"], "reason": adopted["message"],
                "head_version": adopted["head_version"]}

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
