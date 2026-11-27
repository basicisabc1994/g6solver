# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-27 rebind for the 0.3.0 using-g6 update is an amendment on record: it says why and when, before any
arm, pins what frozen.json pins, keeps what it replaced beside it, rebinds the review and every-route digests the
update moves, as they stand, and changes no other route, no task and no bar."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_release_skill_rebind_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-27" / "routing_release_skill_rebind.json"
REASON = ("the 0.3.0 using-g6 update (the Stop hook's default review, the change map as a lead, the operating-system "
          "boundary and the Windows git grants) rewrites the review route's text and the root skill outside the routes; "
          "no session has run; re-frozen before any arm")
#: The amendment before it: the list this one retired is the one that amendment froze.
EARLIER = HERE / "amendments" / "2026-11-27" / "routing_workflow_rebind.json"
REBOUND = {"review", run.EVERY_ROUTE}


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


def test_only_the_review_and_every_route_digests_are_rebound_as_they_stand_and_no_task_changed():
    record = freeze.read(RECORD)
    now = run.load_tasks()
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == run.route_digests()
    assert set(record["routes"]) == REBOUND
    for route in REBOUND:
        assert record["routes"][route] == {"before": before["routes"][route], "after": now["routes"][route]}
        assert record["routes"][route]["before"] != record["routes"][route]["after"]
    assert {route: found for route, found in now["routes"].items() if route not in REBOUND} == \
        {route: found for route, found in before["routes"].items() if route not in REBOUND}
    assert now["tasks"] == before["tasks"]
