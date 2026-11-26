# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-21 re-freeze of the development routing tasks is an amendment on record: it says why and when, before
any arm, pins what frozen.json pins, keeps what it replaced beside it, binds every route as it stands, and changes no
task."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_rebind_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-21" / "routing_rebind.json"
REASON = "tool and route text changed by the 0.3.0 wiring merges; re-frozen before any F0 arm runs"
#: The amendment that followed it (the select route's development tasks): it retired what this one froze, so this
#: one's pins and task list are read from there (tests/validation/test_routing_select_development_amendment.py).
LATER = HERE / "amendments" / "2026-11-21" / "select_development_tasks.json"
#: The 2026-11-26 rebind of the review route alone (tests/validation/test_routing_review_rebind_amendment.py).
REVIEW = HERE / "amendments" / "2026-11-26" / "routing_review_rebind.json"
#: The 2026-11-26 learn rebind (tests/validation/test_routing_learn_rebind_amendment.py): it adds the learn
#: route and moves the every-route digest, so what stood before it is read from its record.
LEARN = HERE / "amendments" / "2026-11-26" / "routing_learn_rebind.json"
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


def _as_they_stood() -> dict:
    """Every route's digest as it stood before the 2026-11-26 rebinds: the review route's before its own,
    and no learn route, with the every-route digest the learn rebind moved."""
    return {**_before_learn(), "review": freeze.read(REVIEW)["routes"]["review"]["before"]}


def _before_learn() -> dict:
    """Every route's digest as it stands, as it stood before the 2026-11-26 learn rebind: no learn route, and
    the every-route digest it moved."""
    learn = freeze.read(LEARN)["routes"]
    stood = {route: found for route, found in _before_question().items() if route != "learn"}
    return {**stood, run.EVERY_ROUTE: learn[run.EVERY_ROUTE]["before"]}


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-21" and record["before_any_arm"] is True and record["reason"] == REASON
    assert record["pins"] == freeze.read(LATER)["previous_pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]       # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_every_route_is_bound_as_it_stands_and_no_task_changed():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(LATER)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert now["routes"] == _as_they_stood() == {route: row["after"] for route, row in record["routes"].items()}
    assert {route: row["before"] for route, row in record["routes"].items() if row["before"]} == before["routes"]
    assert now["tasks"] == before["tasks"]
    learn = freeze.read(LEARN)["routes"]
    assert run.load_tasks()["routes"] == {**now["routes"], "review": freeze.read(REVIEW)["routes"]["review"]["after"],
                                          **{route: row["after"] for route, row in learn.items()},
                                          "question": freeze.read(QUESTION)["routes"]["question"]["after"],
                                          "workflow": freeze.read(WORKFLOW)["routes"]["workflow"]["after"],
                                          "review": freeze.read(REVIEW27)["routes"]["review"]["after"]}
