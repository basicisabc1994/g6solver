# SPDX-License-Identifier: Apache-2.0
"""Applying one strategy and admitting paired credit (0.3 design 14.2, "apply the strategy", "admit paired credit").

``apply`` puts one frozen instruction in front of a task, as ``g6.learning.addendum`` renders it, and changes nothing
else. It takes the task's text and the metadata a trigger reads: it has no way to the expected answer, the judge, an
evidence plan or a gate.

``PairedEvaluator`` runs a baseline arm and a candidate arm on the same instances, with the same agent, judge and
budget, in an order fixed by the seed and the case. It builds both prompts itself, records what it sent, checks that
the two prompts differ by the strategy's rendered block and nothing else, measures each arm's success, time and cost,
and appends one ``PairReceipt`` per case to a ``ChainLedger`` before it returns. An arm that was refused, interrupted or
stopped by the budget stays in the receipt: an incomplete pair is recorded, never dropped.

Credit comes only from receipts read back from that ledger (``receipts``). A receipt built by hand, a digest a caller
asserts and an outcome a caller reports supply nothing. One run measures one candidate: no other strategy is credited.

Every model call here passes section 16's consent check: a model caller is guarded (``g6.core.consent_callers.guard``)
and each prompt is declared as its source. A plain callable agent makes its own sends and is its owner's to guard.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

from g6.core import cloud_consent as cloud
from g6.core.consent_callers import guard
from g6.core.ledger import ChainLedger, canonical
from g6.core.llm import LLMCaller
from g6.learning.addendum import render_addendum, trigger_matches, with_addendum
from g6.learning.schema import PromptAddendum, TaskInstance, Theory
from g6.learning.scoring import Agent, call_agent, score_instance
from g6.learning.strategy import LABEL, NO_STRATEGY, StrategyArtefact, StrategyError, StrategyStore

ARM_STATES = ("completed", "missing", "refused", "interrupted")
SPLITS = ("training", "heldout", "shadow")
ORDERS = ("baseline_first", "candidate_first", "baseline_shared")
KIND = "strategy_pair"
DIFFERENCE = "G6_E_STRATEGY_DIFFERENCE"
REPLAY = "G6_E_STRATEGY_REPLAY"
BUDGET_KEYS = ("max_calls", "max_seconds", "max_usd")
#: Metadata that belongs to the oracle. A trigger never reads it and ``apply`` is never handed it.
ORACLE_KEYS = frozenset({"rubric", "criteria", "score_mode", "score_threshold", "critical"})
_SEPARATOR = "\n\n"
Judge = LLMCaller | Callable[[str], str]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Applied:
    prompt: str
    prompt_digest: str
    strategy_digest: str
    applied: bool                 # False: no strategy, or its trigger did not match this task


def _block(artefact: StrategyArtefact, task_meta: Mapping[str, Any] | None) -> str:
    """The hint block the addendum code renders for this one instruction; ``""`` when its trigger does not match."""
    if not trigger_matches(artefact.trigger, task_meta):
        return ""
    theory = Theory(theory_id=artefact.content_digest, failure_code=LABEL, instruction=artefact.instruction,
                    trigger=artefact.trigger)
    max_chars = int(dict(artefact.config).get("max_chars", "6000"))
    block = render_addendum([theory], task_meta, max_chars, max_theories=1).text
    if block and block != artefact.template.replace("{instruction}", artefact.instruction):
        raise StrategyError(DIFFERENCE, "the hint block rendered for this strategy is not its pinned template")
    return block


def apply(artefact: StrategyArtefact | None, task: str, task_meta: Mapping[str, Any] | None = None) -> Applied:
    """The prompt for ``task`` under one strategy: the strategy's hint block, a blank line, the task; or the task
    unchanged for ``None`` (``NO_STRATEGY``) and for a strategy whose trigger does not match (``applied`` False)."""
    if artefact is None:
        return Applied(task, _sha(task), NO_STRATEGY, False)
    block = _block(artefact, task_meta)
    prompt = with_addendum(PromptAddendum(block), task)
    return Applied(prompt, _sha(prompt), artefact.content_digest, bool(block))


@dataclass(frozen=True)
class Arm:
    state: str                    # completed | missing | refused | interrupted
    success: bool | None          # None unless completed
    seconds: float | None         # the evaluator's clock; None when the arm was not run
    cost_usd: float | None        # from the agent's trace; None when it gives none (never estimated)
    cost_measured: bool
    strategy_digest: str
    prompt_digest: str
    applied: bool

    def __post_init__(self) -> None:
        if self.state not in ARM_STATES:
            raise ValueError(f"an arm's state is one of {ARM_STATES}")
        if (self.success is not None) != (self.state == "completed"):
            raise ValueError("an arm has a measured success exactly when it completed")


@dataclass(frozen=True)
class PairReceipt:
    pair_id: str
    cohort_id: str
    case_id: str
    replay_id: str
    split: str                                    # training | heldout | shadow
    task_digest: str
    agent_id: str
    information_digest: str                       # what both arms were given beyond the task: the trigger metadata
    oracle_digest: str                            # the expected answer, rubric and scoring rule; never sent
    environment_digest: str
    budget: tuple[tuple[str, float], ...]         # max_calls, max_seconds, max_usd
    seed: int
    order: str                                    # baseline_first | candidate_first | baseline_shared
    permitted_difference: str                     # sha256 of the hint block the candidate prompt differs by
    baseline: Arm
    candidate: Arm
    evaluator_ref: str
    baseline_shared_from: str = ""                # the pair whose baseline arm this receipt names, within one replay
    critical: bool = False                        # the case is marked critical: a regression on it is counted apart

    def __post_init__(self) -> None:
        if self.split not in SPLITS or self.order not in ORDERS:
            raise ValueError("a pair receipt's split or order is not one it can have")

    def to_body(self) -> dict[str, Any]:
        body = asdict(self)
        body["budget"] = [list(pair) for pair in self.budget]
        return body

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> "PairReceipt":
        names = {field.name for field in fields(cls)}
        if set(body) != names:
            raise ValueError("not a pair receipt")
        return cls(**{**body, "budget": tuple((str(k), float(v)) for k, v in body["budget"]),
                      "baseline": Arm(**body["baseline"]), "candidate": Arm(**body["candidate"])})

    @property
    def digest(self) -> str:
        return _sha(canonical(self.to_body()))

    @property
    def complete(self) -> bool:
        """Both arms completed, so the pair can say something. An incomplete pair is kept and credits nothing."""
        return self.baseline.state == "completed" and self.candidate.state == "completed"

    @property
    def credits(self) -> bool:
        """Whether this pair is evidence about its candidate: complete, and the strategy was really applied."""
        return self.complete and self.candidate.applied


def receipts(ledger: ChainLedger, *, strategy_digest: str | None = None) -> tuple[PairReceipt, ...]:
    """Every pair receipt the ledger holds (for one candidate strategy, when named), in the order written. The ledger
    is the custody: a chain that does not check out raises, and a receipt that is not in it does not exist."""
    problems = ledger.verify()
    if problems:
        raise ValueError(f"the receipt ledger does not check out: {problems[0]}")
    found = tuple(PairReceipt.from_body(entry["body"]) for entry in ledger.entries() if entry["kind"] == KIND)
    return tuple(r for r in found if strategy_digest is None or r.candidate.strategy_digest == strategy_digest)


class PairedEvaluator:
    """The outside evaluator of one strategy against a baseline; see the module docstring.

    ``budget`` holds ``max_calls`` (agent calls in one ``run``), ``max_seconds`` and ``max_usd`` (per arm). ``clock``
    is the evaluator's own (``time.monotonic``), replaced in tests.
    """

    def __init__(self, *, store: StrategyStore, ledger: ChainLedger, agent: Agent, agent_id: str, cohort_id: str,
                 split: str, seed: int, budget: Mapping[str, float], judge: Judge | None = None,
                 environment_digest: str, evaluator_ref: str, clock: Callable[[], float] = time.monotonic) -> None:
        if split not in SPLITS:
            raise ValueError(f"split is one of {SPLITS}")
        if set(budget) != set(BUDGET_KEYS) or not all(isinstance(budget[k], (int, float)) and budget[k] > 0
                                                      for k in BUDGET_KEYS):
            raise ValueError(f"budget names exactly {BUDGET_KEYS}, each above zero")
        if not all(isinstance(text, str) and text for text in (agent_id, cohort_id, environment_digest, evaluator_ref)):
            raise ValueError("agent, cohort, environment and evaluator are named")
        if type(seed) is not int:
            raise ValueError("seed is a whole number")
        self.store, self.ledger = store, ledger
        self.agent = guard(agent) if isinstance(agent, LLMCaller) else agent
        self.judge = guard(judge) if isinstance(judge, LLMCaller) else judge
        self.agent_id, self.cohort_id, self.split, self.seed = agent_id, cohort_id, split, seed
        self.budget = tuple((key, float(budget[key])) for key in BUDGET_KEYS)
        self.environment_digest, self.evaluator_ref, self.clock = environment_digest, evaluator_ref, clock
        self._calls = 0

    def _resolve(self, digest: str) -> StrategyArtefact | None:
        return None if digest == NO_STRATEGY else self.store.resolve(digest)

    def _again(self, digest: str) -> StrategyArtefact | None:
        """The strategy's bytes, hashed again at send time: bytes that changed after the run began are a difference
        the pair does not permit."""
        try:
            return self._resolve(digest)
        except StrategyError as exc:
            raise StrategyError(DIFFERENCE, f"the strategy's bytes changed after the run began: {exc}") from None

    def _arm(self, instance: TaskInstance, digest: str, meta: Mapping[str, Any]) -> tuple[Arm, str]:
        """Run one arm: resolve the strategy's bytes again, build the prompt, send it, measure. Returns the arm and
        the hint block its prompt carried."""
        budget = dict(self.budget)
        artefact = self._again(digest)
        made = apply(artefact, instance.input, meta)
        block = made.prompt[:-len(instance.input) - len(_SEPARATOR)] if made.applied else ""
        if made.prompt != (block + _SEPARATOR + instance.input if block else instance.input):
            raise StrategyError(DIFFERENCE, "a prompt is not its strategy's block, a blank line and the task")
        base = {"strategy_digest": digest, "prompt_digest": made.prompt_digest, "applied": made.applied}
        if self._calls >= budget["max_calls"]:
            return Arm("missing", None, None, None, False, **base), block
        self._calls += 1
        started = self.clock()
        source = cloud.Source.text(cloud.input_source(made.prompt), "strategy-pair-prompt", made.prompt)
        with cloud.request_sources((source,)):
            reply = call_agent(self.agent, made.prompt)
        seconds = max(0.0, self.clock() - started)
        cost = reply.trace.cost_usd if reply.trace.cost_usd > 0 else None
        timing = {"seconds": seconds, "cost_usd": cost, "cost_measured": cost is not None}
        echoed = getattr(reply.trace, "prompt_sha256", "")
        if echoed and echoed != made.prompt_digest:
            raise StrategyError(DIFFERENCE, "the backend reports a prompt other than the one the evaluator sent")
        if reply.trace.error:
            refused = any(code in reply.trace.error for code in (cloud.REQUIRED, cloud.STATE_UNAVAILABLE))
            return Arm("refused" if refused else "interrupted", None, **timing, **base), block
        if seconds > budget["max_seconds"] or (cost is not None and cost > budget["max_usd"]):
            return Arm("missing", None, **timing, **base), block
        with cloud.request_sources((source,)):
            _, passed, signals = score_instance(instance, reply.output, judge=self.judge)
        if signals.get("judge_error"):
            return Arm("interrupted", None, **timing, **base), block
        return Arm("completed", bool(passed), **timing, **base), block

    def _settings(self, receipt: PairReceipt) -> tuple[Any, ...]:
        return (receipt.seed, receipt.budget, receipt.agent_id, receipt.split, receipt.environment_digest)

    def run(self, instances: Sequence[TaskInstance], *, candidate: str, baseline: str = NO_STRATEGY,
            replay_id: str) -> tuple[PairReceipt, ...]:
        """One receipt per instance, each appended to the ledger before this returns. Both strategies' bytes are
        resolved before any call. Within one ``replay_id`` a case that already has a complete receipt for this pair of
        strategies is not run again, a baseline arm already measured for the case is named instead of repeated, and a
        changed seed, budget, agent, split or environment is refused (``G6_E_STRATEGY_REPLAY``): no arm is reused
        across replays."""
        if not replay_id or candidate == baseline:
            raise ValueError("a run names its replay, and a candidate that is not its own baseline")
        self._resolve(candidate)
        self._resolve(baseline)
        self._calls = 0
        mine = (self.seed, self.budget, self.agent_id, self.split, self.environment_digest)
        earlier = [r for r in receipts(self.ledger) if (r.cohort_id, r.replay_id) == (self.cohort_id, replay_id)]
        if any(self._settings(r) != mine for r in earlier):
            raise StrategyError(REPLAY, f"replay {replay_id!r} was begun with another seed, budget, agent, split or "
                                        "environment; a changed run is a new replay")
        if len({instance.instance_id for instance in instances}) != len(instances):
            raise ValueError("a case appears twice in one run")
        out: list[PairReceipt] = []
        for instance in instances:
            case = instance.instance_id
            same_case = [r for r in earlier if r.case_id == case]
            kept = next((r for r in same_case if r.complete and r.candidate.strategy_digest == candidate
                         and r.baseline.strategy_digest == baseline), None)
            if kept is not None:
                out.append(kept)
                continue
            meta = {key: value for key, value in instance.metadata.items() if key not in ORACLE_KEYS}
            first_bit = int(_sha(f"{self.seed}:{case}")[-1], 16) & 1
            shared = next((r for r in same_case if r.baseline.state == "completed"
                           and r.baseline.strategy_digest == baseline
                           and r.task_digest == _sha(instance.input)), None)
            if shared is not None:
                order, base_arm = "baseline_shared", shared.baseline
                base_block = self._block_of(baseline, meta)
                cand_arm, cand_block = self._arm(instance, candidate, meta)
            elif first_bit:
                order = "candidate_first"
                cand_arm, cand_block = self._arm(instance, candidate, meta)
                base_arm, base_block = self._arm(instance, baseline, meta)
            else:
                order = "baseline_first"
                base_arm, base_block = self._arm(instance, baseline, meta)
                cand_arm, cand_block = self._arm(instance, candidate, meta)
            artefact = self._again(candidate)
            expected = _block(artefact, meta) if artefact is not None else ""
            if cand_block != expected or base_block != self._block_of(baseline, meta):
                raise StrategyError(DIFFERENCE, "the two prompts differ by something other than the strategy's block")
            attempt = sum(r.candidate.strategy_digest == candidate and r.baseline.strategy_digest == baseline
                          for r in same_case)
            pair_id = _sha(canonical([self.cohort_id, replay_id, case, candidate, baseline, attempt]))[:32]
            oracle = _sha(canonical({"expected": instance.expected_output,
                                     "rule": {key: instance.metadata[key] for key in sorted(ORACLE_KEYS)
                                              if key in instance.metadata}}))
            receipt = PairReceipt(
                pair_id=pair_id, cohort_id=self.cohort_id, case_id=case, replay_id=replay_id, split=self.split,
                task_digest=_sha(instance.input), agent_id=self.agent_id,
                information_digest=_sha(canonical(_plain(meta))), oracle_digest=oracle,
                environment_digest=self.environment_digest, budget=self.budget, seed=self.seed, order=order,
                permitted_difference=_sha(canonical({"baseline": base_block, "candidate": cand_block})),
                baseline=base_arm, candidate=cand_arm, evaluator_ref=self.evaluator_ref,
                baseline_shared_from=shared.pair_id if shared is not None else "",
                critical=instance.metadata.get("critical") is True)
            self.ledger.append(KIND, pair_id, receipt.to_body())
            earlier.append(receipt)
            out.append(receipt)
        return tuple(out)

    def _block_of(self, digest: str, meta: Mapping[str, Any]) -> str:
        artefact = self._again(digest)
        return _block(artefact, meta) if artefact is not None else ""


def _plain(value: Any) -> Any:
    """Metadata as JSON can hold it: keys as text, anything else by its ``repr``."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value if isinstance(value, (str, int, bool)) or value is None else repr(value)


__all__ = ["ARM_STATES", "DIFFERENCE", "KIND", "ORACLE_KEYS", "REPLAY", "SPLITS", "Applied", "Arm", "PairReceipt",
           "PairedEvaluator", "apply", "receipts"]
