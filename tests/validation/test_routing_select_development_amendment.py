# SPDX-License-Identifier: Apache-2.0
"""The select route's development tasks (2026-11-21) are an amendment on record: written on development fixtures only,
before any arm, pinning what frozen.json pins, keeping what it replaced beside it, and changing no route, bar or earlier
task. The 2026-11-17 drafts, on moment fixtures, stay unbound."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_select_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-21" / "select_development_tasks.json"
REBIND = HERE / "amendments" / "2026-11-21" / "routing_rebind.json"
ADDED = ["dev.select.missing_tool.1", "dev.select.negative.1", "dev.select.positive.1"]
#: The amendment that followed it (the 2026-11-26 rebind of the review route alone): it retired what this one froze,
#: so this one's pins and task list are read from there (tests/validation/test_routing_review_rebind_amendment.py).
LATER = HERE / "amendments" / "2026-11-26" / "routing_review_rebind.json"
#: The 2026-11-26 learn rebind (tests/validation/test_routing_learn_rebind_amendment.py): it adds the learn
#: route and moves the every-route digest, so what stood before it is read from its record.
LEARN = HERE / "amendments" / "2026-11-26" / "routing_learn_rebind.json"
#: The 2026-11-26 question rebind (tests/validation/test_routing_question_rebind_amendment.py): it moves the
#: question route's digest, so what stood before it is read from its record.
QUESTION = HERE / "amendments" / "2026-11-26" / "routing_question_rebind.json"
#: The 2026-11-26 workflow rebind (tests/validation/test_routing_workflow_rebind_amendment.py): it moves the
#: workflow route's digest, so what stood before it is read from its record.
WORKFLOW = HERE / "amendments" / "2026-11-26" / "routing_workflow_rebind.json"


def _before_question() -> dict:
    """Every route's digest as it stands, the question and workflow routes' as they stood before their 2026-11-26
    rebinds."""
    return {**run.route_digests(), "question": freeze.read(QUESTION)["routes"]["question"]["before"],
            "workflow": freeze.read(WORKFLOW)["routes"]["workflow"]["before"]}


def _as_they_stood() -> dict:
    """Every route's digest as it stood before the 2026-11-26 rebinds: the review route's before its own,
    and no learn route, with the every-route digest the learn rebind moved."""
    return {**_before_learn(), "review": freeze.read(LATER)["routes"]["review"]["before"]}


def _before_learn() -> dict:
    """Every route's digest as it stands, as it stood before the 2026-11-26 learn rebind: no learn route, and
    the every-route digest it moved."""
    learn = freeze.read(LEARN)["routes"]
    stood = {route: found for route, found in _before_question().items() if route != "learn"}
    return {**stood, run.EVERY_ROUTE: learn[run.EVERY_ROUTE]["before"]}


def test_it_is_recorded_before_any_arm_and_follows_the_rebind():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-21" and record["before_any_arm"] is True
    assert record["pins"] == freeze.read(LATER)["previous_pins"]
    assert record["previous_pins"] == freeze.read(REBIND)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]        # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and record["reason"] in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert record["reason"] in tasks["reason"]


def test_it_adds_select_tasks_on_development_fixtures_and_changes_nothing_else():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(LATER)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert now["routes"] == before["routes"] == _as_they_stood()
    added = [t for t in now["tasks"] if t["id"] not in {b["id"] for b in before["tasks"]}]
    assert [t["id"] for t in added] == record["added"] == ADDED
    assert [t for t in now["tasks"] if t["id"] not in ADDED] == before["tasks"]
    assert {t["route"] for t in added} == {"select"} and {t["cohort"] for t in added} == {"development"}
    development = set(run.development_fixtures())
    assert {t["fixture"] for t in added} == set(record["fixtures"]) <= {t["fixture"] for t in before["tasks"]}
    assert set(record["fixtures"]) <= development
    drafts = freeze.read(HERE / "amendments" / "2026-11-17" / "select_tasks.unbound.json")
    assert drafts["binding_status"] == "unbound" and drafts["routes"] == {"select": None}
    moment = {t["fixture"] for t in run.load_moment()["tasks"]}
    assert not moment & {t["fixture"] for t in added}
    for task in added:
        assert "g6" not in task["prompt"].lower() and "select" not in task["prompt"].lower(), task["id"]
