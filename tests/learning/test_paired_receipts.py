# SPDX-License-Identifier: Apache-2.0
"""The paired evaluator and its receipts (0.3 design 14.2, "admit paired credit"): both arms measured on every case,
incomplete arms kept, a fixed order, one permitted difference, no shared credit, and the ledger as the only custody.
Offline: stand-in agents, no model."""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import pytest

from g6.core import cloud_consent as cloud
from g6.core import llm
from g6.core.ledger import ChainLedger
from g6.learning.paired_eval import (ARM_STATES, DIFFERENCE, KIND, REPLAY, Arm, PairedEvaluator, PairReceipt, apply,
                                     receipts)
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import NO_STRATEGY, TEMPLATE, StrategyArtefact, StrategyError, StrategyStore

X = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
Y = dataclasses.replace(X, instruction="Try the smallest case first.")
BUDGET = {"max_calls": 100, "max_seconds": 60.0, "max_usd": 1.0}
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n}",
                      metadata={"rubric": "", "topic": "sums"}) for n in range(1, 9)]


class Clock:
    def __init__(self, step: float = 1.0) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def rig(tmp_path: Path, agent: Any, **changed: Any) -> tuple[PairedEvaluator, StrategyStore, ChainLedger]:
    store = StrategyStore(tmp_path / "strategies")
    store.put(X)
    store.put(Y)
    ledger = changed.pop("ledger", None) or ChainLedger(tmp_path / "receipts.jsonl")
    given = dict(store=store, ledger=ledger, agent=agent, agent_id="stand-in/1", cohort_id="cohort-1",
                 split="heldout", seed=7, budget=BUDGET, environment_digest="e" * 64,
                 evaluator_ref="test://paired-evaluator", clock=Clock())
    given.update(changed)
    return PairedEvaluator(**given), store, ledger


def helped(prompt: str) -> AgentReply:
    """Right with the hint, wrong without: the candidate arm passes and the baseline arm fails."""
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    return AgentReply(str(2 * n) if "Optional hints" in prompt else "?", AgentTrace(num_turns=1, cost_usd=0.002))


def test_both_arms_are_measured_on_every_case(tmp_path: Path) -> None:
    evaluator, _, ledger = rig(tmp_path, helped)
    found = evaluator.run(CASES, candidate=X.content_digest, replay_id="r1")
    assert len(found) == len(CASES) and all(r.complete and r.credits for r in found)
    for receipt in found:
        for arm in (receipt.baseline, receipt.candidate):
            assert arm.state == "completed" and arm.success in (True, False)
            assert arm.seconds == 1.0 and arm.cost_usd == 0.002 and arm.cost_measured
        assert receipt.candidate.success and not receipt.baseline.success
        assert receipt.baseline.strategy_digest == NO_STRATEGY and receipt.candidate.strategy_digest == X.content_digest
        assert receipt.candidate.applied and not receipt.baseline.applied
        assert receipt.task_digest == hashlib.sha256(next(c for c in CASES if c.instance_id == receipt.case_id)
                                                     .input.encode()).hexdigest()
        assert receipt.budget == (("max_calls", 100.0), ("max_seconds", 60.0), ("max_usd", 1.0))
        assert (receipt.cohort_id, receipt.replay_id, receipt.split, receipt.seed) == ("cohort-1", "r1", "heldout", 7)
    assert receipts(ledger) == found and len({r.pair_id for r in found}) == len(found)
    assert [entry["kind"] for entry in ledger.entries()] == [KIND] * len(CASES)
    assert PairReceipt.from_body(found[0].to_body()) == found[0] and len(found[0].digest) == 64


def test_a_cost_the_trace_does_not_give_is_unmeasured_never_estimated(tmp_path: Path) -> None:
    evaluator, _, _ = rig(tmp_path, lambda prompt: "2")
    receipt = evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0]
    assert receipt.complete and receipt.baseline.cost_usd is None and not receipt.baseline.cost_measured
    assert receipt.candidate.cost_usd is None and receipt.candidate.seconds == 1.0


def _raises(error: Exception) -> Any:
    def agent(prompt: str) -> str:
        raise error
    return agent


