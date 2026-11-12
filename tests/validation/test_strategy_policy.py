# SPDX-License-Identifier: Apache-2.0
"""The seed measurement's frozen policy and its runner (0.3 design 14.2, "measure worth"), offline with a stand-in
agent: the policy file is the frozen one, a run under another policy is refused, and incomplete arms stay visible."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from g6.core.ledger import ChainLedger
from g6.learning.paired_eval import receipts
from g6.learning.schema import AgentReply, AgentTrace
from g6.learning.strategy import StrategyError, seed_artefacts
from g6.learning.worth import DEFAULT_POLICY, POLICY, WorthPolicy

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "strategies"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run = _load("g6_validation_strategies_run", HERE / "run.py")


def cohort(tmp_path: Path, n: int = 3, split: str = "heldout") -> Path:
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps({"cohort_id": "synthetic-cohort", "split": split, "instances": [
        {"instance_id": f"case-{i}", "input": f"What is {i} + {i}?", "expected_output": str(2 * i),
         "metadata": {"constraints": True}} for i in range(1, n + 1)]}), encoding="utf-8")
    return path


def agent(prompt: str) -> AgentReply:
    n = int(prompt.rsplit("What is ", 1)[1].split(" ")[0])
    return AgentReply(str(2 * n) if "Propagate stated constraints" in prompt else "?", AgentTrace(cost_usd=0.01))


def test_the_policy_file_is_frozen() -> None:
    policy = run.load_policy()
    assert isinstance(policy, WorthPolicy) and policy == DEFAULT_POLICY
    assert asdict(policy) == {"min_complete_cases": 30, "min_discordant": 5, "alpha": 0.05 / 16, "max_regressions": 0,
                              "max_cost_ratio": 1.25, "max_incomplete_share": 0.2, "format": "g6.strategy_worth/1"}
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    pinned = re.search(r"frozen at policy digest `([0-9a-f]{64})`", readme)
    assert pinned and pinned.group(1) == policy.digest
    assert "evidence_pending" in readme


def test_the_run_measures_every_seed_and_keeps_them_apart(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = run.run(cohort(tmp_path), out, replay_id="r1", agent_id="stand-in/1", agent=agent)
    assert set(report) == set(seed_artefacts()) and len(report) == 16
    helped = report["constraint_propagation"]
    assert (helped["cases"], helped["complete_cases"], helped["wins"], helped["losses"]) == (3, 3, 3, 0)
    assert helped["status"] == "insufficient" and not helped["meets_bar"], "three cases cannot meet a bar of thirty"
    assert all(row["wins"] == 0 for name, row in report.items() if name != "constraint_propagation")
    kept = receipts(ChainLedger(out / "receipts.jsonl"))
    assert len(kept) == 16 * 3 and {r.cohort_id for r in kept} == {"synthetic-cohort"}
    assert sum(r.order != "baseline_shared" for r in kept) == 3, "the baseline arm ran once per case in the replay"
    assert ChainLedger(out / "receipts.jsonl").get("strategy_policy", "synthetic-cohort")["policy_digest"] == \
        DEFAULT_POLICY.digest
    written = json.loads(next(out.glob("worth-*-r1.json")).read_text(encoding="utf-8"))
    assert written["policy_digest"] == DEFAULT_POLICY.digest
    assert written["seeds"] == json.loads(json.dumps(report))


def test_a_seed_whose_trigger_does_not_match_is_recorded_and_credits_nothing(tmp_path: Path) -> None:
    report = run.run(cohort(tmp_path), tmp_path / "out", replay_id="r1", agent_id="stand-in/1", agent=agent)
    unmatched = report["exploit_symmetry"]              # its trigger reads keys this cohort's cases do not carry
    assert (unmatched["cases"], unmatched["complete_cases"], unmatched["status"]) == (3, 0, "insufficient")


def test_run_refuses_a_policy_file_that_is_not_the_frozen_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``policy.json`` edited in place: refused before anything is bound or run, on a cohort already begun and on a
    fresh one alike."""
    out = tmp_path / "out"
    run.run(cohort(tmp_path, 1), out, replay_id="r1", agent_id="stand-in/1", agent=agent)
    looser = tmp_path / "policy.json"
    looser.write_text(json.dumps({**asdict(DEFAULT_POLICY), "min_complete_cases": 1, "alpha": 0.5}), encoding="utf-8")
    before = (out / "receipts.jsonl").read_bytes()
    monkeypatch.setattr(run, "POLICY", looser)
    with pytest.raises(StrategyError) as caught:
        run.run(cohort(tmp_path, 1), out, replay_id="r2", agent_id="stand-in/1", agent=agent)
    assert caught.value.code == POLICY and (out / "receipts.jsonl").read_bytes() == before
    with pytest.raises(StrategyError):
        run.run(cohort(tmp_path, 1), tmp_path / "fresh", replay_id="r1", agent_id="stand-in/1", agent=agent)
    assert not (tmp_path / "fresh" / "receipts.jsonl").exists()


def test_refused_and_interrupted_arms_are_published(tmp_path: Path) -> None:
    def fails(prompt: str) -> str:
        raise RuntimeError("no model answered")

    report = run.run(cohort(tmp_path, 2), tmp_path / "out", replay_id="r1", agent_id="stand-in/1", agent=fails)
    assert all(row["incomplete_pairs"] == 2 and row["complete_cases"] == 0 and row["cases"] == 2
               for row in report.values())


def test_a_cohort_file_outside_its_form_is_refused(tmp_path: Path) -> None:
    for bad in ({"cohort_id": "c", "split": "training", "instances": [{"instance_id": "a", "input": "x"}]},
                {"cohort_id": "c", "split": "heldout", "instances": []},
                {"cohort_id": "c", "split": "heldout", "instances": [{"instance_id": "a", "input": "x"}] * 2},
                {"split": "heldout", "instances": [{"instance_id": "a", "input": "x"}]}):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            run.load_cohort(path)


def test_the_runner_takes_no_bar_from_its_caller(tmp_path: Path) -> None:
    """A fresh cohort, with a looser policy file handed to the runner: three cases must not meet any bar."""
    looser = tmp_path / "policy.json"
    looser.write_text(json.dumps({**asdict(DEFAULT_POLICY), "min_complete_cases": 1, "min_discordant": 1,
                                  "alpha": 0.5}), encoding="utf-8")
    try:
        report = run.run(cohort(tmp_path), tmp_path / "out", replay_id="r1", agent_id="stand-in/1", agent=agent,
                         policy_path=looser)
    except (TypeError, StrategyError):
        return                                                    # no such door, or a refusal: both are correct
    met = sorted(name for name, row in report.items() if row["meets_bar"])
    assert not met, f"three cases were measured under a policy file the caller named, and {met} meet the bar"
