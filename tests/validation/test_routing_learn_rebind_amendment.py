# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-26 rebind for the learn route is an amendment on record: it says why and when, before any arm, pins
what frozen.json pins, keeps what it replaced beside it, binds the learn route merge 5 adds and the every-route digest
it moves, as they stand, and changes no other route, no task and no bar."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_learn_rebind_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-26" / "routing_learn_rebind.json"
REASON = ("merge 5 (claude/distill-reach-fu-3) adds the learn route to the using-g6 skill; no session has run; "
          "re-frozen before any arm")
#: The amendment before it: the list this one retired is the one that amendment froze.
EARLIER = HERE / "amendments" / "2026-11-26" / "routing_review_rebind.json"
REBOUND = {"learn", run.EVERY_ROUTE}
#: The amendment that followed it (the learn route's development tasks): it retired what this one froze, so this
#: one's pins and task list are read from there (tests/validation/test_routing_learn_development_amendment.py).
LATER = HERE / "amendments" / "2026-11-26" / "routing_learn_development_tasks.json"


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-26" and record["before_any_arm"] is True and record["reason"] == REASON
    assert "lead ruling" in record["ruling"]
    assert record["pins"] == freeze.read(LATER)["previous_pins"]
    assert record["previous_pins"] == freeze.read(EARLIER)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]       # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_only_the_learn_and_every_route_digests_are_bound_as_they_stand_and_no_task_changed():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(LATER)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == run.route_digests()
    assert set(record["routes"]) == REBOUND
    assert record["routes"]["learn"] == {"before": None, "after": now["routes"]["learn"]} and "learn" not in \
        before["routes"]
    every = record["routes"][run.EVERY_ROUTE]
    assert every == {"before": before["routes"][run.EVERY_ROUTE], "after": now["routes"][run.EVERY_ROUTE]}
    assert every["before"] != every["after"]
    assert {route: found for route, found in now["routes"].items() if route not in REBOUND} == \
        {route: found for route, found in before["routes"].items() if route not in REBOUND}
    assert now["routes"]["review"] == freeze.read(EARLIER)["routes"]["review"]["after"]   # the review rebind holds
    assert now["tasks"] == before["tasks"]
