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


def test_the_rebind_is_recorded_before_any_arm_with_its_reason():
    record = freeze.read(RECORD)
    assert record["date"] == "2026-11-21" and record["before_any_arm"] is True and record["reason"] == REASON
    assert record["pins"] == freeze.read(run.PINS)
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
    now = freeze.read(run.TASKS)
    before = freeze.read(HERE / record["retired"]["tasks.json"])["retired"]
    assert now["routes"] == run.route_digests() == {route: row["after"] for route, row in record["routes"].items()}
    assert {route: row["before"] for route, row in record["routes"].items() if row["before"]} == before["routes"]
    assert now["tasks"] == before["tasks"]
    assert run.load_tasks() == now