@pytest.mark.parametrize("state", ["refused", "interrupted", "missing"])
def test_missing_refused_and_interrupted_arms_stay_in_the_record(tmp_path: Path, state: str) -> None:
    agents = {"refused": _raises(llm.LLMError(f"{cloud.REQUIRED}: withheld")),
              "interrupted": _raises(RuntimeError("the machine went to sleep")),
              "missing": helped}
    changed = {"budget": {**BUDGET, "max_calls": 1}} if state == "missing" else {}
    evaluator, _, ledger = rig(tmp_path, agents[state], **changed)
    found = evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")
    assert len(found) == 1 and not found[0].complete and not found[0].credits
    states = {found[0].baseline.state, found[0].candidate.state}
    assert state in states and states <= set(ARM_STATES)
    for arm in (found[0].baseline, found[0].candidate):
        assert (arm.success is None) == (arm.state != "completed")
    assert receipts(ledger) == found                                   # written and read back, not dropped


def test_an_arm_over_its_time_or_cost_budget_is_missing(tmp_path: Path) -> None:
    slow, _, _ = rig(tmp_path / "slow", helped, clock=Clock(step=61.0))
    assert {slow.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0].candidate.state} == {"missing"}
    dear, _, _ = rig(tmp_path / "dear", lambda p: AgentReply("2", AgentTrace(cost_usd=2.0)))
    receipt = dear.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0]
    assert receipt.candidate.state == "missing" and receipt.candidate.cost_usd == 2.0 and not receipt.complete


def test_order_is_fixed_by_seed_and_case(tmp_path: Path) -> None:
    sent: list[str] = []

    def recording(prompt: str) -> str:
        sent.append(prompt)
        return "2"

    first, _, _ = rig(tmp_path / "a", recording)
    orders = [r.order for r in first.run(CASES, candidate=X.content_digest, replay_id="r1")]
    again, _, _ = rig(tmp_path / "b", recording)
    assert [r.order for r in again.run(CASES, candidate=X.content_digest, replay_id="r1")] == orders
    assert set(orders) == {"baseline_first", "candidate_first"}        # it differs across cases
    for index, order in enumerate(orders):                             # and the order recorded is the order sent
        pair = sent[2 * index:2 * index + 2]
        assert ("Optional hints" in pair[0]) == (order == "candidate_first")
    other_seed, _, _ = rig(tmp_path / "c", recording, seed=8)
    assert [r.order for r in other_seed.run(CASES, candidate=X.content_digest, replay_id="r1")] != orders


def test_a_difference_other_than_the_strategy_is_refused(tmp_path: Path) -> None:
    def altering(prompt: str) -> AgentReply:
        seen = prompt + "\nAlso: answer in French." if "Optional hints" in prompt else prompt
        return AgentReply("2", AgentTrace(prompt_sha256=hashlib.sha256(seen.encode()).hexdigest()))

    evaluator, _, ledger = rig(tmp_path / "wrapper", altering)
    with pytest.raises(StrategyError) as caught:
        evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")
    assert caught.value.code == DIFFERENCE and receipts(ledger, strategy_digest=X.content_digest) == ()

    def honest(prompt: str) -> AgentReply:
        return AgentReply("2", AgentTrace(prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()))

    assert rig(tmp_path / "honest", honest)[0].run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0].complete

    calls = 0
    root = tmp_path / "edited"

    def editing(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:                                # after the run began, the stored bytes change under the digest
            path = root / "strategies" / f"{X.content_digest}.json"
            path.write_bytes(path.read_bytes().replace(b"every stated", b"no stated"))
        return "2"

    late, _, ledger = rig(root, editing, seed=1)
    with pytest.raises(StrategyError) as caught:
        late.run(CASES[:2], candidate=X.content_digest, replay_id="r1")
    assert caught.value.code == DIFFERENCE
    assert all(r.complete for r in receipts(ledger)) and len(receipts(ledger)) <= 1


def test_the_recorded_difference_is_the_rendered_block(tmp_path: Path) -> None:
    from g6.core.ledger import canonical

    evaluator, _, _ = rig(tmp_path, helped)
    receipt = evaluator.run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0]
    block = TEMPLATE.replace("{instruction}", X.instruction)
    assert receipt.permitted_difference == hashlib.sha256(
        canonical({"baseline": "", "candidate": block}).encode()).hexdigest()
    assert receipt.candidate.prompt_digest == apply(X, CASES[0].input, {"topic": "sums"}).prompt_digest
    assert receipt.baseline.prompt_digest == hashlib.sha256(CASES[0].input.encode()).hexdigest()


