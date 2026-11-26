# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-27 rebind of the review route is an amendment on record: it says why and when, before any arm, pins
what frozen.json pins, keeps what it replaced beside it, rebinds the review route alone as it stands, and changes no
task."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_review_rebind_2026_11_27_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-27" / "routing_review_rebind.json"
REASON = ("the must-close fold (claude/rc030-mustclose) rewrites the review route's detail "
          "(references/lifecycle.md); no session has run; re-frozen before any arm")
#: The amendment before it: the list this one retired is the one that amendment froze.
EARLIER = HERE / "amendments" / "2026-11-26" / "routing_workflow_rebind.json"


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-27" and record["before_any_arm"] is True and record["reason"] == REASON
    assert "lead ruling" in record["ruling"]
    assert record["pins"] == freeze.read(run.PINS)
    assert record["previous_pins"] == freeze.read(EARLIER)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]       # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_only_the_review_route_is_rebound_as_it_stands_and_no_task_changed():
    record = freeze.read(RECORD)
    now = run.load_tasks()
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == run.route_digests()
    assert set(record["routes"]) == {"review"}
    assert record["routes"]["review"] == {"before": before["routes"]["review"], "after": now["routes"]["review"]}
    assert record["routes"]["review"]["before"] != record["routes"]["review"]["after"]
    assert {route: found for route, found in now["routes"].items() if route != "review"} == \
        {route: found for route, found in before["routes"].items() if route != "review"}
    assert now["tasks"] == before["tasks"]
