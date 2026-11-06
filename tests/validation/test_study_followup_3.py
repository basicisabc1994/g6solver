# SPDX-License-Identifier: Apache-2.0
"""The study-trust follow-up's review 3: its findings on 29768de, each a test that failed on 29768de for the reason the review gave,
and the classes they belong to, each removed at its root with an inventory built from the code.

1. A session's ending is decided once, from all its facts (findings 1, 4 and 7; failure_read_as_success and one_fact_two_rules):
   one rule (``run.session_end``) every way out of ``run_session`` and ``host_session`` returns through; a model refusal is one
   fact among the others and never hides a failed read, a failed exit or a failed cleanup; each cleanup step runs in its own guard
   (``run.cleanup``), so one failing never skips another, and its failure is part of the ending.
2. A file's name in a patch is the one git's own reader gives (finding 5; own_parser_for_foreign_format): ``layers`` reads no
   name itself. Finding 2 (identity_from_rendering: one edit read differently under different context) is offered, not repaired:
   every root repair changes a behaviour earlier repairs pinned, and the archive holds no parent for 32 of the 34 bugs.

Fakes only: no Docker, no model, no network; git is run only to read a patch (``git apply --numstat``)."""
from __future__ import annotations

import ast
import builtins
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import layers  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
INIT = {"type": "system", "subtype": "init", "tools": ["Read", "Edit", "Write", "Glob", "Grep", "mcp__box__run"],
        "mcp_servers": [{"name": "box", "status": "connected"}]}
SAID_NO = {"type": "assistant", "message": {"model": c.MODEL, "stop_reason": "refusal"}}
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"}


def _called(func) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _own(fn) -> list:
    """The nodes of ``fn``'s own body, not those of a def, a lambda or a class inside it."""
    out, todo = [], list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        out.append(node)
        if not isinstance(node, (*FUNCS, ast.Lambda, ast.ClassDef)):
            todo.extend(ast.iter_child_nodes(node))
    return out


def _functions(module: str) -> dict:
    tree = ast.parse((STUDY / f"{module}.py").read_text(encoding="utf-8"))
    return {fn.name: fn for fn in ast.walk(tree) if isinstance(fn, FUNCS)}


def stream_of(*records) -> bytes:
    return b"".join(json.dumps(r).encode() + b"\n" for r in records)


# --- 1: a session's ending is decided once, from all its facts ----------------------------------------------------------------
class Proc:
    """A session's process (its launcher on the host, its client in a container): it exits with ``code`` by itself, or runs on
    until it is ended (``hangs``). What was done to it is kept."""

    def __init__(self, out, *, code=0, hangs=False, stderr=None):
        self.stdin, self.stdout, self.stderr = io.BytesIO(), out, io.BytesIO() if stderr is None else stderr
        self.code, self.hangs, self.killed, self.waited = code, hangs, False, False

    def poll(self):
        return None if self.hangs and not self.killed else self.code

    def kill(self):
        self.killed = True
        self.code = -9 if self.hangs else self.code

    def wait(self, timeout=None):
        self.waited = True
        if self.poll() is None:
            raise subprocess.TimeoutExpired("fake session", timeout)
        return self.code


class Unread:
    """A process's stderr that cannot be read to its end."""

    def read(self):
        raise OSError("the session's stderr could not be read to its end")


class BrokenOut:
    """A process's stdout that gives its lines, then fails."""

    def __init__(self, data: bytes):
        self.lines = io.BytesIO(data).readlines()

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        raise OSError("the session's stdout could not be read to its end")


def failing_open(*args, **kwargs):
    """``open`` whose file closes, then says it failed to."""
    stream = builtins.open(*args, **kwargs)

    def close():
        stream.close()
        raise OSError("stream close failed")

    return SimpleNamespace(write=stream.write, flush=stream.flush, close=close)


def on_host(tmp_path, monkeypatch, proc, *, ending=lambda proc: ""):
    monkeypatch.setattr(we, "host_start", lambda *a, **kw: SimpleNamespace(proc=proc))
    monkeypatch.setattr(we, "end_session", lambda *a: ending(proc))
    monkeypatch.setattr(we.shutil, "which", lambda name: name)
    archive = c.Archive(tmp_path / "archive")
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    ctx = SimpleNamespace(model=c.MODEL, starter=None, archive=archive, environ={})
    ended = we.host_session(ctx, run, tmp_path, tmp_path / "mcp", tmp_path / "settings", "prompt", "stream.jsonl", seconds=60,
                            tally=c.Tally())
    return ended, archive, run