def test_an_unmatched_trigger_is_recorded_and_credits_nothing(tmp_path: Path) -> None:
    scoped = dataclasses.replace(X, trigger="topic:geometry")
    evaluator, store, _ = rig(tmp_path, helped)
    store.put(scoped)
    receipt = evaluator.run(CASES[:1], candidate=scoped.content_digest, replay_id="r1")[0]
    assert receipt.complete and not receipt.candidate.applied and not receipt.credits
    assert receipt.candidate.prompt_digest == receipt.baseline.prompt_digest


def test_the_oracle_never_reaches_a_prompt(tmp_path: Path) -> None:
    sent: list[str] = []
    case = TaskInstance(input="Name the capital of France.", expected_output="Paris", instance_id="c",
                        metadata={"rubric": "RUBRIC-SECRET", "score_threshold": 0.5, "critical": True, "topic": "x"})
    evaluator, _, _ = rig(tmp_path, lambda prompt: sent.append(prompt) or "Paris", judge=lambda text: '{"score": 1}')
    receipt = evaluator.run([case], candidate=X.content_digest, replay_id="r1")[0]
    assert receipt.complete and receipt.critical and len(sent) == 2
    assert not any("RUBRIC-SECRET" in prompt or "Paris" in prompt for prompt in sent)
    assert case.expected_output == "Paris" and case.metadata["rubric"] == "RUBRIC-SECRET"       # unchanged by the run
    plain = dataclasses.replace(case, metadata={"topic": "x"})
    other = rig(tmp_path / "plain", lambda prompt: "Paris")[0].run([plain], candidate=X.content_digest, replay_id="r1")
    assert other[0].oracle_digest != receipt.oracle_digest and other[0].information_digest == receipt.information_digest


def test_changed_bytes_start_unmeasured(tmp_path: Path) -> None:
    evaluator, store, ledger = rig(tmp_path, helped)
    evaluator.run(CASES[:3], candidate=X.content_digest, replay_id="r1")
    renamed_body = dataclasses.replace(X, instruction=X.instruction + " Twice.")       # the same name, a new body
    store.put(renamed_body)
    assert len(receipts(ledger, strategy_digest=X.content_digest)) == 3
    assert receipts(ledger, strategy_digest=renamed_body.content_digest) == ()


def test_no_shared_credit(tmp_path: Path) -> None:
    evaluator, _, ledger = rig(tmp_path, helped)
    evaluator.run(CASES[:4], candidate=X.content_digest, replay_id="r1")
    named = {r.candidate.strategy_digest for r in receipts(ledger)} | {r.baseline.strategy_digest
                                                                       for r in receipts(ledger)}
    assert named == {X.content_digest, NO_STRATEGY} and Y.content_digest not in named
    assert receipts(ledger, strategy_digest=Y.content_digest) == ()


def test_a_shared_baseline_is_run_once_per_case_in_a_replay_and_named(tmp_path: Path) -> None:
    sent: list[str] = []

    def recording(prompt: str) -> str:
        sent.append(prompt)
        return "2"

    evaluator, _, ledger = rig(tmp_path, recording)
    first = evaluator.run(CASES[:3], candidate=X.content_digest, replay_id="r1")
    assert len(sent) == 6
    second = evaluator.run(CASES[:3], candidate=Y.content_digest, replay_id="r1")
    assert len(sent) == 9, "the baseline arm was run again inside one replay"
    for earlier, later in zip(first, second):
        assert later.order == "baseline_shared" and later.baseline_shared_from == earlier.pair_id
        assert later.baseline == earlier.baseline
    third = evaluator.run(CASES[:3], candidate=Y.content_digest, replay_id="r2")          # another replay shares nothing
    assert len(sent) == 15 and all(r.baseline_shared_from == "" for r in third)
    assert len(receipts(ledger)) == 9


