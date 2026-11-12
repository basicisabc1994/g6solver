# SPDX-License-Identifier: Apache-2.0
"""The routing scorer (0.3 design 14.1): no constant policy passes, every denominator is full, and a run without the
person's grant sends nothing. Offline: synthetic transcripts and a stand-in client; no session is started."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", ROOT / "validation" / "routing" / "score.py")
run = _load("g6_validation_routing_run", ROOT / "validation" / "routing" / "run.py")
T = score.Transcript
BUDGET = {"seconds": 100, "calls": 4, "questions": 0}
GATES = {"format": score.FORMAT_GATES, "final_tasks_digest": None, "min_positive_completion": 0.70,
         "min_positive_lower_bound": 0.50, "max_negative_unnecessary": 0.15, "max_malformed": 0.10,
         "min_missing_tool_recovery": 0.80, "interval": "wilson95", "release_rule": "at_least_one_client_passes",
         "final_cohort_shape": {"positive_per_route": 2, "negative_per_route": 2, "missing_tool_per_route": 1},
         "consistency_repeats": 2}
TOOLS = ("g6_alpha", "g6_beta", "g6_find")
SCHEMAS = {f"g6/{tool}": {"properties": {"query": {}, "repo": {}}, "required": ["query"]} for tool in TOOLS}


def tasks(routes: int = 9) -> dict[str, Any]:
    """Nine synthetic routes with two positive, two negative and one missing-tool task each: the final cohort's shape."""
    listed = []
    for r in range(routes):
        route = f"route{r}"
        tool = TOOLS[r % 2]
        for n in (1, 2):
            listed.append({"id": f"{route}.positive.{n}", "route": route, "polarity": "positive", "cohort": "final",
                           "prompt": "do the positive thing for this route", "fixture": "notes",
                           "expected_calls": [{"tool": tool, "args_include": {"query": "wanted"}}],
                           "completion": {"kind": "calls_in_order"}, "budget": BUDGET})
            listed.append({"id": f"{route}.negative.{n}", "route": route, "polarity": "negative", "cohort": "final",
                           "prompt": "a task this route's tools do not fit", "fixture": "notes",
                           "forbidden": [{"tool": tool}], "budget": BUDGET})
        listed.append({"id": f"{route}.missing_tool.1", "route": route, "polarity": "missing_tool",
                       "cohort": "final", "prompt": "a task whose tool is withheld here", "fixture": "notes",
                       "withhold": [tool], "budget": BUDGET})
    return {"format": score.FORMAT_TASKS, "routes": {f"route{r}": "0" * 64 for r in range(routes)}, "tasks": listed}


def call(tool: str, seconds: float = 1.0, **args: Any) -> dict[str, Any]:
    return {"server": "g6", "tool": tool, "args": args or {"query": "wanted"}, "seconds": seconds, "valid": True}


def ideal(task: dict[str, Any], client: str = "codex") -> Any:
    """What a well-routed agent does on one task."""
    if task["polarity"] == "positive":
        return T(task["id"], client, "completed", calls=(call(task["expected_calls"][0]["tool"]),), seconds=5.0)
    if task["polarity"] == "negative":
        return T(task["id"], client, "completed", seconds=5.0)
    tool = task["withhold"][0]
    return T(task["id"], client, "completed", calls=(call(tool), call("g6_find")), seconds=5.0,
             final_text=f"{tool} is not available on this server, so I used g6_find.")


