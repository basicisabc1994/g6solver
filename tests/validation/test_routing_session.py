# SPDX-License-Identifier: Apache-2.0
"""What one routing session is, held to the code: its event parsers raise on nothing an event can hold, the one
definition of a finished session, a row for every session that was started, a G6 home of its own made through G6's
own reader, the digest of what every session reads, which ``g6`` package served it, and G6's stop before every
start. Each inventory is derived from the code (every path of a real event stream, every file the skill folder
holds, every line of the root skill), not from the cases a review found. No client is started, no model is called."""
from __future__ import annotations

import fnmatch
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
server = _load("g6_validation_routing_server", HERE / "server.py")
freeze = _load("g6_validation_freeze", ROOT / "validation" / "freeze.py")
KEPT = ("dev.discover.negative.1", "dev.rules.negative.1")
#: A well-formed stream for each client: a call, its result, the final message and the client's success event.
STREAMS = {
    "claude_code": [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "a", "name": "mcp__g6__g6_find", "input": {"query": "claims"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": False,
             "content": [{"type": "text", "text": json.dumps({"completion_state": "qualified-draft"})}]}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Done.", "total_cost_usd": 0.25}],
    "codex": [
        {"type": "thread.started"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "g6", "tool": "g6_find",
                                            "arguments": {"query": "claims"}, "status": "completed", "error": None,
                                            "result": {"content": [{"type": "text", "text": json.dumps(
                                                {"completion_state": "qualified-draft"})}]}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
        {"type": "turn.completed"}],
}
#: What a model or a client may put where something else was expected.
ODD = (None, True, False, 0, 7, -1.5, float("nan"), float("inf"), "", "x", "mcp__g6", "mcp__", [], [7], [None],
       {}, {"text": 7}, {"type": "tool_use"}, [{"type": "tool_result", "tool_use_id": "a", "content": 7}])


def _text(events: list[Any]) -> bytes:
    return "\n".join(json.dumps(event) for event in events).encode("utf-8")


def _paths(value: Any, at: tuple = ()) -> list[tuple]:
    """Every path into a JSON value, the value's own included."""
    found = [at]
    for key, inner in (value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()):
        found += _paths(inner, (*at, key))
    return found


def _with(value: Any, path: tuple, other: Any) -> Any:
    if not path:
        return other
    copy = dict(value) if isinstance(value, dict) else list(value)
    copy[path[0]] = _with(value[path[0]], path[1:], other)
    return copy


@pytest.mark.parametrize("client", sorted(STREAMS))
def test_the_parsers_raise_on_nothing_an_event_can_hold(client: str) -> None:
    """Every path of a well-formed stream, replaced in turn by every odd value: the reading is made, its calls are
    whole, its cost is a finite number or none, and it is ``completed`` only with the exit code 0."""
    assert run.reading(client, _text(STREAMS[client]), 0)["state"] == "completed"       # the stream is a real finish
    tried = 0
    for index, event in enumerate(STREAMS[client]):
        for path in _paths(event):
            for other in ODD:
                events = [*STREAMS[client][:index], _with(event, path, other), *STREAMS[client][index + 1:]]
                for exit_code in (0, 1):
                    found = run.reading(client, _text(events), exit_code)
                    assert found["state"] in ("completed", "interrupted") and (exit_code == 0 or found["state"] != "completed")
                    assert all(set(call) == {"server", "tool", "args", "seconds", "valid", "result_state"}
                               and isinstance(call["server"], str) and isinstance(call["tool"], str)
                               and isinstance(call["args"], dict) and isinstance(call["valid"], bool)
                               and isinstance(call["result_state"], str) for call in found["calls"])
                    assert isinstance(found["final_text"], str)
                    assert found["cost_usd"] is None or 0 <= found["cost_usd"] < float("inf")
                    score.Transcript("t", client, **found)
                    tried += 1
    assert tried > 500
    for raw in (b"", b"\xff\xfe\x00", b"not json\n{]\n", b"[" * 200000, b'{"type": "result"}' * 3, b"7\nnull\n[]\n\"x\""):
        assert run.reading(client, raw, 0)["state"] == "interrupted"
    assert run.reading(client, None, 0) == {"state": "interrupted", "code": run.UNFINISHED, "calls": (),
                                           "final_text": "", "cost_usd": None, "questions": 0}


def test_finished_is_the_clients_one_success_event_and_exit_code_zero() -> None:
    """The definition, held from both sides: every part of each success event is needed, and nothing else stands in
    for it. An event this file does not know is not a finish."""
    for client, stream in STREAMS.items():
        done = _text(stream)
        for exit_code in (None, 1, -1, True, False, "0", 0.0, 2):
            assert run.reading(client, done, exit_code)["state"] == "interrupted", (client, exit_code)
        assert run.reading(client, _text(stream[:-1]), 0)["state"] == "interrupted"           # no success event
    result = STREAMS["claude_code"][-1]
    for key in ("type", "subtype", "is_error"):
        for other in (*ODD, "error_max_turns", "error_during_execution", "completed"):
            if other is result[key]:
                continue                                     # the value the success event itself has
            changed = [*STREAMS["claude_code"][:-1], {**result, key: other}]
            assert not run.finished_claude(_text(changed).decode()), (key, other)
        missing = {k: v for k, v in result.items() if k != key}
        assert not run.finished_claude(_text([*STREAMS["claude_code"][:-1], missing]).decode()), key
    # a success after a result that was not one is not a finished session
    assert not run.finished_claude(_text([{"type": "result", "subtype": "error_max_turns", "is_error": False},
                                          result]).decode())
    codex = STREAMS["codex"]
    for other in ODD + ("turn.failed", "turn.aborted", "error", "item.completed", "turn.complete"):
        assert not run.finished_codex(_text([*codex[:-1], {"type": other}]).decode()), other
    for extra in ({"type": "turn.failed"}, {"type": "error", "message": "stream disconnected"}):
        assert not run.finished_codex(_text([*codex, extra]).decode()) and not run.finished_codex(_text([extra, *codex]).decode())
    assert run.finished_codex(_text(codex).decode()) and run.finished_claude(_text(STREAMS["claude_code"]).decode())
    assert {name: (spec.parse, spec.finished) for name, spec in run.CLIENTS.items()} == {
        "claude_code": (run.parse_claude, run.finished_claude), "codex": (run.parse_codex, run.finished_codex)}


class StandIn:
    """A scripted stand-in passed as ``runner``."""

    def __init__(self, text: str | bytes = _text(STREAMS["codex"]), exit_code: int = 0, during: Any = None) -> None:
        self.text, self.exit_code, self.during, self.started = text, exit_code, during, 0
        self.stops: list[Any] = []

    def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path, stop: Any = None) -> Any:
        from g6.agents._runtime.cli import OUTPUT_LOG, CliRun

        self.started += 1
        self.stops.append(stop)
        (scratch / OUTPUT_LOG).write_bytes(self.text if isinstance(self.text, bytes) else self.text.encode("utf-8"))
        if self.during is not None:
            self.during(argv)
        return CliRun(argv=tuple(argv), exit_code=self.exit_code, output_tail="", output_bytes=0, elapsed_s=1.0,
                      cleanup_confirmed=True, env_names=())


def _few(tmp_path: Path, kept: tuple[str, ...] = KEPT) -> Path:
    tasks = json.loads(run.TASKS.read_text(encoding="utf-8"))
    tasks["tasks"] = [task for task in tasks["tasks"] if task["id"] in kept]
    assert len(tasks["tasks"]) == len(kept)
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def _grant(path: Path, out: Path, client: str = "codex") -> None:
    from g6.core import cloud_consent as cloud

    for row in run.sources(run.load_tasks(path), out, client, "development"):
        assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                            "the test's stand-in person") == "granted"


