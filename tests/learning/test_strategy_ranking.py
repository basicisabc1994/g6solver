# SPDX-License-Identifier: Apache-2.0
"""Ranking strategies by measured paired worth, beside the evidence plan (0.3 design 14.2 and 15.6): rows carry the
paired statistic and nothing named a success proxy, worth is read from the evaluator's ledger and not from the caller,
and the selection is the same with and without strategies."""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from g6.cognitive_bridge import MetacognitiveMethodSelector, MethodVariant, ProblemSignature
from g6.cognitive_bridge.metacognition import ROW_FIELDS
from g6.core.ledger import ChainLedger
from g6.learning.method_kind import STRATEGY_ROW_FIELDS, rank_seeds, rank_strategies, variant_fields
from g6.learning.paired_eval import PairedEvaluator
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import StrategyArtefact, StrategyStore, seed_artefacts
from g6.learning.worth import DEFAULT_POLICY, bind_policy
from g6.mcp import invoke_reply
from g6.skills import routing

GOOD = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
BAD = dataclasses.replace(GOOD, instruction="Answer with a question mark.")
NEW = dataclasses.replace(GOOD, instruction="Try the smallest case first.")
CONTEXT = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n}")
         for n in range(1, 41)]                  # the frozen bar asks for thirty complete cases


def heuristic_variant(artefact: StrategyArtefact, *, id: str, estimated_cost: float) -> MethodVariant:
    return MethodVariant(**variant_fields(artefact, id=id, estimated_cost=estimated_cost))


def agent(prompt: str) -> AgentReply:
    """Right with GOOD's hint, wrong with BAD's, and right on the first twenty cases with no hint."""
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    if GOOD.instruction in prompt:
        answer = str(2 * n)
    elif BAD.instruction in prompt:
        answer = "?"
    else:
        answer = str(2 * n) if n <= 20 else "?"
    return AgentReply(answer, AgentTrace(cost_usd=0.01))


def measured(tmp_path: Path) -> tuple[list[MethodVariant], StrategyStore, ChainLedger]:
    store, ledger = StrategyStore(tmp_path / "strategies"), None
    for artefact in (GOOD, BAD, NEW):
        store.put(artefact)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    bind_policy(ledger, "cohort-1")              # the frozen policy, before the first trial
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=agent, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget={"max_calls": 999, "max_seconds": 60, "max_usd": 5},
                                environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    evaluator.run(CASES, candidate=GOOD.content_digest, replay_id="r1")
    evaluator.run(CASES, candidate=BAD.content_digest, replay_id="r1")
    variants = [heuristic_variant(NEW, id="a-new", estimated_cost=0), heuristic_variant(BAD, id="b-bad", estimated_cost=0),
                heuristic_variant(GOOD, id="c-good", estimated_cost=0)]
    return variants, store, ledger


