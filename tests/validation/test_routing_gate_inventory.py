# SPDX-License-Identifier: Apache-2.0
"""One inventory for one class: a routing gate read as passed without the evidence it names.

The rule under test: a score is a function of the sessions' kept event logs and of frozen files, and of nothing a
row says about itself. The inventory is derived from the code, not from the cases reviews found: every field of a
transcript row (``dataclasses.fields``), every key of the session's record a run writes, every kept log, every
frozen file and every door a reading can leave by is changed in turn on a run that passes, and the reading must stay
the same (the field is read again from the log) or stop being ``pass``. Nothing here reads the source's spelling.
No client is started and no model is called: a fake stands where ``CliAgentRunner`` is."""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
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
#: What a well-routed agent does on six of the development list's own tasks.
SCRIPT = {
    "dev.discover.positive.1": [("g6_find", {"query": "check a claim"}), ("g6_describe", {"component": "grounding"})],
    "dev.improve.positive.1": [("g6_improve_status", {})],
    "dev.compose.positive.1": [("g6_compose", {"composition_json": '{"stages": [{"component": "grounding"}]}'})],
    "dev.rules.positive.1": [("g6_invoke", {"component": "datalog", "op": "check"})],
    "dev.discover.negative.1": [],
    "dev.review.missing_tool.1": [("g6_find", {"query": "review a patch"})],
}
ABSENT = "g6_review_patch is not listed by this server, so I used g6_find."


def codex_events(calls: list[tuple[str, dict[str, Any]]], text: str, *, last: str = "turn.completed") -> str:
    rows = [{"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "g6", "tool": tool,
                                                "arguments": args, "status": "completed", "result": {}}}
            for tool, args in calls]
    rows += [{"type": "item.completed", "item": {"type": "agent_message", "text": text}}, {"type": last}]
    return "\n".join(json.dumps(row) for row in rows)


DONE = codex_events([], "Done.")


def _fake_client(monkeypatch: Any, answer: Any) -> list[str]:
    """A fake in the place of ``CliAgentRunner``: ``run.run`` goes down its real-client path and starts nothing."""
    from g6.agents._runtime import cli

    started: list[str] = []

    class FakeCli(cli.CliAgentRunner):
        def locate(self) -> str:
            return "codex"

        def require(self, *, stop: Any = None) -> Any:
            return SimpleNamespace(found=True, detail="codex-cli 0.0.0")

        def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
                stop: Any = None) -> Any:
            started.append(prompt)
            text, code = answer(prompt)
            (scratch / cli.OUTPUT_LOG).write_text(text, encoding="utf-8")
            return cli.CliRun(argv=tuple(argv), exit_code=code, output_tail="", output_bytes=0, elapsed_s=3.0,
                              cleanup_confirmed=True, env_names=())

    monkeypatch.setattr(cli, "CliAgentRunner", FakeCli)
    return started