def report(transcripts: list[Any], listed: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """The gates' arithmetic alone: these transcripts name no client, so the scorer's own default (a row that names
    no client cannot pass) is set aside here and held in ``test_the_default_reading_needs_a_named_client``."""
    kwargs.setdefault("require_client", False)
    return score.score(listed or tasks(), transcripts, GATES, client=kwargs.pop("client", "codex"), cohort="final",
                       **kwargs)


def test_the_default_reading_needs_a_named_client() -> None:
    listed = tasks()
    rows = [ideal(task) for task in listed["tasks"]]
    assert score.score(listed, rows, GATES, client="codex", cohort="final")["decision"] == "unattributed"
    assert report(rows, listed)["decision"] == "pass"
    named = [T(t.task_id, t.client, t.state, calls=t.calls, seconds=t.seconds, final_text=t.final_text,
               produced="client:codex 0.1") for t in rows]
    assert score.score(listed, named, GATES, client="codex", cohort="final")["decision"] == "pass"


def test_a_well_routed_agent_passes_every_gate() -> None:
    listed = tasks()
    found = report([ideal(task) for task in listed["tasks"]], listed, schemas=SCHEMAS)
    assert found["decision"] == "pass" and all(gate["passed"] for gate in found["gates"].values())
    assert found["overall"]["scheduled"] == 45 and found["overall"]["completed"] == 45
    assert set(found["routes"]) == {f"route{r}" for r in range(9)}
    assert found["routes"]["route0"]["positive"] == {"tasks": 2, "useful_completion": 2, "expected_calls": 2,
                                                     "timely_valid_calls": 2}


def test_calling_everything_fails() -> None:
    listed = tasks()
    everything = tuple(call(tool) for tool in TOOLS)
    found = report([T(task["id"], "codex", "completed", calls=everything, seconds=5.0,
                      final_text="g6_alpha and g6_beta are missing; g6_find used.") for task in listed["tasks"]], listed)
    assert found["gates"]["min_positive_completion"]["passed"]
    assert not found["gates"]["max_negative_unnecessary"]["passed"]
    assert found["gates"]["max_negative_unnecessary"]["value"] == 1.0 and found["decision"] == "fail"


def test_calling_nothing_fails() -> None:
    listed = tasks()
    found = report([T(task["id"], "codex", "completed", seconds=5.0) for task in listed["tasks"]], listed)
    assert found["gates"]["max_negative_unnecessary"]["passed"]
    assert not found["gates"]["min_positive_completion"]["passed"]
    assert not found["gates"]["min_missing_tool_recovery"]["passed"] and found["decision"] == "fail"


def test_refusing_everything_fails() -> None:
    listed = tasks()
    found = report([T(task["id"], "codex", "refused", code="G6_E_CLOUD_CONSENT_REQUIRED") for task in listed["tasks"]],
                   listed)
    assert found["overall"]["refused"] == 45 and found["overall"]["scheduled"] == 45
    assert found["overall"]["positive"] == {"tasks": 18, "useful_completion": 0, "expected_calls": 18,
                                            "timely_valid_calls": 0}
    assert found["gates"]["min_positive_completion"] == {"value": 0.0, "bar": 0.70, "passed": False}
    # a refused task shows nothing, so it counts against the quiet, well-formed and recovery gates too
    assert not any(gate["passed"] for gate in found["gates"].values()) and found["decision"] == "fail"


def test_a_missing_run_is_counted_not_skipped() -> None:
    listed = tasks()
    kept = [ideal(task) for task in listed["tasks"] if task["id"] != "route3.positive.1"]
    found = report(kept, listed)
    assert found["overall"]["scheduled"] == 45 and found["overall"]["unavailable"] == 1
    assert found["overall"]["positive"]["tasks"] == 18 and found["overall"]["positive"]["useful_completion"] == 17
    row = next(row for row in found["tasks"] if row["task_id"] == "route3.positive.1")
    assert row["state"] == "unavailable" and row["useful_completion"] is False
    assert report([], listed)["decision"] == "not_run"


def test_late_or_over_budget_calls_do_not_count_as_timely() -> None:
    listed = tasks()
    task = listed["tasks"][0]
    tool = task["expected_calls"][0]["tool"]

    def one(transcript: Any) -> dict[str, Any]:
        return score.judge(task, transcript)

    assert one(T(task["id"], "codex", "completed", calls=(call(tool, seconds=99.0),)))["useful_completion"]
    late = one(T(task["id"], "codex", "completed", calls=(call(tool, seconds=101.0),)))
    assert late["timely_valid_calls"] == 0 and not late["useful_completion"]
    fifth = one(T(task["id"], "codex", "completed", calls=(*[call("g6_find")] * 4, call(tool))))
    assert fifth["timely_valid_calls"] == 0 and fifth["over_budget"]["calls"] and not fifth["useful_completion"]
    untimed = dict(call(tool), seconds=None)
    assert one(T(task["id"], "codex", "completed", calls=(untimed,), seconds=100.0))["useful_completion"]
    slow = one(T(task["id"], "codex", "completed", calls=(untimed,), seconds=100.5))
    assert not slow["useful_completion"] and slow["over_budget"]["seconds"]
    asked = one(T(task["id"], "codex", "completed", calls=(call(tool),), questions=1))
    assert asked["over_budget"]["questions"] and not asked["useful_completion"]
    wrong = one(T(task["id"], "codex", "completed", calls=(call(tool, query="something else"),)))
    assert wrong["timely_valid_calls"] == 0


def test_expected_calls_count_only_in_their_order() -> None:
    task = dict(tasks()["tasks"][0], expected_calls=[{"tool": "g6_alpha"}, {"tool": "g6_beta"}])
    forward = score.judge(task, T(task["id"], "codex", "completed", calls=(call("g6_alpha"), call("g6_beta"))))
    backward = score.judge(task, T(task["id"], "codex", "completed", calls=(call("g6_beta"), call("g6_alpha"))))
    assert forward["useful_completion"] and backward["timely_valid_calls"] == 1 and not backward["useful_completion"]


def test_a_malformed_call_is_counted_even_on_a_positive_task() -> None:
    listed = tasks()
    task = listed["tasks"][0]
    tool = task["expected_calls"][0]["tool"]
    said = dict(call(tool), valid=False)
    by_flag = score.judge(task, T(task["id"], "codex", "completed", calls=(said, call(tool))))
    assert by_flag["malformed_calls"] == 1 and by_flag["useful_completion"] and not by_flag["well_formed"]
    for bad in (call(tool, query="wanted", invented=1), call(tool, repo="x"), call("g6_never_registered")):
        row = score.judge(task, T(task["id"], "codex", "completed", calls=(bad,)), SCHEMAS)
        assert row["malformed_calls"] == 1 and row["timely_valid_calls"] == 0, bad
    transcripts = [ideal(t) for t in listed["tasks"]]
    for index in range(5):   # five of 45 tasks with a malformed call: 0.111, over the 0.10 bar
        transcripts[index] = T(transcripts[index].task_id, "codex", "completed", seconds=5.0,
                               calls=(*transcripts[index].calls, dict(call("g6_find"), valid=False)))
    found = report(transcripts, listed)
    assert found["overall"]["malformed"]["with_malformed_calls"] == 5
    assert not found["gates"]["max_malformed"]["passed"]


def test_recovery_means_saying_so_or_finding_and_never_repeating_the_call() -> None:
    task = next(t for t in tasks()["tasks"] if t["polarity"] == "missing_tool")
    tool = task["withhold"][0]

    def one(*calls: dict[str, Any], text: str = "") -> dict[str, Any]:
        return score.judge(task, T(task["id"], "codex", "completed", calls=calls, final_text=text), SCHEMAS)

    assert one(call(tool), text=f"The tool {tool} is not listed by this server.")["recovered"]
    assert one(call(tool), call("g6_find"))["recovered"]
    assert one(text=f"{tool} is absent here.")["recovered"]
    again = one(call(tool), call(tool), call("g6_find"), text=f"{tool} is not available.")
    assert again["withheld_calls"] == 2 and not again["recovered"]
    assert not one(call(tool), text="Done.")["recovered"]
    assert not one(text="Some tool is missing.")["recovered"]            # it does not say which
    assert one(call(tool), call("g6_find"))["malformed_calls"] == 0      # a call to an absent tool is not malformed


def test_meeting_the_rate_is_not_enough_below_the_interval_floor() -> None:
    assert round(score.wilson_lower(14, 18), 2) == 0.55 and round(score.wilson_lower(13, 18), 2) == 0.49
    assert score.wilson_lower(0, 0) == 0.0
    listed = tasks()

    def with_positives(done: int) -> dict[str, Any]:
        positives = [t["id"] for t in listed["tasks"] if t["polarity"] == "positive"]
        dropped = set(positives[done:])
        return report([T(t["id"], "codex", "completed", seconds=5.0) if t["id"] in dropped else ideal(t)
                       for t in listed["tasks"]], listed)

    thirteen, fourteen = with_positives(13), with_positives(14)
    assert thirteen["gates"]["min_positive_completion"]["passed"]          # 13 of 18 is 0.72
    assert not thirteen["gates"]["min_positive_lower_bound"]["passed"] and thirteen["decision"] == "fail"
    assert fourteen["decision"] == "pass"


def test_clients_are_scored_apart_and_one_must_pass() -> None:
    listed = tasks()
    good = [ideal(task, "codex") for task in listed["tasks"]]
    bad = [T(task["id"], "claude_code", "completed", seconds=5.0) for task in listed["tasks"]]
    codex = report(good + bad, listed, client="codex")
    claude = report(good + bad, listed, client="claude_code")
    assert codex["decision"] == "pass" and claude["decision"] == "fail"
    # the release reading takes a pass only from a gate reading: the pinned bars, a client named for every row, and
    # the evidence block ``run.gate_reading`` adds; a scorer's bare report says ``pass`` and is not one
    assert score.release([codex, claude])["clients"]["codex"] in ("other_bars", "unattributed")
    gate = dict(codex, bars_digest=score.pinned()["bars_digest"], produced={"client": 45, "stand_in": 0, "unattributed": 0})
    assert score.release([gate, claude])["clients"] == {"codex": "unevidenced", "claude_code": "fail"}
    codex = dict(gate, evidence={"rederived": 45, "unevidenced": {}})
    assert score.release([codex, claude]) == {"decision": "pass", "clients": {"codex": "pass", "claude_code": "fail"},
                                              "known_limits": {"claude_code": "routing bar not met"}}
    absent = report(good, listed, client="claude_code")
    assert absent["decision"] == "not_run"
    assert score.release([codex, absent])["known_limits"] == {"claude_code": "not run"}
    assert score.release([claude])["decision"] == "fail" and score.release([])["decision"] == "not_run"
    with pytest.raises(score.TaskError):                 # one client's final cohort is read once
        score.release([claude, absent])


def test_consistency_is_published_with_no_bar() -> None:
    listed = tasks()
    first = [ideal(task) for task in listed["tasks"]]
    positives = [t for t in listed["tasks"] if t["polarity"] == "positive"]
    repeats = [T(t["id"], "codex", "completed", calls=ideal(t).calls, seconds=5.0, repeat=r)
               for t in positives for r in (1, 2)]
    repeats[0] = T(repeats[0].task_id, "codex", "completed", seconds=5.0, repeat=1)      # one repeat misses
    found = report(first + repeats, listed)
    assert found["consistency"] == {"positive_tasks_run_three_times": 18, "passed_all_three": 17, "bar": None}
    assert found["decision"] == "pass"


def test_a_task_list_or_gate_file_outside_its_format_is_refused() -> None:
    listed = tasks()
    for change in (lambda d: d["tasks"][0].update(polarity="maybe"),
                   lambda d: d["tasks"][0].update(expected_calls=[]),
                   lambda d: d["tasks"][0].update(expected_calls=[{"tool": "g6_*"}]),
                   lambda d: d["tasks"][1].pop("forbidden"),
                   lambda d: d["tasks"][4].update(withhold=[]),
                   lambda d: d["tasks"][0].update(budget={"seconds": 0, "calls": 1, "questions": 0}),
                   lambda d: d["tasks"].append(dict(d["tasks"][0])),
                   lambda d: d["tasks"][0].update(route="unbound")):
        broken = json.loads(json.dumps(listed))
        change(broken)
        with pytest.raises(score.TaskError):
            score.validate_tasks(broken)
    for change in (lambda g: g.update(max_malformed=2.0), lambda g: g.pop("min_positive_lower_bound"),
                   lambda g: g.update(extra_bar=0.5), lambda g: g.update(final_tasks_digest="soon")):
        broken = dict(GATES)
        change(broken)
        with pytest.raises(score.TaskError):
            score.validate_gates(broken)
    with pytest.raises(score.TaskError):
        T("x", "codex", "skipped")


class StandIn:
    """A client that would answer, and records whether it was ever started."""

    def __init__(self, events: str = "") -> None:
        self.started: list[list[str]] = []
        self.events = events

    def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path) -> Any:
        from g6.agents._runtime.cli import OUTPUT_LOG, CliRun

        self.started.append(argv)
        (scratch / OUTPUT_LOG).write_text(self.events, encoding="utf-8")
        return CliRun(argv=tuple(argv), exit_code=0, output_tail="", output_bytes=0, elapsed_s=2.0,
                      cleanup_confirmed=True, env_names=())