def in_container(tmp_path, monkeypatch, proc):
    monkeypatch.setattr(we, "unbounded", lambda docker: docker)
    archive = c.Archive(tmp_path / "archive")
    run = archive.start("run", "bug", arm="K2", repeat=1)
    docker = SimpleNamespace(popen=lambda *a, **kw: proc, sh=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b""))
    ctx = SimpleNamespace(docker=docker, client_env=lambda key: {}, archive=archive)
    return we.run_session(ctx, run, "agent", ["exec"], "key", c.Tally(), "prompt", "stream.jsonl", seconds=60.0, stop_usd=10.0)


def test_finding_1_a_model_refusal_does_not_hide_a_failed_stderr_reader(tmp_path, monkeypatch):
    proc = Proc(io.BytesIO(stream_of(INIT, SAID_NO, SUCCESS)), stderr=Unread())
    real_unread, reader_endings = we.unread, []

    def unread(*args, **kwargs):
        reader_endings.append(real_unread(*args, **kwargs))
        return reader_endings[-1]

    monkeypatch.setattr(we, "unread", unread)
    ended, archive, run = on_host(tmp_path, monkeypatch, proc)
    assert reader_endings == ["error"]
    assert we.host_facts(run.path / "stream.jsonl", c.MODEL)["refusals"]
    assert ended == "error", "failed stderr read was masked by model_refused"
    assert c.ending(archive, run.rel)["status"] == "error"


def test_finding_4_a_model_refusal_does_not_hide_the_sessions_own_failed_exit(tmp_path, monkeypatch):
    proc = Proc(io.BytesIO(stream_of(INIT, SAID_NO, SUCCESS)), code=1)
    ended, archive, run = on_host(tmp_path, monkeypatch, proc)
    exit_record = json.loads((run.path / "stream_exit.json").read_bytes())
    assert exit_record["exit_code"] == 1 and exit_record["ended_by_g6"] is False
    assert we.session_ending("", 1, SUCCESS) == "error"
    assert ended == "error", "host refusal bypassed session_ending and hid the process's own failed exit"
    assert c.ending(archive, run.rel)["status"] == "error"


@pytest.mark.parametrize("executor", ["host", "container"])
def test_finding_7_a_failed_close_of_the_sessions_stream_still_ends_and_waits_for_it_and_ends_it_error(tmp_path, monkeypatch,
                                                                                                         executor):
    """The review's case: closing the stream's file fails while the process still runs. Every other cleanup step still runs (the
    process is ended and its exit read), and the failure is the session's ending, "error", never lost."""
    proc = Proc(io.BytesIO(stream_of(INIT, SUCCESS)), hangs=True)
    monkeypatch.setattr(we, "open", failing_open, raising=False)
    if executor == "host":
        ended, *_ = on_host(tmp_path, monkeypatch, proc, ending=lambda p: p.kill() or "")
    else:
        ended = in_container(tmp_path, monkeypatch, proc)
    assert proc.killed and proc.waited, "closing the log skipped session termination and its exit wait"
    assert ended == "error"


REFUSED = stream_of(INIT, SAID_NO, SUCCESS)


@pytest.mark.parametrize(("failure", "ended"), [
    ("none", "model_refused"),             # a refusal alone: a run made, scored on its patch, as before
    ("ended by G6", "model_refused"),      # its process outlived its stream and G6 ended it: its code is G6's
    ("stdout read", "error"),              # the pattern the review did not name: a failed read of its stream
    ("stderr read", "error"),              # finding 1
    ("exit", "error"),                     # finding 4
    ("close", "error"),                    # finding 7
])
def test_a_refusal_is_one_fact_and_never_hides_a_failed_read_exit_or_close(tmp_path, monkeypatch, failure, ended):
    proc = Proc(BrokenOut(REFUSED) if failure == "stdout read" else io.BytesIO(REFUSED), code=1 if failure == "exit" else 0,
                hangs=failure == "ended by G6", stderr=Unread() if failure == "stderr read" else None)
    if failure == "close":
        monkeypatch.setattr(we, "open", failing_open, raising=False)
    got, archive, run = on_host(tmp_path, monkeypatch, proc)
    assert we.host_facts(run.path / "stream.jsonl", c.MODEL)["refusals"]
    assert got == ended, failure
    assert proc.waited and (proc.killed or not proc.hangs)
    assert (c.ending(archive, run.rel) or {}).get("status") == (None if ended == "model_refused" else "error")


