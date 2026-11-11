# SPDX-License-Identifier: Apache-2.0
"""Running the routing trials (0.3 design 14.1): one fresh agent session per task, on a copy of a synthetic
repository, with G6's MCP server and the installed ``using-g6`` skill.

What is sent, and where: each session sends the task's prompt, the fixture repository the agent reads, and G6's own
guidance to the client's provider (``anthropic:claude_code`` or ``openai:codex``). Every session passes section 16's
one consent check first (``g6.agents._runtime.consent.consent_run``), with the fixture declared as a source; with no
grant nothing is started and the task's transcript is ``refused`` with the consent code. The fixtures are synthetic:
no user data is in them. ``sources`` lists exactly what a person grants before a run.

What a transcript is evidence of: a session is ``completed`` only when the client exited with code 0 and printed its
own terminal event; anything else is ``interrupted``. Each transcript records how it was produced (the client program
and its version, or the stand-in that took its place), the exit code and the digest of the event log. The log holds
one transcript per task and repeat: a second run into the same log is refused. Nothing runs, and nothing is scored,
on a gate file or a task list that is not the pinned one (``frozen.json``).

The two clients' command lines and event parsers below are written from each client's documented interface. No
session was started when this was built, so neither has been observed: README.md, "Before the first run".

    python validation/routing/run.py sources --client codex --out <folder>
    python validation/routing/run.py run --client codex --cohort development --out <folder>
    python validation/routing/run.py score --client codex --cohort development --out <folder>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
TASKS, FINAL_TASKS, GATES, FIXTURES = HERE / "tasks.json", HERE / "final_tasks.json", HERE / "gates.json", HERE / "fixtures"
#: The pinned digests: of the bars (the gate file with no final cohort bound) and of the development task list.
PINS = HERE / "frozen.json"
#: Tools that put a question to the person; a call of one is counted as a question asked.
DIALOG_TOOLS = frozenset({"g6_confirm_behaviour", "g6_mission_confirm", "g6_mission_create", "g6_cloud_grant",
                          "g6_improve_start", "metaharness_confirm", "metaharness_approve", "metaharness_rollback"})
WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"
#: The setting the server reads its host permissions from (``g6.core.door.SENSITIVE_ENV``).
SENSITIVE_ENV = "G6_MCP_SENSITIVE"
GRACE_S = 60.0
UNFINISHED = "G6_E_ROUTING_SESSION_UNFINISHED"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = sys.modules.get("g6_validation_routing_score") or _load("g6_validation_routing_score", HERE / "score.py")
freeze = sys.modules.get("g6_validation_freeze") or _load("g6_validation_freeze", HERE.parent / "freeze.py")
Transcript = score.Transcript


def route_digests(manifest: Any = None) -> dict[str, str]:
    """Route id -> the digest that binds a route: its anchor, its members, what its own section of the root skill
    says and the bytes of each of its detail files. Adding a tool to a route, moving its anchor or changing what the
    route tells an agent changes that route's digest and no other's."""
    from g6.mcp import registrations
    from g6.skills import routing

    manifest = manifest or routing.load()
    package = registrations.package_root()
    root = (package / routing.ROOT).read_text(encoding="utf-8")
    out = {}
    for route_id, route in manifest.routes.items():
        said = routing.section(root, route.anchor) or ""
        out[route_id] = freeze.digest({
            "id": route_id, "anchor": route.anchor, "detail": list(route.detail),
            "members": sorted(key for key, entry in manifest.entries.items() if entry.route == route_id),
            "section": hashlib.sha256(said.encode("utf-8")).hexdigest(),
            "detail_bytes": {path: hashlib.sha256((package / path).read_bytes()).hexdigest() if (package / path).is_file()
                             else "" for path in route.detail}})
    return out


def pins() -> dict[str, str]:
    found = freeze.read(PINS)
    if set(found) != {"bars_digest", "tasks_digest"}:
        raise freeze.FrozenError("frozen.json holds exactly bars_digest and tasks_digest")
    return found


def load_gates() -> dict[str, Any]:
    """The gate file, when its bars are the pinned ones (``freeze.bound``, the final cohort's binding aside); a gate
    file whose bars changed after the freeze is refused."""
    return score.validate_gates(freeze.bound(GATES, pins()["bars_digest"], what="the routing bars",
                                             mask={"final_tasks_digest": None}))


def load_tasks(path: Path = TASKS, *, cohort: str = "development") -> dict[str, Any]:
    """The task list at ``path``, checked: inside its format; every route bound to the manifest and the guidance as
    they stand; for the development cohort, every task the pinned list's own (a list may hold fewer tasks, never a
    changed one); for the final cohort, the whole list bound by the gate file. ``FrozenError`` or ``TaskError``
    otherwise; nothing runs on a list that does not bind."""
    tasks = score.validate_tasks(freeze.read(path))
    now = route_digests()
    for route_id, bound in tasks["routes"].items():
        if now.get(route_id) != bound:
            raise freeze.FrozenError(f"route {route_id!r} changed since its tasks were written (or is gone): review "
                                     "its tasks, then bind its new digest")
    if cohort == "final":
        gates = load_gates()
        if gates["final_tasks_digest"] is None or gates["final_tasks_digest"] != freeze.digest(tasks):
            raise freeze.FrozenError("the final cohort is not frozen: gates.json does not bind this task list")
    else:
        frozen = {task["id"]: task for task in freeze.bound(TASKS, pins()["tasks_digest"],
                                                           what="the development task list")["tasks"]}
        for task in tasks["tasks"]:
            if frozen.get(task["id"]) != task:
                raise freeze.FrozenError(f"task {task['id']} is not the frozen development list's own task")
    return tasks


@dataclass(frozen=True)
class Client:
    name: str
    provider: str
    skills_dir: str                                                    # where the client reads skills, in the workspace
    program: Callable[[], Any]                                         # its OutsideProgram, as G6's adapter declares it
    argv: Callable[[str, Path, Mapping[str, Any]], list[str]]          # (program path, mcp config file, config) -> argv
    parse: Callable[[str], tuple[list[dict[str, Any]], str, float | None]]   # event text -> calls, final text, cost
    finished: Callable[[str], bool]                                    # whether the events hold the client's terminal event


def _events(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _tool(name: str) -> tuple[str, str]:
    """``mcp__<server>__<tool>`` as Claude Code names an MCP tool -> (server, tool)."""
    _, server, tool = name.split("__", 2)
    return server, tool


def _state(content: Any) -> str:
    """The ``completion_state`` of a tool result, when its text is a JSON object that carries one."""
    texts = [content] if isinstance(content, str) else [part.get("text", "") for part in content or ()
                                                        if isinstance(part, dict)]
    for text in texts:
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("completion_state"), str):
            return value["completion_state"]
    return ""


def parse_claude(text: str) -> tuple[list[dict[str, Any]], str, float | None]:
    """Calls, final text and reported cost from ``claude -p --output-format stream-json --verbose``: one JSON event a
    line; ``assistant`` messages carry ``tool_use`` parts, ``user`` messages their ``tool_result`` (``is_error`` for a
    refused or malformed call), and the last ``result`` event the final text and ``total_cost_usd``."""
    calls: dict[str, dict[str, Any]] = {}
    final, cost = "", None
    for event in _events(text):
        parts = (event.get("message") or {}).get("content") if isinstance(event.get("message"), dict) else None
        for part in parts if isinstance(parts, list) else ():
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and str(part.get("name", "")).startswith("mcp__"):
                server, tool = _tool(part["name"])
                calls[str(part.get("id"))] = {"server": server, "tool": tool, "args": part.get("input") or {},
                                             "seconds": None, "valid": True, "result_state": ""}
            elif part.get("type") == "tool_result" and str(part.get("tool_use_id")) in calls:
                call = calls[str(part["tool_use_id"])]
                call["valid"] = not part.get("is_error", False)
                call["result_state"] = _state(part.get("content"))
        if event.get("type") == "result":
            final = str(event.get("result") or "")
            cost = event.get("total_cost_usd") if isinstance(event.get("total_cost_usd"), (int, float)) else None
    return list(calls.values()), final, cost


def finished_claude(text: str) -> bool:
    """Claude Code's terminal event: a ``result`` event that is not an error."""
    return any(event.get("type") == "result" and not event.get("is_error", False) for event in _events(text))


