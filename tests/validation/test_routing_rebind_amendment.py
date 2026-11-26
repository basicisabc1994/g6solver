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


def _as_they_stood() -> dict:
    """Every route's digest as it stands, the review route's as it stood before its 2026-11-26 rebind."""
    return {**run.route_digests(), "review": freeze.read(REVIEW)["routes"]["review"]["before"]}


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
    assert run.load_tasks()["routes"] == {**now["routes"], "review": freeze.read(REVIEW)["routes"]["review"]["after"]}
