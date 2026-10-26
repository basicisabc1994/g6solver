# SPDX-License-Identifier: Apache-2.0
"""``run_learning``: measure proposed theories with paired arms on the training set, then the frozen harness on a
held-out set. A run is a measurement; it never says ``verified``.

Rewritten from the archive's ``runner.py`` and the train / evaluate / holdout ops of ``learning_block.py``
(audit 006):

- every arm runs every instance of its set; nothing is subsampled (LL-004: the archive's cycle-0 baseline was one
  sampled instance, and ``lift`` compared it with a full final arm);
- each cycle the strategy proposes theories; each runs with and without it on the same training instances, and only
  disagreeing pairs credit it (LL-005). A theory that wins more pairs than it loses joins the run's harness, and the
  next cycle measures against that harness;
- every prompt is built afresh from the harness (LL-010: the archive wrapped the previous cycle's wrapper);
- the held-out set is measured read-only: the raw agent against the agent with the frozen harness, on the same
  instances, and each harness theory alone for its promotion evidence (LL-003: the archive's holdout ran the raw agent
  only). Nothing from it is written to the store;
- a training set with a protected split, or one sharing an instance with the held-out set, is refused (LL-012);
- a finished measurement is ``qualified-draft``; a refusal or an early stop (budget, a failing agent) is
  ``blocked-escalated`` (LL-002: the archive said ``verified`` unless its stop reason contained one of three words).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from g6.core.llm import LLMCaller
from g6.learning.addendum import render_addendum, trigger_matches, with_addendum
from g6.learning.failure_classifier import DefaultClassifier
from g6.learning.observer import EventEmitter, LearningObserver, ObserverFailure
from g6.learning.ooda import ObservationResult
from g6.learning.paired import CONFIRMED, DISCONFIRMED, ArmComparison, PairedOutcome
from g6.learning.promotion import HeldOutEvidence
from g6.learning.schema import PROTECTED_SPLITS, AgentReply, AgentTrace, Dataset, PromptAddendum, TaskInstance, \
    TaskResult, Theory
from g6.learning.scoring import Agent, call_agent, score_instance
from g6.learning.strategies import LearningStrategy
from g6.learning.theory_store import TheoryStore

MEASURED = "G6_E_LEARNING_UNVERIFIED_MEASUREMENT"
BUDGET_EXCEEDED = "G6_E_LEARNING_BUDGET_EXCEEDED"
AGENT_FAILED = "G6_E_LEARNING_AGENT_FAILED"
#: Codes that mean the run was refused or stopped early: the report is ``blocked-escalated``.
BLOCKING_CODES = frozenset({"G6_E_LEARNING_NO_AGENT", "G6_E_LEARNING_EMPTY_DATASET", "G6_E_LEARNING_NO_HOLDOUT",
                            "G6_E_LEARNING_HOLDOUT_SPLIT", "G6_E_LEARNING_HOLDOUT_OVERLAP",
                            "G6_E_LEARNING_EXPECTED_MISSING", "G6_E_LEARNING_NO_JUDGE", BUDGET_EXCEEDED, AGENT_FAILED})

Judge = LLMCaller | Callable[[str], str]


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class _Budget:
    max_usd: float | None = None
    max_calls: int | None = None
    calls: int = 0
    cost_usd: float = 0.0

    def before_call(self) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"the run reached max_agent_calls={self.max_calls}")
        self.calls += 1

    def after_call(self, trace: AgentTrace) -> None:
        self.cost_usd += max(0.0, trace.cost_usd)
        if self.max_usd is not None and self.cost_usd > self.max_usd:
            raise BudgetExceeded(f"agent cost ${self.cost_usd:.4f} exceeded budget_usd=${self.max_usd:.4f}")


@dataclass
class CycleRecord:
    cycle: int
    n: int
    accuracy: float
    failure_breakdown: dict[str, int] = field(default_factory=dict)
    proposed: list[str] = field(default_factory=list)
    measured: dict[str, dict[str, int]] = field(default_factory=dict)
    harness: list[str] = field(default_factory=list)


@dataclass
class LearningReport:
    """What a run measured. ``holdout`` pairs the raw agent with the frozen ``harness`` on the held-out instances;
    ``evidence`` holds each harness theory's own held-out pairs, for :func:`g6.learning.promote`."""

    strategy: str
    code: str = MEASURED
    reason: str = ""
    stop_reason: str = ""
    cycles: list[CycleRecord] = field(default_factory=list)
    harness: tuple[Theory, ...] = ()
    holdout: ArmComparison | None = None
    evidence: dict[str, HeldOutEvidence] = field(default_factory=dict)
    training_instance_ids: tuple[str, ...] = ()
    agent_calls: int = 0
    cost_usd: float = 0.0
    store_path: str = ""
    store_ephemeral: bool = True
    run_id: str = ""
    request_id: str = ""
    observer_failures: list[ObserverFailure] = field(default_factory=list)

    @property
    def completion_state(self) -> str:
        # audit 006, LL-002: a measurement on the caller's tasks has no path to a verified state
        return "blocked-escalated" if self.code in BLOCKING_CODES else "qualified-draft"

    def addendum(self, task_meta: dict[str, Any] | None = None, max_chars: int = 6000) -> PromptAddendum:
        """The frozen harness as hint text for a task (for export; using it on agents needs promotion first)."""
        return render_addendum(self.harness, task_meta, max_chars)

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "code": self.code, "reason": self.reason, "stop_reason": self.stop_reason,
                "cycles": [asdict(c) for c in self.cycles], "harness": [t.theory_id for t in self.harness],
                "holdout": self.holdout.to_dict() if self.holdout else None,
                "held_out_evidence": {tid: {"pairs": len(e.pairs), "wins": e.wins, "losses": e.losses}
                                      for tid, e in self.evidence.items()},
                "n_training_instances": len(self.training_instance_ids), "agent_calls": self.agent_calls,
                "cost_usd": self.cost_usd, "store_path": self.store_path, "store_ephemeral": self.store_ephemeral}

    def to_envelope(self) -> dict[str, Any]:
        from g6.core.reliability_envelope import envelope_fields, warning_card

        message = self.reason or ("A measurement on the caller's held-out tasks, not a verification. A theory is used "
                                  "outside training only after promote() recommends it and a person accepts it.")
        evidence = []
        if self.holdout is not None:
            h = self.holdout
            evidence.append({"source": "g6.learning.run_learning",
                             "summary": f"held-out: baseline {h.baseline_passed}/{h.n}, adapted {h.adapted_passed}/"
                                        f"{h.n}, {h.wins} wins, {h.losses} losses", **h.to_dict()})
        return {**self.to_dict(), **envelope_fields(
            reliability_label=self.completion_state,  # type: ignore[arg-type]
            warning_card=warning_card(self.code, message), evidence=evidence, request_id=self.request_id,
            run_id=self.run_id)}


