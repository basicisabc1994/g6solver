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
3. A file G6 writes that the one writer of store files does not guard is made where nothing else can be (finding 3;
   guard_covers_less_than_it_guards): created exclusively, in a folder made fresh for it (``wecommon.created``, ``fresh``).
4. No ending is no value (finding 6; boundary_case): no comparison in the rows' writer counts two absent facts as a match.

Fakes only: no Docker, no model, no network; git is run only to read a patch (``git apply --numstat``)."""
from __future__ import annotations

import ast
import builtins
import contextlib
import hashlib
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

import final_review as fr  # noqa: E402
import layers  # noqa: E402
import loop  # noqa: E402
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


# --- 3: a file G6 writes beside the store writer is made where nothing else can be ---------------------------------------------
def test_finding_3_the_private_review_copy_cannot_overwrite_a_saved_run_row(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    row = c.keep(archive, {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: c.ending_sha256(archive, run.rel)})
    before = row.read_bytes()
    patch = tmp_path / "candidate.patch"
    raw = b"diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -1 +1 @@\n-x = 0\n+x = 1\n"
    patch.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    staging = tmp_path / "critic_packs" / f"held-{sha}-{os.getpid()}.patch"
    staging.parent.mkdir()
    os.link(row, staging)
    assert os.path.samefile(staging, row)
    held = []
    try:
        with fr.held(SimpleNamespace(archive=archive), patch) as (own, got):
            held.append((own.read_bytes(), got, os.path.samefile(own, row)))
    except (ValueError, OSError, c.ArchiveError):
        pass  # refusing the alias made in advance also keeps the verdict
    assert row.read_bytes() == before, "private review copy overwrote the saved row through its staging hard link"
    assert c.shelf(archive, lambda task: None).get(("A", "bug", 1)).passes is True
    assert held == [(raw, sha, False)]                     # its own copy, of the bytes read, in a file no other name holds
    assert [p.name for p in (tmp_path / "critic_packs").iterdir()] == [staging.name]      # its own folder went with it


def test_a_file_g6_makes_is_refused_where_anything_is_already_there_and_nothing_is_written_through(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"another file's bytes")
    folder = c.fresh(tmp_path / "packs")
    assert folder.parent == tmp_path / "packs" and not list(folder.iterdir())
    os.link(outside, folder / "taken")
    with pytest.raises(c.ArchiveError, match="exists"):
        c.created(folder / "taken", b"new")
    assert outside.read_bytes() == b"another file's bytes"
    assert c.created(folder / "text.txt", "a\nb").read_bytes() == "a\nb".replace("\n", os.linesep).encode()   # as write_text
    archive = c.Archive(tmp_path / "archive")
    run = archive.start("run", "bug", arm="A", repeat=1)
    os.link(outside, run.path / "final.patch")
    with pytest.raises(c.ArchiveError, match="exists"):
        run.write("final.patch", b"candidate")
    assert outside.read_bytes() == b"another file's bytes"


def test_the_index_is_never_appended_to_through_a_second_name(tmp_path):
    archive = c.Archive(tmp_path)
    archive.start("run", "bug", arm="A", repeat=1)
    outside = tmp_path / "outside.json"
    outside.write_bytes(archive.index.read_bytes())
    archive.index.unlink()
    os.link(outside, archive.index)
    before = outside.read_bytes()
    with pytest.raises(c.ArchiveError, match="second name"):
        archive.append({"event": "note"})
    assert outside.read_bytes() == before


def test_a_harness_file_already_there_is_refused_never_written_over(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"another file's bytes")
    os.link(outside, harness / "G1.txt")
    monkeypatch.setattr(we, "HARNESS", harness)
    monkeypatch.setattr(loop, "spent", lambda root: 0.0)
    monkeypatch.setattr(loop.distill, "distill", lambda *a, **kw: ("a candidate text", []))
    monkeypatch.setattr(loop, "commit", lambda *a: pytest.fail("nothing is committed once the file is refused"))
    gens = loop.Generations.__new__(loop.Generations)
    gens.root, gens.best, gens.best_text, gens.best_arm, gens.caller, gens.notes, gens.n, gens.misses = \
        tmp_path / "archive", [], "", "L9", None, [], 1, 0
    with pytest.raises(c.ArchiveError, match="exists"):
        gens.distilled(harness / "G1.txt")
    assert outside.read_bytes() == b"another file's bytes"


#: Every write of a file in the modules that keep a store beside the saved rows, by module and function, from the code: each call
#: of the one writer of store files (``write_whole``), of the maker of every other file (``created``, ``fresh``, and a run's own
#: ``RunFolder.write``) and of every write primitive; and how what it writes is guarded. Kinds: "store writer"; "created" (made
#: exclusively, in a folder made fresh for it or a run's own); "made" (the maker itself: ``open`` "xb", ``mkdir`` with no
#: exist_ok); "lock" (made exclusively, or opened only to be locked); "index" (appended to, refused with a second name); "sync"
#: and "read" (opened, never written); "no file" (``str.replace``, ``copy.copy``, ``dataclasses.replace``, a tar read).
GUARDS = {
    ("evolve", "Training.proposed"): ("created", ["created", "write", "write", "write_whole"]),
    ("evolve", "train"): ("store writer", ["write_whole"]),
    ("final_review", "_review"): ("created", ["created", "created", "created", "created", "open", "write_text"]),
    ("final_review", "_step"): ("created", ["created", "created", "created", "created", "open", "write_text"]),
    ("final_review", "held"): ("created", ["created", "fresh"]),
    ("final_review", "keep"): ("store writer", ["write_whole"]),
    ("loop", "Generations.distilled"): ("created", ["created", "write"]),
    ("loop", "main"): ("store writer", ["write_whole"]),
    ("product_review", "read_at"): ("no file", ["replace", "replace"]),
    ("replay3", "main"): ("lock", ["one_runner"]),
    ("replay3", "one_runner"): ("lock", ["open", "write"]),
    ("replay3", "review_bug"): ("created", ["created", "fresh", "write_whole"]),
    ("replay3", "run_on"): ("no file", ["copy"]),
    ("wecommon", "Archive.append"): ("index", ["open", "open", "truncate", "write", "write"]),
    ("wecommon", "Redactor.__call__"): ("no file", ["replace"]),
    ("wecommon", "RunFolder.write"): ("created", ["created"]),
    ("wecommon", "RunFolder.write_json"): ("created", ["write"]),
    ("wecommon", "_locked"): ("lock", ["open"]),
    ("wecommon", "_unique"): ("no file", ["replace", "replace"]),
    ("wecommon", "created"): ("made", ["open", "replace", "write"]),
    ("wecommon", "durable"): ("sync", ["open"]),
    ("wecommon", "fresh"): ("made", []),
    ("wecommon", "keep"): ("store writer", ["write_whole"]),
    ("wecommon", "sha256_file"): ("read", ["open"]),
    ("wecommon", "write_whole"): ("store writer", ["fdopen", "mkstemp", "replace", "replace", "write"]),
}
PRIMITIVES = {"write_text", "write_bytes", "write", "replace", "rename", "link", "hardlink_to", "symlink_to", "mkstemp", "dump",
              "copy", "copy2", "copyfile", "copytree", "move", "truncate", "open", "fdopen", "touch"}
MAKERS = {"write_whole", "created", "fresh", "one_runner"}


def _writes() -> dict[tuple[str, str], list[str]]:
    """Every write primitive and every call of a maker of files, in every module that defines or calls a store's writer or checker
    (``beside``, ``keep``, ``write_whole``), by the function it is in, from the code; a maker that calls none has no calls."""
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(STUDY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {_called(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)} | \
            {n.name for n in ast.walk(tree) if isinstance(n, FUNCS)}
        if not names & {"beside", "keep", "write_whole"}:
            continue

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (*FUNCS, ast.ClassDef)):
                    inner = f"{where}.{child.name}" if where else child.name
                    if isinstance(child, FUNCS) and child.name in MAKERS:
                        found.setdefault((path.stem, inner), [])
                    walk(child, inner)
                    continue
                if isinstance(child, ast.Call) and _called(child.func) in PRIMITIVES | MAKERS:
                    found.setdefault((path.stem, where), []).append(_called(child.func))
                walk(child, where)

        walk(tree, "")
    return {k: sorted(v) for k, v in found.items()}


def _mode(call) -> str:
    """The mode an ``open`` call names (``open(path, "xb")`` or ``path.open("a+b")``), "" for none."""
    return next((a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)), "") or next(
        (k.value.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), "")


def test_every_file_g6_writes_beside_the_store_writer_is_made_where_nothing_else_can_be():
    """The write inventory, from the code: no write is excused by what it is for. Each is the one writer of store files, or made by
    ``created`` (``open`` "xb", in a folder ``fresh`` makes with no exist_ok, a critic's pack made the same way, or a run's own
    folder), or one of the files that cannot be made fresh, each with its own guard: the replay's lock (O_EXCL) and the index's
    (opened only to be locked), the index itself (refused with a second name), a file synced or read. A file-writing primitive
    (``write_bytes``, ``copyfile`` and the like) is nowhere; ``write_text`` only writes a file its function claimed by
    ``created`` on an earlier line (a critic pack's issue, whose line is kept as it was)."""
    assert _writes() == {k: v[1] for k, v in GUARDS.items()}
    assert not {p for k, v in _writes().items() for p in v} & {"write_bytes", "copyfile", "copy2", "copytree", "move", "dump",
                                                                "touch", "link", "hardlink_to", "symlink_to", "rename"}
    fns = {(module, name): fn for module in {m for m, _ in GUARDS} for name, fn in _functions(module).items()}
    for key in GUARDS:
        fn = fns[(key[0], key[1].split(".")[-1])]
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        claims = {(ast.unparse(n.args[0]), n.lineno) for n in calls if _called(n.func) == "created"}
        for n in calls:
            if _called(n.func) == "write_text":
                target = ast.unparse(n.func.value)
                assert any(path == target and line < n.lineno for path, line in claims), (key, ast.unparse(n))
    opens = {key: [n for n in ast.walk(fns[(key[0], key[1].split(".")[-1])]) if isinstance(n, ast.Call) and _called(n.func) == "open"]
             for key in GUARDS}
    assert [_mode(n) for n in opens[("wecommon", "created")]] == ["xb"]
    fresh = fns[("wecommon", "fresh")]
    mkdirs = [n for n in ast.walk(fresh) if isinstance(n, ast.Call) and _called(n.func) == "mkdir"]
    assert any(not n.keywords for n in mkdirs) and "secrets.token_hex" in ast.unparse(fresh)
    for key in (("final_review", "_review"), ("final_review", "_step")):      # the pack: made with no exist_ok
        assert "pack.mkdir(parents=True)" in {ast.unparse(n) for n in ast.walk(fns[key]) if isinstance(n, ast.Call)}
    for key in (("final_review", "held"), ("replay3", "review_bug")):
        assert {"fresh", "created"} <= {_called(n.func) for n in ast.walk(fns[key]) if isinstance(n, ast.Call)}
    assert "created" in {_called(n.func) for n in ast.walk(fns[("wecommon", "write")]) if isinstance(n, ast.Call)}
    assert [_mode(n) for n in opens[("wecommon", "_locked")]] == ["a+b"]
    assert not [n for n in ast.walk(fns[("wecommon", "_locked")]) if isinstance(n, ast.Call) and _called(n.func) == "write"]
    assert sorted(_mode(n) for n in opens[("wecommon", "Archive.append")]) == ["a+b", "xb"]
    assert "st_nlink" in ast.unparse(fns[("wecommon", "append")])
    assert "O_EXCL" in ast.unparse(fns[("replay3", "one_runner")])
    assert [_mode(n) for n in opens[("wecommon", "durable")]] == ["r+b"]
    assert [_mode(n) for n in opens[("wecommon", "sha256_file")]] == ["rb"]


# --- 4: no ending is no value ---------------------------------------------------------------------------------------------------
def test_finding_6_no_ending_cannot_authorize_a_new_row_without_a_digest(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    facts = c.run_facts(archive, run.rel)
    assert facts.ending is None and facts.ending_sha256 is None
    row = {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: facts.ending_sha256}
    assert c.verdict(archive, run.rel, row) is None
    with pytest.raises(c.ArchiveError):
        c.keep(archive, row)
    assert not (tmp_path / c.SAVED / "A-bug-1.json").exists()


#: Every comparison in the rows' writer (``keep``, the run's attributions it checks, ``disagrees``, and the one writer of store
#: files, ``write_whole``), from the code: whether two facts that may both be absent are compared, and what keeps two absences from
#: being a match. "presence": it tests a fact's presence itself; "present": one side is never absent; "guarded": both may be
#: absent, and the writer refuses an absent one before it compares.
COMPARISONS = {
    ("keep", "run not in read"): "presence",
    ("keep", "facts is None"): "presence",
    ("keep", "facts.repeat is None"): "presence",
    ("keep", "held.run != facts.name"): "present: a run's facts always name it",
    ("keep", "os.path.normcase(p.name) != os.path.normcase(dest)"): "present: two file names",
    ("keep", "now is None"): "presence",
    ("keep", "made != now"): "guarded: a run with no ending's digest (now is None) is refused first",
    ("disagrees", "key in row"): "presence",
    ("disagrees", "str(row.get(key) or '') != getattr(facts, key)"): "guarded: a start that names no arm and task is refused first",
    ("disagrees", "'repeat' in row"): "presence",
    ("disagrees", "said != facts.repeat"): "guarded: a start that names no repeat is refused",
    ("disagrees", "facts.repeat is not None"): "presence",
    ("write_whole", "os.path.normcase(os.fspath(where())) != os.path.normcase(os.fspath(dest))"): "present: two paths",
    ("write_whole", "os.lstat(path).st_nlink != 1"): "present: a count",
}


def test_no_comparison_in_the_rows_writer_counts_two_absent_facts_as_a_match(tmp_path):
    fns = _functions("wecommon")
    found = {(name, ast.unparse(n)) for name in ("keep", "disagrees", "write_whole") for n in ast.walk(fns[name])
             if isinstance(n, ast.Compare)}
    assert found == set(COMPARISONS), sorted(found ^ set(COMPARISONS))
    archive = c.Archive(tmp_path)
    cases = {
        "made != now": (archive.start("run", "bug", arm="A", repeat=1), {c.MADE_UNDER: None}),
        "str(row.get(key) or '') != getattr(facts, key)": (archive.start("run", "bug", repeat=2), {"arm": None}),
        "said != facts.repeat": (archive.start("run", "bug", arm="A"), {"repeat": None}),
    }
    assert set(cases) == {text for (_, text), how in COMPARISONS.items() if how.startswith("guarded")}
    for text, (run, said) in cases.items():
        if text != "made != now":
            run.finish({"agent_started": True}, "finished")
        row = {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: c.ending_sha256(archive, run.rel), **said}
        with pytest.raises(c.ArchiveError):
            c.keep(archive, row)
    assert not (tmp_path / c.SAVED).exists() or not [p for p in (tmp_path / c.SAVED).rglob("*") if p.is_file()]


def test_a_row_kept_before_with_no_digest_is_read_as_it_was(tmp_path, monkeypatch):
    """The PLAN of 2026-11-07: rows kept before a digest was kept with them are read as they were; only new rows need one."""
    monkeypatch.setattr(we, "amendment", lambda task: None)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    old = {"arm": "A", "task": "bug", "repeat": 1, "run": run.rel, "status": "finished", "passes": True, "failing": 0}
    (tmp_path / c.SAVED).mkdir()
    (tmp_path / c.SAVED / "A-bug-1.json").write_text(json.dumps(old), "utf-8")
    assert c.verdict(archive, run.rel, old) is True and c.shelf(archive, we.amendment).get(("A", "bug", 1)).passes is True
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, {**old, "passes": False})
