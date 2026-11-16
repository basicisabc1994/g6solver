# SPDX-License-Identifier: Apache-2.0
"""Running the routing trials (0.3 design 14.1): one fresh agent session per task, on a copy of a synthetic
repository, with G6's MCP server and the installed ``using-g6`` skill.

What is sent, and where: each session sends the task's prompt, the fixture repository the agent reads, and G6's own
guidance to the client's provider (``anthropic:claude_code`` or ``openai:codex``). Every session passes section 16's
one consent check first (``g6.agents._runtime.consent.consent_run``), with the fixture declared as a source; with no
grant nothing is started and the task's transcript is ``refused`` with the consent code. The fixtures are synthetic:
no user data is in them. ``sources`` lists exactly what a person grants before a run.

What a score is a function of: each session's event log, kept in the run's folder with its bytes as the client
printed them under their sha256 (``events/<sha256>.log``), and frozen files. ``reading`` is the one reading of such a
log: a session is ``completed`` only when the client exited with code 0 and its log holds the client's one success
event (Claude Code: every ``result`` event has subtype ``success`` and ``is_error`` false; Codex: ``turn.completed``
with no ``turn.failed`` and no ``error``); anything else, an event this file does not know included, is
``interrupted``. ``run`` writes that reading, and ``score`` and ``release`` take it again from the kept log, never
from what a row says about itself: a ``completed`` row whose log is absent or does not hash to its digest cannot
pass (``unevidenced``). Each session's record names the digests of its task, its task list, the bars and the ``g6``
package it ran on, and a record of another is refused. Every session that was started has a row, whatever its events
hold. A final cohort is run once per client: the record that it ran is kept beside the gate file
(``final_runs.jsonl``), not in a folder the caller picks. Nothing signs a kept log: a person who writes a log, its
digest and its row by hand has written evidence, and this file does not see it.

The two clients' command lines and event parsers below are written from each client's documented interface. No
session was started when this was built, so neither has been observed: README.md, "Before the first run".

    python validation/routing/run.py sources --client codex --out <folder>
    python validation/routing/run.py run --client codex --cohort development --out <folder>
    python validation/routing/run.py score --client codex --cohort development --out <folder>
    python validation/routing/run.py release --out <codex's final folder> --out <claude's final folder>
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
TASKS, FINAL_TASKS, GATES, FIXTURES = HERE / "tasks.json", HERE / "final_tasks.json", HERE / "gates.json", HERE / "fixtures"
#: The pinned digests: of the bars (the gate file with no final cohort bound) and of the development task list.
PINS = HERE / "frozen.json"
#: Beside the gate file: one line for each final cohort a client was run on. The first line freezes the final list.
FINAL_RUNS = "final_runs.jsonl"
#: The folder of a run that keeps each session's event log, as ``<sha256 of its bytes>.log``.
EVENTS = "events"
#: The key, among a task list's ``routes``, of the digest of what every session reads and no route owns.
EVERY_ROUTE = "every-route"
#: The setting that hands a trial server the digest of the ``g6`` package the tasks are bound to (``server.py``).
BOUND_ENV = "G6_ROUTING_G6"
#: The files, in a session's G6 home, in which its servers say which ``g6`` package they found (``server.py``).
SERVED = "routing-served-*.json"
#: Tools that put a question to the person; a call of one is counted as a question asked.
DIALOG_TOOLS = frozenset({"g6_confirm_behaviour", "g6_mission_confirm", "g6_mission_create", "g6_cloud_grant",
                          "g6_improve_start", "metaharness_confirm", "metaharness_approve", "metaharness_rollback"})
WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"
#: The setting the server reads its host permissions from (``g6.core.door.SENSITIVE_ENV``).
SENSITIVE_ENV = "G6_MCP_SENSITIVE"
GRACE_S = 60.0
UNFINISHED = "G6_E_ROUTING_SESSION_UNFINISHED"
#: The moment-of-need cohort: its frozen list, its bars, their pins, and the fixtures only it uses (kept apart from
#: ``fixtures/`` so the development cohort's bound digest of the fixtures does not move).
MOMENT_TASKS, MOMENT_GATES, MOMENT_PINS = HERE / "moment_tasks.json", HERE / "moment_gates.json", HERE / "moment_frozen.json"
MOMENT_FIXTURES = HERE / "moment_fixtures"
#: The moment cohort's outcome arm: the held-out checks (one folder per task id, never copied where a session reads)
#: and the outcome bar, both pinned in ``moment_frozen.json`` beside the list and the moment bars.
OUTCOME, OUTCOME_GATES = HERE / "moment_outcome", HERE / "moment_outcome_gates.json"
#: What a session's final diff leaves out: what the interpreter and pytest write as they run.
UNKEPT = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/.pytest_cache/**", ":(exclude,glob)**/*.pyc")
#: The Claude Code plugin this tree ships (hooks and skills): a moment session of Claude Code loads it.
PLUGIN = HERE.parents[1] / "plugins" / "g6"
#: What the moment cohort's Claude Code arm may run beyond the development arm's tools: the tests, and nothing else.
TEST_RUNS = ("Bash(python -m pytest:*)", "Bash(pytest:*)")
#: A moment session is not started while the person's decisions share a source the cohort says a session lacks.
NOT_FRESH = "G6_E_ROUTING_NOT_FRESH"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = sys.modules.get("g6_validation_routing_score") or _load("g6_validation_routing_score", HERE / "score.py")
freeze = sys.modules.get("g6_validation_freeze") or _load("g6_validation_freeze", HERE.parent / "freeze.py")
Transcript = score.Transcript


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=8)
def _tree_digest(root: str) -> str:
    return freeze.tree_digest(Path(root))


def package_digest() -> str:
    """The digest of the ``g6`` package this process imports: the one its tasks, its skill and its servers are read
    from. A trial server that imports another serves nothing (``server.py``)."""
    from g6.mcp import registrations

    return _tree_digest(str(registrations.package_root()))


def _shown() -> dict[str, str]:
    """What the built servers show an agent before it reads any skill: ``server:<id>`` -> the digest of the server's
    instructions, and ``mcp:<server>/<tool>`` -> the digest of the tool's description and input schema."""
    from g6.mcp import registrations

    said = registrations.shown()
    out = {key: _sha(text) for key, text in said.items() if key.startswith("server:")}
    for server, tools in registrations._all().items():
        for tool, schema in tools.items():
            out[f"mcp:{server}/{tool}"] = freeze.digest({"description": said.get(f"tool:{server}/{tool}", ""),
                                                        "schema": schema})
    return out