@pytest.fixture()
def ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A cut-down development list, a G6 home of the test's own with every grant the run lists, nothing stopped."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    path, out = _few(tmp_path), tmp_path / "out"
    _grant(path, out)
    return SimpleNamespace(path=path, out=out, home=tmp_path / "home")


def _env(argv: list[str], name: str = "g6") -> dict[str, str]:
    """The environment a Codex session's server ``name`` is given, read from the client's command line."""
    lead = f"mcp_servers.{name}.env."
    return {arg[len(lead):].split("=", 1)[0]: json.loads(arg.split("=", 1)[1]) for arg in argv if arg.startswith(lead)}


@pytest.mark.parametrize("fault", [RuntimeError("the runner broke"), OSError("the disk is full"), ValueError("odd"),
                                   KeyError("gone"), KeyboardInterrupt()], ids=lambda fault: type(fault).__name__)
def test_every_session_that_was_started_has_a_row_whatever_was_raised(ready: SimpleNamespace, fault: BaseException) -> None:
    def raises(argv: list[str]) -> None:
        raise fault

    client = StandIn(during=raises)
    try:
        done = run.run(ready.path, ready.out, client="codex", cohort="development", runner=client)
    except KeyboardInterrupt:
        done = None
    rows = run.read_transcripts(ready.out, "codex", "development")
    assert len(rows) == client.started and {t.state for t in rows} == {"interrupted"}
    assert all(t.events_sha256 and run.kept(ready.out, t.events_sha256)[0] == _text(STREAMS["codex"]) for t in rows)
    if isinstance(fault, KeyboardInterrupt):
        assert done is None and client.started == 1          # the person's own interrupt ends the run, row written
    else:
        assert client.started == len(KEPT) == len(done)      # a fault of one session does not cost the others theirs
    with pytest.raises(freeze.FrozenError, match="once"):    # and the tasks that ran cannot be run again unseen
        run.run(ready.path, ready.out, client="codex", cohort="development", runner=StandIn())