def _final_cohort(folder: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """A six-task final cohort made of the development list's own tasks, bound by a gate file with the real bars."""
    tasks = json.loads(run.TASKS.read_text(encoding="utf-8"))
    kept, by_prompt = [], {}
    for task in tasks["tasks"]:
        if task["id"] in SCRIPT:
            by_prompt[task["prompt"]] = codex_events(SCRIPT[task["id"]], ABSENT if task["polarity"] == "missing_tool"
                                                     else "Done.")
            kept.append(dict(task, id=task["id"].replace("dev.", "final."), cohort="final"))
    tasks["tasks"] = kept
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "final_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    real = json.loads(HERE.joinpath("gates.json").read_text(encoding="utf-8"))
    (folder / "gates.json").write_text(json.dumps(dict(real, final_tasks_digest=freeze.digest(tasks))), encoding="utf-8")
    return tasks, by_prompt


def _point(monkeypatch: Any, folder: Path) -> None:
    monkeypatch.setattr(run, "FINAL_TASKS", folder / "final_tasks.json")
    monkeypatch.setattr(run, "GATES", folder / "gates.json")


@pytest.fixture(scope="module")
def passed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One final cohort, run once by the fake client with every task done well: ``frozen/`` holds its list, its gate
    file and the record that it ran; ``out/`` its rows and kept logs. Each test works on a copy."""
    base = tmp_path_factory.mktemp("passed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("G6_HOME", str(base / "home"))
        patch.delenv("G6_STOP", raising=False)
        patch.delenv("G6_DISABLE_LLM", raising=False)
        _, by_prompt = _final_cohort(base / "frozen")
        _point(patch, base / "frozen")
        _fake_client(patch, lambda prompt: (by_prompt[prompt], 0))
        done = run.run(base / "frozen" / "final_tasks.json", base / "out", client="codex", cohort="final")
        assert {t.state for t in done} == {"completed"}
    shutil.rmtree(base / "out" / "work", ignore_errors=True)
    return base


@pytest.fixture()
def copy(passed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    shutil.copytree(passed / "frozen", tmp_path / "frozen")
    shutil.copytree(passed / "out", tmp_path / "out")
    _point(monkeypatch, tmp_path / "frozen")
    log = tmp_path / "out" / "transcripts-codex-final.jsonl"
    return SimpleNamespace(out=tmp_path / "out", frozen=tmp_path / "frozen", log=log,
                           rows=[json.loads(line) for line in log.read_text("utf-8").splitlines()])


def _write(copy: SimpleNamespace, rows: list[dict[str, Any]]) -> None:
    copy.log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _decision(copy: SimpleNamespace) -> str:
    """The gate's reading of the copy, by its strictest door; a refusal is a reading that is not ``pass``."""
    try:
        return run.release([copy.out])["decision"]
    except (freeze.FrozenError, score.TaskError) as refusal:
        return f"refused: {refusal}"


def test_the_untouched_run_passes_by_every_door(copy: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    """The control for everything below: the run reads ``pass`` by each door a reading can leave by."""
    assert run.main(["score", "--client", "codex", "--cohort", "final", "--out", str(copy.out)]) == 0
    written = json.loads((copy.out / "report-codex-final.json").read_text(encoding="utf-8"))
    reading = run.gate_reading(copy.out, client="codex", cohort="final")
    assert written == reading and reading["decision"] == "pass"
    assert reading["produced"] == {"client": 6, "stand_in": 0, "unattributed": 0}
    assert reading["evidence"]["rederived"] == 6 and reading["evidence"]["unevidenced"] == {}
    assert score.release([reading])["decision"] == "pass" and run.release([copy.out])["decision"] == "pass"
    assert run.main(["release", "--out", str(copy.out)]) == 0
    assert all(row["produced"] == "client:codex codex-cli 0.0.0" for row in copy.rows)


#: Every field of a transcript row, by what the gate's reading does with it. A field added to ``Transcript`` is
#: classified here or the inventory fails.
REDERIVED = {"state": "interrupted", "code": "G6_E_OTHER", "calls": [], "questions": 9, "cost_usd": 99.0,
             "final_text": "something else"}
HELD = {"task_id": "final.unknown.task", "client": "claude_code", "repeat": 1, "produced": "", "exit_code": 1,
        "events_sha256": "0" * 64, "seconds": 100000.0}


def test_every_field_of_a_row_is_read_again_from_the_log_or_costs_the_pass(copy: SimpleNamespace) -> None:
    assert {field.name for field in dataclasses.fields(T)} == set(REDERIVED) | set(HELD)
    assert set(copy.rows[0]) == set(REDERIVED) | set(HELD) | {"session"}
    baseline = run.gate_reading(copy.out, client="codex", cohort="final")
    for name, other in REDERIVED.items():            # what a row says of these is dropped: the report does not move
        _write(copy, [dict(row, **{name: other}) for row in copy.rows])
        assert run.gate_reading(copy.out, client="codex", cohort="final") == baseline, name
    for name, other in HELD.items():                 # these are what a row is held to: another value is not a pass
        _write(copy, [dict(row, **{name: other}) for row in copy.rows])
        assert _decision(copy) != "pass", name
        _write(copy, [dict(copy.rows[0], **{name: other}), *copy.rows[1:]])          # and one row is enough
        assert _decision(copy) != "pass" or name == "seconds", name


def test_a_row_cannot_say_more_than_its_log(copy: SimpleNamespace) -> None:
    """The other direction: a failed session's row that says ``completed``, with better calls, reads as the log."""
    failed = codex_events([], "I could not finish.", last="turn.failed").encode("utf-8")
    digest = hashlib.sha256(failed).hexdigest()
    (copy.out / "events" / f"{digest}.log").write_bytes(failed)
    _write(copy, [dict(row, events_sha256=digest) for row in copy.rows])       # the rows still say completed
    reading = run.gate_reading(copy.out, client="codex", cohort="final")
    assert reading["overall"]["completed"] == 0 and reading["decision"] == "fail"


def test_every_key_of_a_sessions_record_binds(copy: SimpleNamespace) -> None:
    record = copy.rows[0]["session"]
    assert set(record) == {"run", "bound", "served"} and set(record["bound"]) == {"task", "tasks", "bars", "g6"}
    for name in record["bound"]:                     # a record of another task, list, bars or package is refused
        changed = [dict(row, session=dict(row["session"], bound=dict(row["session"]["bound"], **{name: "0" * 64})))
                   for row in copy.rows]
        _write(copy, changed)
        assert _decision(copy).startswith("refused"), name
        with pytest.raises(freeze.FrozenError, match="ran on another"):
            run.gate_reading(copy.out, client="codex", cohort="final")
        dropped = [dict(row, session=dict(row["session"], bound={k: v for k, v in row["session"]["bound"].items()
                                                                 if k != name})) for row in copy.rows]
        _write(copy, dropped)                        # and a record that does not name it is not evidence
        assert run.gate_reading(copy.out, client="codex", cohort="final")["decision"] == "unevidenced", name
    for changed in ([dict(row, session=dict(row["session"], run="0" * 32)) for row in copy.rows],
                    [{k: v for k, v in row.items() if k != "session"} for row in copy.rows],
                    [dict(row, session=dict(row["session"], served={"g6": {"g6": "0" * 64, "serves": True}}))
                     for row in copy.rows],
                    [dict(row, session=dict(row["session"], served={"g6": {"g6": row["session"]["bound"]["g6"],
                                                                           "serves": False}})) for row in copy.rows]):
        _write(copy, changed)
        assert run.gate_reading(copy.out, client="codex", cohort="final")["decision"] == "unevidenced"
        assert run.release([copy.out])["decision"] == "fail"
    served = [dict(row, session=dict(row["session"], served={"g6": {"g6": row["session"]["bound"]["g6"],
                                                                    "serves": True}})) for row in copy.rows]
    _write(copy, served)                             # a server that found the bound package is no fault
    assert _decision(copy) == "pass"


def test_every_kept_log_is_needed_as_it_was_written(copy: SimpleNamespace) -> None:
    logs = sorted((copy.out / "events").glob("*.log"))
    assert {path.stem for path in logs} == {row["events_sha256"] for row in copy.rows}
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == path.stem for path in logs)
    for path in logs:
        kept = path.read_bytes()
        for change in (lambda: path.unlink(), lambda: path.write_bytes(kept + b"\n"),
                       lambda: path.write_bytes(kept.replace(b"turn.completed", b"turn.failed"))):
            change()
            reading = run.gate_reading(copy.out, client="codex", cohort="final")
            assert reading["decision"] == "unevidenced" and reading["evidence"]["unevidenced"], path.name
            assert run.release([copy.out])["decision"] == "fail"
            path.write_bytes(kept)
    assert _decision(copy) == "pass"


def test_every_frozen_file_binds_the_reading(copy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    listed = json.loads((copy.frozen / "final_tasks.json").read_text("utf-8"))
    gates = json.loads((copy.frozen / "gates.json").read_text("utf-8"))
    runs = copy.frozen / run.FINAL_RUNS
    assert [json.loads(line)["client"] for line in runs.read_text("utf-8").splitlines()] == ["codex"]
    # the list, changed and bound again by the gate file: the first final run froze the list it ran on
    edited = json.loads(json.dumps(listed))
    edited["tasks"][0]["budget"] = dict(edited["tasks"][0]["budget"], calls=99)
    (copy.frozen / "final_tasks.json").write_text(json.dumps(edited), "utf-8")
    assert "not frozen" in _decision(copy)
    (copy.frozen / "gates.json").write_text(json.dumps(dict(gates, final_tasks_digest=freeze.digest(edited))), "utf-8")
    assert "frozen once" in _decision(copy)
    # with the run's record gone too, the rows' own records still name the list they ran on
    kept_runs = runs.read_text("utf-8")
    runs.unlink()
    assert "ran on another" in _decision(copy)
    (copy.frozen / "final_tasks.json").write_text(json.dumps(listed), "utf-8")
    (copy.frozen / "gates.json").write_text(json.dumps(gates), "utf-8")
    assert run.gate_reading(copy.out, client="codex", cohort="final")["decision"] == "unevidenced"   # no recorded run
    runs.write_text(kept_runs, "utf-8")
    assert _decision(copy) == "pass"
    # the bars
    (copy.frozen / "gates.json").write_text(json.dumps(dict(gates, max_malformed=1.0)), "utf-8")
    assert _decision(copy).startswith("refused")
    (copy.frozen / "gates.json").write_text(json.dumps(gates), "utf-8")
    # the g6 package the sessions ran on
    monkeypatch.setattr(run, "package_digest", lambda: "f" * 64)
    assert "ran on another g6" in _decision(copy)


def test_a_final_cohort_is_run_once_whatever_the_folder(copy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    started = _fake_client(monkeypatch, lambda prompt: (DONE, 0))
    for out in (copy.out, tmp_path / "elsewhere"):
        with pytest.raises(freeze.FrozenError, match="once"):
            run.run(copy.frozen / "final_tasks.json", out, client="codex", cohort="final")
    assert started == [] and not (tmp_path / "elsewhere").exists()
    repeat = run.run(copy.frozen / "final_tasks.json", tmp_path / "elsewhere", client="codex", cohort="final", repeat=1)
    assert {t.repeat for t in repeat} == {1} and len(started) == 6          # a consistency repeat is its own run
    with pytest.raises(freeze.FrozenError, match="once"):
        run.run(copy.frozen / "final_tasks.json", tmp_path / "third", client="codex", cohort="final", repeat=1)
    assert run.final_run("claude_code", 0) is None                           # the other client has its own one run


def test_a_final_run_in_which_nothing_started_is_void_and_a_stopped_run_spends_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    _, by_prompt = _final_cohort(tmp_path / "frozen")
    _point(monkeypatch, tmp_path / "frozen")
    path = tmp_path / "frozen" / "final_tasks.json"

    class Ungranted:                                 # not a CliAgentRunner: the consent check stands before it
        started = 0

        def run(self, argv: list[str], **_: Any) -> Any:
            self.started += 1

    monkeypatch.setenv("G6_STOP", "1")
    with pytest.raises(Exception, match="G6 is stopped"):
        run.run(path, tmp_path / "stopped", client="codex", cohort="final", runner=Ungranted())
    assert run._final_runs() == [] and not (tmp_path / "stopped").exists()
    monkeypatch.delenv("G6_STOP")
    refused = run.run(path, tmp_path / "ungranted", client="codex", cohort="final", runner=Ungranted())
    assert {t.state for t in refused} == {"refused"} and Ungranted.started == 0
    assert [sorted(row) for row in run._final_runs()][-1] == ["run", "void"] and run.final_run("codex", 0) is None
    started = _fake_client(monkeypatch, lambda prompt: (by_prompt[prompt], 0))
    done = run.run(path, tmp_path / "out", client="codex", cohort="final")
    assert len(started) == 6 and {t.state for t in done} == {"completed"}
    assert run.release([tmp_path / "out"])["decision"] == "pass"
    assert run.release([tmp_path / "ungranted"])["decision"] == "fail"       # the void run's rows are no one's pass


def test_rows_typed_by_hand_pass_by_no_door(copy: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    """Six rows that say everything a perfect session's rows say, with no kept log behind them."""
    typed = [{k: v for k, v in row.items() if k != "session"} for row in copy.rows]
    shutil.rmtree(copy.out / "events")
    for said in ({}, {"events_sha256": ""}, {"exit_code": None, "events_sha256": ""}, {"produced": "client:"},
                 {"produced": "client:codex"}):
        _write(copy, [dict(row, **said) for row in typed])
        reading = run.gate_reading(copy.out, client="codex", cohort="final")
        assert reading["decision"] in ("unevidenced", "unattributed"), said
        assert run.main(["score", "--client", "codex", "--cohort", "final", "--out", str(copy.out)]) == 0
        assert json.loads((copy.out / "report-codex-final.json").read_text("utf-8"))["decision"] != "pass"
        assert run.release([copy.out])["decision"] == "fail"
        # the scorer scores what it is handed; the release reading takes no pass from it without the evidence block
        handed = score.score(run.load_tasks(run.FINAL_TASKS, cohort="final"),
                             run.read_transcripts(copy.out, "codex", "final"), run.load_gates(), client="codex",
                             cohort="final")
        assert score.release([handed])["decision"] == "fail"
    for produced, reading in (("stand-in:Scripted", "simulated"), ("", "unattributed")):
        _write(copy, [dict(row, produced=produced) for row in typed])
        assert run.gate_reading(copy.out, client="codex", cohort="final")["decision"] == reading


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


def test_the_scorers_default_is_the_strict_one_and_release_holds_the_pins() -> None:
    listed, gates = _cohort(), run.load_gates()

    def report(produced: str, bars: dict[str, Any] = gates, **kwargs: Any) -> dict[str, Any]:
        return score.score(listed, _ideal(listed, produced), bars, client="codex", cohort="final", **kwargs)

    assert report("client:/usr/bin/codex 0.99")["decision"] == "pass"
    assert report("stand-in:Scripted")["decision"] == "simulated"
    assert report("")["decision"] == report("client:")["decision"] == report("client: ")["decision"] == "unattributed"
    mixed = _ideal(listed, "client:/usr/bin/codex 0.99")
    mixed[0] = T(mixed[0].task_id, "codex", "completed", calls=mixed[0].calls, seconds=5.0, produced="stand-in:x")
    assert score.score(listed, mixed, gates, client="codex", cohort="final")["decision"] == "simulated"
    simulated = report("stand-in:x")
    assert simulated["produced"] == {"client": 0, "stand_in": 45, "unattributed": 0}
    assert score.release([simulated]) == {"decision": "fail", "clients": {"codex": "simulated"},
                                          "known_limits": {"codex": "simulated sessions only"}}
    assert simulated["gates_digest"] == freeze.file_digest(run.GATES) and simulated["tasks_digest"] == freeze.digest(listed)
    assert simulated["bars_digest"] == run.pins()["bars_digest"] == score.pinned()["bars_digest"]
    good = report("client:codex 0.99")
    evidenced = dict(good, evidence={"rederived": 45, "unevidenced": {}})
    assert score.release([good])["clients"] == {"codex": "unevidenced"}       # a scorer's report is not a gate reading
    assert score.release([evidenced])["decision"] == "pass"
    for change, reading in (({"evidence": {"rederived": 45, "unevidenced": {"r0.positive.1": "no log"}}}, "unevidenced"),
                            ({"evidence": {"rederived": 0, "unevidenced": {}}}, "unevidenced"),
                            ({"bars_digest": "0" * 64}, "other_bars"),
                            ({"produced": {"client": 44, "stand_in": 1, "unattributed": 0}}, "simulated"),
                            ({"produced": {"client": 44, "stand_in": 0, "unattributed": 1}}, "unattributed"),
                            ({"produced": {}}, "unattributed"), ({"decision": "passed"}, "fail"),
                            ({"decision": "unevidenced"}, "unevidenced")):
        assert score.release([dict(evidenced, **change)])["clients"] == {"codex": reading}, change
    loose = report("client:codex 0.99", dict(gates, max_negative_unnecessary=1.0))
    assert score.release([dict(loose, evidence=evidenced["evidence"])])["clients"] == {"codex": "other_bars"}
    assert set(score.release([dict(evidenced, decision=d)])["known_limits"]["codex"] for d in score.READINGS) \
        == set(score.READINGS.values())


class Client:
    """A stand-in passed as ``runner``; it takes no ``stop``, as the earliest stand-ins of these tests did not."""

    def __init__(self, events: str | bytes = DONE, exit_code: int = 0) -> None:
        self.events, self.exit_code, self.started = events, exit_code, 0

    def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path) -> Any:
        from g6.agents._runtime.cli import OUTPUT_LOG, CliRun

        self.started += 1
        (scratch / OUTPUT_LOG).write_bytes(self.events if isinstance(self.events, bytes) else self.events.encode("utf-8"))
        return CliRun(argv=tuple(argv), exit_code=self.exit_code, output_tail="", output_bytes=0, elapsed_s=1.0,
                      cleanup_confirmed=True, env_names=())


def _few(tmp_path: Path, kept: tuple[str, ...] = KEPT) -> Path:
    tasks = json.loads(run.TASKS.read_text(encoding="utf-8"))
    tasks["tasks"] = [task for task in tasks["tasks"] if task["id"] in kept]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def _grant(path: Path, out: Path, client: str = "codex") -> None:
    from g6.core import cloud_consent as cloud

    for row in run.sources(run.load_tasks(path), out, client, "development"):
        assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                            "the test's stand-in person") == "granted"


def _reading(path: Path, out: Path) -> dict[str, Any]:
    return run.gate_reading(out, client="codex", cohort="development", tasks_path=path)


@pytest.mark.parametrize("case", ["did_not_run", "ran_and_failed", "no_success_event", "a_message_and_no_turn_end",
                                  "stand_in_finished"])
def test_no_session_short_of_a_real_finished_one_reads_as_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               case: str) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path, out = _few(tmp_path), tmp_path / "out"
    message = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}})
    client = {"did_not_run": Client(), "ran_and_failed": Client("error: usage limit reached\n", 1),
              "no_success_event": Client('{"type": "item.started"}\n', 0),
              "a_message_and_no_turn_end": Client(message, 0), "stand_in_finished": Client()}[case]
    if case != "did_not_run":
        _grant(path, out)                                   # with no grant nothing starts: the session did not run
    done = run.run(path, out, client="codex", cohort="development", runner=client)
    assert {t.state for t in done} == {"refused" if case == "did_not_run" else
                                       "completed" if case == "stand_in_finished" else "interrupted"}
    assert all(t.produced == "stand-in:Client" for t in done)
    assert all((t.exit_code, bool(t.events_sha256)) == ((None, False) if case == "did_not_run" else
                                                        (client.exit_code, True)) for t in done)
    assert [t.to_json() for t in run.read_transcripts(out, "codex", "development")] == [t.to_json() for t in done]
    report = _reading(path, out)
    assert report["decision"] != "pass"
    if case != "stand_in_finished":                         # nothing was shown, so no gate passes on it
        assert not any(gate["passed"] for gate in report["gates"].values())
    else:                                                   # read from the kept logs, and a stand-in's all the same
        assert report["evidence"] == {**report["evidence"], "rederived": len(KEPT), "unevidenced": {}}
        assert report["produced"] == {"client": 0, "stand_in": len(KEPT), "unattributed": 0}


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
    log.write_text(log.read_text("utf-8") + "not a row\n", "utf-8")
    with pytest.raises(score.TaskError, match="is not a transcript row"):
        run.read_transcripts(out, "codex", "development")