@pytest.mark.parametrize("failure", ["close", "exit", "stdout read"])
def test_a_container_sessions_failed_close_exit_or_read_ends_it_error(tmp_path, monkeypatch, failure):
    proc = Proc(BrokenOut(stream_of(SUCCESS)) if failure == "stdout read" else io.BytesIO(stream_of(SUCCESS)),
                code=125 if failure == "exit" else 0)
    if failure == "close":
        monkeypatch.setattr(we, "open", failing_open, raising=False)
    assert in_container(tmp_path, monkeypatch, proc) == "error"
    assert proc.waited


NOT_SAID = {"limit": None, "refused": "", "refusals": (), "failed": ()}
CAP = {**SUCCESS, "subtype": "error_max_turns", "is_error": True}
ERROR = {**SUCCESS, "is_error": True}


@pytest.mark.parametrize(("stop", "code", "result", "by_g6", "facts", "ended"), [
    ("", 0, SUCCESS, False, {}, ""),
    ("", 0, SUCCESS, False, {"refusals": [{}]}, "model_refused"),
    ("", 0, ERROR, False, {"refusals": [{}]}, "model_refused"),           # Claude Code marks a refused request's result an error
    ("timeout", -9, SUCCESS, True, {"refusals": [{}]}, "model_refused"),   # over a stop at the clock, as before
    ("", -9, None, True, {"refusals": [{}]}, "model_refused"),             # G6 ended it: its code is G6's
    ("", 1, SUCCESS, False, {"refusals": [{}]}, "error"),                  # a failed exit (finding 4)
    ("", None, SUCCESS, False, {"refusals": [{}]}, "error"),               # an exit nobody read
    ("", True, SUCCESS, False, {"refusals": [{}]}, "error"),
    ("error", 0, SUCCESS, False, {"refusals": [{}]}, "error"),             # its stream not read to its end
    ("", 0, SUCCESS, False, {"refusals": [{}], "failed": ["stderr"]}, "error"),    # finding 1
    ("", 0, SUCCESS, False, {"refusals": [{}], "failed": ["close"]}, "error"),     # finding 7
    ("", 0, SUCCESS, False, {"refusals": [{}], "limit": "resets 5pm"}, "limited"),
    ("", 0, SUCCESS, False, {"refusals": [{}], "refused": "an init with Bash"}, "refused"),
    ("", 1, SUCCESS, False, {"failed": ["close"], "limit": ""}, "limited"),          # the limit first, as before
    ("", 0, SUCCESS, False, {"failed": ["close"]}, "error"),
    ("timeout", 0, SUCCESS, False, {"failed": ["the exit"]}, "error"),
    ("", 1, CAP, False, {}, "cap_turns"),                                  # the session rule as it was: its result's cap first
    ("timeout", 0, SUCCESS, False, {}, "timeout"),
])
def test_the_one_rule_of_a_sessions_ending_from_every_fact_it_has(stop, code, result, by_g6, facts, ended):
    got, why = we.session_end(stop, code, result, ended_by_g6=by_g6, **{**NOT_SAID, **facts})
    assert got == ended
    assert bool(why) is (got in ("error", "refused") or got == "limited" and bool(facts["limit"]))


def test_the_one_rule_takes_every_fact_and_no_caller_can_leave_one_out():
    for left_out in ("ended_by_g6", "failed", "limit", "refused", "refusals"):
        given = {"ended_by_g6": False, **NOT_SAID}
        given.pop(left_out)
        with pytest.raises(TypeError):
            we.session_end("", 0, SUCCESS, **given)


