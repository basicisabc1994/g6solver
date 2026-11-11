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
from g6.cognitive_bridge.strategy import STRATEGY_ROW_FIELDS, heuristic_variant, rank_seeds, rank_strategies
from g6.core.ledger import ChainLedger
from g6.learning.paired_eval import PairedEvaluator
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance
from g6.learning.strategy import StrategyArtefact, StrategyStore, seed_artefacts
from g6.learning.worth import DEFAULT_POLICY, WorthPolicy
from g6.mcp import invoke_reply
from g6.skills import routing

GOOD = StrategyArtefact(instruction="Check the answer against every stated constraint.", trigger="always", scope="search")
BAD = dataclasses.replace(GOOD, instruction="Answer with a question mark.")
NEW = dataclasses.replace(GOOD, instruction="Try the smallest case first.")
CONTEXT = ProblemSignature("arithmetic", "exact", "answer", 1, 10)
POLICY = WorthPolicy(min_complete_cases=4, min_discordant=2, alpha=0.2, max_regressions=0, max_cost_ratio=1.25,
                     max_incomplete_share=0.2)
CASES = [TaskInstance(input=f"What is {n} + {n}?", expected_output=str(2 * n), instance_id=f"case-{n}")
         for n in range(1, 9)]


def agent(prompt: str) -> AgentReply:
    """Right with GOOD's hint, wrong with BAD's, and right on the first four cases with no hint."""
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    if GOOD.instruction in prompt:
        answer = str(2 * n)
    elif BAD.instruction in prompt:
        answer = "?"
    else:
        answer = str(2 * n) if n <= 4 else "?"
    return AgentReply(answer, AgentTrace(cost_usd=0.01))


def measured(tmp_path: Path) -> tuple[list[MethodVariant], StrategyStore, ChainLedger]:
    store, ledger = StrategyStore(tmp_path / "strategies"), None
    for artefact in (GOOD, BAD, NEW):
        store.put(artefact)
    ledger = ChainLedger(tmp_path / "receipts.jsonl")
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=agent, agent_id="stand-in/1", cohort_id="cohort-1",
                                split="heldout", seed=3, budget={"max_calls": 99, "max_seconds": 60, "max_usd": 5},
                                environment_digest="e" * 64, evaluator_ref="test://paired-evaluator")
    evaluator.run(CASES, candidate=GOOD.content_digest, replay_id="r1")
    evaluator.run(CASES, candidate=BAD.content_digest, replay_id="r1")
    variants = [heuristic_variant(NEW, id="a-new", estimated_cost=0), heuristic_variant(BAD, id="b-bad", estimated_cost=0),
                heuristic_variant(GOOD, id="c-good", estimated_cost=0)]
    return variants, store, ledger