def parse_codex(text: str) -> tuple[list[dict[str, Any]], str, float | None]:
    """Calls and final text from ``codex exec --json``: one JSON event a line; a completed item of type
    ``mcp_tool_call`` carries ``server``, ``tool``, ``arguments`` and ``status``, and one of type ``agent_message``
    the text. Codex reports tokens, not a cost, so the cost is ``None`` (never estimated)."""
    calls, final = [], ""
    for event in _events(text):
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        if item.get("type") == "mcp_tool_call":
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            calls.append({"server": str(item.get("server", "")), "tool": str(item.get("tool", "")),
                          "args": item.get("arguments") or {}, "seconds": None,
                          "valid": item.get("status") == "completed" and not item.get("error"),
                          "result_state": _state(result.get("content"))})
        elif item.get("type") == "agent_message":
            final = str(item.get("text") or "")
    return calls, final, None


def finished_codex(text: str) -> bool:
    """Codex's terminal event: ``turn.completed``, or the agent's completed final message."""
    return any(event.get("type") == "turn.completed"
               or (event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
                   and event["item"].get("type") == "agent_message") for event in _events(text))


def _claude_argv(program: str, config_file: Path, config: Mapping[str, Any]) -> list[str]:
    servers = [f"mcp__{name}" for name in config["mcpServers"]]
    return [program, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "default",
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(config_file),
            "--allowedTools", *servers, "Read", "Glob", "Grep", "Edit", "Write", "Skill",
            "--disallowedTools", "Bash", "BashOutput", "KillShell", "PowerShell", "WebFetch", "WebSearch", "Task",
            "Agent", "--max-turns", "30"]