def test_a_resumed_run_reuses_no_arm_across_replays(tmp_path: Path) -> None:
    calls = 0

    def stops_after_three_cases(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if 6 < calls <= 8:
            raise RuntimeError("the machine went to sleep")
        return "2"

    evaluator, _, ledger = rig(tmp_path, stops_after_three_cases)
    first = evaluator.run(CASES[:4], candidate=X.content_digest, replay_id="r1")
    assert [r.complete for r in first] == [True, True, True, False]
    resumed = evaluator.run(CASES[:4], candidate=X.content_digest, replay_id="r1")
    assert calls == 10, "a case that already had a complete receipt was run again"
    assert resumed[:3] == first[:3] and resumed[3].complete and resumed[3].pair_id != first[3].pair_id
    kept = receipts(ledger)
    assert len(kept) == 5 and sum(r.case_id == "case-4" for r in kept) == 2          # the interrupted pair stays
    for changed in ({"seed": 8}, {"budget": {**BUDGET, "max_seconds": 30.0}}, {"agent_id": "another-agent"},
                    {"environment_digest": "f" * 64}, {"split": "shadow"}):
        other, _, _ = rig(tmp_path, stops_after_three_cases, ledger=ledger, **changed)
        with pytest.raises(StrategyError) as caught:
            other.run(CASES[:4], candidate=X.content_digest, replay_id="r1")
        assert caught.value.code == REPLAY
    fresh, _, _ = rig(tmp_path, stops_after_three_cases, ledger=ledger, seed=8)
    again = fresh.run(CASES[:4], candidate=X.content_digest, replay_id="r2")
    assert not {r.pair_id for r in again} & {r.pair_id for r in kept} and all(r.seed == 8 for r in again)


def test_the_ledger_is_the_custody(tmp_path: Path) -> None:
    evaluator, _, ledger = rig(tmp_path, helped)
    real = evaluator.run(CASES[:2], candidate=X.content_digest, replay_id="r1")
    arm = Arm("completed", True, 1.0, None, False, Y.content_digest, "0" * 64, True)
    by_hand = dataclasses.replace(real[0], pair_id="made-by-hand", candidate=arm)
    assert by_hand not in receipts(ledger) and receipts(ledger, strategy_digest=Y.content_digest) == ()
    path = tmp_path / "receipts.jsonl"
    before = path.read_bytes()
    edited = before.replace(b"false", b"true", 1)                     # one measured outcome flipped on disk
    assert edited != before
    path.write_bytes(edited)
    assert ChainLedger.audit(path)
    with pytest.raises(ValueError):
        receipts(ChainLedger(path))


def test_a_run_resolves_both_strategies_before_any_call(tmp_path: Path) -> None:
    sent: list[str] = []
    evaluator, _, ledger = rig(tmp_path, lambda prompt: sent.append(prompt) or "2")
    with pytest.raises(StrategyError) as caught:
        evaluator.run(CASES, candidate="0" * 64, replay_id="r1")
    assert caught.value.code == "G6_E_STRATEGY_UNKNOWN" and sent == [] and receipts(ledger) == ()
    with pytest.raises(ValueError):
        evaluator.run(CASES, candidate=X.content_digest, baseline=X.content_digest, replay_id="r1")
    with pytest.raises(ValueError):
        evaluator.run([CASES[0], CASES[0]], candidate=X.content_digest, replay_id="r1")


def test_evaluation_calls_pass_the_consent_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cloud agent with no grant: both arms are refused before anything is sent."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    posted: list[Any] = []
    monkeypatch.setattr(llm, "_post_json", lambda *args, **kwargs: posted.append(args) or {})
    agent = llm.OpenAICompatibleCaller("some-model", base_url="https://models.example.invalid/v1", api_key="k")
    evaluator, _, ledger = rig(tmp_path, agent)
    found = evaluator.run(CASES[:2], candidate=X.content_digest, replay_id="r1")
    assert posted == [], "a prompt was sent to a cloud endpoint without a grant"
    assert [(r.baseline.state, r.candidate.state) for r in found] == [("refused", "refused")] * 2
    assert cloud.disclosures() == [] and receipts(ledger) == found
    local = llm.FakeLLM(lambda system, user: "2")
    assert rig(tmp_path / "local", local)[0].run(CASES[:1], candidate=X.content_digest, replay_id="r1")[0].complete


def test_the_evaluators_call_site_is_in_the_consent_inventory() -> None:
    import ast
    import inspect

    from g6.learning import paired_eval

    tree = ast.parse(inspect.getsource(paired_eval))
    imported = " ".join(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))
    assert "from g6.core.consent_callers import guard" in imported
    assert "provider_from_env" not in imported and "host_models" not in imported
    arm = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_arm")
    sends = [node for node in ast.walk(arm) if isinstance(node, ast.Call) and getattr(node.func, "id", "") in
             ("call_agent", "score_instance")]
    guarded = [node for node in ast.walk(arm) if isinstance(node, ast.With)
               and any("request_sources" in ast.unparse(item.context_expr) for item in node.items)]
    assert len(sends) == 2 and all(any(send in ast.walk(block) for block in guarded) for send in sends)


