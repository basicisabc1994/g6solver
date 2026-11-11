# SPDX-License-Identifier: Apache-2.0
"""Running the routing trials (0.3 design 14.1): one fresh agent session per task, on a copy of a synthetic
repository, with G6's MCP server and the installed ``using-g6`` skill.

What is sent, and where: each session sends the task's prompt, the fixture repository the agent reads, and G6's own
guidance to the client's provider (``anthropic:claude_code`` or ``openai:codex``). Every session passes section 16's
one consent check first (``g6.agents._runtime.consent.consent_run``), with the fixture declared as a source; with no
grant nothing is started and the task's transcript is ``refused`` with the consent code. The fixtures are synthetic:
no user data is in them. ``sources`` lists exactly what a person grants before a run.

The two clients' command lines and event parsers below are written from each client's documented interface. No
session was started when this was built, so neither has been observed: README.md, "Before the first run".

    python validation/routing/run.py sources --client codex --out <folder>
    python validation/routing/run.py run --client codex --cohort development --out <folder>
    python validation/routing/run.py score --client codex --cohort development --out <folder>
"""
from __future__ import annotations

import argparse
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
#: Tools that put a question to the person; a call of one is counted as a question asked.
DIALOG_TOOLS = frozenset({"g6_confirm_behaviour", "g6_mission_confirm", "g6_mission_create", "g6_cloud_grant",
                          "g6_improve_start", "metaharness_confirm", "metaharness_approve", "metaharness_rollback"})
WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"
GRACE_S = 60.0


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
    """Route id -> the digest that binds a route: its anchor, its detail files and its members. Adding a tool to a
    route, or moving its anchor, changes that route's digest and no other's."""
    from g6.skills import routing

    manifest = manifest or routing.load()
    return {route_id: freeze.digest({
        "id": route_id, "anchor": route.anchor, "detail": list(route.detail),
        "members": sorted(key for key, entry in manifest.entries.items() if entry.route == route_id)})
        for route_id, route in manifest.routes.items()}


def load_tasks(path: Path = TASKS, *, cohort: str = "development") -> dict[str, Any]:
    """The task list at ``path``, checked: inside its format, every route bound to the manifest as it stands, and, for
    the final cohort, frozen by the gate file. ``FrozenError`` or ``TaskError`` otherwise; nothing runs on a list that
    does not bind."""
    tasks = score.validate_tasks(freeze.read(path))
    now = route_digests()
    for route_id, bound in tasks["routes"].items():
        if now.get(route_id) != bound:
            raise freeze.FrozenError(f"route {route_id!r} changed since its tasks were written (or is gone): review "
                                     "its tasks, then bind its new digest")
    if cohort == "final":
        gates = score.validate_gates(freeze.read(GATES))
        if gates["final_tasks_digest"] is None or gates["final_tasks_digest"] != freeze.digest(tasks):
            raise freeze.FrozenError("the final cohort is not frozen: gates.json does not bind this task list")
    return tasks


@dataclass(frozen=True)
class Client:
    name: str
    provider: str
    skills_dir: str                                                    # where the client reads skills, in the workspace
    program: Callable[[], Any]                                         # its OutsideProgram, as G6's adapter declares it
    argv: Callable[[str, Path, Mapping[str, Any]], list[str]]          # (program path, mcp config file, config) -> argv
    parse: Callable[[str], tuple[list[dict[str, Any]], str, float | None]]   # event text -> calls, final text, cost


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
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
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


def parse_codex(text: str) -> tuple[list[dict[str, Any]], str, float | None]:
    """Calls and final text from ``codex exec --json``: one JSON event a line; a completed item of type
    ``mcp_tool_call`` carries ``server``, ``tool``, ``arguments`` and ``status``, and one of type ``agent_message``
    the text. Codex reports tokens, not a cost, so the cost is ``None`` (never estimated)."""
    calls, final = [], ""
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
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
                          parse_claude),
    "codex": Client("codex", "openai:codex", ".codex/skills", _codex_program, _codex_argv, parse_codex),
}


def mcp_config(task: Mapping[str, Any], python: str = sys.executable) -> dict[str, Any]:
    """The servers one session gets: each of the task's servers, started through ``server.py`` so the task's withheld
    tools are absent from what the client lists."""
    withhold = ",".join(task.get("withhold", ()))
    return {"mcpServers": {name: {"command": python, "args": ["-P", str(HERE / "server.py"), name],
                                  "env": {WITHHOLD_ENV: withhold}}
                           for name in task.get("servers", ["g6"])}}


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


def run(tasks_path: Path, out_dir: Path, *, client: str, cohort: str, runner: Any = None, repeat: int = 0,
        python: str = sys.executable) -> list[Any]:
    """One fresh session per task of ``cohort`` for ``client``; each transcript is appended to
    ``<out_dir>/transcripts-<client>-<cohort>.jsonl`` as it ends, and all are returned. A consent refusal is
    ``refused`` with its code and starts nothing; a client that is not installed is ``unavailable``; a deadline or a
    failed start is ``interrupted``. ``runner`` stands in for the client in tests; the default starts the real one."""
    from g6.agents._runtime.cli import OUTPUT_LOG, CliAgentRunner
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as cloud
    from g6.core import outside

    spec = CLIENTS[client]
    tasks = load_tasks(Path(tasks_path), cohort=cohort)
    runner = runner or CliAgentRunner(spec.program())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"transcripts-{client}-{cohort}.jsonl"
    done: list[Any] = []
    for task in sorted((t for t in tasks["tasks"] if t["cohort"] == cohort), key=lambda t: t["id"]):
        base = {"task_id": task["id"], "client": client, "repeat": repeat}
        try:
            program = runner.locate() if hasattr(runner, "locate") else spec.name
            workspace = prepare(task, out_dir, spec)
            config = mcp_config(task, python)
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
            transcript = Transcript(state="completed", calls=tuple(calls), final_text=final, cost_usd=cost,
                                    questions=sum(call["tool"] in DIALOG_TOOLS for call in calls),
                                    seconds=float(getattr(ended, "elapsed_s", 0.0)), **base)
        except AgentRefusal as refusal:
            consent = refusal.code in (cloud.REQUIRED, cloud.STATE_UNAVAILABLE)
            missing = refusal.code == outside.OUTSIDE_PROGRAM_MISSING
            transcript = Transcript(state="refused" if consent else "unavailable" if missing else "interrupted",
                                    code=refusal.code, **base)
        with log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(transcript.to_json(), sort_keys=True) + "\n")
        done.append(transcript)
    return done


def read_transcripts(out_dir: Path, client: str, cohort: str) -> list[Any]:
    log = Path(out_dir) / f"transcripts-{client}-{cohort}.jsonl"
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
        report = score.score(load_tasks(path, cohort=args.cohort), read_transcripts(args.out, args.client, args.cohort),
                             freeze.read(GATES), client=args.client, cohort=args.cohort, schemas=schemas())
        target = args.out / f"report-{args.client}-{args.cohort}.json"
        target.write_text(freeze.render(report), encoding="utf-8", newline="\n")
        print(json.dumps({"decision": report["decision"], "gates": report["gates"], "report": str(target)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