def test_run_refuses_without_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    client = StandIn()
    done = run.run(run.TASKS, tmp_path / "out", client="codex", cohort="development", runner=client)
    scheduled = [t for t in run.load_tasks()["tasks"] if t["cohort"] == "development"]
    assert client.started == [], "a session was started without the person's grant"
    assert len(done) == len(scheduled) and {t.state for t in done} == {"refused"}
    assert {t.code for t in done} == {cloud.REQUIRED}
    assert cloud.disclosures() == []
    kept = run.read_transcripts(tmp_path / "out", "codex", "development")
    assert [t.to_json() for t in kept] == [t.to_json() for t in done]
    found = score.score(run.load_tasks(), kept, json.loads(run.GATES.read_text("utf-8")), client="codex",
                        cohort="development")
    assert found["decision"] == "fail" and found["overall"]["refused"] == len(scheduled)


def test_run_with_every_grant_starts_one_session_per_task_and_records_what_it_saw(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    out = tmp_path / "out"
    for row in run.sources(run.load_tasks(), out, "claude_code", "development"):
        assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                            "the test's stand-in person") == "granted"
    events = "\n".join(json.dumps(event) for event in (
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__g6__g6_find", "input": {"query": "claims"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Found nothing.",
         "total_cost_usd": 0.01}))
    client = StandIn(events)
    done = run.run(run.TASKS, out, client="claude_code", cohort="development", runner=client)
    assert len(client.started) == len(done) and {t.state for t in done} == {"completed"}
    assert all(t.calls == ({"server": "g6", "tool": "g6_find", "args": {"query": "claims"}, "seconds": None,
                            "valid": True, "result_state": ""},) and t.cost_usd == 0.01 and t.seconds == 2.0
               for t in done)
    assert len(cloud.disclosures()) == len(done)
    shop = out / "work" / "shop"
    assert (shop / ".claude" / "skills" / "using-g6" / "SKILL.md").is_file() and (shop / ".git").is_dir()


def test_a_client_that_is_not_installed_is_unavailable_and_a_deadline_is_interrupted(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.agents._runtime import codes
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import outside

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    class Raises:
        def __init__(self, code: str) -> None:
            self.code = code

        def locate(self) -> str:
            raise AgentRefusal(self.code, "as the test says")

    absent = run.run(run.TASKS, tmp_path / "a", client="codex", cohort="development",
                     runner=Raises(outside.OUTSIDE_PROGRAM_MISSING))
    late = run.run(run.TASKS, tmp_path / "b", client="codex", cohort="development", runner=Raises(codes.DEADLINE))
    assert {t.state for t in absent} == {"unavailable"} and {t.state for t in late} == {"interrupted"}