def test_the_evaluators_sends_are_section_16s_own_and_each_is_made_under_its_declared_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read from the objects and from a run, never from a spelling: the guard, the agent call and the scorer the
    evaluator uses are section 16's and ``g6.learning``'s own objects; its module binds no provider module, class or
    factory but the caller protocol; a model caller it is handed is replaced by the guard's; and when each send is
    made, by the agent and by the judge, the declared source is the prompt that arm was sent."""
    import inspect
    import json

    from g6.core import consent_callers, host_models
    from g6.learning import paired_eval, scoring

    assert paired_eval.guard is consent_callers.guard and paired_eval.cloud is cloud
    assert paired_eval.call_agent is scoring.call_agent and paired_eval.score_instance is scoring.score_instance
    providers = [value for module in (llm, host_models) for value in vars(module).values()
                 if (inspect.isclass(value) or inspect.isfunction(value)) and value.__module__ == module.__name__]
    bound = sorted(name for name, value in vars(paired_eval).items()
                   if value is llm or value is host_models or any(value is known for known in providers))
    assert bound == ["LLMCaller"], f"the evaluator's module binds provider objects: {bound}"

    handed: list[Any] = []
    guarded = object()
    model, judging = llm.FakeLLM(lambda system, user: "2"), llm.FakeLLM(lambda system, user: "{}")
    with monkeypatch.context() as patched:
        patched.setattr(paired_eval, "guard", lambda caller: handed.append(caller) or guarded)
        evaluator, _, _ = rig(tmp_path / "guarded", model, judge=judging)
    assert handed == [model, judging] and evaluator.agent is guarded and evaluator.judge is guarded

    sent: list[tuple[str, tuple]] = []
    judged: list[tuple] = []

    def agent(prompt: str) -> str:
        sent.append((prompt, cloud.current_sources()))
        return "four"

    def judge(text: str) -> str:
        judged.append(cloud.current_sources())
        return json.dumps({"score": 1.0, "reason": "compared"})

    case = TaskInstance(input="Write two plus two as a word.", expected_output="four", instance_id="words-1",
                        metadata={"rubric": "The answer is the word four."})
    evaluator, _, _ = rig(tmp_path / "declared", agent, judge=judge)
    receipt = evaluator.run([case], candidate=X.content_digest, replay_id="r1")[0]
    assert receipt.complete and len(sent) == 2 and len(judged) == 2
    assert cloud.current_sources() == ()                               # nothing stays declared after the run
    declared = [cloud.boundary_sources((cloud.Source.text(cloud.input_source(prompt), "strategy-pair-prompt", prompt),))
                for prompt, _ in sent]
    assert [sources for _, sources in sent] == declared and all(declared)
    assert judged == declared                                          # each verdict is asked under its own arm's prompt