def run_arm(instances: Sequence[TaskInstance], agent: Agent, theories: Sequence[Theory] = (), *,
            judge: Judge | None = None, max_chars: int = 6000, max_theories: int = 10,
            budget: _Budget | None = None) -> list[TaskResult]:
    """Run every instance once, with the hint block ``theories`` give that instance, scored by the instance."""
    results = []
    for inst in instances:
        prompt = with_addendum(render_addendum(theories, inst.metadata, max_chars, max_theories=max_theories),
                               inst.input)
        if budget is not None:
            budget.before_call()
        reply = call_agent(agent, prompt)
        if budget is not None:
            budget.after_call(reply.trace)
        results.append(_result_for(inst, prompt, reply, judge))
    return results


def compare_arms(dataset: Dataset, agent: Agent, theories: Sequence[Theory] = (), *, judge: Judge | None = None,
                 max_chars: int = 6000, max_theories: int = 10) -> ArmComparison:
    """The raw agent and the agent with ``theories`` on every instance of ``dataset`` (with none, one arm serves
    as both). Evaluating a frozen harness on new tasks is this call."""
    instances = list(dataset.instances)
    base = run_arm(instances, agent, (), judge=judge)
    adapted = run_arm(instances, agent, theories, judge=judge, max_chars=max_chars,
                      max_theories=max_theories) if theories else base
    return ArmComparison.from_results(base, adapted, [t.theory_id for t in theories])


