# SPDX-License-Identifier: Apache-2.0
"""The 2026-11-26 rebind of the question route is an amendment on record: it says why and when, before any arm, pins
what frozen.json pins, keeps what it replaced beside it, rebinds the question route alone as it stands, and changes no
task."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "routing"
spec = importlib.util.spec_from_file_location("routing_question_rebind_amendment_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = run
spec.loader.exec_module(run)
freeze = run.freeze
RECORD = HERE / "amendments" / "2026-11-26" / "routing_question_rebind.json"
REASON = ("the census caption fix (claude/census-caption-fix) rewrites the question route's detail "
          "(references/question.md); no session has run; re-frozen before any arm")
#: The amendment before it: the list this one retired is the one that amendment froze.
EARLIER = HERE / "amendments" / "2026-11-26" / "routing_learn_development_tasks.json"
#: The amendment that followed it (the workflow rebind): it retired what this one froze, so this one's pins and
#: task list are read from there (tests/validation/test_routing_workflow_rebind_amendment.py).
#: The 2026-11-26 workflow rebind (tests/validation/test_routing_workflow_rebind_amendment.py): it moves the
#: workflow route's digest, so what stood before it is read from its record.
WORKFLOW = HERE / "amendments" / "2026-11-26" / "routing_workflow_rebind.json"
#: The 2026-11-27 review rebind (tests/validation/test_routing_review_rebind_2026_11_27_amendment.py): it moves
#: the review route's digest again, so what stood before it is read from its record.
REVIEW27 = HERE / "amendments" / "2026-11-27" / "routing_review_rebind.json"
#: The 2026-11-27 release-skill rebind (tests/validation/test_routing_release_skill_rebind_amendment.py): it
#: moves the review and every-route digests, so what stood before it is read from its record.
RELEASE = HERE / "amendments" / "2026-11-27" / "routing_release_skill_rebind.json"


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-26" and record["before_any_arm"] is True and record["reason"] == REASON
    assert "lead ruling" in record["ruling"]
    assert record["pins"] == freeze.read(WORKFLOW)["previous_pins"]
    assert record["previous_pins"] == freeze.read(EARLIER)["pins"]
    assert record["pins"]["bars_digest"] == record["previous_pins"]["bars_digest"]       # no bar changes


def test_what_it_replaced_is_kept_beside_it_with_the_reason():
    record = freeze.read(RECORD)
    tasks = freeze.read(HERE / record["retired"]["tasks.json"])
    pins = freeze.read(HERE / record["retired"]["frozen.json"])
    assert pins["retired"] == record["previous_pins"] and REASON in pins["reason"]
    assert tasks["digest"] == record["previous_pins"]["tasks_digest"] == freeze.digest(tasks["retired"])
    assert REASON in tasks["reason"]


def test_only_the_question_route_is_rebound_as_it_stands_and_no_task_changed():
    record = freeze.read(RECORD)
    now = freeze.read(HERE / freeze.read(WORKFLOW)["retired"]["tasks.json"])["retired"]     # the list as it froze it
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert freeze.digest(now) == record["pins"]["tasks_digest"]
    assert now["routes"] == {**run.route_digests(), "workflow": freeze.read(WORKFLOW)["routes"]["workflow"]["before"],
                             "review": freeze.read(REVIEW27)["routes"]["review"]["before"],
                             run.EVERY_ROUTE: freeze.read(RELEASE)["routes"][run.EVERY_ROUTE]["before"]}
    assert set(record["routes"]) == {"question"}
    assert record["routes"]["question"] == {"before": before["routes"]["question"], "after": now["routes"]["question"]}
    assert record["routes"]["question"]["before"] != record["routes"]["question"]["after"]
    assert {route: found for route, found in now["routes"].items() if route != "question"} == \
        {route: found for route, found in before["routes"].items() if route != "question"}
    assert now["tasks"] == before["tasks"]
