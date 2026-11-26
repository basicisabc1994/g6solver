# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-26 learn route's development tasks are an amendment on record: written before any arm, after the learn
rebind, on a development fixture, one positive, one negative and one missing-tool task, with no G6 or route cue in a
prompt; every earlier task, route digest and bar is unchanged."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_learn_development_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-26" / "routing_learn_development_tasks.json"
REBIND = HERE / "amendments" / "2026-11-26" / "routing_learn_rebind.json"
REASON = "merge 5 adds the learn route; no session has run"
ADDED = ["dev.learn.missing_tool.1", "dev.learn.negative.1", "dev.learn.positive.1"]
#: The 2026-11-26 question rebind (tests/validation/test_routing_question_rebind_amendment.py): it moves the
#: question route's digest, so what stood before it is read from its record.
QUESTION = HERE / "amendments" / "2026-11-26" / "routing_question_rebind.json"
#: The 2026-11-26 workflow rebind (tests/validation/test_routing_workflow_rebind_amendment.py): it moves the
#: workflow route's digest, so what stood before it is read from its record.
WORKFLOW = HERE / "amendments" / "2026-11-26" / "routing_workflow_rebind.json"
#: The 2026-11-27 review rebind (tests/validation/test_routing_review_rebind_2026_11_27_amendment.py): it moves
#: the review route's digest again, so what stood before it is read from its record.
REVIEW27 = HERE / "amendments" / "2026-11-27" / "routing_review_rebind.json"


def _before_question() -> dict:
    """Every route's digest as it stands, the question and workflow routes' as they stood before their 2026-11-26
    rebinds."""
    return {**run.route_digests(), "question": freeze.read(QUESTION)["routes"]["question"]["before"],
            "workflow": freeze.read(WORKFLOW)["routes"]["workflow"]["before"],
            "review": freeze.read(REVIEW27)["routes"]["review"]["before"]}


def test_it_is_recorded_before_any_arm_and_follows_the_learn_rebind():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-26" and record["before_any_arm"] is True and record["reason"] == REASON
    assert "lead ruling" in record["ruling"]
    assert record["pins"] == freeze.read(QUESTION)["previous_pins"]
    assert record["previous_pins"] == freeze.read(REBIND)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]        # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_it_adds_learn_tasks_on_a_development_fixture_and_changes_nothing_else():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(QUESTION)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == before["routes"] == _before_question()
    added = [t for t in now["tasks"] if t["id"] not in {b["id"] for b in before["tasks"]}]
    assert sorted(t["id"] for t in added) == record["added"] == ADDED
    assert [t for t in now["tasks"] if t["id"] not in ADDED] == before["tasks"]
    assert {t["route"] for t in added} == {"learn"} and {t["cohort"] for t in added} == {"development"}
    assert {t["polarity"] for t in added} == {"positive", "negative", "missing_tool"}
    assert {t["fixture"] for t in added} == set(record["fixtures"]) <= set(run.development_fixtures())
    moment = {t["fixture"] for t in run.load_moment()["tasks"]}
    assert not moment & {t["fixture"] for t in added}
    for task in added:
        assert "g6" not in task["prompt"].lower() and "learn" not in task["prompt"].lower(), task["id"]