def run_learning(strategy: LearningStrategy, dataset: Dataset, agent: Agent | None, *, holdout: Dataset | None,
                 store: TheoryStore | None = None, judge: Judge | None = None, max_cycles: int = 3,
                 max_theories: int = 10, max_addendum_chars: int = 6000, budget_usd: float | None = None,
                 max_agent_calls: int | None = None, observers: list[LearningObserver] | None = None,
                 run_id: str = "", request_id: str = "", proposer: str = "") -> LearningReport:
    """Train ``strategy`` on ``dataset`` and measure the result on ``holdout`` (see the module docstring).

    Without ``store`` the run uses an in-memory store, reported as ephemeral, and nothing it learns persists.
    ``proposer`` (who asked for the run) is recorded on each theory it adds, so that name cannot accept them.
    """
    owned = store is None
    store = store if store is not None else TheoryStore(":memory:", scope=f"learning-run:{run_id or 'unnamed'}")
    emitter = EventEmitter(observers)
    budget = _Budget(budget_usd, max_agent_calls)
    report = LearningReport(strategy=str(getattr(strategy, "name", type(strategy).__name__)), store_path=store.path,
                            store_ephemeral=store.ephemeral, run_id=run_id, request_id=request_id)
    try:
        refusal = _refusal(dataset, holdout, agent, judge)
        if refusal:
            report.code, report.reason = refusal
            return _finish(report, emitter, budget)
        assert agent is not None and holdout is not None

        def arm(instances: Sequence[TaskInstance], theories: Sequence[Theory]) -> list[TaskResult]:
            return run_arm(instances, agent, theories, judge=judge, max_chars=max_addendum_chars,
                           max_theories=max_theories, budget=budget)

        train = list(dataset.instances)
        report.training_instance_ids = tuple(i.instance_id for i in train)
        harness: list[Theory] = []
        measured: set[str] = set()
        try:
            for cycle in range(max_cycles):
                # every theory of this cycle is measured against the same control: the harness at the cycle's start
                cycle_harness = list(harness)
                base = arm(train, cycle_harness)
                if cycle == 0 and all(r.trace.error for r in base):
                    report.code, report.reason = AGENT_FAILED, f"every agent call failed: {base[0].trace.error}"
                    return _finish(report, emitter, budget)
                observations = ObservationResult(tuple(base), cycle)
                record = CycleRecord(cycle, len(base), observations.accuracy, _breakdown(base))
                report.cycles.append(record)
                emitter.emit("cycle_observed", cycle=cycle, n=len(base), accuracy=observations.accuracy)
                pending = _admit(strategy.propose(observations, store, cycle=cycle), store, measured, max_theories,
                                 record, proposer)
                if not pending:
                    report.stop_reason = "no new candidate theory to measure"
                    break
                without = {r.instance_id: r.passed for r in base}
                for theory in pending:
                    matched = [i for i in train if trigger_matches(theory.trigger, i.metadata)]
                    tally = Counter(store.record_paired_outcome(theory.theory_id, r.instance_id, r.passed,
                                                                without[r.instance_id], run_id=run_id)
                                    for r in arm(matched, [*cycle_harness, theory]))
                    measured.add(theory.theory_id)
                    record.measured[theory.theory_id] = dict(tally)
                    emitter.emit("theory_credited", cycle=cycle, theory_id=theory.theory_id, **tally)
                    if tally[CONFIRMED] > tally[DISCONFIRMED]:
                        harness.append(store.get(theory.theory_id) or theory)
                record.harness = [t.theory_id for t in harness]
            else:
                report.stop_reason = f"max cycles ({max_cycles}) reached"
            report.harness = tuple(harness)
            report.holdout, report.evidence = _measure_holdout(list(holdout.instances), harness, arm,
                                                               report.training_instance_ids, run_id)
        except BudgetExceeded as exc:
            report.harness = tuple(harness)
            report.code, report.reason = BUDGET_EXCEEDED, f"stopped before the end: {exc}"
        return _finish(report, emitter, budget)
    finally:
        if owned:
            store.close()


def _measure_holdout(held: list[TaskInstance], harness: list[Theory],
                     arm: Callable[[Sequence[TaskInstance], Sequence[Theory]], list[TaskResult]],
                     training_ids: Sequence[str], run_id: str) -> tuple[ArmComparison, dict[str, HeldOutEvidence]]:
    # audit 006, LL-003: the adapted arm applies the frozen harness; the baseline is the raw agent, same instances.
    base = arm(held, [])
    adapted = arm(held, harness) if harness else base
    comparison = ArmComparison.from_results(base, adapted, [t.theory_id for t in harness])
    without = {r.instance_id: r.passed for r in base}
    evidence: dict[str, HeldOutEvidence] = {}
    for theory in harness:
        matched = [i for i in held if trigger_matches(theory.trigger, i.metadata)]
        alone = adapted if len(harness) == 1 and len(matched) == len(held) else arm(matched, [theory])
        evidence[theory.theory_id] = HeldOutEvidence(
            theory.theory_id, theory.digest,
            tuple(PairedOutcome(r.instance_id, r.passed, without[r.instance_id]) for r in alone),
            frozenset(training_ids), "holdout", run_id)
    return comparison, evidence