def test_rows_carry_exactly_the_strategy_fields(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    rows = rank_strategies(CONTEXT, variants, ledger=ledger, store=store)
    assert len(rows) == 3 and all(tuple(sorted(row)) == tuple(sorted(STRATEGY_ROW_FIELDS)) for row in rows)
    proxies = {"smoothed_success_proxy", "ranking_score", "exploration_bonus", "historical_resolutions",
               "success_rate", "score"}
    assert not proxies & set(STRATEGY_ROW_FIELDS) and proxies & set(ROW_FIELDS)
    assert [row["variant_id"] for row in rows] == ["c-good", "b-bad", "a-new"]
    good, bad, new = rows
    assert (good["status"], good["wins"], good["losses"], good["net_benefit"], good["meets_bar"]) == \
        ("measured", 20, 0, 0.5, True)
    assert (bad["status"], bad["wins"], bad["losses"], bad["meets_bar"]) == ("measured", 0, 20, False)
    assert bad["regressions"] == sorted(f"case-{n}" for n in range(1, 21))
    assert (new["status"], new["cases"], new["net_benefit"]) == ("unmeasured", 0, None)
    assert {row["policy_digest"] for row in rows} == {DEFAULT_POLICY.digest}
    json.dumps(rows)                                                    # plain data


def test_ranking_reads_the_ledger_not_the_caller(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    parameters = set(inspect.signature(rank_strategies).parameters)
    assert parameters == {"context", "variants", "ledger", "store", "evidence"}       # no receipt and no bar
    empty = rank_strategies(CONTEXT, variants, ledger=ChainLedger(), store=store)
    assert {row["status"] for row in empty} == {"unmeasured"}           # the same variants, no receipts in this ledger
    assert [row["variant_id"] for row in empty] == ["a-new", "b-bad", "c-good"]


def test_a_strategy_whose_bytes_do_not_resolve_is_unmeasured(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    path = tmp_path / "strategies" / f"{GOOD.content_digest}.json"
    path.write_bytes(path.read_bytes().replace(b"every stated", b"no stated"))
    rows = {row["variant_id"]: row for row in rank_strategies(CONTEXT, variants, ledger=ledger, store=store)}
    assert rows["c-good"]["status"] == "unmeasured" and rows["b-bad"]["status"] == "measured"


def test_ranking_selects_applies_and_executes_nothing(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    before = (ledger.head(), sorted(p.name for p in (tmp_path / "strategies").iterdir()))
    kept = sorted(p.name for p in (tmp_path / "receipts.jsonl.evidence").iterdir())
    engine = MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)
    rows = rank_strategies(CONTEXT, [*variants, engine], ledger=ledger, store=store)
    assert [row["variant_id"] for row in rows] == ["c-good", "b-bad", "a-new"]              # no engine row
    assert (ledger.head(), sorted(p.name for p in (tmp_path / "strategies").iterdir())) == before
    assert sorted(p.name for p in (tmp_path / "receipts.jsonl.evidence").iterdir()) == kept
    # read from the objects the function reaches, not from its text: it reaches the reading of worth, and neither
    # the strategy's application, the evaluator, the agent call nor the selector
    from g6.learning import method_kind, paired_eval, scoring
    from g6.learning import worth as worth_module

    codes, names = [rank_strategies.__code__], set()
    while codes:
        code = codes.pop()
        names |= set(code.co_names)
        codes += [const for const in code.co_consts if inspect.iscode(const)]
    reached = {name: vars(method_kind)[name] for name in names if name in vars(method_kind)}
    assert reached["worth"] is worth_module.worth and reached["unmeasured"] is worth_module.unmeasured
    doors = (paired_eval.apply, paired_eval.PairedEvaluator, scoring.call_agent, MetacognitiveMethodSelector)
    assert not [name for name, found in reached.items() if any(found is door for door in doors)]


def test_the_evidence_plan_is_identical_with_and_without_strategies(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    engines = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)]
    selector = MetacognitiveMethodSelector()
    without = selector.select(CONTEXT, engines, required_methods=["formal"], max_optional=2)
    rank_strategies(CONTEXT, variants, ledger=ledger, store=store)
    with_them = selector.select(CONTEXT, [*engines, *variants], required_methods=["formal"], max_optional=2)
    assert json.dumps(with_them.to_dict(), sort_keys=True) == json.dumps(without.to_dict(), sort_keys=True)


def test_a_variant_the_context_cannot_afford_has_no_row(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    dear = heuristic_variant(NEW, id="dear", estimated_cost=11)
    rows = rank_strategies(CONTEXT, [variants[2], dear], ledger=ledger, store=store)
    assert [row["variant_id"] for row in rows] == ["c-good"]
    with pytest.raises(ValueError, match="duplicate"):
        rank_strategies(CONTEXT, [variants[2], variants[2]], ledger=ledger, store=store)


def test_the_seeds_are_unmeasured_until_a_run_writes_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    rows = rank_seeds()
    assert [row["variant_id"] for row in rows] == sorted(seed_artefacts())
    assert {row["status"] for row in rows} == {"unmeasured"} and {row["policy_digest"] for row in rows} == {
        DEFAULT_POLICY.digest}
    assert not (tmp_path / "home" / "strategies").exists(), "reading the ranking wrote something"


def test_the_op_is_routed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    entry = routing.load().entries["op:cognitive_bridge/rank_strategies"]
    assert entry.agent_facing and entry.route == "discover" and entry.since == "0.3.0"
    reply: Any = invoke_reply("cognitive_bridge", "rank_strategies", "{}")
    assert reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    assert len(reply["result"]["strategies"]) == 16
    assert {row["status"] for row in reply["result"]["strategies"]} == {"unmeasured"}
    assert all(sorted(row) == sorted(STRATEGY_ROW_FIELDS) for row in reply["result"]["strategies"])
    refused = invoke_reply("cognitive_bridge", "rank_strategies", json.dumps({"ledger": "/tmp/receipts.jsonl"}))
    assert refused["ok"] is False and refused["completion_state"] == "blocked-escalated"      # no path from a caller