def _codex_argv(program: str, config_file: Path, config: Mapping[str, Any]) -> list[str]:
    argv = [program, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
            "--color", "never"]
    for name, server in config["mcpServers"].items():
        argv += ["-c", f"mcp_servers.{name}.command={json.dumps(server['command'])}",
                 "-c", f"mcp_servers.{name}.args={json.dumps(server['args'])}"]
        for key, value in server.get("env", {}).items():
            argv += ["-c", f"mcp_servers.{name}.env.{key}={json.dumps(value)}"]
    return [*argv, "-"]


def _claude_program() -> Any:
    from g6.core import host_models
    return host_models.CLAUDE


def _codex_program() -> Any:
    from g6.core import host_models
    return host_models.CODEX


CLIENTS: dict[str, Client] = {
    "claude_code": Client("claude_code", "anthropic:claude_code", ".claude/skills", _claude_program, _claude_argv,
                          parse_claude, finished_claude),
    "codex": Client("codex", "openai:codex", ".codex/skills", _codex_program, _codex_argv, parse_codex,
                    finished_codex),
}


def mcp_config(task: Mapping[str, Any], python: str = sys.executable, home: Path | None = None) -> dict[str, Any]:
    """The servers one session gets: each of the task's servers, started through ``server.py`` so the task's withheld
    tools are absent from what the client lists, with the host permissions the task states (and no other), and, when
    ``home`` is given, with that folder as its G6 home so a session never touches the person's own G6 state."""
    env = {WITHHOLD_ENV: ",".join(task.get("withhold", ()))}
    if task.get("permissions"):
        env[SENSITIVE_ENV] = ",".join(task["permissions"])
    if home is not None:
        env["G6_HOME"] = str(home)
    return {"mcpServers": {name: {"command": python, "args": ["-P", str(HERE / "server.py"), name], "env": dict(env)}
                           for name in task.get("servers", ["g6"])}}


def trial_home(out_dir: Path) -> Path:
    """The G6 home the trial's servers use: a folder of the run, holding a copy of the person's sharing decisions (so
    a granted source is not withheld from the session) and nothing else of theirs."""
    from g6.core import cloud_consent as cloud
    from g6.core.storage import g6_home

    home = Path(out_dir).resolve() / "g6_home"
    ledger = cloud.ledger_path()
    if ledger.is_file():
        target = home / ledger.relative_to(g6_home())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger, target)
    home.mkdir(parents=True, exist_ok=True)
    return home


def workspace_for(task: Mapping[str, Any], out_dir: Path) -> Path:
    """Where a task's fixture is copied: one folder per fixture, emptied before each session, so the repository a
    person grants is the same from task to task."""
    return Path(out_dir).resolve() / "work" / task["fixture"]


