# SPDX-License-Identifier: Apache-2.0
"""The routing trials' frozen task list and gates (0.3 design 14.1): every route of the manifest has its tasks, the
tasks bind the routes as they stand, the bars are the adopted ones, and the two cohorts stay apart."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from g6.mcp import registrations
from g6.skills import routing

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
freeze = _load("g6_validation_freeze", ROOT / "validation" / "freeze.py")
TASKS = json.loads(run.TASKS.read_text(encoding="utf-8"))
GATES = json.loads(run.GATES.read_text(encoding="utf-8"))
MANIFEST = routing.load()
RECORDED = registrations.recorded()


def _of(cohort: str, route: str, polarity: str) -> list[dict[str, Any]]:
    return [t for t in TASKS["tasks"] if (t["cohort"], t["route"], t["polarity"]) == (cohort, route, polarity)]


def test_every_route_has_a_positive_and_a_negative_task_in_each_cohort() -> None:
    """Routes are read from the manifest, so a route added later without its tasks fails here. The development cohort
    is in ``tasks.json``; the final cohort is written by someone other than a slice's builder after the last route of
    the release has merged, so until ``final_tasks.json`` exists only its shape is fixed (in ``gates.json``)."""
    score.validate_tasks(TASKS)
    assert set(TASKS["routes"]) == set(MANIFEST.routes) | {run.EVERY_ROUTE}      # and what every session reads
    for route in MANIFEST.routes:
        assert _of("development", route, "positive") and _of("development", route, "negative"), route
    if run.FINAL_TASKS.is_file():
        final = score.validate_tasks(json.loads(run.FINAL_TASKS.read_text(encoding="utf-8")))
        shape = GATES["final_cohort_shape"]
        for route in MANIFEST.routes:
            count = {p: sum((t["route"], t["polarity"]) == (route, p) for t in final["tasks"])
                     for p in score.POLARITIES}
            assert count == {"positive": shape["positive_per_route"], "negative": shape["negative_per_route"],
                             "missing_tool": shape["missing_tool_per_route"]}, route
    else:
        assert GATES["final_tasks_digest"] is None


def test_every_agent_facing_route_has_a_missing_tool_task() -> None:
    for route in MANIFEST.routes:
        found = _of("development", route, "missing_tool")
        assert found, f"route {route} has no task that withholds one of its tools"
    known = {tool for tools in RECORDED.values() for tool in tools}
    for task in TASKS["tasks"]:
        assert set(task.get("withhold", ())) <= known, task["id"]


def test_tasks_bind_the_manifest_and_gates_bind_the_tasks() -> None:
    assert TASKS["routes"] == {route: digest for route, digest in run.route_digests().items()
                               if route in TASKS["routes"]}
    assert run.load_tasks() == TASKS
    # the gate file binds the final cohort's task list; nothing is bound, and the final cohort cannot run, until then
    if GATES["final_tasks_digest"] is None:
        assert not run.FINAL_TASKS.exists()
        with pytest.raises(freeze.FrozenError, match="not frozen"):
            run.load_tasks(run.TASKS, cohort="final")
    else:
        assert GATES["final_tasks_digest"] == freeze.file_digest(run.FINAL_TASKS)


def test_a_route_that_changed_unbinds_its_tasks_and_no_other(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = dict(MANIFEST.entries)
    entries["mcp:g6/g6_added_to_review"] = routing.Entry("mcp:g6/g6_added_to_review", True, "review", "", "0.3.0")
    changed = routing.Manifest(MANIFEST.format, MANIFEST.release, MANIFEST.routes, entries)
    before, after = run.route_digests(), run.route_digests(changed)
    assert {route for route in before if before[route] != after[route]} == {"review"}
    monkeypatch.setattr(routing, "load", lambda path=None: changed)
    with pytest.raises(freeze.FrozenError, match="'review'"):
        run.load_tasks()


def test_the_bars_are_the_adopted_ones_and_the_file_is_frozen() -> None:
    """The four bars and the interval floor the measurement research adopted. The digest is pinned in the README: a
    bar changed after it was frozen fails here until the old file is retired with its reason."""
    score.validate_gates(GATES)
    assert {bar: GATES[bar] for bar in score.BARS} == {
        "min_positive_completion": 0.70, "min_positive_lower_bound": 0.50, "max_negative_unnecessary": 0.15,
        "max_malformed": 0.10, "min_missing_tool_recovery": 0.80}
    assert GATES["final_cohort_shape"] == {"positive_per_route": 2, "negative_per_route": 2,
                                           "missing_tool_per_route": 1}
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    pinned = re.search(r"gates\.json` is frozen at digest `([0-9a-f]{64})`", readme)
    assert pinned and pinned.group(1) == freeze.file_digest(run.GATES)


def test_expected_calls_name_only_registered_tools() -> None:
    for task in TASKS["tasks"]:
        servers = task.get("servers", ["g6"])
        assert set(servers) <= set(RECORDED), task["id"]
        patterns = [*task.get("expected_calls", ()), *(task.get("forbidden") if isinstance(task.get("forbidden"), list)
                                                        else ())]
        for pattern in patterns:
            assert pattern["tool"] in RECORDED[pattern.get("server", "g6")], (task["id"], pattern)
        for pattern in task.get("expected_calls", ()):
            assert pattern.get("server", "g6") in servers, (task["id"], pattern)
            key = f"mcp:{pattern.get('server', 'g6')}/{pattern['tool']}"
            assert MANIFEST.entries[key].agent_facing, key


def test_a_positive_task_expects_its_own_routes_tool_or_op() -> None:
    """A positive task for a route is satisfied by that route's members, not by another route's."""
    for task in TASKS["tasks"]:
        if task["polarity"] != "positive":
            continue
        routes = set()
        for pattern in task["expected_calls"]:
            routes.add(MANIFEST.entries[f"mcp:{pattern.get('server', 'g6')}/{pattern['tool']}"].route)
            component = pattern.get("args_include", {}).get("component")
            if pattern["tool"] == "g6_invoke" and component:
                routes |= {entry.route for key, entry in MANIFEST.entries.items()
                           if key.startswith(f"op:{component}/")}
        assert task["route"] in routes, (task["id"], routes)


def test_no_task_is_in_both_cohorts() -> None:
    ids = [task["id"] for task in TASKS["tasks"]]
    assert len(ids) == len(set(ids))
    assert {task["cohort"] for task in TASKS["tasks"]} == {"development"}, "the final cohort has its own file"
    prompts: dict[str, set[str]] = {}
    for task in TASKS["tasks"]:
        prompts.setdefault(task["cohort"], set()).add((task["prompt"], task["polarity"]))
    if run.FINAL_TASKS.is_file():
        final = json.loads(run.FINAL_TASKS.read_text(encoding="utf-8"))
        assert {t["cohort"] for t in final["tasks"]} == {"final"}
        assert not {t["id"] for t in final["tasks"]} & set(ids)
        assert not {(t["prompt"], t["polarity"]) for t in final["tasks"]} & prompts["development"]


def test_every_fixture_exists_and_is_synthetic() -> None:
    for task in TASKS["tasks"]:
        folder = run.FIXTURES / task["fixture"]
        assert (folder / "README.md").is_file(), task["id"]
        assert "synthetic" in (folder / "README.md").read_text(encoding="utf-8").lower()


def test_the_final_cohorts_transcripts_are_read_only_by_the_scorer() -> None:
    """Nothing under ``src`` reads a trial's output: a final-cohort transcript cannot reach the skill text."""
    for path in (ROOT / "src" / "g6").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "validation/routing" not in text and "transcripts-" not in text, path