def test_a_development_record_is_scored_on_the_list_it_ran_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    path, out = _few(tmp_path), tmp_path / "out"
    _grant(path, out)
    run.run(path, out, client="codex", cohort="development", runner=Client())
    assert _reading(path, out)["evidence"]["rederived"] == len(KEPT)
    (tmp_path / "less").mkdir()
    with pytest.raises(freeze.FrozenError, match="ran on another tasks"):       # a cut of the list it did not run on
        _reading(_few(tmp_path / "less", KEPT[:2]), out)


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
    unbound = json.loads(path.read_text("utf-8"))
    del unbound["routes"][run.EVERY_ROUTE]                  # a list that binds nothing of what every session reads
    edited.write_text(json.dumps(unbound), encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="every session reads"):
        run.load_tasks(edited)
    loosened = tmp_path / "gates.json"
    loosened.write_text(json.dumps(dict(json.loads(run.GATES.read_text("utf-8")), max_malformed=1.0)), "utf-8")
    monkeypatch.setattr(run, "GATES", loosened)
    for call in (run.load_gates, lambda: run.run(path, out, client="codex", cohort="development", runner=client),
                 lambda: run.main(["score", "--client", "codex", "--cohort", "development", "--out", str(out)])):
        with pytest.raises(freeze.FrozenError):
            call()
    assert client.started == 0
    twice = tmp_path / "twice.json"
    twice.write_text('{"bars_digest": "a", "bars_digest": "b"}', encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="twice"):
        freeze.read(twice)
    monkeypatch.setattr(run, "PINS", twice)
    with pytest.raises(freeze.FrozenError, match="twice"):
        run.pins()


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