def _writable(function: Any, path: str, _error: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def prepare(task: Mapping[str, Any], out_dir: Path, client: Client) -> Path:
    """A fresh copy of the task's fixture as a clean git repository with the skill installed where the client reads
    it (the folder ``g6 skills install --client <client>`` writes), committed so the tree starts clean."""
    from g6.core import skills

    workspace = workspace_for(task, out_dir)
    if workspace.exists():
        shutil.rmtree(workspace, onexc=_writable)      # git's object files are read-only on Windows
    shutil.copytree(FIXTURES / task["fixture"], workspace)
    shutil.copytree(skills.guide_dir(), workspace / client.skills_dir / skills.GUIDE,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=routing-trial", "-c", "user.email=routing-trial@example.invalid", "commit", "-q",
                  "-m", "fixture"]):
        subprocess.run(["git", *argv], cwd=workspace, check=True, capture_output=True)
    return workspace


def fixture_sources(task: Mapping[str, Any]) -> tuple[Any, ...]:
    """The fixture a task reads, declared as one source: ``routing-fixture.<name>``, with the prompt and every fixture
    file's bytes in its content digest."""
    from g6.core import cloud_consent as cloud

    root = FIXTURES / task["fixture"]
    content = freeze.digest({"prompt": task["prompt"],
                             "files": {path.relative_to(root).as_posix(): cloud.sha256(path.read_bytes())
                                       for path in sorted(root.rglob("*")) if path.is_file()}})
    return (cloud.Source.text(f"routing-fixture.{task['fixture']}", task["id"], content),)


def sources(tasks: Mapping[str, Any], out_dir: Path, client: str, cohort: str) -> list[dict[str, str]]:
    """What a person grants before a run, per provider: the client's own environment, each fixture, and each
    workspace folder the agent can read."""
    from g6.core import cloud_consent as cloud

    provider = CLIENTS[client].provider
    wanted = {"agent_environment": "the client's own configuration and whatever else it reads"}
    for task in tasks["tasks"]:
        if task["cohort"] == cohort:
            wanted[f"routing-fixture.{task['fixture']}"] = f"the synthetic fixture {task['fixture']} and its prompts"
            workspace = workspace_for(task, out_dir)
            workspace.mkdir(parents=True, exist_ok=True)
            wanted[cloud.repository_source(workspace)] = f"the workspace folder {workspace}"
    return [{"source": source, "provider": provider, "what": what} for source, what in sorted(wanted.items())]


def _log(out_dir: Path, client: str, cohort: str) -> Path:
    return Path(out_dir) / f"transcripts-{client}-{cohort}.jsonl"


def _record(log: Path, transcript: Any) -> None:
    """The one place a row enters a transcript log."""
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(transcript.to_json(), sort_keys=True) + "\n")


