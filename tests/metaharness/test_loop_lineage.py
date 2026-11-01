# SPDX-License-Identifier: Apache-2.0
"""What a cycle keeps of what a proposer found, and the harness's numbered generations, on FakeLLM only.

The generations are the loop's own numbers, kept in the chain of adoptions it already had: the lineage shows losses as
well as gains, and its figures are measured on the splits they name."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core.ledger import LedgerCorrupt
from g6.core.llm import FakeLLM
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.shadow import lineage
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)
FOUND = {"structure": "every failure drops a unit", "resource": "the task states its units",
         "obstruction": "cases with one unit do not show it", "composed_from": []}


def _finder(shown: list[dict], *, said=FOUND, change=None):
    """A proposer that reads the failures, says what it found, and offers a change; the reflecting proposer gets plain
    changes, since it has no record to keep."""
    change = {"prompt": domains.WORKING} if change is None else change

    def reply(system: str, user: str) -> str:
        if "development failures" not in system:
            return json.dumps({"prompt": domains.WORKING})
        shown.append(json.loads(user))
        return json.dumps({**change, **said})

    return FakeLLM(reply)


def _enrolled(tmp_path, domain=None, proposer=None):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=proposer, now=lambda: NOW, clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


# The four fields -----------------------------------------------------------------------------------------------------

def test_a_cycle_keeps_what_the_proposer_found(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([]))
    got = loop.tick()
    row = loop.log.cycles()[-1]
    assert row["structure"] == FOUND["structure"] and row["resource"] == FOUND["resource"]
    assert row["obstruction"] == FOUND["obstruction"] and row["composed_from"] == []
    assert got["structure"] == FOUND["structure"]


def test_when_a_proposer_supplies_none_the_fields_stay_empty(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    row = loop.log.cycles()[-1]
    assert (row["structure"], row["resource"], row["obstruction"], row["composed_from"]) == ("", "", "", [])


def test_the_next_tick_can_read_them_and_build_on_them(tmp_path):
    shown: list[dict] = []
    domain = domains.WordProblems(tmp_path, weak=0.6)
    tries = iter(range(9))

    def reply(system: str, user: str) -> str:
        if "development failures" not in system:
            return json.dumps({"prompt": domains.WORKING})
        shown.append(json.loads(user))
        builds = [c["tick"] for c in shown[-1].get("earlier_cycles", ())]
        return json.dumps({"prompt": f"{domains.WORKING} Variant {next(tries)}.", **FOUND, "composed_from": builds})

    loop, _ = _enrolled(tmp_path, domain, FakeLLM(reply))
    loop.tick()
    assert "earlier_cycles" not in shown[0]
    loop.tick()
    assert shown[1]["earlier_cycles"] == [{"tick": 1, **FOUND}]
    assert loop.log.cycles()[-1]["composed_from"] == [1]


def test_a_proposer_can_build_only_on_a_cycle_it_was_shown(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([], said={**FOUND, "composed_from": [7, 99]}))
    loop.tick()
    assert loop.log.cycles()[-1]["composed_from"] == []


def test_earlier_cycles_show_proposers_nothing_the_gate_read(tmp_path):
    shown: list[dict] = []
    domain = domains.WordProblems(tmp_path, weak=0.6)
    tries = iter(range(9))

    def reply(system: str, user: str) -> str:
        if "development failures" not in system:
            return json.dumps({"prompt": domains.WORKING})
        shown.append(json.loads(user))
        return json.dumps({"prompt": f"{domains.WORKING} Variant {next(tries)}.", **FOUND})

    loop, _ = _enrolled(tmp_path, domain, FakeLLM(reply))
    loop.tick()
    loop.tick()
    assert set(shown[1]["earlier_cycles"][0]) == {"tick", *FOUND}


def test_what_a_proposer_found_never_reaches_a_profile(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([]))
    loop.tick()
    head = loop.mh._head()[1].model_dump(mode="json")
    assert FOUND["structure"] not in json.dumps(head) and domains.WORKING in head["prompt"]


def test_a_proposer_with_no_change_can_still_say_what_it_found(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([], change={}))
    loop.tick()
    assert loop.log.cycles()[-1]["structure"] == FOUND["structure"]


def test_a_finding_too_long_is_dropped_and_the_change_beside_it_stands(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([], said={"structure": "x" * 5000}))
    assert loop.tick()["outcome"] == "adopted"
    assert loop.log.cycles()[-1]["structure"] == ""


def test_outside_the_loop_the_strategy_takes_what_it_always_took(tmp_path):
    strategy = FailureTraceStrategy(FakeLLM(default=json.dumps({"prompt": "x", "structure": "y"})))
    assert strategy.earlier is None and strategy.findings == {}
    # test_merge_failure_strategy.py holds the reply it refuses; this holds the field that keeps it so


# The generations -----------------------------------------------------------------------------------------------------

def _worse_after_it_was_adopted(tmp_path, proposer=None):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    loop, _ = _enrolled(tmp_path, domain, proposer)
    assert loop.tick()["outcome"] == "adopted"
    domain.tutor.collapsed = True
    assert loop.tick()["outcome"] == "rolled_back"
    return loop, domain


def test_no_adoption_yet_is_generation_nought(tmp_path):
    loop, _ = _enrolled(tmp_path)
    assert lineage(loop.root / "workspace") == {"current": 0, "recent": []}
    assert lineage(tmp_path / "no-such-folder") == {"current": 0, "recent": []}


def test_an_adoption_is_a_numbered_generation_with_its_parent_and_its_measurement(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=_finder([]))
    loop.tick()
    found = lineage(loop.root / "workspace")
    assert found["current"] == 1
    [one] = found["recent"]
    assert (one["generation"], one["parent"], one["kind"]) == (1, 0, "adopt")
    assert one["profile_digest"] == loop.mh._head()[1].digest()
    assert "failure_trace" in one["change"] or "changed" in one["change"]
    measured = one["measured"]
    assert measured["split"] == "held_out" and measured["cases"] == len(loop.cut.held_out)
    assert 0 <= measured["before"] < measured["after"] <= 1
    assert one["structure"] == FOUND["structure"] and one["composed_from"] == []


def test_a_rollback_is_a_generation_too_so_the_lineage_shows_the_loss(tmp_path):
    loop, _ = _worse_after_it_was_adopted(tmp_path)
    found = lineage(loop.root / "workspace")
    assert found["current"] == 2
    adopted, undone = found["recent"]
    assert (undone["generation"], undone["parent"], undone["kind"]) == (2, 1, "rollback")
    assert undone["restores"] == 0 and undone["undoes"] == 1
    assert undone["profile_digest"] == loop.mh._head()[1].digest() != adopted["profile_digest"]
    assert undone["measured"]["before"] == adopted["measured"]["after"]
    assert undone["measured"]["after"] == adopted["measured"]["before"]
    assert undone["measured"]["monitor"]["pairs"] >= 18 and undone["change"]


def test_the_next_adoption_after_a_rollback_names_the_rollback_as_its_parent(tmp_path):
    tries = iter(range(9))

    def always_something_new(system: str, user: str) -> str:
        return json.dumps({"prompt": f"{domains.WORKING} Variant {next(tries)}."})

    loop, domain = _worse_after_it_was_adopted(tmp_path, FakeLLM(always_something_new))
    domain.tutor.collapsed = False
    assert loop.tick()["outcome"] == "adopted"
    three = lineage(loop.root / "workspace")["recent"][-1]
    assert (three["generation"], three["parent"]) == (3, 2)


def test_the_lineage_stays_in_the_chain_of_adoptions_with_no_second_ledger(tmp_path):
    loop, _ = _enrolled(tmp_path)
    before = {p.name for p in (loop.root / "workspace").iterdir()}
    loop.tick()
    added = {p.name for p in (loop.root / "workspace").iterdir()} - before
    assert added <= {"adoptions.jsonl", "ledger.jsonl"}            # the two chains the shadow always kept
    path = loop.root / "workspace" / "adoptions.jsonl"
    text = path.read_text(encoding="utf-8")
    assert '"measured"' in text
    path.write_text(text.replace('"held_out"', '"sealed"'), encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        lineage(loop.root / "workspace")


def test_recent_keeps_the_newest_generations_only(tmp_path):
    loop, _ = _worse_after_it_was_adopted(tmp_path)
    found = lineage(loop.root / "workspace", last=1)
    assert found["current"] == 2 and [g["generation"] for g in found["recent"]] == [2]


def test_a_row_written_before_generations_had_their_fields_still_reads(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    assert lineage(loop.root / "workspace")["recent"][0]["composed_from"] == []


def test_the_records_say_measured_and_nothing_stronger(tmp_path):
    loop, _ = _worse_after_it_was_adopted(tmp_path)
    said = json.dumps(lineage(loop.root / "workspace")).lower()
    assert "measured" in said and "proved" not in said and "verified" not in said


def test_the_generations_are_the_loops_own_numbers(tmp_path):
    version = g6.__version__
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    assert g6.__version__ == version and "version" not in lineage(loop.root / "workspace")["recent"][0]
    assert loop.status()["generation"] == 1