def route_digests(manifest: Any = None) -> dict[str, str]:
    """Route id -> the digest that binds a route: its anchor, its members, what its own section of the root skill
    says, the bytes of each of its detail files, what the servers show for each of its tools (description and input
    schema) and any text one of its tools answers with (``g6_help``'s). Adding a tool to a route, moving its anchor
    or changing what the route tells an agent changes that route's digest and no other's.

    One more key, ``EVERY_ROUTE``, binds everything a session reads that no route owns: the root skill outside the
    routes' sections (its front matter, the rule for an absent tool, "Read and ask"), every other file of the skill
    folder ``prepare`` installs, each server's instructions, the tools no route holds, and the fixtures."""
    from g6.core import skills
    from g6.mcp import registrations
    from g6.skills import routing

    manifest = manifest or routing.load()
    package = registrations.package_root()
    root = (package / routing.ROOT).read_text(encoding="utf-8")
    shown = _shown()
    spoken = {key.split(":", 1)[1]: _sha(text) for key, text in registrations.guidance().items()
              if key.startswith("help:")}
    routed = {key: entry.route for key, entry in manifest.entries.items() if entry.agent_facing}
    out, rest, owned = {}, "\n".join(root.splitlines()), {routing.ROOT}
    for route_id, route in manifest.routes.items():
        said = routing.section(root, route.anchor) or ""
        rest = rest.replace(said, "", 1) if said else rest
        owned |= set(route.detail)
        members = sorted(key for key, entry in manifest.entries.items() if entry.route == route_id)
        out[route_id] = freeze.digest({
            "id": route_id, "anchor": route.anchor, "detail": list(route.detail), "members": members,
            "section": _sha(said),
            "detail_bytes": {path: hashlib.sha256((package / path).read_bytes()).hexdigest() if (package / path).is_file()
                             else "" for path in route.detail},
            "shown": {key: shown.get(key, "") for key in members if key.startswith("mcp:")},
            "spoken": {tool: text for tool, text in sorted(spoken.items()) if routed.get(f"mcp:g6/{tool}") == route_id}})
    guide, inside = skills.guide_dir(), Path("skills") / skills.GUIDE
    installed = {(inside / path.relative_to(guide)).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(guide.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
    out[EVERY_ROUTE] = freeze.digest({
        "root": _sha(rest), "installed": {path: found for path, found in installed.items() if path not in owned},
        "shown": {key: found for key, found in sorted(shown.items()) if key not in routed},
        "spoken": {tool: text for tool, text in sorted(spoken.items()) if f"mcp:g6/{tool}" not in routed},
        "fixtures": {path.name: freeze.tree_digest(path) for path in sorted(FIXTURES.iterdir()) if path.is_dir()}})
    return out


def pins() -> dict[str, str]:
    try:
        return score.pinned(PINS)
    except score.TaskError as exc:
        raise freeze.FrozenError(str(exc)) from exc


def load_gates() -> dict[str, Any]:
    """The gate file, when its bars are the pinned ones (``freeze.bound``); a gate file whose bars changed after the
    freeze is refused. The bars' pin does not hold ``final_tasks_digest``: the final list is frozen by the first
    line of ``final_runs.jsonl``, written when a final cohort is first run, and ``load_tasks`` holds it to that."""
    return score.validate_gates(freeze.bound(GATES, pins()["bars_digest"], what="the routing bars",
                                             mask={"final_tasks_digest": None}))


def _final_runs() -> list[dict[str, Any]]:
    path = GATES.with_name(FINAL_RUNS)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else ():
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError as exc:
            raise freeze.FrozenError(f"{FINAL_RUNS} holds a line that is not a record") from exc
        if row is not None:
            if not isinstance(row, dict) or not isinstance(row.get("run"), str):
                raise freeze.FrozenError(f"{FINAL_RUNS} holds a line that is not a record")
            rows.append(row)
    return rows


def final_run(client: str, repeat: int) -> dict[str, Any] | None:
    """The record that ``client``'s final cohort was run (``repeat`` 0 is the gate run), or ``None``. A run in
    which no session was started (every task refused or the client absent) is void and does not count."""
    rows = _final_runs()
    void = {row["run"] for row in rows if "void" in row}
    live = [row for row in rows if "void" not in row and row["run"] not in void
            and (row.get("client"), row.get("repeat")) == (client, repeat)]
    return live[-1] if live else None


def final_frozen() -> str | None:
    """The digest of the final task list the first final run was made on: from then on the final list."""
    first = next((row for row in _final_runs() if "void" not in row), None)
    return first.get("tasks_digest") if first else None


def _final_record(row: Mapping[str, Any]) -> None:
    path = GATES.with_name(FINAL_RUNS)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def load_tasks(path: Path = TASKS, *, cohort: str = "development") -> dict[str, Any]:
    """The task list at ``path``, checked: inside its format; every route, and what every session reads besides
    (``EVERY_ROUTE``), bound to the manifest, the guidance and the servers as they stand; for the development cohort,
    every task the pinned list's own (a list may hold fewer tasks, never a changed one); for the final cohort, the
    whole list bound by the gate file, and the list the first final run was made on. ``FrozenError`` or ``TaskError``
    otherwise; nothing runs on a list that does not bind."""
    tasks = score.validate_tasks(freeze.read(path))
    now = route_digests()
    if EVERY_ROUTE not in tasks["routes"]:
        raise freeze.FrozenError("the task list binds no digest of what every session reads besides its route "
                                 f"({EVERY_ROUTE!r})")
    for route_id, bound in tasks["routes"].items():
        if now.get(route_id) != bound and route_id == EVERY_ROUTE:
            raise freeze.FrozenError("what every session reads besides its route (the root skill outside the routes' "
                                     "sections, the skill folder, the servers' instructions, the fixtures) changed "
                                     "since the tasks were written: review the tasks, then bind the new digest")
        if now.get(route_id) != bound:
            raise freeze.FrozenError(f"route {route_id!r} changed since its tasks were written (or is gone): review "
                                     "its tasks, then bind its new digest")
    if cohort == "final":
        gates = load_gates()
        if gates["final_tasks_digest"] is None or gates["final_tasks_digest"] != freeze.digest(tasks):
            raise freeze.FrozenError("the final cohort is not frozen: gates.json does not bind this task list")
        frozen = final_frozen()
        if frozen is not None and frozen != freeze.digest(tasks):
            raise freeze.FrozenError(f"the final cohort was run on the task list {frozen[:12]}, and this list is "
                                     f"another: a final list is frozen once ({FINAL_RUNS})")
    else:
        frozen = {task["id"]: task for task in freeze.bound(TASKS, pins()["tasks_digest"],
                                                           what="the development task list")["tasks"]}
        for task in tasks["tasks"]:
            if frozen.get(task["id"]) != task:
                raise freeze.FrozenError(f"task {task['id']} is not the frozen development list's own task")
    return tasks


def moment_pins() -> dict[str, str]:
    """The pinned digests of the moment bars and the moment task list (``moment_frozen.json``)."""
    try:
        return score.pinned(MOMENT_PINS)
    except score.TaskError as exc:
        raise freeze.FrozenError(str(exc)) from exc


def load_moment_gates() -> dict[str, Any]:
    """The moment bars, when they are the pinned ones."""
    return score.validate_moment_gates(freeze.bound(MOMENT_GATES, moment_pins()["bars_digest"], what="the moment bars"))


def load_moment(path: Path = MOMENT_TASKS) -> dict[str, Any]:
    """The moment task list at ``path``, checked: inside its format, its header the frozen list's own, and every task
    the frozen list's own (a list may hold fewer tasks, to try one first, never a changed one). The moment list binds
    no route: it is run before and after the guidance changes, and each session records what guidance it had."""
    tasks = score.validate_moment_tasks(freeze.read(path))
    frozen = freeze.bound(MOMENT_TASKS, moment_pins()["tasks_digest"], what="the moment task list")
    if {k: v for k, v in tasks.items() if k != "tasks"} != {k: v for k, v in frozen.items() if k != "tasks"}:
        raise freeze.FrozenError("the moment list's header is not the frozen list's own")
    own = {task["id"]: task for task in frozen["tasks"]}
    for task in tasks["tasks"]:
        if own.get(task["id"]) != task:
            raise freeze.FrozenError(f"task {task['id']} is not the frozen moment list's own task")
    return tasks


def user_home() -> Path:
    """The person's home folder, where ``g6 setup`` writes the guidance each client reads."""
    return Path.home()


def guidance(workspace: Path, client: "Client", plugin: Path | None = None) -> dict[str, Any]:
    """The digests of the G6 guidance a session could read: the skill installed in its workspace, the plugin it
    loaded, what the servers show (instructions, tool descriptions and schemas), and what ``g6 setup`` writes in the
    person's home for each client (the skill folders, and Codex's ``AGENTS.md``). An absent one is "". The labels name
    no folder of the person's machine. The before and after arms are compared on these."""
    from g6.core import skills

    home = user_home()

    def tree(path: Path) -> str:
        return freeze.tree_digest(path) if path.is_dir() else ""

    def one(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

    return {"workspace_skill": tree(Path(workspace) / client.skills_dir / skills.GUIDE),
            "plugin": tree(plugin) if plugin is not None else "", "servers": freeze.digest(_shown()),
            "user": {f"~/.claude/skills/{skills.GUIDE}": tree(home / ".claude" / "skills" / skills.GUIDE),
                     f"~/.codex/skills/{skills.GUIDE}": tree(home / ".codex" / "skills" / skills.GUIDE),
                     "~/.codex/AGENTS.md": one(home / ".codex" / "AGENTS.md")}}


def _plugin(scratch: Path) -> Path | None:
    """A copy of the plugin this tree ships, for one session: its hooks and skills as shipped, its ``.mcp.json`` left
    out because the session's trial server stands in its place. ``None`` when this tree ships no plugin."""
    if not PLUGIN.is_dir():
        return None
    target = Path(scratch) / "plugin"
    shutil.copytree(PLUGIN, target, ignore=shutil.ignore_patterns(".mcp.json", "__pycache__"))
    return target


def _fresh(tasks: Mapping[str, Any], provider: str) -> None:
    """Refuse a moment session, before anything starts, while the person's decisions share with ``provider`` a source
    the cohort says a session starts without (the component catalogue included: it is not pre-seeded)."""
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as cloud

    try:
        held = [source for source in tasks["ungranted"] if cloud.preview(source, provider)[0]]
    except (cloud.LLMError, OSError, ValueError) as exc:
        raise AgentRefusal(cloud.STATE_UNAVAILABLE, "the person's sharing decisions could not be read "
                                                    f"({type(exc).__name__})", detail={"started": False}) from None
    if held:
        raise AgentRefusal(NOT_FRESH, f"{', '.join(held)} {'is' if len(held) == 1 else 'are'} shared with {provider}, "
                                      "and a moment session starts without it: revoke it (g6_cloud_revoke) for the "
                                      "run, or run where it is not shared", detail={"started": False})


@contextlib.contextmanager
def _environment(runner: Any, extra: Mapping[str, str]) -> Any:
    """For one session, ``runner``'s client is given ``extra`` beside its own environment (the plugin's hooks start G6
    in the client's environment, so they get the session's G6 home); afterwards the runner is as it was. A stand-in
    with no environment, or nothing extra, is left alone."""
    base = getattr(runner, "environment", None)
    if base is None or not extra:
        yield
        return
    own = vars(runner).get("environment")
    runner.environment = lambda scratch: {**base(scratch), **extra}
    try:
        yield
    finally:
        if own is None:
            del runner.environment
        else:
            runner.environment = own


@dataclass(frozen=True)
class Client:
    name: str
    provider: str
    skills_dir: str                                                    # where the client reads skills, in the workspace
    program: Callable[[], Any]                                         # its OutsideProgram, as G6's adapter declares it
    argv: Callable[[str, Path, Mapping[str, Any]], list[str]]          # (program path, mcp config file, config) -> argv
    parse: Callable[[str], tuple[list[dict[str, Any]], str, float | None]]   # event text -> calls, final text, cost
    finished: Callable[[str], bool]                                    # whether the events hold the client's terminal event
    steps: Callable[[str], list[dict[str, Any]]]                       # event text -> the session's steps, in order


def _events(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _tool(name: str) -> tuple[str, str]:
    """``mcp__<server>__<tool>`` as Claude Code names an MCP tool -> (server, tool); an empty part where the name
    has none (such a call is to no registered tool, and the scorer counts it as malformed)."""
    parts = name.split("__", 2)
    return (parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")


def _state(content: Any) -> str:
    """The ``completion_state`` of a tool result, when its text is a JSON object that carries one."""
    texts = [content] if isinstance(content, str) else \
        [part.get("text") for part in content if isinstance(part, dict)] if isinstance(content, list) else []
    for text in texts:
        try:
            value = json.loads(text) if isinstance(text, str) else None
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and isinstance(value.get("completion_state"), str):
            return value["completion_state"]
    return ""


def _call(server: Any, tool: Any, args: Any, valid: Any, state: str = "") -> dict[str, Any]:
    """One recorded call. Arguments that are not an object are kept as none, and the call is then not valid."""
    return {"server": server if isinstance(server, str) else "", "tool": tool if isinstance(tool, str) else "",
            "args": args if isinstance(args, dict) else {}, "seconds": None,
            "valid": bool(valid) and (args is None or isinstance(args, dict)), "result_state": state}


def parse_claude(text: str) -> tuple[list[dict[str, Any]], str, float | None]:
    """Calls, final text and reported cost from ``claude -p --output-format stream-json --verbose``: one JSON event a
    line; ``assistant`` messages carry ``tool_use`` parts, ``user`` messages their ``tool_result`` (``is_error`` for a
    refused or malformed call), and the last ``result`` event the final text and ``total_cost_usd``. It reads what
    it knows and raises on nothing an event can hold."""
    calls: dict[str, dict[str, Any]] = {}
    final, cost = "", None
    for event in _events(text):
        message = event.get("message")
        parts = message.get("content") if isinstance(message, dict) else None
        for part in parts if isinstance(parts, list) else ():
            if not isinstance(part, dict):
                continue
            name = part.get("name")
            if part.get("type") == "tool_use" and isinstance(name, str) and name.startswith("mcp__"):
                calls[repr(part.get("id"))] = _call(*_tool(name), part.get("input"), True)
            elif part.get("type") == "tool_result" and repr(part.get("tool_use_id")) in calls:
                call = calls[repr(part.get("tool_use_id"))]
                call["valid"] = call["valid"] and part.get("is_error", False) is False
                call["result_state"] = _state(part.get("content"))
        if event.get("type") == "result":
            said, spent = event.get("result"), event.get("total_cost_usd")
            final = said if isinstance(said, str) else ""
            cost = float(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool) \
                and math.isfinite(spent) and spent >= 0 else None
    return list(calls.values()), final, cost


def finished_claude(text: str) -> bool:
    """Claude Code's one success event: a ``result`` event whose subtype is ``success`` and whose ``is_error`` is
    false. A session with a ``result`` event of any other kind (the turn cap, an error during execution, a subtype
    this file does not know, no ``is_error``) did not finish."""
    results = [event for event in _events(text) if event.get("type") == "result"]
    return bool(results) and all(event.get("subtype") == "success" and event.get("is_error") is False
                                 for event in results)


def parse_codex(text: str) -> tuple[list[dict[str, Any]], str, float | None]:
    """Calls and final text from ``codex exec --json``: one JSON event a line; a completed item of type
    ``mcp_tool_call`` carries ``server``, ``tool``, ``arguments`` and ``status``, and one of type ``agent_message``
    the text. Codex reports tokens, not a cost, so the cost is ``None`` (never estimated). It reads what it knows
    and raises on nothing an event can hold."""
    calls, final = [], ""
    for event in _events(text):
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        if item.get("type") == "mcp_tool_call":
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            calls.append(_call(item.get("server"), item.get("tool"), item.get("arguments"),
                               item.get("status") == "completed" and not item.get("error"),
                               _state(result.get("content"))))
        elif item.get("type") == "agent_message":
            final = item.get("text") if isinstance(item.get("text"), str) else ""
    return calls, final, None


def finished_codex(text: str) -> bool:
    """Codex's one success event: ``turn.completed``, in a log with no ``turn.failed`` and no ``error`` event. An
    agent message is not it: Codex prints such messages between tool calls."""
    kinds = [event.get("type") for event in _events(text)]
    return "turn.completed" in kinds and "turn.failed" not in kinds and "error" not in kinds


#: A shell command that runs tests, and what a test run's output says when it failed.
#: Only a whole command word counts: a command that names ``pytest.ini`` or ``pytest-plugins.txt`` runs no tests.
TEST_COMMAND = re.compile(r"(?<![\w.-])(?:pytest|unittest)(?![\w.-])")
_FAILED = re.compile(r"\b\d+ (?:failed|errors?)\b|\bExit code [1-9]")
_G6_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
#: An error a client reports for a refused call leads with its code, after at most an "Error ...:" preamble.
_LEADING_CODE = re.compile(r"\s*(?:[^\[\n]*?:\s*)?\[?(G6_E_[A-Z0-9_]+)\]?")
#: Claude Code's file tools, and the argument that names the file each one changes.
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


def _said(content: Any) -> str:
    """The text of a tool result or an error, whatever shape it came in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part if isinstance(part, str) else str(part.get("text", "")) if isinstance(part, dict) else ""
                         for part in content)
    if isinstance(content, dict):
        return str(content.get("message", content.get("text", "")))
    return ""


def _code(content: Any) -> str:
    """The ``G6_E_*`` code a reply carries as its own: how a refused call is told from an answered one. A JSON reply's
    code is its envelope's (``warning_card.code``, else a top-level or ``error`` code); a text reply's is the code an
    error leads with. A code the reply's text only mentions (help text, a note) is not the reply's code."""
    text = _said(content)
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict) and "job_id" in value and isinstance(value.get("result"), dict):
        value = value["result"]          # a finished job (metaharness_job) wraps the reply it ran: that reply's code
    if isinstance(value, dict):
        for holder in (value.get("warning_card"), value, value.get("error")):
            code = holder.get("code") if isinstance(holder, dict) else None
            if isinstance(code, str) and _G6_CODE.fullmatch(code):
                return code
        error = value.get("error")
        found = _LEADING_CODE.match(error.split("\n", 1)[0]) if isinstance(error, str) else None
        return found.group(1) if found else ""
    found = _LEADING_CODE.match(text.split("\n", 1)[0])
    return found.group(1) if found else ""


def _step_call(server: Any, tool: Any, args: Any, valid: Any, state: str = "", code: str = "") -> dict[str, Any]:
    return {"kind": "call", **_call(server, tool, args, valid, state), "code": code}


def steps_claude(text: str) -> list[dict[str, Any]]:
    """The steps of a ``claude -p --output-format stream-json`` session in the order its log holds them: each MCP
    call (as ``parse_claude`` reads it, with the code its reply carried), each file a file tool changed, each shell
    command that ran tests (failed when its result is an error or says tests failed), and the final message."""
    out: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for event in _events(text):
        message = event.get("message")
        parts = message.get("content") if isinstance(message, dict) else None
        for part in parts if isinstance(parts, list) else ():
            if not isinstance(part, dict):
                continue
            name, args = part.get("name"), part.get("input")
            if part.get("type") == "tool_use" and isinstance(name, str):
                if name.startswith("mcp__"):
                    step = _step_call(*_tool(name), args, True)
                elif name in EDIT_TOOLS and isinstance(args, dict):
                    step = {"kind": "edit", "path": str(args.get(EDIT_TOOLS[name], ""))}
                elif name == "Bash" and isinstance(args, dict) and TEST_COMMAND.search(str(args.get("command", ""))):
                    step = {"kind": "test", "command": str(args.get("command", "")), "failed": None}
                else:
                    continue
                out.append(step)
                pending[repr(part.get("id"))] = step
            elif part.get("type") == "tool_result" and repr(part.get("tool_use_id")) in pending:
                step = pending.pop(repr(part.get("tool_use_id")))
                if step["kind"] == "call":
                    step.update(valid=step["valid"] and part.get("is_error", False) is False,
                                result_state=_state(part.get("content")), code=_code(part.get("content")))
                elif step["kind"] == "test":
                    step["failed"] = part.get("is_error") is True or bool(_FAILED.search(_said(part.get("content"))))
        if event.get("type") == "result":
            said = event.get("result")
            out.append({"kind": "final", "text": said if isinstance(said, str) else ""})
    return out


def steps_codex(text: str) -> list[dict[str, Any]]:
    """The steps of a ``codex exec --json`` session in the order its log holds them: each MCP call (as
    ``parse_codex`` reads it, with the code its reply or error carried), each file a ``file_change`` item changed,
    each command that ran tests (failed on a non-zero exit code or a failed status), each agent message, and the
    last agent message as the final one."""
    out: list[dict[str, Any]] = []
    for event in _events(text):
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        kind = item.get("type")
        if kind == "mcp_tool_call":
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            out.append(_step_call(item.get("server"), item.get("tool"), item.get("arguments"),
                                  item.get("status") == "completed" and not item.get("error"),
                                  _state(result.get("content")),
                                  _code(result.get("content")) or _code(item.get("error"))))
        elif kind == "file_change":
            changes = item.get("changes")
            out += [{"kind": "edit", "path": str(change.get("path", ""))}
                    for change in (changes if isinstance(changes, list) else ()) if isinstance(change, dict)]
        elif kind == "command_execution":
            command = item.get("command")
            command = " ".join(map(str, command)) if isinstance(command, list) else str(command or "")
            if TEST_COMMAND.search(command):
                code = item.get("exit_code")
                out.append({"kind": "test", "command": command,
                            "failed": (type(code) is int and code != 0) or item.get("status") == "failed"})
        elif kind == "agent_message":
            out.append({"kind": "text", "text": item.get("text") if isinstance(item.get("text"), str) else ""})
    for step in reversed(out):
        if step["kind"] == "text":
            step["kind"] = "final"
            break
    return out


def reading(client: str, data: bytes | None, exit_code: Any) -> dict[str, Any]:
    """The one reading of a session: its state, code, calls, final text, cost and questions, from its event log's
    bytes and its exit code. ``run`` writes it and ``read_sessions`` (so ``score`` and ``release``) takes it again
    from the kept log. ``completed`` only when the exit code is 0 and the log holds the client's one success event
    (``Client.finished``); no log, any other exit code and any other log are ``interrupted``."""
    spec = CLIENTS[client]
    text = data.decode("utf-8", errors="replace") if data is not None else ""
    calls, final, cost = spec.parse(text)
    finished = data is not None and type(exit_code) is int and exit_code == 0 and spec.finished(text)
    return {"state": "completed" if finished else "interrupted", "code": "" if finished else UNFINISHED,
            "calls": tuple(calls), "final_text": final, "cost_usd": cost,
            "questions": sum(call["tool"] in DIALOG_TOOLS for call in calls), "timeline": tuple(spec.steps(text))}


def _claude_argv(program: str, config_file: Path, config: Mapping[str, Any]) -> list[str]:
    servers = [f"mcp__{name}" for name in config["mcpServers"]]
    return [program, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "default",
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(config_file),
            "--allowedTools", *servers, "Read", "Glob", "Grep", "Edit", "Write", "Skill",
            "--disallowedTools", "Bash", "BashOutput", "KillShell", "PowerShell", "WebFetch", "WebSearch", "Task",
            "Agent", "--max-turns", "30"]


def _claude_moment_argv(program: str, config_file: Path, config: Mapping[str, Any], plugin: Path | None) -> list[str]:
    """The moment cohort's Claude Code session: the development arm's, with the plugin this tree ships loaded (its
    hooks and skills, as installed) and the tests allowed to run, so a test run can fail and be seen. The rest of the
    shell, the web and sub-agents stay refused."""
    servers = [f"mcp__{name}" for name in config["mcpServers"]]
    return [program, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "default",
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(config_file),
            *(["--plugin-dir", str(plugin)] if plugin is not None else []),
            "--allowedTools", *servers, "Read", "Glob", "Grep", "Edit", "Write", "Skill", *TEST_RUNS,
            "--disallowedTools", "BashOutput", "KillShell", "PowerShell", "WebFetch", "WebSearch", "Task", "Agent",
            "--max-turns", "40"]


def _codex_argv(program: str, config_file: Path, config: Mapping[str, Any]) -> list[str]:
    argv = [program, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
            "--color", "never"]
    for name, server in config["mcpServers"].items():
        # `codex exec` asks no one, so with no setting every MCP call fails ("requires approval, but approval policy
        # is never"): the trial server's tools are approved, as a person who always allows G6's tools sets them.
        argv += ["-c", f"mcp_servers.{name}.command={json.dumps(server['command'])}",
                 "-c", f"mcp_servers.{name}.args={json.dumps(server['args'])}",
                 "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"']
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
                          parse_claude, finished_claude, steps_claude),
    "codex": Client("codex", "openai:codex", ".codex/skills", _codex_program, _codex_argv, parse_codex,
                    finished_codex, steps_codex),
}


def mcp_config(task: Mapping[str, Any], python: str = sys.executable, home: Path | None = None, *,
               moment: bool = False) -> dict[str, Any]:
    """The servers one session gets: each of the task's servers, started through ``server.py`` so the task's withheld
    tools are absent from what the client lists, with the host permissions the task states (and no other), with the
    digest of the ``g6`` package the tasks are bound to (a server that imports another serves nothing), and, when
    ``home`` is given, with that folder as its G6 home so a session never touches the person's own G6 state. A moment
    session states its permissions even when they are none, so a registration of the person's own cannot add any."""
    env = {WITHHOLD_ENV: ",".join(task.get("withhold", ())), BOUND_ENV: package_digest()}
    if task.get("permissions") or moment:
        env[SENSITIVE_ENV] = ",".join(task.get("permissions", ()))
    if home is not None:
        env["G6_HOME"] = str(home)
    return {"mcpServers": {name: {"command": python, "args": ["-P", str(HERE / "server.py"), name], "env": dict(env)}
                           for name in task.get("servers", ["g6"])}}


def trial_home(out_dir: Path, session: str = "") -> Path:
    """A G6 home for a trial's servers: ``<out>/g6_home``, or, for one session, ``<out>/g6_homes/<session>``, made
    empty. It holds the person's sharing decisions (so a granted source is not withheld from the session) and nothing
    else of theirs. The decisions are read as G6 reads them (``cloud_consent``'s own locked reader, which checks the
    chain and its checkpoint), written with their checkpoint, and the copy is read back through G6's ledger reader
    before anything uses it."""
    from g6.core import cloud_consent as cloud
    from g6.core.ledger import ChainLedger

    home = Path(out_dir).resolve() / "g6_home"
    if session:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", session):
            raise freeze.FrozenError(f"a session's name is its task id and repeat, not {session!r}")
        home = Path(out_dir).resolve() / "g6_homes" / session
        if home.exists():
            shutil.rmtree(home, onexc=_writable)
    with cloud._locked() as ledger:
        head = ledger.head()
        data = ledger.path.read_bytes() if ledger.path.is_file() else b""
    home.mkdir(parents=True, exist_ok=True)
    if data:
        folder = home / "cloud_consent"
        folder.mkdir(exist_ok=True)
        (folder / "events.jsonl").write_bytes(data)
        (folder / "head").write_text(head, encoding="ascii", newline="\n")
        copy = ChainLedger(folder / "events.jsonl", expect_head=head, validate=cloud._validate)
        if copy.head() != head or copy.verify():
            raise cloud.LLMError(f"{cloud.STATE_UNAVAILABLE}: the trial's copy of the sharing decisions does not "
                                 "read as the person's own; nothing was started")
    return home


def _served(home: Path | None) -> dict[str, Any]:
    """Server id -> which ``g6`` package that server of the session found, and whether it served: what each trial
    server wrote in the session's home (``server.py``). Empty when no server of the session got that far."""
    out: dict[str, Any] = {}
    for path in sorted(home.glob(SERVED)) if home is not None and home.is_dir() else ():
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(found, dict) and isinstance(found.get("server"), str) and isinstance(found.get("g6"), str):
            out[found["server"]] = {"g6": found["g6"], "serves": found.get("serves") is True}
    return out


def fixture_dir(name: str) -> Path:
    """The synthetic repository a task names: in ``fixtures/``, or, for one only the moment cohort uses, in
    ``moment_fixtures/``."""
    shared = FIXTURES / name
    return shared if shared.is_dir() else MOMENT_FIXTURES / name


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
    shutil.copytree(fixture_dir(task["fixture"]), workspace, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(skills.guide_dir(), workspace / client.skills_dir / skills.GUIDE,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=routing-trial", "-c", "user.email=routing-trial@example.invalid", "commit", "-q",
                  "-m", "fixture"]):
        _git(workspace, *argv)
    return workspace


def _git(cwd: Path, *argv: str, env: Mapping[str, str] | None = None) -> bytes:
    """What git prints for ``argv`` in ``cwd``: the one program this file starts directly. ``CalledProcessError`` when
    it fails."""
    return subprocess.run(["git", *argv], cwd=cwd, env=None if env is None else dict(env), check=True,
                          capture_output=True, timeout=120).stdout


def fixture_sources(task: Mapping[str, Any]) -> tuple[Any, ...]:
    """The fixture a task reads, declared as one source: ``routing-fixture.<name>``, with the prompt and every fixture
    file's bytes in its content digest."""
    from g6.core import cloud_consent as cloud

    root = fixture_dir(task["fixture"])
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


def _record(log: Path, transcript: Any, session: Mapping[str, Any]) -> None:
    """The one place a row enters a transcript log: the transcript, and beside it the session's record (the run it
    belongs to, the digests it ran on, which ``g6`` its servers found)."""
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({**transcript.to_json(), "session": dict(session)}, sort_keys=True) + "\n")


def _keep(out_dir: Path, data: bytes, suffix: str = ".log") -> str:
    """Keep an event log's bytes (or, ``.diff``, a session's final diff) in the run's folder under their sha256, and
    answer it."""
    digest = hashlib.sha256(data).hexdigest()
    folder = Path(out_dir) / EVENTS
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / f"{digest}{suffix}").is_file():
        (folder / f"{digest}{suffix}").write_bytes(data)
    return digest


def kept(out_dir: Path, digest: str, suffix: str = ".log") -> tuple[bytes | None, str]:
    """(the kept event log, or with ``.diff`` the kept final diff, named by ``digest``, "") or (``None``, why it
    cannot be read as that file)."""
    what = "final diff" if suffix == ".diff" else "event log"
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        return None, f"the row names no {what}"
    path = Path(out_dir) / EVENTS / f"{digest}{suffix}"
    if not path.is_file():
        return None, f"the {what} the row names is not in the run's folder"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        return None, f"the kept {what} does not hash to the digest the row names"
    return data, ""


def _head(workspace: Path) -> str:
    """The commit ``prepare`` made of the fixture: what a session's final diff is taken against."""
    return _git(workspace, "rev-parse", "HEAD").decode("ascii").strip()


def final_diff(workspace: Path, base: str, client: Client) -> bytes:
    """The workspace as the session left it, against the fixture commit ``base``: every change, deletion and new file
    (a git-ignored one and the agent's own commits too), as a binary git diff with no line ending rewritten. What the
    interpreter and pytest write (``UNKEPT``) and the skill the runner installed are left out. Read through an index
    of its own, so the workspace's index stays as the session left it."""
    from g6.core import skills

    spec = [".", *UNKEPT, f":(exclude){client.skills_dir}/{skills.GUIDE}"]
    raw = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false")
    with tempfile.TemporaryDirectory(prefix="g6_routing_diff_", ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(workspace, *raw, "read-tree", base, env=env)
        _git(workspace, *raw, "add", "-A", "--force", "--", *spec, env=env)
        return _git(workspace, *raw, "diff", "--cached", "--binary", "--full-index", "--no-renames", "--no-color",
                    "--no-ext-diff", "--no-textconv", base, "--", *spec, env=env)


def _produced(program: str, detected: Any, digests: dict[str, str]) -> str:
    """``client:<the program file's name> <what its version check answered> sha256:<the program file's digest>``:
    which client produced a session, with no folder of the person's machine in it."""
    path = Path(program)
    said = str(getattr(detected, "version", "") or getattr(detected, "detail", ""))
    for folder in (str(path.parent) if path.parent != Path(".") else "", str(Path.home())):
        said = said.replace(folder, "") if folder else said
    if program not in digests:
        digests[program] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    return " ".join(part for part in (f"client:{path.name}", " ".join(said.split()),
                                      f"sha256:{digests[program]}" if digests[program] else "") if part)


def run(tasks_path: Path, out_dir: Path, *, client: str, cohort: str, runner: Any = None, repeat: int = 0,
        python: str = sys.executable) -> list[Any]:
    """One fresh session per task of ``cohort`` for ``client``, each in a G6 home of its own; each transcript is
    appended to ``<out_dir>/transcripts-<client>-<cohort>.jsonl`` as its session ends, with the session's record, and
    its event log is kept in ``<out_dir>/events``. All transcripts are returned.

    Nothing is started while G6's stop is engaged or model calls are switched off: the run is refused whole, with
    no row, and a final cohort is not spent. A session's state is ``reading``'s. A consent refusal is ``refused``
    with its code and starts nothing; a client that is not installed is ``unavailable``; a deadline, a failed start,
    a non-zero exit, a log with no success event and a fault of this runner's own are ``interrupted``. Every task
    that was reached has a row, written in a ``finally``.

    A log that already holds a row for one of these tasks and this ``repeat`` refuses the run before anything
    starts. A final cohort is run once per client and repeat whatever the folder: the record is written beside the
    gate file before the first session, and only a run in which no session was started is taken back (void).
    ``runner`` stands in for the client in tests; its transcripts say so (``produced``), and no stand-in's
    transcript can pass a gate's reading.

    The moment cohort (``cohort="moment"``) runs from ``moment_tasks.json`` or a part of it. Each session also starts
    only while the person's decisions share none of the sources the list says a session lacks (``NOT_FRESH``), its
    server states the task's permissions even when they are none, its client is given the session's G6 home, a
    Claude Code session loads the plugin this tree ships and may run the tests, and its record names the digests of
    the guidance it could read (``guidance``) and the digest of its final diff (``final_diff_sha256``: the workspace as
    the session left it against the fixture commit, kept as ``<out_dir>/events/<sha256>.diff`` before the next session
    on that fixture wipes the folder; "" when no workspace was made)."""
    from g6.agents._runtime import cli
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as cloud
    from g6.core import outside
    from g6.core import stop as core_stop

    spec = CLIENTS[client]
    moment = cohort == score.MOMENT
    if moment:
        tasks = load_moment(Path(tasks_path))
        load_moment_gates()
        listed, bars = moment_pins()["tasks_digest"], moment_pins()["bars_digest"]
    else:
        tasks = load_tasks(Path(tasks_path), cohort=cohort)
        load_gates()
        listed, bars = freeze.digest(tasks), pins()["bars_digest"]
    scheduled = sorted((t for t in tasks["tasks"] if t["cohort"] == cohort), key=lambda t: t["id"])
    out_dir = Path(out_dir)
    if cohort == "final" and final_run(client, repeat) is not None:
        raise freeze.FrozenError(f"{client}'s final cohort (repeat {repeat}) was already run: {FINAL_RUNS}, beside "
                                 "the gate file, records it; a gate run is made once, whatever the folder")
    held = {(t.task_id, t.repeat) for t in map(Transcript.from_json, _rows(out_dir, client, cohort))}
    again = sorted(task["id"] for task in scheduled if (task["id"], repeat) in held)
    if again:
        raise freeze.FrozenError(f"this log already holds a row (repeat {repeat}) for {again[:3]}: a run is made "
                                 "once in a folder; a development cohort run again needs its own folder or repeat")
    if os.environ.get("G6_DISABLE_LLM"):
        raise AgentRefusal("G6_E_LLM_DISABLED", "model calls are disabled by G6_DISABLE_LLM, and every session runs "
                                                "a model, so no session was started", detail={"started": False})
    stopped = cli._stopped(core_stop.engaged)
    if stopped:
        raise AgentRefusal(core_stop.STOPPED, f"G6 is stopped: {stopped}, so no session was started",
                           detail={"started": False})
    real = runner is None
    runner = runner or cli.CliAgentRunner(spec.program())
    bound = {"tasks": listed, "bars": bars, "g6": package_digest()}
    run_id = uuid.uuid4().hex
    if cohort == "final":
        _final_record({"client": client, "repeat": repeat, "run": run_id, "tasks_digest": bound["tasks"],
                       "bars_digest": bound["bars"], "g6_digest": bound["g6"]})
    out_dir.mkdir(parents=True, exist_ok=True)
    log, programs = _log(out_dir, client, cohort), {}
    done: list[Any] = []
    for task in scheduled:
        said = {"task_id": task["id"], "client": client, "repeat": repeat,
                "produced": "" if real else f"stand-in:{type(runner).__name__}"}
        session: dict[str, Any] = {"run": run_id, "bound": {**bound, "task": freeze.digest(task)}, "served": {}}
        ended: dict[str, Any] = {"state": "interrupted", "code": UNFINISHED}     # until the session says otherwise
        data, exit_code, seconds, home, workspace, base = None, None, 0.0, None, None, ""
        try:
            program = runner.locate() if hasattr(runner, "locate") else spec.name
            if real:
                said["produced"] = _produced(program, runner.require(stop=core_stop.engaged), programs)
            if moment:
                _fresh(tasks, spec.provider)
            try:
                home = trial_home(out_dir, f"{task['id']}.{repeat}")
            except (cloud.LLMError, OSError, ValueError) as exc:
                raise AgentRefusal(cloud.STATE_UNAVAILABLE, "the person's sharing decisions could not be read for "
                                                            f"the session's G6 home ({type(exc).__name__})") from None
            workspace = prepare(task, out_dir, spec)
            base = _head(workspace) if moment else ""
            config = mcp_config(task, python, home, moment=moment)
            with tempfile.TemporaryDirectory(prefix="g6_routing_", ignore_cleanup_errors=True) as tmp:
                scratch = Path(tmp)
                config_file = scratch / "mcp.json"
                config_file.write_text(json.dumps(config), encoding="utf-8")
                plugin = _plugin(scratch) if moment and client == "claude_code" else None
                argv = _claude_moment_argv(program, config_file, config, plugin) if moment and client == "claude_code" \
                    else spec.argv(program, config_file, config)
                if moment:
                    session["guidance"] = guidance(workspace, spec, plugin)
                result = None
                try:
                    with _environment(runner, {"G6_HOME": str(home)} if moment else {}), \
                            cloud.request_sources(fixture_sources(task)):
                        result = consent_run(runner, argv, provider=spec.provider,
                                             prompt=task["prompt"], cwd=workspace,
                                             seconds=task["budget"]["seconds"] + GRACE_S, scratch=scratch)
                    exit_code, seconds = getattr(result, "exit_code", None), float(getattr(result, "elapsed_s", 0.0))
                finally:                               # whatever the client printed is kept, however the session ended
                    if (scratch / cli.OUTPUT_LOG).is_file():
                        data = (scratch / cli.OUTPUT_LOG).read_bytes()
                    elif result is not None:
                        data = str(getattr(result, "output_tail", "")).encode("utf-8")
            ended = {}                                 # the session ran to its end: its state is the log's reading
        except AgentRefusal as refusal:
            unstarted = refusal.code in (cloud.REQUIRED, cloud.STATE_UNAVAILABLE) \
                or refusal.detail.get("started") is False
            missing = refusal.code == outside.OUTSIDE_PROGRAM_MISSING
            ended = {"state": "refused" if unstarted else "unavailable" if missing else "interrupted",
                     "code": refusal.code}
            if unstarted or missing:
                data = None                            # nothing was started: there is no log of a session to keep
        except Exception as exc:  # noqa: BLE001 - a fault of this runner's own: the task has its row and the run goes on
            session["fault"] = type(exc).__name__
        finally:
            if moment:                                 # the final work, kept before the next session wipes the folder
                try:
                    session["final_diff_sha256"] = _keep(out_dir, final_diff(workspace, base, spec), ".diff") \
                        if base else ""
                except Exception as exc:  # noqa: BLE001 - a diff that cannot be made costs the session no row
                    session["final_diff_sha256"], session["diff_fault"] = "", type(exc).__name__
            try:
                session["served"] = _served(home)
                if home is not None:
                    shutil.rmtree(home, onexc=_writable)
                found = reading(client, data, exit_code)
            except Exception as exc:  # noqa: BLE001 - nothing after a session may cost it its row
                session.setdefault("fault", type(exc).__name__)
                found = reading(client, None, None)
            if ended:
                found = {**found, **ended}
            transcript = Transcript(seconds=seconds, exit_code=exit_code if type(exit_code) is int else None,
                                    events_sha256=_keep(out_dir, data) if data is not None else "", **said, **found)
            _record(log, transcript, session)
            done.append(transcript)
    if cohort == "final" and all(t.state in ("refused", "unavailable") for t in done):
        _final_record({"run": run_id, "void": "no session was started"})
    return done


@dataclass(frozen=True)
class Session:
    transcript: Any                 # the row's transcript, read again from its kept event log where there is one
    record: Mapping[str, Any]       # the session's record, as the row holds it
    fault: str                      # "" when the transcript is the kept log's own reading and its record binds


def _rows(out_dir: Path, client: str, cohort: str) -> list[dict[str, Any]]:
    log = _log(out_dir, client, cohort)
    rows = []
    for number, line in enumerate(log.read_text(encoding="utf-8").splitlines() if log.is_file() else (), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            Transcript.from_json(row)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise score.TaskError(f"line {number} of {log.name} is not a transcript row") from exc
        rows.append(row)
    return rows


def read_sessions(out_dir: Path, client: str, cohort: str, tasks: Mapping[str, Any] | None = None) -> list[Session]:
    """Every row of a log as a ``Session``. A row that names a kept event log is read again from that log
    (``reading``), and what the row says of its own state, calls, final text, cost and questions is dropped. A row
    that says ``completed`` and has no such log, or whose record does not bind, keeps its words and carries a
    ``fault``: it is scored as it reads, so a fault in it still shows, and its gate cannot read ``pass``.

    With ``tasks`` (the gate's reading) each record is held to the files as they stand: a record that names another
    task, task list, bars or ``g6`` package raises ``FrozenError``; a record that names none, or, for the final
    cohort, is not of the run recorded beside the gate file, is a fault.

    A moment record is held to the pinned moment list and bars, and not to this tree's ``g6`` package: its two arms
    run on two trees. It must name the package it ran on, and its servers must have found that one."""
    moment = cohort == score.MOMENT
    by_id = {task["id"]: task for task in tasks["tasks"]} if tasks is not None else {}
    want = ({"tasks": moment_pins()["tasks_digest"], "bars": moment_pins()["bars_digest"]} if moment else
            {"tasks": freeze.digest(tasks), "bars": pins()["bars_digest"], "g6": package_digest()}) if tasks else {}
    out = []
    for row in _rows(out_dir, client, cohort):
        typed = Transcript.from_json(row)
        record = row.get("session") if isinstance(row.get("session"), dict) else {}
        data, fault = kept(out_dir, typed.events_sha256)
        transcript = dataclasses.replace(typed, **reading(typed.client, data, typed.exit_code)) \
            if data is not None and typed.client in CLIENTS else typed
        if typed.task_id in by_id:
            bound = record.get("bound") if isinstance(record.get("bound"), dict) else {}
            for name, now in {**want, "task": freeze.digest(by_id[typed.task_id])}.items():
                if name in bound and bound[name] != now:
                    raise freeze.FrozenError(
                        f"the session of {typed.task_id} ran on another {name} ({str(bound[name])[:12]}) than the "
                        f"one it would be scored on now ({now[:12]}): a record is scored on what it ran on")
                if name not in bound:
                    fault = fault or "the row's record names no digests of what the session ran on"
            ran_on = bound.get("g6") if moment else want["g6"]
            if moment and not re.fullmatch(r"[0-9a-f]{64}", str(ran_on or "")):
                fault = fault or "the row's record names no digest of the g6 package the session ran on"
            served = record.get("served") if isinstance(record.get("served"), dict) else {}
            if any(not isinstance(found, dict) or found.get("g6") != ran_on or found.get("serves") is not True
                   for found in served.values()):
                fault = fault or "a server of the session found another g6 package than the one the tasks bind"
            if cohort == "final":
                recorded = final_run(typed.client, typed.repeat)
                if recorded is None or record.get("run") != recorded["run"]:
                    fault = fault or f"the row is not of the final run recorded beside the gate file ({FINAL_RUNS})"
        out.append(Session(transcript, record, fault if transcript.state == "completed" else ""))
    return out


def read_transcripts(out_dir: Path, client: str, cohort: str) -> list[Any]:
    """The log's transcripts, each read again from its kept event log where it names one (``read_sessions``)."""
    return [session.transcript for session in read_sessions(out_dir, client, cohort)]


def gate_reading(out_dir: Path, *, client: str, cohort: str, tasks_path: Path | None = None) -> dict[str, Any]:
    """The gate's own reading of one client's cohort in ``out_dir``: the pinned bars, the bound task list, the
    transcripts read again from the kept event logs, and only rows a named client produced. The report's
    ``evidence`` block counts the gate rows read from a kept log and names each ``completed`` row that was not;
    with any such row a reading that would be ``pass`` is ``unevidenced``."""
    path = tasks_path or (FINAL_TASKS if cohort == "final" else TASKS)
    tasks, gates = load_tasks(path, cohort=cohort), load_gates()
    sessions = read_sessions(out_dir, client, cohort, tasks)
    report = score.score(tasks, [session.transcript for session in sessions], gates, client=client, cohort=cohort,
                         schemas=schemas(), require_client=True)
    ids = {task["id"] for task in tasks["tasks"] if task["cohort"] == cohort}
    gate = [s for s in sessions if s.transcript.client == client and s.transcript.repeat == 0
            and s.transcript.task_id in ids]
    report["evidence"] = {
        "read_from": f"{EVENTS}/<sha256>.log",
        "rederived": sum(not s.fault and s.transcript.state == "completed" for s in gate),
        "unevidenced": {s.transcript.task_id: s.fault for s in gate if s.fault},
        "g6_digest": package_digest(),
        "sessions_with_a_server_record": sum(bool(s.record.get("served")) for s in gate)}
    if report["decision"] == "pass" and report["evidence"]["unevidenced"]:
        report["decision"] = "unevidenced"
    return report


def moment_reading(out_dir: Path, *, client: str) -> dict[str, Any]:
    """One arm of the moment cohort for one client, read as ``gate_reading`` reads a cohort: the pinned moment list
    and bars, the transcripts read again from the kept event logs, the ``evidence`` block. ``arm`` names the ``g6``
    package and the guidance the arm's sessions ran with, so two arms can be told apart and compared."""
    tasks, gates = load_moment(), load_moment_gates()
    sessions = read_sessions(out_dir, client, score.MOMENT, tasks)
    report = score.score_moment(tasks, [session.transcript for session in sessions], gates, client=client,
                                schemas=schemas(), require_client=True)
    ids = {task["id"] for task in tasks["tasks"]}
    gate = [s for s in sessions if s.transcript.client == client and s.transcript.repeat == 0
            and s.transcript.task_id in ids]
    report["evidence"] = {
        "read_from": f"{EVENTS}/<sha256>.log",
        "rederived": sum(not s.fault and s.transcript.state == "completed" for s in gate),
        "unevidenced": {s.transcript.task_id: s.fault for s in gate if s.fault},
        "sessions_with_a_server_record": sum(bool(s.record.get("served")) for s in gate)}
    bound = [s.record.get("bound") if isinstance(s.record.get("bound"), dict) else {} for s in gate]
    seen = [s.record["guidance"] for s in gate if isinstance(s.record.get("guidance"), dict)]
    report["arm"] = {"g6": sorted({str(b.get("g6", "")) for b in bound}),
                     "guidance": list({freeze.digest(g): g for g in seen}.values())}
    if report["decision"] == "pass" and report["evidence"]["unevidenced"]:
        report["decision"] = "unevidenced"
    return report


def compare(before: Path, after: Path, *, client: str) -> dict[str, Any]:
    """The before and after arms of one client, each read again from its own folder's kept logs, paired by task
    (``score.compare``: an exact McNemar test per kind, and no claim), with what each arm ran on."""
    first, second = moment_reading(Path(before), client=client), moment_reading(Path(after), client=client)
    return {**score.compare(first, second), "arms": {"before": first["arm"], "after": second["arm"]}}


def release(out_dirs: Sequence[Path], cohort: str = "final") -> dict[str, Any]:
    """The release reading, made here because the pins are here: each client's final report is made again from the
    kept event logs of its folder (a report file is never read), then read by ``score.release`` against the pinned
    bars. For the moment cohort, each client's arm in the folders given, against the moment bars."""
    if cohort == score.MOMENT:
        found = [moment_reading(Path(out), client=client)
                 for out in out_dirs for client in sorted(CLIENTS) if _log(Path(out), client, cohort).is_file()]
        return score.release_moment(found, bars_digest=moment_pins()["bars_digest"])
    reports = [gate_reading(Path(out), client=client, cohort="final")
               for out in out_dirs for client in sorted(CLIENTS) if _log(Path(out), client, "final").is_file()]
    return score.release(reports, bars_digest=pins()["bars_digest"])


def schemas() -> dict[str, dict[str, Any]]:
    """``<server>/<tool>`` -> its registered input schema, for the scorer's offline check of each call."""
    from g6.mcp import registrations

    return {f"{server}/{tool}": schema for server, tools in registrations._all().items()
            for tool, schema in tools.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("sources", "run", "score", "release", "compare"))
    parser.add_argument("--client", choices=sorted(CLIENTS))
    parser.add_argument("--cohort", choices=(*score.COHORTS, score.MOMENT), default="development")
    parser.add_argument("--out", type=Path, action="append", required=True)
    parser.add_argument("--repeat", type=int, default=0)
    args = parser.parse_args(argv)
    moment = args.cohort == score.MOMENT
    if args.command == "release":
        print(json.dumps(release(args.out, score.MOMENT if moment else "final"), indent=2))
        return 0
    if args.command == "compare":
        if args.client is None or len(args.out) != 2:
            parser.error("compare takes --client and two --out: the before arm's folder, then the after arm's")
        print(json.dumps(compare(args.out[0], args.out[1], client=args.client), indent=2))
        return 0
    if args.client is None or len(args.out) != 1:
        parser.error(f"{args.command} takes --client and one --out")
    out = args.out[0]
    path = MOMENT_TASKS if moment else FINAL_TASKS if args.cohort == "final" else TASKS
    if args.command == "sources":
        listed = load_moment(path) if moment else load_tasks(path, cohort=args.cohort)
        print(json.dumps(sources(listed, out, args.client, args.cohort), indent=2))
    elif args.command == "run":
        states = [t.state for t in run(path, out, client=args.client, cohort=args.cohort, repeat=args.repeat)]
        print(json.dumps({state: states.count(state) for state in score.STATES}))
    else:
        report = moment_reading(out, client=args.client) if moment else \
            gate_reading(out, client=args.client, cohort=args.cohort)
        target = out / f"report-{args.client}-{args.cohort}.json"
        target.write_text(freeze.render(report), encoding="utf-8", newline="\n")
        print(json.dumps({"decision": report["decision"], "gates": report["gates"], "report": str(target)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