def _refusal(dataset: Dataset, holdout: Dataset | None, agent: Agent | None,
             judge: Judge | None) -> tuple[str, str] | None:
    if agent is None:
        return "G6_E_LEARNING_NO_AGENT", "no agent was given, so nothing can be measured"
    if not dataset.instances:
        return "G6_E_LEARNING_EMPTY_DATASET", f"the training set {dataset.name!r} is empty"
    if holdout is None or not holdout.instances:
        return "G6_E_LEARNING_NO_HOLDOUT", "a non-empty held-out set is required; training results never promote"
    if dataset.split.lower() in PROTECTED_SPLITS:  # audit 006, LL-012
        return ("G6_E_LEARNING_HOLDOUT_SPLIT", f"the training set {dataset.name!r} has split {dataset.split!r}, which "
                "is held out; learning from it would tune on the evaluation data")
    shared_ids = {i.instance_id for i in dataset.instances} & {i.instance_id for i in holdout.instances}
    shared_inputs = {i.input for i in dataset.instances} & {i.input for i in holdout.instances}
    if shared_ids or shared_inputs:
        return ("G6_E_LEARNING_HOLDOUT_OVERLAP", f"the training and held-out sets share {len(shared_ids)} instance "
                f"id(s) and {len(shared_inputs)} task text(s)")
    everything = [*dataset.instances, *holdout.instances]
    unscorable = [i.instance_id for i in everything if not i.scorable]
    if unscorable:  # audit 006, LL-001: nothing to score against is a refusal, never a pass
        return ("G6_E_LEARNING_EXPECTED_MISSING", f"{len(unscorable)} instance(s) have neither an expected answer nor "
                f"a rubric, for example {unscorable[0]!r}")
    if judge is None and any(i.needs_judge for i in everything):
        return "G6_E_LEARNING_NO_JUDGE", "rubric or judge-scored instances need an injected judge"
    return None


def _admit(proposals: list[Theory], store: TheoryStore, measured: set[str], max_theories: int,
           record: CycleRecord, proposer: str = "") -> list[Theory]:
    added = store.add_candidates([replace(t, proposer=t.proposer or proposer) for t in proposals
                                  if store.get(t.theory_id) is None])
    record.proposed = [t.theory_id for t in added]
    chosen: dict[str, Theory] = {}
    for proposal in proposals:
        stored = store.get(proposal.theory_id)
        if stored is not None and stored.status == "candidate" and stored.theory_id not in measured:
            chosen.setdefault(stored.theory_id, stored)
    return list(chosen.values())[:max_theories]


def _breakdown(results: Sequence[TaskResult]) -> dict[str, int]:
    classifier = DefaultClassifier()
    return dict(Counter(classifier.classify(r).primary_code for r in results if not r.passed))


def _result_for(inst: TaskInstance, prompt: str, reply: AgentReply, judge: Judge | None) -> TaskResult:
    if reply.trace.error:
        score, passed, signals = 0.0, False, {"score_mode": inst.score_mode, "agent_error": reply.trace.error}
    else:
        score, passed, signals = score_instance(inst, reply.output, judge=judge)
    signals["prompt_chars"] = len(prompt)  # audit 006, LL-010: prompt growth is measured, not silent
    return TaskResult(inst.instance_id, reply.output, passed, score, signals, reply.trace)


def _finish(report: LearningReport, emitter: EventEmitter, budget: _Budget) -> LearningReport:
    report.agent_calls, report.cost_usd = budget.calls, budget.cost_usd
    emitter.emit("run_complete", code=report.code, stop_reason=report.stop_reason, agent_calls=budget.calls)
    report.observer_failures = list(emitter.failures)
    return report


__all__ = ["AGENT_FAILED", "BLOCKING_CODES", "BUDGET_EXCEEDED", "MEASURED", "BudgetExceeded", "CycleRecord",
           "LearningReport", "compare_arms", "run_arm", "run_learning"]