def test_each_session_has_a_home_of_its_own_made_through_g6s_reader(ready: SimpleNamespace,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    seen: list[tuple[Path, bool, list[str]]] = []
    fixtures = {task["fixture"] for task in run.load_tasks(ready.path)["tasks"]}

    def looks(argv: list[str]) -> None:
        home = Path(_env(argv)["G6_HOME"])
        with pytest.MonkeyPatch.context() as inside:         # what the session's server reads there, as G6 reads it
            inside.setenv("G6_HOME", str(home))
            granted = all(cloud.preview(f"routing-fixture.{name}", "openai:codex")[0] for name in fixtures)
        seen.append((home, granted, sorted(path.name for path in home.iterdir())))
        (home / "left_by_this_session.json").write_text("{}", encoding="utf-8")

    for repeat in (0, 1):
        run.run(ready.path, ready.out, client="codex", cohort="development", runner=StandIn(during=looks), repeat=repeat)
    assert len({home for home, _, _ in seen}) == len(seen) == 2 * len(KEPT)                    # one home a session
    assert all(granted and found == ["cloud_consent"] for _, granted, found in seen), seen     # granted, and nothing else
    assert all(home.parent == ready.out.resolve() / "g6_homes" and not home.exists() for home, _, _ in seen)
    assert cloud.ledger_path().read_bytes() and not (ready.home / "left_by_this_session.json").exists()
    with pytest.raises(freeze.FrozenError, match="task id and repeat"):
        run.trial_home(ready.out, "../elsewhere")
    # the person's ledger without its checkpoint cannot be read: no home is made from it and nothing is started
    (ready.home / "cloud_consent" / "head").unlink()
    with pytest.raises(cloud.LLMError, match=cloud.STATE_UNAVAILABLE):
        run.trial_home(ready.out, "a.0")
    client = StandIn()
    done = run.run(ready.path, ready.out, client="codex", cohort="development", runner=client, repeat=2)
    assert client.started == 0 and {(t.state, t.code) for t in done} == {("refused", cloud.STATE_UNAVAILABLE)}


def test_a_trial_home_reads_as_the_persons_own_decisions_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    for source in ("routing-fixture.shop", "routing-fixture.notes"):
        assert cloud._grant(source, "openai:codex", cloud.preview(source, "openai:codex")[1], "a person") == "granted"
    cloud.revoke("routing-fixture.notes", "openai:codex")
    own = {source: cloud.preview(source, "openai:codex") for source in ("routing-fixture.shop", "routing-fixture.notes",
                                                                        "routing-fixture.other")}
    home = run.trial_home(tmp_path / "out", "task.0")
    assert (home / "cloud_consent" / "events.jsonl").read_bytes() == cloud.ledger_path().read_bytes()
    assert (home / "cloud_consent" / "head").read_bytes() == cloud.ledger_path().with_name("head").read_bytes()
    monkeypatch.setenv("G6_HOME", str(home))
    assert {source: cloud.preview(source, "openai:codex") for source in own} == own
    assert own["routing-fixture.shop"][0] is True and own["routing-fixture.notes"][0] is False
    monkeypatch.setenv("G6_HOME", str(tmp_path / "empty"))
    fresh = run.trial_home(tmp_path / "out", "task.1")
    assert fresh.is_dir() and not (fresh / "cloud_consent").exists()


def test_the_trial_server_serves_only_the_g6_package_the_tasks_are_bound_to(ready: SimpleNamespace,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    assert server.BOUND_ENV == run.BOUND_ENV and fnmatch.fnmatch(server.SERVED.format(server="g6"), run.SERVED)
    bound = run.package_digest()
    assert server.served_package() == bound == freeze.tree_digest(Path(sys.modules["g6"].__file__).resolve().parent)
    task = next(task for task in run.load_tasks()["tasks"] if len(task.get("servers", ())) > 1)
    assert {name: entry["env"][run.BOUND_ENV] for name, entry in run.mcp_config(task)["mcpServers"].items()} \
        == {name: bound for name in task["servers"]}
    home = ready.out / "home"
    home.mkdir(parents=True)
    assert server.admit("g6", {run.BOUND_ENV: bound, "G6_HOME": str(home)}) == (bound, True)
    assert server.admit("abstraction", {run.BOUND_ENV: "0" * 64, "G6_HOME": str(home)}) == (bound, False)
    assert server.admit("by_hand", {}) == (bound, True)      # a server started by hand is handed no digest
    assert run._served(home) == {"g6": {"g6": bound, "serves": True}, "abstraction": {"g6": bound, "serves": False}}
    (home / "routing-served-odd.json").write_text("not json", encoding="utf-8")
    assert set(run._served(home)) == {"g6", "abstraction"} and run._served(None) == {}
    # the server's own entry point: another package serves nothing
    monkeypatch.setattr(server, "build", lambda *_: pytest.fail("a server of another g6 package was built"))
    monkeypatch.setattr(sys, "argv", ["server.py", "g6"])
    monkeypatch.setenv(run.BOUND_ENV, "0" * 64)
    with pytest.raises(SystemExit, match="nothing is served"):
        server.main()


def test_a_sessions_record_says_which_g6_its_servers_found(ready: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in that does what the client does with the session's configuration: it starts the task's server,
    here by calling the server's own admission with the environment the configuration gives it."""
    def serves(argv: list[str]) -> None:
        server.admit("g6", _env(argv))

    run.run(ready.path, ready.out, client="codex", cohort="development", runner=StandIn(during=serves))
    sessions = run.read_sessions(ready.out, "codex", "development", run.load_tasks(ready.path))
    bound = run.package_digest()
    assert [s.record["served"] for s in sessions] == [{"g6": {"g6": bound, "serves": True}}] * len(KEPT)
    assert [s.fault for s in sessions] == ["", ""]
    assert all(s.record["bound"]["g6"] == bound and s.record["run"] for s in sessions)
    reading = run.gate_reading(ready.out, client="codex", cohort="development", tasks_path=ready.path)
    assert reading["evidence"]["sessions_with_a_server_record"] == len(KEPT) and reading["evidence"]["g6_digest"] == bound
    monkeypatch.setattr(server, "served_package", lambda: "0" * 64)      # an interpreter whose g6 is another tree
    run.run(ready.path, ready.out, client="codex", cohort="development", runner=StandIn(during=serves), repeat=1)
    other = [s for s in run.read_sessions(ready.out, "codex", "development", run.load_tasks(ready.path))
             if s.transcript.repeat == 1]
    assert [s.record["served"] for s in other] == [{"g6": {"g6": "0" * 64, "serves": False}}] * len(KEPT)
    assert all("another g6 package" in s.fault for s in other)


@pytest.fixture()
def guidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A copy of everything ``route_digests`` reads from files (the skill folder a workspace gets, every route's
    detail files, the fixtures), in a folder the test may edit; the manifest and the servers stay the shipped ones."""
    from g6.core import skills
    from g6.mcp import registrations
    from g6.skills import routing

    real, copy = registrations.package_root(), tmp_path / "g6"
    shutil.copytree(skills.guide_dir(), copy / "skills" / skills.GUIDE, ignore=shutil.ignore_patterns("__pycache__"))
    for path in {path for route in routing.load().routes.values() for path in route.detail}:
        (copy / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real / path, copy / path)
    shutil.copytree(run.FIXTURES, tmp_path / "fixtures", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(registrations, "package_root", lambda: copy)
    monkeypatch.setattr(skills, "guide_dir", lambda: copy / "skills" / skills.GUIDE)
    monkeypatch.setattr(run, "FIXTURES", tmp_path / "fixtures")
    before = run.route_digests()
    assert before == freeze.read(run.TASKS)["routes"], "the copy does not bind as the tree does"
    # the servers are built once for the file and line inventories; the test of what they show reads them live
    live = SimpleNamespace(shown=run._shown, guidance=registrations.guidance)
    spoken = {key: text for key, text in registrations.guidance().items() if key.startswith("help:")}
    found = run._shown()
    monkeypatch.setattr(run, "_shown", lambda: found)
    monkeypatch.setattr(registrations, "guidance", lambda repo=None: spoken)
    assert run.route_digests() == before
    return SimpleNamespace(package=copy, fixtures=tmp_path / "fixtures", before=before, routes=routing.load().routes,
                           root=copy / routing.ROOT, live=live)


def _moved(guidance: SimpleNamespace) -> set[str]:
    after = run.route_digests()
    return {key for key in set(after) | set(guidance.before) if after.get(key) != guidance.before.get(key)}


def test_every_file_a_session_reads_is_bound_by_a_digest(guidance: SimpleNamespace) -> None:
    """Every file the workspace gets or a route links, changed in turn: a route's own detail file moves that route's
    digest and no other's; every other file moves the digest of what every session reads; none moves nothing."""
    assert set(guidance.before) == set(guidance.routes) | {run.EVERY_ROUTE}
    owners: dict[str, set[str]] = {}
    for route_id, route in guidance.routes.items():
        for path in route.detail:
            owners.setdefault(path, set()).add(route_id)
    files = sorted(path for folder in (guidance.package, guidance.fixtures) for path in folder.rglob("*") if path.is_file())
    assert len(files) > 15
    for path in files:
        if path == guidance.root:
            continue                                         # line by line, below
        kept = path.read_bytes()
        path.write_bytes(kept + b"\nchanged\n")
        inside = path.relative_to(guidance.package).as_posix() if guidance.package in path.parents else ""
        assert _moved(guidance) == owners.get(inside, {run.EVERY_ROUTE}), path.name
        path.write_bytes(kept)
    assert _moved(guidance) == set()
    extra = guidance.package / "skills" / "using-g6" / "references" / "new.md"
    extra.write_text("Call g6_invoke on every task.\n", encoding="utf-8")       # a file added to the skill folder
    assert _moved(guidance) == {run.EVERY_ROUTE}


def test_every_line_of_the_root_skill_is_bound_by_a_digest(guidance: SimpleNamespace) -> None:
    from g6.skills import routing

    text = guidance.root.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections = {route_id: routing.section(text, route.anchor) for route_id, route in guidance.routes.items()}
    assert all(sections.values())
    unowned = 0
    for index, line in enumerate(lines):
        guidance.root.write_text("\n".join([*lines[:index], line + " (changed)", *lines[index + 1:]]) + "\n",
                                 encoding="utf-8", newline="\n")
        moved = _moved(guidance)
        assert moved, f"line {index + 1} of the root skill can change and every digest stays: {line!r}"
        owned = {route_id for route_id, said in sections.items() if line in said.splitlines()}
        if line.strip() and not owned:
            assert moved == {run.EVERY_ROUTE}, line
            unowned += 1
    assert unowned > 10                                      # the front matter, the absent-tool rule, "Read and ask"
    guidance.root.write_text(text, encoding="utf-8", newline="\n")
    assert _moved(guidance) == set()


def test_what_the_servers_show_and_say_is_bound_by_a_digest(guidance: SimpleNamespace,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import discovery
    from g6.mcp import registrations
    from g6.skills import routing

    monkeypatch.setattr(run, "_shown", guidance.live.shown)
    monkeypatch.setattr(registrations, "guidance", guidance.live.guidance)
    entries, shown, schemas = routing.load().entries, registrations.shown(), registrations._all()
    assert {f"tool:{server}/{tool}" for server, tools in schemas.items() for tool in tools} >= \
        {key for key in shown if key.startswith("tool:")} and any(key.startswith("server:") for key in shown)

    def owner(server_id: str, tool: str) -> str:
        entry = entries[f"mcp:{server_id}/{tool}"]
        return entry.route if entry.agent_facing else run.EVERY_ROUTE

    for key in sorted(shown):                                # each text a server shows, changed in turn
        with monkeypatch.context() as patch:
            patch.setattr(registrations, "shown", lambda servers=None, key=key: {**shown, key: shown[key] + " Always."})
            patch.setattr(registrations, "_all", lambda servers=None: schemas)
            kind, _, name = key.partition(":")
            assert _moved(guidance) == {run.EVERY_ROUTE if kind == "server" else owner(*name.split("/", 1))}, key
    for server_id, tools in sorted(schemas.items()):         # and each tool's input schema
        for tool in sorted(tools):
            changed = {**schemas, server_id: {**tools, tool: {**tools[tool], "added": True}}}
            with monkeypatch.context() as patch:
                patch.setattr(registrations, "shown", lambda servers=None: shown)
                patch.setattr(registrations, "_all", lambda servers=None, changed=changed: changed)
                assert _moved(guidance) == {owner(server_id, tool)}, tool
    monkeypatch.setattr(discovery, "HELP", "Call g6_invoke on every task, whatever it is.")
    assert _moved(guidance) == {entries["mcp:g6/g6_help"].route}


@pytest.mark.parametrize("how", ["G6_STOP", "stop_file", "in_process", "G6_DISABLE_LLM"])
def test_nothing_is_started_and_nothing_is_spent_while_g6_is_stopped(ready: SimpleNamespace,
                                                                      monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import stop as core_stop

    if how == "stop_file":
        core_stop.stop_file().write_text("stopped by the person\n", encoding="utf-8")
    elif how == "in_process":
        core_stop.engage("stopped by the test")
    else:
        monkeypatch.setenv(how, "1")
    client = StandIn()
    try:
        with pytest.raises(AgentRefusal) as refused:
            run.run(ready.path, ready.out, client="codex", cohort="development", runner=client)
    finally:
        core_stop.release()
    assert refused.value.code == ("G6_E_LLM_DISABLED" if how == "G6_DISABLE_LLM" else core_stop.STOPPED)
    assert refused.value.detail == {"started": False} and client.started == 0 and not ready.out.joinpath("events").exists()
    assert run.read_transcripts(ready.out, "codex", "development") == []


def test_a_stop_engaged_during_a_run_starts_no_further_session_and_the_runner_holds_it(ready: SimpleNamespace) -> None:
    from g6.core import stop as core_stop

    client = StandIn(during=lambda argv: core_stop.engage("stopped by the person"))
    try:
        done = run.run(ready.path, ready.out, client="codex", cohort="development", runner=client)
    finally:
        core_stop.release()
    assert client.started == 1 and client.stops == [core_stop.engaged]          # the runner was handed G6's stop
    assert [(t.state, t.code) for t in done] == [("completed", ""), ("refused", core_stop.STOPPED)]
    assert done[1].events_sha256 == "" and done[1].exit_code is None


def test_the_adapters_and_the_runner_start_a_client_through_one_helper(monkeypatch: pytest.MonkeyPatch,
                                                                       tmp_path: Path) -> None:
    from g6.agents._runtime import consent
    from g6.agents._runtime.model import AgentRefusal
    from g6.agents.agent_claude import adapter as claude
    from g6.agents.agent_codex import adapter as codex
    from g6.core import cloud_consent as cloud
    from g6.core import stop as core_stop

    assert claude.consent_run is codex.consent_run is consent.consent_run
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    calls: list[dict[str, Any]] = []

    class Takes:
        def run(self, argv: list[str], *, prompt: str, cwd: Path, stop: Any = None) -> str:
            calls.append({"stop": stop})
            return "ran"

    class TakesNone:
        def run(self, argv: list[str], *, prompt: str, cwd: Path) -> str:
            calls.append({})
            return "ran"

    def start(runner: Any, **more: Any) -> Any:
        source = cloud.Source.text("routing-fixture.shop", "t", "x")
        for name in ("agent_environment", "routing-fixture.shop", cloud.repository_source(tmp_path)):
            cloud._grant(name, "openai:codex", cloud.preview(name, "openai:codex")[1], "a person")
        with cloud.request_sources((source,)):
            return consent.consent_run(runner, ["codex"], provider="openai:codex", prompt="p", cwd=tmp_path, **more)

    mine = lambda: None  # noqa: E731
    assert start(Takes()) == start(TakesNone()) == start(Takes(), stop=mine) == "ran"
    assert calls == [{"stop": core_stop.engaged}, {}, {"stop": mine}]
    for engage, code in ((lambda: monkeypatch.setenv("G6_DISABLE_LLM", "1"), "G6_E_LLM_DISABLED"),
                         (lambda: (monkeypatch.delenv("G6_DISABLE_LLM"), monkeypatch.setenv("G6_STOP", "1")),
                          core_stop.STOPPED)):
        engage()
        for runner in (Takes(), TakesNone()):
            with pytest.raises(AgentRefusal) as refused:
                start(runner)
            assert refused.value.code == code and refused.value.detail == {"started": False}
    monkeypatch.delenv("G6_STOP")
    with pytest.raises(AgentRefusal) as refused:
        start(Takes(), stop=lambda: "the caller's own stop")
    assert refused.value.code == core_stop.STOPPED and len(calls) == 3


def test_what_produced_a_session_names_the_program_and_no_folder(tmp_path: Path, ready: SimpleNamespace) -> None:
    program = tmp_path / "bin with spaces" / "codex.cmd"
    program.parent.mkdir()
    program.write_bytes(b"@echo off\n")
    detected = SimpleNamespace(detail=f"codex-cli 1.2.3 ({program.parent}) {Path.home()}")
    said = run._produced(str(program), detected, {})
    assert said.startswith("client:codex.cmd codex-cli 1.2.3") and said.endswith(
        "sha256:" + __import__("hashlib").sha256(b"@echo off\n").hexdigest())
    assert str(program.parent) not in said and str(Path.home()) not in said and score.named_client(said)
    assert run._produced("codex", SimpleNamespace(detail="codex-cli 0.0.0"), {}) == "client:codex codex-cli 0.0.0"
    run.run(ready.path, ready.out, client="codex", cohort="development", runner=StandIn())
    log = (ready.out / "transcripts-codex-development.jsonl").read_text(encoding="utf-8")
    for private in (str(tmp_path), str(Path.home()), str(ROOT)):
        assert private not in log and json.dumps(private)[1:-1] not in log