def test_every_way_out_of_a_session_returns_through_the_one_rule():
    """From run.py's own code: in ``run_session`` and ``host_session`` the one rule (``session_end``) is called once, every return
    gives its value, the names it is bound to are bound nowhere else, and ``host_ending`` (its host stream's reading) gives its
    value too."""
    fns = _functions("run")
    for name in ("run_session", "host_session", "host_ending"):
        own = _own(fns[name])
        rules = [n for n in own if isinstance(n, ast.Call) and _called(n.func) == "session_end"]
        assert len(rules) == 1, name
        held = {t.id for n in own if isinstance(n, ast.Assign) and n.value is rules[0]
                for target in n.targets for t in ast.walk(target) if isinstance(t, ast.Name)}
        returns = [n for n in own if isinstance(n, ast.Return)]
        assert returns, name
        for ret in returns:
            value = ret.value.value if isinstance(ret.value, ast.Subscript) else ret.value
            assert value is rules[0] or (isinstance(value, ast.Name) and value.id in held), (name, ast.unparse(ret))
        others = [n for n in own if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and n.value is not rules[0]
                  for target in (n.targets if isinstance(n, ast.Assign) else [n.target]) for t in ast.walk(target)
                  if isinstance(t, ast.Name) and t.id in held]
        assert not others, (name, [ast.unparse(n) for n in others])


def _steps(name: str) -> list[str]:
    """The cleanup steps of a session function, from its code: each ``with cleanup(...)`` in its one ``finally``, in the order
    written. Every call there is made inside one of them: no step is unguarded."""
    fn = _functions("run")[name]
    tries = [n for n in _own(fn) if isinstance(n, ast.Try) and n.finalbody]
    assert len(tries) == 1, name
    nodes = [n for stmt in tries[0].finalbody for n in ast.walk(stmt)]
    guarded = sorted((n for n in nodes if isinstance(n, ast.With) and len(n.items) == 1
                      and isinstance(n.items[0].context_expr, ast.Call) and _called(n.items[0].context_expr.func) == "cleanup"),
                     key=lambda n: n.lineno)
    inside = {id(x) for g in guarded for x in ast.walk(g)}
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    assert calls and all(id(n) in inside for n in calls), (name, [ast.unparse(n) for n in calls if id(n) not in inside])
    return [g.items[0].context_expr.args[1].value for g in guarded]


@pytest.mark.parametrize("executor", ["container", "host"])
def test_every_cleanup_step_runs_when_each_other_one_raises(tmp_path, monkeypatch, executor):
    """The inventory, from the code: each cleanup step of each session function made to fail in turn, after doing its work, on a
    process that outlives its stream, so every step is reached; every other step still runs, and the session ends "error"."""
    steps = _steps("run_session" if executor == "container" else "host_session")
    assert len(steps) == (5 if executor == "container" else 7)
    real = we.cleanup
    for at, failing in enumerate([None, *steps]):
        attempted = []

        @contextlib.contextmanager
        def cleanup(failed, what, failing=failing, attempted=attempted):
            attempted.append(what)
            with real(failed, what):
                yield
                if what == failing:          # the step did its work, then failed
                    raise OSError(f"{what}: made to fail by the test")

        with monkeypatch.context() as patch:
            patch.setattr(we, "cleanup", cleanup)
            proc = Proc(io.BytesIO(stream_of(INIT, SUCCESS)), hangs=True)
            folder = tmp_path / str(at)
            folder.mkdir()
            got = in_container(folder, patch, proc) if executor == "container" else on_host(folder, patch, proc)[0]
        assert attempted == steps, (failing, attempted)
        assert got == ("" if failing is None else "error"), failing
        assert proc.killed and proc.waited


# --- 2: a file's name in a patch is the one git's own reader gives ------------------------------------------------------------
def _git_names(patch: str, tmp_path: Path) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path.parent)
    done = subprocess.run(["git", "apply", "--numstat", "-z"], input=patch.encode(), cwd=tmp_path, env=env, capture_output=True,
                          timeout=30)        # --numstat only reads the patch: no index, work tree or git state is written
    assert done.returncode == 0, done.stderr
    return c.numstat_names(done.stdout)


def test_finding_5_a_files_name_is_the_one_git_reads_however_its_header_spells_it(tmp_path):
    patches = [f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-x = 0\n+x = 1\n"
               for name in ("pkg/api.py", "pkg//api.py")]
    assert [_git_names(p, tmp_path) for p in patches] == [["pkg/api.py"], ["pkg/api.py"]]
    assert layers.change(patches[0]) == layers.change(patches[1]), "layers split the same Git path into pkg/api.py and pkg//api.py"


