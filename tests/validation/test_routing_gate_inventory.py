# SPDX-License-Identifier: Apache-2.0
"""One inventory for one class: a routing gate read as passed without the evidence it names. Derived from the code:
every way a row can enter a transcript log, and every way a gate's reading can be ``pass``, each held against a
session that did not run, ran and failed, ran twice, or was scored against files changed after the freeze."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

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
T = score.Transcript
KEPT = ("dev.discover.negative.1", "dev.rules.negative.1", "dev.discover.positive.1")
DONE = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}})


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)}


def test_a_row_enters_a_log_in_one_place_and_completed_has_one_condition() -> None:
    functions = _functions(HERE / "run.py")
    writers = [name for name, node in functions.items()
               if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("open", "write_text")
                      and "log" in ast.unparse(n.func.value) for n in ast.walk(node))]
    assert writers == ["_record"], writers
    callers = [name for name, node in functions.items()
               if any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_record" for n in ast.walk(node))]
    assert callers == ["run"]
    source = ast.unparse(functions["run"])
    assert source.count("'completed'") == 1 and "'completed' if finished else 'interrupted'" in source
    assert "finished = exit_code == 0 and spec.finished(events)" in source
    for name in ("_arm", "main", "read_transcripts", "sources"):       # nothing else can make a transcript
        assert name not in functions or "Transcript(" not in ast.unparse(functions[name])


def test_a_gate_reads_pass_in_one_place() -> None:
    functions = _functions(HERE / "score.py")
    assigns = [ast.unparse(node) for node in ast.walk(functions["score"]) if isinstance(node, ast.Assign)
               and any(isinstance(n, ast.Constant) and n.value == "pass" for n in ast.walk(node.value))]
    assert assigns == ["decision = 'pass'"], assigns
    release = ast.unparse(functions["release"])
    assert "'pass' if 'pass' in clients.values()" in release                 # only what a client's report decided
    main = ast.unparse(_functions(HERE / "run.py")["main"])
    assert "require_client=True" in main and "load_gates()" in main and "freeze.read(GATES)" not in main


def _ideal(listed: dict[str, Any], produced: str) -> list[Any]:
    out = []
    for task in listed["tasks"]:
        calls: tuple = ()
        text = ""
        if task["polarity"] == "positive":
            calls = ({"server": "g6", "tool": task["expected_calls"][0]["tool"], "args": {}, "seconds": 1.0, "valid": True},)
        elif task["polarity"] == "missing_tool":
            text = f"{task['withhold'][0]} is not listed by this server."
        out.append(T(task["id"], "codex", "completed", calls=calls, seconds=5.0, final_text=text, produced=produced))
    return out


def _cohort() -> dict[str, Any]:
    budget = {"seconds": 100, "calls": 4, "questions": 0}
    listed = []
    for r in range(9):
        for n in (1, 2):
            listed.append({"id": f"r{r}.positive.{n}", "route": f"r{r}", "polarity": "positive", "cohort": "final",
                           "prompt": "do the positive thing for this route", "fixture": "notes",
                           "expected_calls": [{"tool": "g6_alpha"}], "completion": {"kind": "calls_in_order"},
                           "budget": budget})
            listed.append({"id": f"r{r}.negative.{n}", "route": f"r{r}", "polarity": "negative", "cohort": "final",
                           "prompt": "a task this route's tools do not fit", "fixture": "notes",
                           "forbidden": [{"tool": "g6_alpha"}], "budget": budget})
        listed.append({"id": f"r{r}.missing_tool.1", "route": f"r{r}", "polarity": "missing_tool", "cohort": "final",
                       "prompt": "a task whose tool is withheld here", "fixture": "notes", "withhold": ["g6_alpha"],
                       "budget": budget})
    return {"format": score.FORMAT_TASKS, "routes": {f"r{r}": "0" * 64 for r in range(9)}, "tasks": listed}


def test_only_a_real_clients_rows_read_as_pass() -> None:
    listed, gates = _cohort(), run.load_gates()

    def decision(produced: str, **kwargs: Any) -> str:
        return score.score(listed, _ideal(listed, produced), gates, client="codex", cohort="final", **kwargs)["decision"]

    assert decision("client:/usr/bin/codex 0.99", require_client=True) == "pass"
    assert decision("stand-in:Scripted") == decision("stand-in:Scripted", require_client=True) == "simulated"
    assert decision("", require_client=True) == "unattributed"
    mixed = _ideal(listed, "client:/usr/bin/codex 0.99")
    mixed[0] = T(mixed[0].task_id, "codex", "completed", calls=mixed[0].calls, seconds=5.0, produced="stand-in:x")
    assert score.score(listed, mixed, gates, client="codex", cohort="final", require_client=True)["decision"] == "simulated"
    report = score.score(listed, _ideal(listed, "stand-in:x"), gates, client="codex", cohort="final")
    assert report["produced"] == {"client": 0, "stand_in": 45, "unattributed": 0}
    assert score.release([report]) == {"decision": "fail", "clients": {"codex": "simulated"},
                                       "known_limits": {"codex": "simulated sessions only"}}
    assert report["gates_digest"] == freeze.file_digest(run.GATES) and report["tasks_digest"] == freeze.digest(listed)
    assert report["bars_digest"] == run.pins()["bars_digest"]


class Client:
    def __init__(self, events: str = DONE, exit_code: int = 0) -> None:
        self.events, self.exit_code, self.started = events, exit_code, 0

    def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path) -> Any:
        from g6.agents._runtime.cli import OUTPUT_LOG, CliRun

        self.started += 1
        (scratch / OUTPUT_LOG).write_text(self.events, encoding="utf-8")
        return CliRun(argv=tuple(argv), exit_code=self.exit_code, output_tail="", output_bytes=0, elapsed_s=1.0,
                      cleanup_confirmed=True, env_names=())


def _few(tmp_path: Path) -> Path:
    tasks = json.loads(run.TASKS.read_text(encoding="utf-8"))
    tasks["tasks"] = [task for task in tasks["tasks"] if task["id"] in KEPT]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def _grant(path: Path, out: Path) -> None:
    from g6.core import cloud_consent as cloud

    for row in run.sources(run.load_tasks(path), out, "codex", "development"):
        assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                            "the test's stand-in person") == "granted"


def _reading(path: Path, out: Path) -> dict[str, Any]:
    return score.score(run.load_tasks(path), run.read_transcripts(out, "codex", "development"), run.load_gates(),
                       client="codex", cohort="development", require_client=True)


@pytest.mark.parametrize("case", ["did_not_run", "ran_and_failed", "no_terminal_event", "stand_in_finished"])
def test_no_session_short_of_a_real_finished_one_reads_as_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               case: str) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path, out = _few(tmp_path), tmp_path / "out"
    client = {"did_not_run": Client(), "ran_and_failed": Client("error: usage limit reached\n", 1),
              "no_terminal_event": Client('{"type": "item.started"}\n', 0), "stand_in_finished": Client()}[case]
    if case != "did_not_run":
        _grant(path, out)                                   # with no grant nothing starts: the session did not run
    done = run.run(path, out, client="codex", cohort="development", runner=client)
    states = {t.state for t in done}
    assert states == {"did_not_run": {"refused"}, "ran_and_failed": {"interrupted"},
                      "no_terminal_event": {"interrupted"}, "stand_in_finished": {"completed"}}[case]
    assert all(t.produced == "stand-in:Client" for t in done)
    assert all((t.exit_code, bool(t.events_sha256)) == ((None, False) if case == "did_not_run" else
                                                        (client.exit_code, True)) for t in done)
    report = _reading(path, out)
    assert report["decision"] != "pass"
    if case != "stand_in_finished":                         # nothing was shown, so no gate passes on it
        assert not any(gate["passed"] for gate in report["gates"].values())


def test_a_cohort_run_twice_is_refused_and_a_log_with_two_rows_is_not_read(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path, out = _few(tmp_path), tmp_path / "out"
    _grant(path, out)
    first = run.run(path, out, client="codex", cohort="development", runner=Client("", 1))
    again = Client()
    with pytest.raises(freeze.FrozenError, match="once"):
        run.run(path, out, client="codex", cohort="development", runner=again)
    assert again.started == 0 and len(run.read_transcripts(out, "codex", "development")) == len(first)
    repeat = run.run(path, out, client="codex", cohort="development", runner=Client(), repeat=1)    # a repeat is its own
    assert {t.repeat for t in repeat} == {1}
    log = out / "transcripts-codex-development.jsonl"
    log.write_text(log.read_text("utf-8") + json.dumps(T(KEPT[0], "codex", "completed").to_json()) + "\n", "utf-8")
    with pytest.raises(score.TaskError, match="two transcripts"):
        _reading(path, out)


def test_nothing_runs_or_is_scored_against_files_changed_after_the_freeze(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    assert freeze.file_digest(run.TASKS) == run.pins()["tasks_digest"]
    assert freeze.digest({**json.loads(run.GATES.read_text("utf-8")), "final_tasks_digest": None}) == run.pins()["bars_digest"]
    path, out = _few(tmp_path), tmp_path / "out"
    changed = json.loads(path.read_text("utf-8"))
    changed["tasks"][0]["budget"] = dict(changed["tasks"][0]["budget"], calls=99)
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(changed), encoding="utf-8")
    client = Client()
    for call in (lambda: run.load_tasks(edited), lambda: run.run(edited, out, client="codex", cohort="development",
                                                                 runner=client)):
        with pytest.raises(freeze.FrozenError):
            call()
    loosened = tmp_path / "gates.json"
    loosened.write_text(json.dumps(dict(json.loads(run.GATES.read_text("utf-8")), max_malformed=1.0)), "utf-8")
    monkeypatch.setattr(run, "GATES", loosened)
    for call in (run.load_gates, lambda: run.main(["score", "--client", "codex", "--cohort", "development",
                                                   "--out", str(out)])):
        with pytest.raises(freeze.FrozenError):
            call()
    assert client.started == 0
    twice = tmp_path / "twice.json"
    twice.write_text('{"bars_digest": "a", "bars_digest": "b"}', encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="twice"):
        freeze.read(twice)


def test_the_trial_server_uses_a_home_of_the_run_and_the_permissions_a_task_states(tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    home = run.trial_home(tmp_path / "out")
    assert home == (tmp_path / "out").resolve() / "g6_home" and home.is_dir() and home != tmp_path / "home"
    review = next(t for t in run.load_tasks()["tasks"] if t["id"] == "dev.review.positive.1")
    env = run.mcp_config(review, home=home)["mcpServers"]["g6"]["env"]
    assert env[run.SENSITIVE_ENV] == "patch_review.review" and env["G6_HOME"] == str(home)
    quiet = next(t for t in run.load_tasks()["tasks"] if t["id"] == "dev.discover.negative.1")
    assert run.SENSITIVE_ENV not in run.mcp_config(quiet, home=home)["mcpServers"]["g6"]["env"]
    from g6.core.door import SENSITIVE_ENV

    assert run.SENSITIVE_ENV == SENSITIVE_ENV
