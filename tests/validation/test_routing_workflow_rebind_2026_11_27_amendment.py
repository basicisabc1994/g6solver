# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-27 rebind of the workflow route is an amendment on record: it says why and when, before any arm, pins
what frozen.json pins, keeps what it replaced beside it, rebinds the workflow route alone as it stands, and changes no
task."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_workflow_rebind_2026_11_27_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-27" / "routing_workflow_rebind.json"
REASON = ("the end-of-task card fold (claude/rc030-card) rewrites the workflow tools' own text that the "
          "workflow route binds; no session has run; re-frozen before any arm")
#: The amendment before it: the list this one retired is the one that amendment froze.
EARLIER = HERE / "amendments" / "2026-11-27" / "routing_review_rebind.json"
#: The amendment that followed it (the 2026-11-27 release-skill rebind): it retired what this one froze, so this
#: one's pins and task list are read from there.
#: The 2026-11-27 release-skill rebind (tests/validation/test_routing_release_skill_rebind_amendment.py): it
#: moves the review and every-route digests, so what stood before it is read from its record.
RELEASE = HERE / "amendments" / "2026-11-27" / "routing_release_skill_rebind.json"


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-27" and record["before_any_arm"] is True and record["reason"] == REASON
    assert "lead ruling" in record["ruling"]
    assert record["pins"] == freeze.read(RELEASE)["previous_pins"]
    assert record["previous_pins"] == freeze.read(EARLIER)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]       # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_only_the_workflow_route_is_rebound_as_it_stands_and_no_task_changed():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(RELEASE)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == {**run.route_digests(), "review": freeze.read(RELEASE)["routes"]["review"]["before"], run.EVERY_ROUTE: freeze.read(RELEASE)["routes"][run.EVERY_ROUTE]["before"]}
    assert set(record["routes"]) == {"workflow"}
    assert record["routes"]["workflow"] == {"before": before["routes"]["workflow"], "after": now["routes"]["workflow"]}
    assert record["routes"]["workflow"]["before"] != record["routes"]["workflow"]["after"]
    assert {route: found for route, found in now["routes"].items() if route != "workflow"} == \
        {route: found for route, found in before["routes"].items() if route != "workflow"}
    assert now["tasks"] == before["tasks"]