def _spelled(old: str, new: str) -> str:
    return (f"diff --git {old} {new}\n--- {old}\n+++ {new}\n@@ -1,3 +1,3 @@\n def value():\n-    return 0\n+    return 1\n"
            " # end\n")


SPELLINGS = {
    "doubled separators": ("a/pkg//api.py", "b/pkg//api.py"),
    "a dot part": ("a/./pkg/api.py", "b/./pkg/api.py"),
    "quoted": ('"a/pkg/api.py"', '"b/pkg/api.py"'),
    "quoted, with doubled separators": ('"a/pkg//api.py"', '"b/pkg//api.py"'),
    "other prefixes": ("old/pkg/api.py", "new/pkg/api.py"),
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_one_edit_has_one_identity_however_its_files_name_is_spelled(spelling):
    plain = layers.change(_spelled("a/pkg/api.py", "b/pkg/api.py"))
    assert plain and plain[0][0] == "pkg/api.py"
    assert layers.change(_spelled(*SPELLINGS[spelling])) == plain, spelling
    escaped = layers.change(_spelled('"a/pkg/\\303\\251.py"', '"b/pkg/\\303\\251.py"'))   # a name git escapes, and as UTF-8
    assert escaped == layers.change(_spelled("a/pkg/é.py", "b/pkg/é.py")) and escaped[0][0] == "pkg/é.py"


def test_a_part_git_cannot_read_has_no_name_and_is_kept_whole_as_written():
    corrupt = "diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -x +y @@\n-x = 0\n+x = 1\n"     # no hunk git can read
    got = layers.change(corrupt)
    assert got == (("", (("", layers.AS_WRITTEN, corrupt),)),)          # never a name guessed, never dropped
    assert layers.change(corrupt.replace("x = 1", "x = 2")) != got
    tests_only = "diff --git a/tests/test_api.py b/tests/test_api.py\n--- a/tests/test_api.py\n+++ b/tests/test_api.py\n" \
                 "@@ -1 +1 @@\n-x = 0\n+x = 1\n"
    assert layers.change(tests_only) == ()                          # a test file git names is no source change, as before


#: Every function of the study that reads which file a patch changes, from the code (it splits a patch at git's own "diff --git"
#: line, reads a header's "---" or "+++" line, or asks git's reader of the format): how. Only product_review.touched reads a header
#: itself, as the product's own listing of a patch's files does; layers reads no name but git's.
PATCH_NAMES = {
    ("export_reviewer_cases", "changed"): "parts, each named by git",
    ("export_reviewer_cases", "touched"): "git",
    ("layers", "change"): "parts, each named by git",
    ("layers", "named"): "git",
    ("product_review", "touched"): "its own reading of a header, as the product's listing reads it",
    ("run", "test_changes"): "git",
}
OWN_READERS = {"_sides", "_applied", "_unquoted"}


def _header_texts(fn) -> set[str]:
    """What ``fn`` looks for at a line's start (``startswith``), or splits a patch at (a pattern of git's "diff --git" line)."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _called(n.func) == "startswith":
            out |= {x.value for a in n.args for x in ast.walk(a) if isinstance(x, ast.Constant) and isinstance(x.value, str)}
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "(?=diff --git" in n.value:
            out.add(n.value)
    return out


#: The readers of a patch's file names by git's own reader: git's machine-readable output (``numstat_names``), and the one helper
#: that runs git on a part (``export_reviewer_cases.touched``, as layers calls it).
BY_GIT = {"numstat_names", "c.numstat_names", "erc.touched"}


def test_every_reader_of_a_patchs_file_names_is_listed_and_layers_asks_git_alone():
    found = {}
    for path in sorted(STUDY.glob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, FUNCS):
                continue
            calls = {ast.unparse(n.func).removeprefix("pr.") for n in ast.walk(fn) if isinstance(n, ast.Call)}
            if calls & (OWN_READERS | BY_GIT) or any(
                    t.startswith(("diff --git", "+++ ", "--- ")) or "(?=diff --git" in t for t in _header_texts(fn)):
                found[(path.stem, fn.name)] = calls
    assert set(found) == set(PATCH_NAMES), sorted(found)
    for (module, name), calls in found.items():
        assert bool(calls & OWN_READERS) is (module == "product_review"), (module, name)
    assert found[("layers", "named")] & BY_GIT and "named" in found[("layers", "change")]