def run(tasks_path: Path, out_dir: Path, *, client: str, cohort: str, runner: Any = None, repeat: int = 0,
        python: str = sys.executable) -> list[Any]:
    """One fresh session per task of ``cohort`` for ``client``; each transcript is appended to
    ``<out_dir>/transcripts-<client>-<cohort>.jsonl`` as it ends, and all are returned.

    A session is ``completed`` only on evidence that it finished: the client exited with code 0 and its events hold
    its own terminal event. A consent refusal is ``refused`` with its code and starts nothing; a client that is not
    installed is ``unavailable``; a deadline, a failed start, a non-zero exit and an event log with no terminal event
    are ``interrupted``. A log that already holds a transcript for one of these tasks and this ``repeat`` refuses the
    run before anything starts: a gate run happens once. ``runner`` stands in for the client in tests; its
    transcripts say so (``produced``), and no stand-in's transcript can pass a gate's release reading."""
    from g6.agents._runtime.cli import OUTPUT_LOG, CliAgentRunner
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as cloud
    from g6.core import outside

    spec = CLIENTS[client]
    tasks = load_tasks(Path(tasks_path), cohort=cohort)
    scheduled = sorted((t for t in tasks["tasks"] if t["cohort"] == cohort), key=lambda t: t["id"])
    out_dir = Path(out_dir)
    held = {(t.task_id, t.repeat) for t in read_transcripts(out_dir, client, cohort)}
    again = sorted(task["id"] for task in scheduled if (task["id"], repeat) in held)
    if again:
        raise freeze.FrozenError(f"this log already holds a transcript (repeat {repeat}) for {again[:3]}: a run is "
                                 "made once; another run needs its own folder or repeat number")
    real = runner is None
    runner = runner or CliAgentRunner(spec.program())
    out_dir.mkdir(parents=True, exist_ok=True)
    log, home = _log(out_dir, client, cohort), trial_home(out_dir)
    done: list[Any] = []
    for task in scheduled:
        base = {"task_id": task["id"], "client": client, "repeat": repeat,
                "produced": "" if real else f"stand-in:{type(runner).__name__}"}
        try:
            program = runner.locate() if hasattr(runner, "locate") else spec.name
            if real:
                base["produced"] = f"client:{program} {getattr(runner.require(), 'detail', '')}".strip()
            workspace = prepare(task, out_dir, spec)
            config = mcp_config(task, python, home)
            with tempfile.TemporaryDirectory(prefix="g6_routing_", ignore_cleanup_errors=True) as tmp:
                scratch = Path(tmp)
                config_file = scratch / "mcp.json"
                config_file.write_text(json.dumps(config), encoding="utf-8")
                with cloud.request_sources(fixture_sources(task)):
                    ended = consent_run(runner, spec.argv(program, config_file, config), provider=spec.provider,
                                        prompt=task["prompt"], cwd=workspace,
                                        seconds=task["budget"]["seconds"] + GRACE_S, scratch=scratch)
                events = (scratch / OUTPUT_LOG).read_text(encoding="utf-8", errors="replace") \
                    if (scratch / OUTPUT_LOG).is_file() else str(getattr(ended, "output_tail", ""))
            calls, final, cost = spec.parse(events)
            exit_code = getattr(ended, "exit_code", None)
            finished = exit_code == 0 and spec.finished(events)
            transcript = Transcript(
                state="completed" if finished else "interrupted", code="" if finished else UNFINISHED,
                calls=tuple(calls), final_text=final, cost_usd=cost, exit_code=exit_code,
                events_sha256=hashlib.sha256(events.encode("utf-8")).hexdigest(),
                questions=sum(call["tool"] in DIALOG_TOOLS for call in calls),
                seconds=float(getattr(ended, "elapsed_s", 0.0)), **base)
        except AgentRefusal as refusal:
            consent = refusal.code in (cloud.REQUIRED, cloud.STATE_UNAVAILABLE)
            missing = refusal.code == outside.OUTSIDE_PROGRAM_MISSING
            transcript = Transcript(state="refused" if consent else "unavailable" if missing else "interrupted",
                                    code=refusal.code, **base)
        _record(log, transcript)
        done.append(transcript)
    return done


def read_transcripts(out_dir: Path, client: str, cohort: str) -> list[Any]:
    log = _log(out_dir, client, cohort)
    lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    return [Transcript.from_json(json.loads(line)) for line in lines if line.strip()]


def schemas() -> dict[str, dict[str, Any]]:
    """``<server>/<tool>`` -> its registered input schema, for the scorer's offline check of each call."""
    from g6.mcp import registrations

    return {f"{server}/{tool}": schema for server, tools in registrations._all().items()
            for tool, schema in tools.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("sources", "run", "score"))
    parser.add_argument("--client", choices=sorted(CLIENTS), required=True)
    parser.add_argument("--cohort", choices=score.COHORTS, default="development")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=0)
    args = parser.parse_args(argv)
    path = FINAL_TASKS if args.cohort == "final" else TASKS
    if args.command == "sources":
        print(json.dumps(sources(load_tasks(path, cohort=args.cohort), args.out, args.client, args.cohort), indent=2))
    elif args.command == "run":
        states = [t.state for t in run(path, args.out, client=args.client, cohort=args.cohort, repeat=args.repeat)]
        print(json.dumps({state: states.count(state) for state in score.STATES}))
    else:
        # the gate's reading: the pinned bars, the bound task list, and only rows a real client produced can pass
        report = score.score(load_tasks(path, cohort=args.cohort), read_transcripts(args.out, args.client, args.cohort),
                             load_gates(), client=args.client, cohort=args.cohort, schemas=schemas(),
                             require_client=True)
        target = args.out / f"report-{args.client}-{args.cohort}.json"
        target.write_text(freeze.render(report), encoding="utf-8", newline="\n")
        print(json.dumps({"decision": report["decision"], "gates": report["gates"], "report": str(target)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
