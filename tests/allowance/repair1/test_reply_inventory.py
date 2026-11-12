"""Every caller that renders an allowance, beside each kind of observer.

No reply waits for an observer, no observer is stopped, and a reading is shown with its own time: one that is too
old is never shown as current. The callers and the observers are read from the code, so a new one must be listed here.
"""
import ast
import asyncio
import functools
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "g6"
CODEX = {"ordinaryUsageAllowed": True, "rateLimits": {
    "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 2000000000}}}


@functools.cache
def _trees():
    return [(path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(SRC.rglob("*.py")) if "vendor" not in path.parts]


def _dotted(path):
    """``mcp/__init__.py`` is ``g6.mcp``; ``_allowance/store.py`` is ``g6._allowance.store``."""
    parts = path[:-3].split("/")
    return ".".join(["g6", *(parts[:-1] if parts[-1] == "__init__" else parts)])


def _references():
    """Every use, called or handed on, of a name that resolves through a module's imports or its own definitions.

    Returns ``{dotted name: {module path: [the node that uses it]}}``. A name bound by ``import a.b``,
    ``import a.b as c``, ``from a import b`` or ``from a import b as c`` (relative or not) resolves to what it was
    imported as; a name a module defines at its top resolves to that module's own.
    """
    found = {}
    for path, tree in _trees():
        own = _dotted(path)
        package = own if path.endswith("__init__.py") else own.rpartition(".")[0]
        bound = {node.name: own + "." + node.name for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    up = package.split(".")
                    base = ".".join([*up[:len(up) - node.level + 1], *([base] if base else [])])
                for alias in node.names:
                    assert alias.name != "*" or not base.startswith(("g6.allowance", "g6._allowance")), (
                        path, "a star import hides what is used")
                    bound[alias.asname or alias.name] = base + "." + alias.name
        inner = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in inner:
                parts, value = [], node
                while isinstance(value, ast.Attribute):
                    parts.append(value.attr)
                    value = value.value
                if isinstance(value, ast.Name) and isinstance(value.ctx, ast.Load) and value.id in bound:
                    name = ".".join([bound[value.id], *reversed(parts)])
                    found.setdefault(name, {}).setdefault(path, []).append(node)
    return found


def _users(references, name):
    """The modules that use ``name`` or anything reached through it."""
    return {path for found, where in references.items() if found == name or found.startswith(name + ".")
            for path in where}


def test_the_callers_that_render_an_allowance_and_the_observers_they_start_are_these():
    references = _references()
    # The renderers: the MCP tool and the plugin hook, through the one report; advice is rendered there alone.
    assert _users(references, "g6.allowance.report") == {"mcp/__init__.py", "_allowance/hook.py"}
    assert _users(references, "g6._allowance.advice.advise") == {"allowance.py"}
    assert _users(references, "g6.allowance.display") == {"_allowance/advice.py"}
    # The observers, whether called or handed to a thread: Codex's refresh, from a report that asks for it, and
    # Claude's status-line process.
    assert _users(references, "g6._allowance.store.start_refresh") == {"allowance.py"}
    assert _users(references, "g6._allowance.store.refresh_codex") == {"_allowance/store.py"}
    assert _users(references, "g6._allowance.statusline.observe_in_worker") == {"_allowance/statusline.py"}
    assert _users(references, "g6._allowance.statusline.observe_payload") == {"_allowance/statusline.py"}
    assert _users(references, "g6._allowance.statusline.run_wrapper") == {"_allowance/statusline.py"}
    # A method is reached through a value, not an import: any attribute of that name anywhere counts.
    assert {path for path, tree in _trees() for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "observe_claude"} == {"_allowance/statusline.py"}
    # The one thread an observer runs in is the refresh's, and the hook's one report asks for no refresh.
    handed = [node for node in references["g6._allowance.store.refresh_codex"]["_allowance/store.py"]]
    targets = [k for _, tree in _trees() for call in ast.walk(tree) if isinstance(call, ast.Call)
               for k in call.keywords if k.arg == "target" and any(k.value is node for node in handed)]
    assert len(handed) == len(targets) == 1
    calls = {id(node.func): node for _, tree in _trees() for node in ast.walk(tree) if isinstance(node, ast.Call)}
    reports = [calls[id(node)] for node in references["g6.allowance.report"]["_allowance/hook.py"]]
    assert [ast.unparse(node) for node in reports] == ["report(refresh=False)"], "the hook starts no observer"


def _home(tmp_path, monkeypatch, profiles):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    root = tmp_path / "allowances"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
    return root


@functools.cache
def _server():
    from g6.mcp import build_server

    return build_server()


def mcp_tool():
    """The real tool, as an agent calls it."""
    from fastmcp import Client

    async def call():
        async with Client(_server()) as client:
            return (await client.call_tool("g6_allowances", {"useful_parallel_work": True})).data
    return asyncio.run(call())


def hook(capsys):
    """The real plugin hook; its one context line, or nothing."""
    from g6._allowance import hook as module

    capsys.readouterr()
    assert module.main() == 0
    printed = capsys.readouterr().out
    return json.loads(printed)["hookSpecificOutput"]["additionalContext"] if printed.strip() else ""


class Observer:
    """A stand-in for Codex's raw reader, never the provider's executable."""

    def __init__(self, kind):
        self.kind, self.calls = kind, 0
        self.entered, self.release, self.left = threading.Event(), threading.Event(), threading.Event()

    def read(self):
        self.calls += 1
        self.entered.set()
        try:
            if self.kind in {"slow", "hung"}:
                self.release.wait(60)
            if self.kind == "failing":
                raise OSError("synthetic provider failure")
            return CODEX
        finally:
            self.left.set()


@contextmanager
def codex(monkeypatch, kind):
    from g6.self_maintenance import usage

    observer = Observer(kind)
    monkeypatch.setattr(usage, "codex_limits", observer.read)
    try:
        yield observer
    finally:
        observer.release.set()
        if observer.entered.is_set():
            observer.left.wait(15)


def _saved(root):
    from g6._allowance.store import Store

    return Store(root).load("codex", "p")


def _until(condition, seconds=15):
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            return False
        time.sleep(.02)
    return True


@pytest.mark.parametrize("kind", ["fast", "slow", "hung", "failing"])
def test_the_mcp_tool_never_waits_for_codex_and_never_stops_it(tmp_path, monkeypatch, kind):
    root = _home(tmp_path, monkeypatch, {"codex": "p"})
    with codex(monkeypatch, kind) as observer:
        first = mcp_tool()["providers"]["codex"]
        assert observer.entered.wait(15), "a report that asks for a refresh starts the observer"
        if kind in {"slow", "hung"}:
            assert not observer.left.is_set(), "the reply came back while the observation was still in flight"
            assert first["windows"] == [] and first["recommendation"] == "unknown"
            again = mcp_tool()["providers"]["codex"]  # a second reply beside the same observation
            assert observer.calls == 1 and again["windows"] == [] and not observer.left.is_set()
        if kind == "hung":
            return  # still in flight when the replies are done: nothing waited for it and nothing stopped it
        observer.release.set()
        assert observer.left.wait(15)
        assert _until(lambda: _saved(root).failure != "no observation"), "the observation was dropped"
        after = datetime.now(UTC)
        late = mcp_tool()["providers"]["codex"]
        if kind == "failing":
            assert late["failure"] == "read failed" and late["windows"] == [] and late["recommendation"] == "unknown"
            return
        row = next(w for w in late["windows"] if w["identity"] == "default.primary")
        # The reading carries the time it was read, and its age is counted from that time.
        assert datetime.fromisoformat(row["observed_at"]) <= after and row["age_seconds"] >= 0
        assert row["availability"] == "available" and row["headroom_share"] == .9


@pytest.mark.parametrize("kind", ["fast", "slow", "hung", "failing"])
def test_the_hook_starts_no_observer_whatever_state_one_would_be_in(tmp_path, monkeypatch, capsys, kind):
    _home(tmp_path, monkeypatch, {"codex": "p", "claude": "q"})
    with codex(monkeypatch, kind) as observer:
        note = hook(capsys)
        assert observer.calls == 0 and not observer.entered.is_set()
        assert "Codex unknown; Claude unknown" in note
        assert not any(thread.name == "g6-allowance-refresh" for thread in threading.enumerate())


@pytest.mark.parametrize("caller", ["mcp", "hook"])
def test_an_observer_holding_the_cache_delays_no_reply_and_shows_nothing_as_current(tmp_path, monkeypatch, capsys, caller):
    """Claude's observer is another process; the most it can do to a reply is hold the cache's write lock."""
    from g6._allowance.readers import codex_reading
    from g6._allowance.store import Store

    root = _home(tmp_path, monkeypatch, {"codex": "p"})
    with codex(monkeypatch, "fast") as observer:
        store = Store(root)
        store.bind("codex", "p")
        store.save(codex_reading(CODEX, "p", datetime.now(UTC)))
        holder = sqlite3.connect(store.path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")  # an observer stopped in the middle of its write
        try:
            if caller == "mcp":
                reply = mcp_tool()
                assert all(r["windows"] == [] and r["recommendation"] == "unknown" and not r["suggest_increase"]
                           for r in reply["providers"].values())
                assert observer.calls == 0, "no refresh is started from a cache that cannot be read"
            else:
                assert hook(capsys) == ""
        finally:
            holder.rollback()
            holder.close()
        # Nothing was lost to the reply, and the next reply reads the cache again.
        if caller == "mcp":
            assert any(w["availability"] == "available" for w in mcp_tool()["providers"]["codex"]["windows"])
        else:
            assert "G6 allowances" in hook(capsys)


@pytest.mark.parametrize("caller", ["mcp", "hook"])
def test_no_caller_shows_a_reading_past_its_age_as_current(tmp_path, monkeypatch, capsys, caller):
    """A reading's age is counted from the time it was read, whenever its observer wrote it."""
    from g6._allowance.advice import Settings
    from g6._allowance.readers import codex_reading
    from g6._allowance.store import Store

    root = _home(tmp_path, monkeypatch, {"codex": "p"})
    with codex(monkeypatch, "hung"):
        store = Store(root)
        store.bind("codex", "p")
        limit = Settings().max_age_seconds
        read_at = datetime.now(UTC) - timedelta(seconds=limit + 30)
        store.save(codex_reading(CODEX, "p", read_at))  # written only now, by an observer that ended late
        if caller == "hook":
            assert "Codex unknown" in hook(capsys)
            return
        reply = mcp_tool()["providers"]["codex"]
        assert reply["recommendation"] == "unknown" and not reply["suggest_increase"]
        for row in reply["windows"]:
            assert row["availability"] != "available" and row["headroom_share"] is None
        row = next(w for w in reply["windows"] if w["identity"] == "default.primary")
        assert row["availability"] == "stale" and row["age_seconds"] > limit
        assert datetime.fromisoformat(row["observed_at"]) == read_at


def test_every_window_a_caller_shows_carries_its_time_and_its_age(tmp_path, monkeypatch):
    from g6._allowance.readers import claude_reading, codex_reading
    from g6._allowance.store import Store

    root = _home(tmp_path, monkeypatch, {"codex": "p", "claude": "q"})
    with codex(monkeypatch, "hung"):
        store = Store(root)
        store.bind("codex", "p")
        store.bind("claude", "q")
        at = datetime.now(UTC) - timedelta(seconds=5)
        store.save(codex_reading(CODEX, "p", at))
        store.save(claude_reading({"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": 2000000000}}},
                                  "q", at, version="2.1.319"))
        reply = mcp_tool()
    rows = [row for provider in reply["providers"].values() for row in provider["windows"]]
    assert {row["identity"] for row in rows if row["availability"] == "available"} == {"default.primary", "five_hour"}
    for row in rows:
        assert {"observed_at", "attempted_at", "last_good_at", "age_seconds", "availability"} <= set(row)
        if row["availability"] == "available":
            assert datetime.fromisoformat(row["observed_at"]) == at and 5 <= row["age_seconds"] < 120
        else:
            assert row["headroom_share"] is None
    assert all(provider["last_attempt_at"] for provider in reply["providers"].values())