def test_rows_carry_exactly_the_strategy_fields(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    rows = rank_strategies(CONTEXT, variants, ledger=ledger, store=store, policy=POLICY)
    assert len(rows) == 3 and all(tuple(sorted(row)) == tuple(sorted(STRATEGY_ROW_FIELDS)) for row in rows)
    proxies = {"smoothed_success_proxy", "ranking_score", "exploration_bonus", "historical_resolutions",
               "success_rate", "score"}
    assert not proxies & set(STRATEGY_ROW_FIELDS) and proxies & set(ROW_FIELDS)
    assert [row["variant_id"] for row in rows] == ["c-good", "b-bad", "a-new"]
    good, bad, new = rows
    assert (good["status"], good["wins"], good["losses"], good["net_benefit"], good["meets_bar"]) == \
        ("measured", 4, 0, 0.5, True)
    assert (bad["status"], bad["wins"], bad["losses"], bad["meets_bar"]) == ("measured", 0, 4, False)
    assert bad["regressions"] == ["case-1", "case-2", "case-3", "case-4"]
    assert (new["status"], new["cases"], new["net_benefit"]) == ("unmeasured", 0, None)
    assert {row["policy_digest"] for row in rows} == {POLICY.digest}
    json.dumps(rows)                                                    # plain data


def test_ranking_reads_the_ledger_not_the_caller(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    parameters = set(inspect.signature(rank_strategies).parameters)
    assert parameters == {"context", "variants", "ledger", "store", "policy"}
    empty = rank_strategies(CONTEXT, variants, ledger=ChainLedger(), store=store, policy=POLICY)
    assert {row["status"] for row in empty} == {"unmeasured"}           # the same variants, no receipts in this ledger
    assert [row["variant_id"] for row in empty] == ["a-new", "b-bad", "c-good"]


def test_a_strategy_whose_bytes_do_not_resolve_is_unmeasured(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    path = tmp_path / "strategies" / f"{GOOD.content_digest}.json"
    path.write_bytes(path.read_bytes().replace(b"every stated", b"no stated"))
    rows = {row["variant_id"]: row for row in rank_strategies(CONTEXT, variants, ledger=ledger, store=store,
                                                              policy=POLICY)}
    assert rows["c-good"]["status"] == "unmeasured" and rows["b-bad"]["status"] == "measured"


def test_ranking_selects_applies_and_executes_nothing(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    before = (ledger.head(), sorted(p.name for p in (tmp_path / "strategies").iterdir()))
    engine = MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)
    rows = rank_strategies(CONTEXT, [*variants, engine], ledger=ledger, store=store, policy=POLICY)
    assert [row["variant_id"] for row in rows] == ["c-good", "b-bad", "a-new"]              # no engine row
    assert (ledger.head(), sorted(p.name for p in (tmp_path / "strategies").iterdir())) == before
    source = inspect.getsource(rank_strategies)
    assert "_score" not in source and "apply(" not in source and "select(" not in source


def test_the_evidence_plan_is_identical_with_and_without_strategies(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    engines = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)]
    selector = MetacognitiveMethodSelector()
    without = selector.select(CONTEXT, engines, required_methods=["formal"], max_optional=2)
    rank_strategies(CONTEXT, variants, ledger=ledger, store=store, policy=POLICY)
    with_them = selector.select(CONTEXT, [*engines, *variants], required_methods=["formal"], max_optional=2)
    assert json.dumps(with_them.to_dict(), sort_keys=True) == json.dumps(without.to_dict(), sort_keys=True)


def test_a_variant_the_context_cannot_afford_has_no_row(tmp_path: Path) -> None:
    variants, store, ledger = measured(tmp_path)
    dear = heuristic_variant(NEW, id="dear", estimated_cost=11)
    rows = rank_strategies(CONTEXT, [variants[2], dear], ledger=ledger, store=store, policy=POLICY)
    assert [row["variant_id"] for row in rows] == ["c-good"]
    with pytest.raises(ValueError, match="duplicate"):
        rank_strategies(CONTEXT, [variants[2], variants[2]], ledger=ledger, store=store, policy=POLICY)


def test_the_seeds_are_unmeasured_until_a_run_writes_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    rows = rank_seeds(CONTEXT)
    assert [row["variant_id"] for row in rows] == sorted(seed_artefacts())
    assert {row["status"] for row in rows} == {"unmeasured"} and {row["policy_digest"] for row in rows} == {
        DEFAULT_POLICY.digest}
    assert not (tmp_path / "home" / "strategies").exists(), "reading the ranking wrote something"


def test_the_op_is_routed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    entry = routing.load().entries["op:cognitive_bridge/rank_strategies"]
    assert entry.agent_facing and entry.route == "discover" and entry.since == "0.3.0"
    reply: Any = invoke_reply("cognitive_bridge", "rank_strategies", json.dumps({
        "context": {"domain": "arithmetic", "uncertainty": "exact", "claim_kind": "answer", "risk": 1, "budget": 10}}))
    assert reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    assert len(reply["result"]["strategies"]) == 16
    assert {row["status"] for row in reply["result"]["strategies"]} == {"unmeasured"}
    refused = invoke_reply("cognitive_bridge", "rank_strategies", "{}")
    assert refused["ok"] is False and refused["completion_state"] == "blocked-escalated"
