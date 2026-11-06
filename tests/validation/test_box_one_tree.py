# SPDX-License-Identifier: Apache-2.0
"""The replay cohort's defect 1 (PLAN.md, 2026-11-06): a file written by a command inside the box did not reach the kept patch.

The host's Claude Code edits the workspace; each call of the box's ``run`` mirrored the workspace into the bug's container and ran
the command there, and nothing came back, so the next mirror wrote the workspace over what the command changed, and every capture
read a tree without it (K3 pilot: starlette-3189 repeat 1, click-3404 repeat 2, black-5148 repeat 2). These tests drive scripted
sessions with no model: each session writes in the workspace as Claude Code's Write tool does, or calls the box's ``run`` with the
options the run's own MCP configuration gives the box. The container is a local folder (``test_host_author``'s fakes), the mirror
runs for real on it, and every capture is that folder's tree against the base, byte for byte. Fakes only: no docker, no Claude
Code, no network, no model."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import box_mcp as box  # noqa: E402
import run as we  # noqa: E402

_spec = importlib.util.spec_from_file_location("host_author_helpers_one_tree", Path(__file__).with_name("test_host_author.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)

PULL = getattr(box, "PULL", "\0the box has no pull\0")
BOX_EDIT = b"def f():\n    return 'written by a command in the box'\n"
BOX_NEW = b"made = 'by a command in the box'\n"
TOOL_NEW = b"tool = 'written by the Write tool'\n"
CLEAN = {"verdict": "none", "rule": "", "why": "", "test": "", "seconds": 1.0}
DEFECT = {"verdict": "defect", "rule": "r", "why": "w", "test": helpers.TEST, "seconds": 1.0}


class Killed(BaseException):
    """The box's process ended where it stood: nothing after this point of a call runs, as when the session's tree is killed."""


def snapshot(repo: Path) -> dict[str, bytes]:
    """Every file of a folder but ``.git`` and ``.venv`` at any depth, by its name relative to the folder."""
    found = {}
    for here, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv")]
        for name in files:
            path = Path(here) / name
            found[path.relative_to(repo).as_posix()] = path.read_bytes()
    return found


def tree_patch(base: dict[str, bytes], now: dict[str, bytes]) -> bytes:
    """The fake container's patch: each file that differs from the base, with its bytes as they are now, or ``<removed>``."""
    return b"".join(b"=== " + rel.encode() + b"\n" + now.get(rel, b"<removed>") + b"\n=== end\n"
                    for rel in sorted({*base, *now}) if base.get(rel) != now.get(rel))


class BoxDocker(helpers.HostDocker):
    """The bug's container as a local folder (``HostDocker``), which also runs the box's commands, each a Python step on the folder
    by its command line (``commands``), answers the box's pull with the folder's export (and ``extra`` members), and keeps each
    capture made by ``diff_script`` as the folder's tree against the base (``tree_patch``), for ``docker cp`` to copy out."""

    def __init__(self, root: Path, task) -> None:
        super().__init__(root, task)
        self.base = snapshot(self.repo)
        self.commands: dict[str, object] = {}
        self.files: dict[str, bytes] = {}
        self.extra: list[tuple[str, str, bytes]] = []
        self.pull_fails: list[BaseException] = []

    def __call__(self, *args, input=None, timeout=None, env=None, merged=False):
        if PULL in args:
            self.calls.append(("pull",))
            if self.pull_fails:
                raise self.pull_fails.pop(0)
            export = helpers.export_of(self.repo)
            if self.extra:
                import io
                import tarfile
                members = [(m.name, "dir" if m.isdir() else "file", b"" if m.isdir() else
                            tarfile.open(fileobj=io.BytesIO(export)).extractfile(m.name).read())
                           for m in tarfile.open(fileobj=io.BytesIO(export)).getmembers()]
                export = helpers.tar_of([*members, *self.extra])
            return helpers.ok(export)
        if box.SHELL in args:
            self.calls.append(("command", args[-1]))
            said = self.commands[args[-1]](self.repo)
            return helpers.ok(args[-2].encode() + b"\n" + said)
        if args and args[0] == "cp" and ":" in args[1] and args[1].split(":", 1)[1] in self.files:
            self.calls.append(args)
            Path(args[2]).write_bytes(self.files[args[1].split(":", 1)[1]])
            return helpers.ok()
        return super().__call__(*args, input=input, timeout=timeout, env=env)

    def sh(self, ctr, script, *a, **kw):
        mark = getattr(we, "PATCH_BYTES", "\0no size\0")          # the patch's size, as ``run.patch_bytes`` reads it
        if mark in script:
            self.calls.append(("sh", script))
            return helpers.ok(f"{mark}={len(tree_patch(self.base, snapshot(self.repo)))}\n".encode())
        saved = re.search(r'git diff --cached --binary .* > "([^"]+)"', script)
        if saved:
            self.calls.append(("sh", script))
            self.files[saved[1]] = tree_patch(self.base, snapshot(self.repo))
            return helpers.ok()
        return super().sh(ctr, script, *a, **kw)


def box_options(args: list[str]) -> SimpleNamespace:
    """The box's options as its command line in the run's MCP configuration gives them (``--name value`` pairs)."""
    pairs = dict(zip(args[::2], args[1::2]))
    opts = SimpleNamespace(**{k.lstrip("-"): v for k, v in pairs.items()})
    opts.timeout = float(opts.timeout)
    return opts


def scripted(docker: BoxDocker, sessions: list[tuple[str, list[tuple]]]):
    """A host session's starter with no model. Session i is (its id, its steps): ("write", rel, bytes) writes in the workspace as
    Claude Code's Write tool does; ("run", command) calls the box's ``run`` with the options the run's MCP configuration gives the
    box; ("killed", command) is that call with the box's process ended at the point ``docker.pull_fails`` or a test's patch sets,
    after which the session ends. Each session's stream is a plain success."""
    starts, replies = [], []

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        sid, steps = sessions[len(starts)]
        starts.append({"argv": list(argv), "cwd": cwd})
        config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text("utf-8"))["mcpServers"]["box"]
        opts = box_options(config["args"][2:])
        for step in steps:
            if step[0] == "write":
                (Path(cwd) / step[1]).parent.mkdir(parents=True, exist_ok=True)
                (Path(cwd) / step[1]).write_bytes(step[2])
            elif step[0] == "run":
                replies.append(box.call(opts, {"name": "run", "arguments": {"command": step[1]}}, docker))
            else:
                try:
                    box.call(opts, {"name": "run", "arguments": {"command": step[1]}}, docker)
                except Killed:
                    replies.append("killed")
        return helpers.Proc(helpers.stream(sid))
    return start, starts, replies


def run_with(monkeypatch, tmp_path, arm: str, sessions, *, commands, extra=(), review=lambda n: CLEAN, pull_fails=()):
    """One host run of ``arm`` (``run._run_task``), its sessions scripted (``scripted``), on a ``BoxDocker``."""
    task = we.load_tasks()[0]
    docker = BoxDocker(tmp_path, task)
    docker.commands.update(commands)
    docker.extra.extend(extra)
    docker.pull_fails.extend(pull_fails)
    start, starts, replies = scripted(docker, sessions)
    workspaces = []

    def workspace(ctx, tag):
        folder = tmp_path / "workspaces" / tag
        folder.mkdir(parents=True)
        workspaces.append(folder)
        return folder

    monkeypatch.setattr(we, "key_check", lambda *a, **kw: pytest.fail("the host executor makes no key check"))
    monkeypatch.setattr(we, "start_net", lambda *a, **kw: pytest.fail("the host executor starts no network or proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    monkeypatch.setattr(we, "host_workspace", workspace)
    monkeypatch.setattr(we, "run_critic", lambda ctx, task, run, agent, base, title, text, n=1: review(n))
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: helpers.ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: helpers.AT_PARENT)
    helpers.fake_end(monkeypatch)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: pytest.fail("no key is read"),
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, model=helpers.SONNET, environ={"PATH": "p"}, executor="host", starter=start)
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    return SimpleNamespace(record=record, docker=docker, starts=starts, replies=replies, ctx=ctx, task=task,
                           folder=ctx.archive.root / record["run"], workspaces=workspaces)


def box_edits(repo: Path) -> bytes:
    """A command in the box that edits a file (``sed -i``) and writes a new one (``cat > …``)."""
    (repo / "src" / "mod.py").write_bytes(BOX_EDIT)
    (repo / "src" / "by_box.py").write_bytes(BOX_NEW)
    return b"edited\n"


def box_removes(repo: Path) -> bytes:
    (repo / "tests" / "test_mod.py").unlink()
    return b""


def entry(rel: str, data: bytes) -> bytes:
    return b"=== " + rel.encode() + b"\n" + data + b"\n=== end\n"


# --- the defect, and the places the one-way copy dropped a change -----------------------------------------------------------
def test_a_file_a_box_command_writes_or_edits_reaches_the_kept_patch_byte_for_byte(monkeypatch, tmp_path):
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("write", "src/by_tool.py", TOOL_NEW), ("run", "sed -i ...")])],
                   commands={"sed -i ...": box_edits})
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.replies[0]["isError"] is False, got.replies
    final = (got.folder / "final.patch").read_bytes()
    assert entry("src/mod.py", BOX_EDIT) in final, final           # an edit a command made in the box
    assert entry("src/by_box.py", BOX_NEW) in final                # a file a command wrote in the box
    assert entry("src/by_tool.py", TOOL_NEW) in final              # and the Write tool's file, as before


def test_a_file_a_box_command_removes_is_removed_in_the_kept_patch(monkeypatch, tmp_path):
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("run", "git rm tests/test_mod.py")])],
                   commands={"git rm tests/test_mod.py": box_removes})
    assert got.record["status"] == "finished", got.record.get("error")
    assert entry("tests/test_mod.py", b"<removed>") in (got.folder / "final.patch").read_bytes()


def test_the_call_ends_with_the_commands_changes_in_the_workspace_so_the_agent_reads_what_the_box_holds(tmp_path):
    task = we.load_tasks()[0]
    docker = BoxDocker(tmp_path, task)
    docker.commands["black ."] = box_edits
    ws = tmp_path / "ws"
    helpers.write(ws, {"src/mod.py": "def f():\n    return 0\n", "tests/test_mod.py": "def test_f():\n    pass\n"})
    opts = SimpleNamespace(container="ctr", repo=task.repo, workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "black ."}}, docker)
    assert got["isError"] is False and got["content"][0]["text"] == "exit code 0\nedited\n", got
    assert (ws / "src" / "mod.py").read_bytes() == BOX_EDIT and (ws / "src" / "by_box.py").read_bytes() == BOX_NEW
    assert snapshot(ws) == snapshot(docker.repo), "the call did not end with one tree in the workspace and the container"


def test_k3s_first_patch_kept_before_the_rule_step_holds_a_box_commands_change(monkeypatch, tmp_path):
    got = run_with(monkeypatch, tmp_path, "K3", [("s1", [("run", "sed -i ...")]), ("s1", [])], commands={"sed -i ...": box_edits})
    assert got.record["status"] == "finished", got.record.get("error")
    assert entry("src/mod.py", BOX_EDIT) in we.baseline_of(got.folder).read_bytes()
    assert entry("src/mod.py", BOX_EDIT) in (got.folder / "rounds" / we.RULE_PATCH).read_bytes()


def test_a_repair_made_by_a_box_command_is_a_change_and_reaches_the_kept_patch(monkeypatch, tmp_path):
    got = run_with(monkeypatch, tmp_path, "K2", [("s1", [("write", "src/mod.py", b"def f():\n    return 1\n")]),
                                                 ("s1", [("run", "sed -i repair")])],
                   commands={"sed -i repair": box_edits}, review=lambda n: DEFECT if n == 1 else CLEAN)
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.record["repairs"]["reason"] != "the agent left the code as it was" and got.record["repairs"]["rounds"] == 2
    assert entry("src/mod.py", BOX_EDIT) in (got.folder / "final.patch").read_bytes()


# --- a change the workspace cannot hold is never dropped in silence ---------------------------------------------------------
def test_a_command_that_makes_what_the_workspace_cannot_hold_is_told_and_the_container_is_set_back(monkeypatch, tmp_path):
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("run", "ln")])], commands={"ln": box_edits},
                   extra=[("./evil", "lnk", b"src/mod.py")])        # a hard link: a symbolic link is carried (test_box_links.py)
    reply = got.replies[0]
    assert reply["isError"] is True and "evil" in reply["content"][0]["text"], reply
    assert "set back" in reply["content"][0]["text"] and "exit code 0" in reply["content"][0]["text"]
    assert got.record["status"] == "finished", got.record.get("error")
    final = (got.folder / "final.patch").read_bytes()
    assert b"by a command in the box" not in final, "a change the agent was told was not kept reached the patch"
    calls = json.loads((got.folder / "box_calls.json").read_text("utf-8"))["calls"]
    assert calls[-1]["phase"] == "set back" and "evil" in calls[-1]["error"]


def test_a_pull_that_fails_part_way_says_so_and_leaves_one_tree_never_that_nothing_was_kept(monkeypatch, tmp_path):
    real, writes = box._write_new, []

    def write_new(path, data, **kw):           # the pull's second write of the command's changes fails, after its first
        if data in (BOX_EDIT, BOX_NEW):
            writes.append(path)
            if len(writes) == 2:
                raise OSError(28, "No space left on device")
        return real(path, data, **kw)

    monkeypatch.setattr(box, "_write_new", write_new)
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("run", "sed -i ...")])], commands={"sed -i ...": box_edits})
    text = got.replies[0]["content"][0]["text"]
    assert got.replies[0]["isError"] is True and "only in part" in text and "none of it is kept" not in text, text
    assert "No space left on device" in text and "set back" in text
    final = (got.folder / "final.patch").read_bytes()
    assert final.count(b"=== end") == 1, final   # one of the two changes is in the workspace, and so in the container and the patch
    assert b"=== src/mod.py" not in final        # the file whose new bytes failed to land is as it was, never removed
    assert not box.spare(got.workspaces[0]).exists()       # the box's pull folder goes with the workspace at the run's end


# --- a stop between every two steps that write state ------------------------------------------------------------------------
@pytest.mark.parametrize("at", ["before the command", "after the command", "during the pull", "after the pull"])
def test_a_box_ended_between_its_steps_leaves_no_command_change_out_of_the_kept_patch(monkeypatch, tmp_path, at):
    real_run, real_write, real_state = box.run_command, getattr(box, "_write_new", None), getattr(box, "write_state", None)
    killed = []
    if at == "before the command":
        def run_command(*a, **kw):
            killed.append(at)
            raise Killed
        monkeypatch.setattr(box, "run_command", run_command)
    elif at == "during the pull" and real_write is not None:
        def write_new(path, data, **kw):    # the pull's first write of the command's change, never the export's at the start
            if data in (BOX_EDIT, BOX_NEW) and not killed:
                killed.append(at)
                real_write(path, data, **kw)
                raise Killed
            return real_write(path, data, **kw)
        monkeypatch.setattr(box, "_write_new", write_new)
    elif at == "after the pull" and real_state is not None:
        def write_state(path, entry_):
            if entry_.get("phase") == "settled" and not killed:
                killed.append(at)
                raise Killed
            return real_state(path, entry_)
        monkeypatch.setattr(box, "write_state", write_state)
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("killed", "sed -i ...")])], commands={"sed -i ...": box_edits},
                   pull_fails=[Killed()] if at == "after the command" else [])
    assert got.replies == ["killed"], (got.replies, killed)
    assert killed == ([] if at == "after the command" else [at]) and not got.docker.pull_fails
    assert got.record["status"] == "finished", got.record.get("error")
    final = (got.folder / "final.patch").read_bytes()
    if at == "before the command":
        assert final == b"", "no command ran, so the kept patch is the workspace's, which is the base"
    else:
        assert entry("src/mod.py", BOX_EDIT) in final and entry("src/by_box.py", BOX_NEW) in final, final
    monkeypatch.setattr(box, "run_command", real_run)


def test_a_box_that_ended_after_its_command_while_the_session_went_on_keeps_both_the_commands_and_the_later_edits(monkeypatch,
                                                                                                                  tmp_path):
    later = b"after = 'the Write tool, after the box ended'\n"
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("killed", "sed -i ..."), ("write", "src/after.py", later),
                                                        ("write", "src/mod.py", b"def f():\n    return 'the agent, later'\n")])],
                   commands={"sed -i ...": box_edits}, pull_fails=[Killed()])
    assert got.record["status"] == "finished", got.record.get("error")
    final = (got.folder / "final.patch").read_bytes()
    assert entry("src/by_box.py", BOX_NEW) in final and entry("src/after.py", later) in final
    assert entry("src/mod.py", b"def f():\n    return 'the agent, later'\n") in final     # both changed it: the later edit stands
    assert [s["phase"] for s in got.record["box_settled"]] == ["settled"] and got.record["box_settled"][0]["by"] == "runner"


F, D = "file", "dir"


@pytest.mark.parametrize(("before", "now", "theirs", "after"), [
    ({"a": b"0"}, {"a": b"0"}, {"a": b"box"}, {"a": b"box"}),                       # the command's edit
    ({"a": b"0"}, {"a": b"agent"}, {"a": b"0"}, {"a": b"agent"}),                   # the agent's edit after the mirror
    ({"a": b"0", "b": b"0"}, {"a": b"agent", "b": b"0"}, {"a": b"0", "b": b"box"}, {"a": b"agent", "b": b"box"}),
    ({"a": b"0"}, {"a": b"agent"}, {"a": b"box"}, {"a": b"agent"}),                 # both: the later edit, the workspace's
    ({"a": b"0", "b": b"0"}, {"a": b"0", "b": b"0"}, {"a": b"0"}, {"a": b"0"}),     # removed by the command
    ({"a": b"0", "b": b"0"}, {"a": b"0"}, {"a": b"0", "b": b"0"}, {"a": b"0"}),     # removed by the agent
    ({"p": b"0"}, {"p": b"0"}, {"p/x": b"box"}, {"p/x": b"box"}),                   # a file made a folder by the command
    ({"a.py": b"0"}, {"a.py": b"0"}, {"A.py": b"0"}, {"A.py": b"0"}),               # renamed by case alone
    ({"d/x": b"0"}, {"d/x": b"0", "d/new": b"agent"}, {}, {"d/new": b"agent"}),     # the command removed a folder the agent added to
])
def test_the_pull_takes_each_names_container_side_unless_the_workspace_changed_it_since_the_mirror(tmp_path, before, now, theirs,
                                                                                                    after):
    ws, given = tmp_path / "ws", tmp_path / "given"
    for folder, files in ((ws, before), (given, theirs)):
        folder.mkdir()
        for rel, data in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(data)
    mirrored = box.tree_map(ws)
    for rel in set(before) - set(now):
        (ws / rel).unlink()
    for rel, data in now.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_bytes(data)
    box.bring_in(ws, helpers.export_of(given), before=mirrored)
    assert snapshot(ws) == after
    assert {rel.split("/")[0] for rel in after} == set(os.listdir(ws)), "a name the tree does not hold is left in the workspace"


@pytest.mark.parametrize("bad", [("./evil", "lnk", b"a.py"), ("/abs.py", "file", b"x"), ("./a/../../up.py", "file", b"x"),
                                 ("./A.PY", "file", b"y"), ("./sub/.git/config", "file", b"x"), ("./con", "file", b"x")])
def test_the_pull_refuses_by_the_exports_one_rule_and_writes_nothing(tmp_path, bad):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_bytes(b"x\n")
    mirrored = box.tree_map(ws)
    tree = helpers.tar_of([("./", "dir", b""), ("./a.py", "file", b"changed\n"), bad])
    with pytest.raises(box.Refused):
        box.bring_in(ws, tree, before=mirrored)
    assert snapshot(ws) == {"a.py": b"x\n"}
    dest = tmp_path / "dest"                                 # and the export at the start refuses the same tree
    dest.mkdir()
    with pytest.raises(RuntimeError):
        we.workspace_from(SimpleNamespace(docker=lambda *a, **kw: helpers.ok(tree)), SimpleNamespace(repo="/w/r"), "agent", dest)
    assert os.listdir(dest) == []


def test_the_pull_script_stops_the_users_processes_then_runs_the_one_export_as_the_user():
    assert box.STOP in box.PULL and repr(list(box.EXPORT)) in box.PULL
    assert box.PULL.index("stop(ids[0])") < box.PULL.index("os.setuid") < box.PULL.index("os.execvp")
    compile(box.PULL, "PULL", "exec")


def test_a_box_state_that_cannot_be_read_ends_the_run_error_and_never_guesses(monkeypatch, tmp_path):
    def torn(path, entry_):          # the box's state file torn as it was written, and the box ended there
        Path(path).write_text("{not json", encoding="utf-8")
        raise Killed

    if hasattr(box, "write_state"):
        monkeypatch.setattr(box, "write_state", torn)
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("killed", "sed -i ...")])], commands={"sed -i ...": box_edits})
    assert got.record["status"] == "error" and "box" in got.record.get("error", ""), got.record


# --- the inventory: every place the tree crosses between the workspace and the container -----------------------------------
#: Each call site, by the function it is in, of each step that moves or reads the agent's tree between the host's workspace and
#: the container, with why it cannot drop a change and the test above that shows it. A new site fails this test until it is named.
CROSSINGS = {
    ("box_mcp.py", "mirror", "pack"): "the workspace packed for the container: it holds every command change (pull)",
    ("box_mcp.py", "call", "mirror"): "before the command; the last call ended with one tree (test_the_call_ends_with_...)",
    ("box_mcp.py", "call", "run_command"): "the command; its file changes come back by the pull after it",
    ("box_mcp.py", "call", "pull"): "after the command, whatever it returned (test_a_file_a_box_command_writes_...)",
    ("box_mcp.py", "call", "set_back"): "a pull that could not hold the tree: told, and the container set to the workspace",
    ("box_mcp.py", "pull", "bring_in"): "the container's export made the workspace's, checked whole before anything is written",
    ("box_mcp.py", "set_back", "mirror"): "one tree again after a refused pull (test_a_command_that_makes_what_...)",
    ("box_mcp.py", "settle", "pull"): "a call ended before its pull, finished by the runner (test_a_box_ended_between_...)",
    ("box_mcp.py", "settle", "set_back"): "a reset a call was ended inside, finished by setting the container to the workspace "
                                          "again, never by a pull (test_review_2_finding_3_...)",
    ("box_mcp.py", "call", "settle"): "before the call mirrors anything: a call the box ended before its pull is settled, so no "
                                      "mirror writes over what its command changed (test_a_stop_and_a_restart_between_...)",
    ("box_mcp.py", "bring_in", "tree_map"): "the workspace read before anything is written, and again to check what was",
    ("box_mcp.py", "bring_in.failed", "tree_map"): "a pull that failed part way through links: its link record set to what the "
                                                   "workspace then holds, which the reply tells the agent to read",
    ("run.py", "workspace_from", "bring_in"): "the parent's tree into the empty workspace, by the pull's one rule",
    ("run.py", "_run_task", "workspace_from"): "the parent's tree, once, into an empty workspace before any session",
    ("run.py", "_run_task.turn", "settle"): "after each host session, before its tree is mirrored: a call it ended is settled",
    ("run.py", "_run_task.turn", "mirror"): "after each host session: the workspace holds every command change",
    ("run.py", "_run_task", "mirror"): "before the final patch: the workspace holds every command change",
    ("run.py", "_run_task.<lambda>", "pack"): "K's tree compared before and after a repair: the workspace's, with the commands'",
}
STEPS = {"mirror", "pack", "pull", "bring_in", "settle", "set_back", "run_command", "tree_map", "workspace_from"}


def _crossings(path: Path) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*where, child.name])
            elif isinstance(child, ast.Lambda):
                visit(child, [*where, "<lambda>"])
            else:
                if isinstance(child, ast.Call):
                    name = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                    if name in STEPS and where:
                        found.add((path.name, ".".join(where), name))
                visit(child, where)
    visit(ast.parse(path.read_text("utf-8")), [])
    return found


def test_every_place_the_tree_crosses_between_the_workspace_and_the_container_is_named_and_covered():
    here = ROOT / "validation" / "worked_examples"
    found = _crossings(here / "box_mcp.py") | _crossings(here / "run.py")
    assert found == set(CROSSINGS), (sorted(found - set(CROSSINGS)), sorted(set(CROSSINGS) - found))
    assert "comes back into your working folder" in box.TOOL["description"], "the tool still tells the agent changes are lost"


#: Each crossing above, with the test in ``test_box_links.py`` that drives a symbolic link through it (PLAN.md, 2026-11-06 evening,
#: ruling 1): every place the tree crosses carries a link, as git carries one where links cannot be made.
_REACH = "test_a_link_reaches_the_workspace_as_git_holds_it_and_the_container_as_the_same_link"
_CHANGED = "test_a_link_a_command_changes_comes_back_to_the_workspace_and_into_the_kept_patch"
_SWAPPED = "test_a_link_a_command_makes_a_file_or_a_file_it_makes_a_link_is_that_in_the_workspace_and_the_kept_patch"
_SET_BACK = "test_a_pull_refused_with_links_in_the_tree_sets_the_container_back_with_its_links"
_ENDED = "test_a_box_ended_while_its_pull_brings_links_back_leaves_each_a_link_in_the_kept_patch"
LINKED = {
    ("box_mcp.py", "mirror", "pack"): _REACH,
    ("box_mcp.py", "call", "mirror"): _CHANGED,
    ("box_mcp.py", "call", "run_command"): _CHANGED,
    ("box_mcp.py", "call", "pull"): _CHANGED,
    ("box_mcp.py", "call", "set_back"): _SET_BACK,
    ("box_mcp.py", "pull", "bring_in"): _SWAPPED,
    ("box_mcp.py", "set_back", "mirror"): _SET_BACK,
    ("box_mcp.py", "settle", "pull"): _ENDED,
    ("box_mcp.py", "settle", "set_back"): "test_a_reset_the_box_was_ended_inside_is_finished_with_its_links_and_never_pulled",
    ("box_mcp.py", "call", "settle"): "test_a_box_ended_after_a_command_that_changed_links_is_settled_by_the_next_call_first",
    ("box_mcp.py", "bring_in", "tree_map"): _SWAPPED,
    ("box_mcp.py", "bring_in.failed", "tree_map"): "test_a_pull_that_fails_part_way_through_links_sets_the_record_to_what_the_workspace_holds",
    ("run.py", "workspace_from", "bring_in"): _REACH,
    ("run.py", "_run_task", "workspace_from"): _REACH,
    ("run.py", "_run_task.turn", "settle"): _ENDED,
    ("run.py", "_run_task.turn", "mirror"): "test_the_write_tool_writing_a_links_file_changes_its_target_as_git_does_and_never_writes_through_it",
    ("run.py", "_run_task", "mirror"): _REACH,
    ("run.py", "_run_task.<lambda>", "pack"): "test_arm_ks_check_that_a_repair_changed_the_tree_sees_a_repair_that_changes_only_a_link",
}


def test_every_place_the_tree_crosses_carries_a_symbolic_link_by_a_named_test():
    assert set(LINKED) == set(CROSSINGS), (sorted(set(CROSSINGS) - set(LINKED)), sorted(set(LINKED) - set(CROSSINGS)))
    tests = {node.name for node in ast.parse(Path(__file__).with_name("test_box_links.py").read_text("utf-8")).body
             if isinstance(node, ast.FunctionDef)}
    assert set(LINKED.values()) <= tests, sorted(set(LINKED.values()) - tests)


#: Each crossing above, with the test in ``test_box_modes.py`` that drives an executable file (git's mode 100755) through it and
#: reads its mode on the other side (replay cohort, repair 1, review 1's finding 2): every place the tree crosses carries each
#: entry's kind and mode, so a crossing that drops the mode fails that test, and a new crossing fails here until it is named.
_M_REACH = "test_an_executable_reaches_the_workspace_as_git_holds_it_and_the_container_with_its_mode"
_M_CHANGED = "test_a_mode_a_command_changes_comes_back_to_the_workspace_and_into_the_kept_patch"
_M_ENDED = "test_a_box_ended_while_its_pull_brings_a_mode_back_keeps_it_in_the_kept_patch"
_M_SET_BACK = "test_a_pull_refused_with_modes_in_the_tree_sets_the_container_back_with_its_modes"
MODED = {
    ("box_mcp.py", "mirror", "pack"): _M_REACH,
    ("box_mcp.py", "call", "mirror"): _M_CHANGED,
    ("box_mcp.py", "call", "run_command"): _M_CHANGED,
    ("box_mcp.py", "call", "pull"): _M_CHANGED,
    ("box_mcp.py", "call", "set_back"): _M_SET_BACK,
    ("box_mcp.py", "call", "settle"): _M_ENDED,
    ("box_mcp.py", "pull", "bring_in"): _M_CHANGED,
    ("box_mcp.py", "set_back", "mirror"): _M_SET_BACK,
    ("box_mcp.py", "settle", "pull"): _M_ENDED,
    ("box_mcp.py", "settle", "set_back"): "test_a_reset_the_box_was_ended_inside_is_finished_with_its_modes_and_never_pulled",
    ("box_mcp.py", "bring_in", "tree_map"): _M_CHANGED,
    ("box_mcp.py", "bring_in.failed", "tree_map"): "test_a_pull_that_fails_part_way_through_modes_sets_the_record_to_what_the_"
                                                   "workspace_holds",
    ("run.py", "workspace_from", "bring_in"): _M_REACH,
    ("run.py", "_run_task", "workspace_from"): _M_REACH,
    ("run.py", "_run_task.turn", "settle"): _M_ENDED,
    ("run.py", "_run_task.turn", "mirror"): "test_the_write_tool_writing_an_executables_file_keeps_its_mode",
    ("run.py", "_run_task", "mirror"): _M_REACH,
    ("run.py", "_run_task.<lambda>", "pack"): "test_arm_ks_check_that_a_repair_changed_the_tree_sees_a_repair_that_changes_only_a_mode",
}


def test_every_place_the_tree_crosses_carries_each_entrys_mode_by_a_named_test():
    assert set(MODED) == set(CROSSINGS), (sorted(set(CROSSINGS) - set(MODED)), sorted(set(MODED) - set(CROSSINGS)))
    tests = {node.name for node in ast.parse(Path(__file__).with_name("test_box_modes.py").read_text("utf-8")).body
             if isinstance(node, ast.FunctionDef)}
    assert set(MODED.values()) <= tests, sorted(set(MODED.values()) - tests)
    assert (box.FILE_MODE, box.EXEC_MODE, box.LINK_MODE) == ("100644", "100755", "120000")


# --- one snapshot of a call, settled first (replay cohort, repair 1, review 1's findings 1 and 9) --------------------------------
def test_finding_1_an_edit_made_after_the_mirrors_snapshot_survives_the_pull(tmp_path):
    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = BoxDocker(tmp_path, task)
    ws = tmp_path / "ws"
    helpers.write(ws, {"src/mod.py": "sent\n", "tests/test_mod.py": "pass\n"})
    docker.commands["true"] = lambda repo: b""

    def interleaving(*args, **kw):
        done = docker(*args, **kw)
        if box.MIRROR in args:
            assert done.returncode == 0
            (ws / "src/mod.py").write_bytes(b"concurrent workspace edit\n")      # the sent tar is in the container already
        return done

    for state in (None, tmp_path / "state.json"):
        (ws / "src/mod.py").write_bytes(b"sent\n")
        opts = SimpleNamespace(workspace=ws, container="ctr", repo=task.repo, user="tester", timeout=30, state=state)
        reply = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, interleaving)
        assert reply["isError"] is False, reply
        assert (ws / "src/mod.py").read_bytes() == b"concurrent workspace edit\n", state
    sent = box.read_state(tmp_path / "state.json")["calls"][-1]
    assert sent["phase"] == "settled"


def test_finding_9_a_restarted_box_settles_the_previous_call_before_its_next_mirror(tmp_path):
    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = BoxDocker(tmp_path, task)
    ws = tmp_path / "ws"
    helpers.write(ws, {"src/mod.py": "sent\n", "tests/test_mod.py": "pass\n"})

    def edit(repo):
        (repo / "src/mod.py").write_bytes(b"command edit\n")
        return b""

    docker.commands.update({"edit": edit, "true": lambda repo: b""})
    docker.pull_fails.append(Killed())
    state = tmp_path / "state.json"
    opts = SimpleNamespace(workspace=ws, container="ctr", repo=task.repo, user="tester", timeout=30, state=state)
    with pytest.raises(Killed):
        box.call(opts, {"name": "run", "arguments": {"command": "edit"}}, docker)
    assert (docker.repo / "src/mod.py").read_bytes() == b"command edit\n"
    reply = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, docker)
    assert reply["isError"] is False, reply
    box.settle(ws, docker, state=state, container="ctr", repo=task.repo, user="tester", timeout=30)
    assert (ws / "src/mod.py").read_bytes() == b"command edit\n"
    assert "came back now, before this command" in reply["content"][0]["text"], reply      # and the agent is told
    assert [c["phase"] for c in box.read_state(state)["calls"]] == ["settled", "settled"]


#: The steps of one call, in order, and where the box is ended between two of them: the mirror, the call's state ("mirrored"),
#: the command, the pull's export, its state ("pulling"), its writes, its state ("settled"); for a call that first settles the
#: one before it, that settling's own last step; and (replay cohort, repair 2) a point inside each step that writes a tree in more
#: than one write (``MULTI``): the call's mirror, a folder made a file, a file made a folder, a folder removed, and the container
#: set back after a pull that could not be made.
STOPS = ["after the mirror", "after the mirrored state", "after the command", "after the export", "after the pulling state",
         "while the pull writes", "before the settled state", "while a previous call is settled",
         "inside the call's mirror", "while a folder becomes a file", "while a file becomes a folder", "while a folder is removed",
         "inside the setting back"]
#: The fake container's tree beyond ``HostDocker``'s, so the command below changes names in more than one write.
MORE = {"pkg/a.py": b"a = 1\n", "pkg/b.py": b"b = 1\n", "conf": b"conf = 1\n", "old/x.py": b"x = 1\n", "old/y.py": b"y = 1\n"}
PKG = b"pkg = 'a folder the command made a file'\n"
CONF = b"c = 'in a folder the command made of a file'\n"


def many_edits(repo: Path) -> bytes:
    """``box_edits``, and a folder made a file (``pkg``), a file made a folder (``conf``) and a folder removed (``old``)."""
    box_edits(repo)
    shutil.rmtree(repo / "pkg")
    (repo / "pkg").write_bytes(PKG)
    (repo / "conf").unlink()
    (repo / "conf").mkdir()
    (repo / "conf" / "c.py").write_bytes(CONF)
    shutil.rmtree(repo / "old")
    return b"edited\n"


#: Run before MIRROR, in the container's place: the script stops (exit 88) right after the first call of the given path step on a
#: name ending as given, so the container is left between two of the mirror's writes.
STOP_AFTER = '''import os, shutil
def _stopping(real):
    def step(path, *a, **kw):
        real(path, *a, **kw)
        if os.path.basename(path).endswith(%r):
            raise SystemExit(88)
    return step
%s = _stopping(%s)
'''


@pytest.mark.parametrize("restart", ["the next call", "the runner"])
@pytest.mark.parametrize("at", STOPS)
def test_a_stop_and_a_restart_between_each_two_steps_of_a_call_loses_no_change(monkeypatch, tmp_path, at, restart):
    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = BoxDocker(tmp_path, task)
    for rel, data in MORE.items():
        (docker.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (docker.repo / rel).write_bytes(data)
    base = snapshot(docker.repo)
    ws = tmp_path / "ws"
    ws.mkdir()
    box.bring_in(ws, helpers.export_of(docker.repo))                 # the workspace, as the run's export makes it
    held = ws.resolve()                                              # the names as the pull gives them
    ran = []

    def edit(repo):
        ran.append(1)
        if at == "inside the setting back":                       # a name the host cannot hold: the pull is refused
            docker.extra.append(("./conf/held:name", "file", b"x"))
        return many_edits(repo)

    docker.commands.update({"edit": edit, "true": lambda repo: b""})
    state = tmp_path / "state.json"
    opts = SimpleNamespace(workspace=ws, container="ctr", repo=task.repo, user="tester", timeout=30, state=state)
    killed = []
    real_state, real_run, real_bring, real_write = box.write_state, box.run_command, box.bring_in, box._write_new
    real_mkdir, real_remove = os.mkdir, os.remove

    def once(name):
        if killed:
            return False
        killed.append(name)
        return True

    def write_state(path, entry_):
        phase = entry_.get("phase")
        if ((at == "after the mirror" and phase == "mirrored") or (at == "after the export" and phase == "pulling")
                or (at in ("before the settled state", "while a previous call is settled") and phase == "settled")) and once(at):
            raise Killed
        return real_state(path, entry_)

    def run_command(*a, **kw):
        if at == "after the mirrored state" and once(at):
            raise Killed
        return real_run(*a, **kw)

    def bring_in(*a, **kw):
        if at == "after the pulling state" and once(at):
            raise Killed
        return real_bring(*a, **kw)

    def write_new(path, data, **kw):
        if at == "while a folder becomes a file" and data == PKG and once(at):
            raise Killed                                        # pkg's folder removed, its file not yet put at its name
        real_write(path, data, **kw)
        if at == "while the pull writes" and data in (BOX_EDIT, BOX_NEW) and once(at):
            raise Killed

    def mkdir(path, *a, **kw):
        if at == "while a file becomes a folder" and Path(path) == held / "conf" and once(at):
            raise Killed                                        # conf's file removed, its folder not yet made
        return real_mkdir(path, *a, **kw)

    def remove(path, *a, **kw):
        real_remove(path, *a, **kw)
        if at == "while a folder is removed" and Path(path).parent == held / "old" and once(at):
            raise Killed                                        # one of old's files removed, the other and the folder not yet

    inside = {"inside the call's mirror": (".py", "os.remove"), "inside the setting back": ("conf", "shutil.rmtree")}.get(at)

    def container(*args, **kw):          # the container, its own mirror stopped part way where a point says
        if box.MIRROR in args and inside and bool(ran) == (at == "inside the setting back") and once(at):
            done = subprocess.run([sys.executable, "-I", "-c", STOP_AFTER % (inside[0], inside[1], inside[1]) + box.MIRROR,
                                   str(docker.repo), "tester"], input=kw["input"], capture_output=True, timeout=60)
            assert done.returncode == 88, done.stderr
            docker.extra.clear()
            raise Killed
        return docker(*args, **kw)

    monkeypatch.setattr(box, "write_state", write_state)
    monkeypatch.setattr(box, "run_command", run_command)
    monkeypatch.setattr(box, "bring_in", bring_in)
    monkeypatch.setattr(box, "_write_new", write_new)
    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(os, "remove", remove)
    if at in ("after the command", "while a previous call is settled"):
        docker.pull_fails.append(Killed())
    if at == "inside the call's mirror":                          # the agent's edits, which the call's mirror sends
        (ws / "old" / "x.py").unlink()
        (ws / "old" / "y.py").unlink()
    with pytest.raises(Killed):
        box.call(opts, {"name": "run", "arguments": {"command": "edit"}}, container)
    if at == "while a previous call is settled":               # the next call settles the first, and is ended doing it
        with pytest.raises(Killed):
            box.call(opts, {"name": "run", "arguments": {"command": "true"}}, container)
    assert killed == ([] if at == "after the command" else [at]) and not docker.pull_fails
    later = b"later = 'the Write tool, after the box ended'\n"
    (ws / "src" / "later.py").write_bytes(later)                # the session goes on writing before the box starts again
    where = {"container": "ctr", "repo": task.repo, "user": "tester", "timeout": 30.0}
    if restart == "the next call":
        reply = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, container)
        assert reply["isError"] is False, reply
    else:                                                       # as the runner's turn does after the session
        box.settle(ws, container, state=state, **where)
        box.mirror(ws, container, **where)
    never = at in ("after the mirror", "after the mirrored state", "inside the call's mirror")      # the command never ran
    kept = not never and at != "inside the setting back"        # it ran, and what it changed came back
    want = dict(base)
    if at == "inside the call's mirror":
        del want["old/x.py"], want["old/y.py"]
    if kept:
        for rel in ("pkg/a.py", "pkg/b.py", "conf", "old/x.py", "old/y.py"):
            del want[rel]
        want.update({"src/mod.py": BOX_EDIT, "src/by_box.py": BOX_NEW, "pkg": PKG, "conf/c.py": CONF})
    want["src/later.py"] = later
    assert snapshot(ws) == want
    assert snapshot(ws) == snapshot(docker.repo), "the call did not end with one tree in the workspace and the container"
    calls = box.read_state(state)["calls"]       # a call ended at its mirror is kept too: its intent was written before it
    reset = at in ("after the mirror", "inside the call's mirror", "inside the setting back")
    assert calls[-1]["phase"] == ("set back" if restart == "the runner" and reset else "settled"), calls
    if restart == "the next call":                              # and the agent is told what became of the call it lost
        said = reply["content"][0]["text"]
        assert ("never ran" if at in ("after the mirror", "inside the call's mirror") else "none of it is kept"
                if at == "inside the setting back" else "came back now, before this command") in said, said


# --- every step that writes a tree in more than one write says what it does first (replay cohort, repair 2) -----------------------
def test_review_2_finding_1_a_restart_keeps_a_folder_a_command_made_a_file(monkeypatch, tmp_path):
    replacement = b"the command replaced the directory with this file\n"
    real_write, interrupted = box._write_new, []

    def stop_before_replacement(path, data, **kw):
        if path.name == "src" and data == replacement and not interrupted:
            assert not path.exists()            # the pull has removed src and its old child, and not yet put the new file there
            interrupted.append(True)
            raise Killed()
        return real_write(path, data, **kw)

    def replace_directory(repo):
        shutil.rmtree(repo / "src")
        (repo / "src").write_bytes(replacement)
        return b"replaced\n"

    monkeypatch.setattr(box, "_write_new", stop_before_replacement)
    got = run_with(monkeypatch, tmp_path, "A", [("s1", [("killed", "replace src")])], commands={"replace src": replace_directory})
    assert interrupted == [True] and got.replies == ["killed"]
    assert got.record["status"] == "finished", got.record.get("error")
    final = (got.folder / "final.patch").read_bytes()
    assert entry("src", replacement) in final, "restart dropped the command's replacement file: " + repr(final)


def test_review_2_finding_3_a_reset_ended_inside_its_mirror_is_finished_as_a_reset_and_never_pulled(tmp_path):
    class ResetKilledDocker(BoxDocker):
        mirrors = 0

        def __call__(self, *args, **kw):
            if box.MIRROR in args:
                self.mirrors += 1
                if self.mirrors == 2:           # the real MIRROR, stopped just after it clears a folder for the host's own file
                    done = subprocess.run([sys.executable, "-I", "-c", STOP_AFTER % ("mod.py", "shutil.rmtree", "shutil.rmtree")
                                           + box.MIRROR, str(self.repo), "tester"], input=kw["input"], capture_output=True,
                                          timeout=30)
                    assert done.returncode == 88, done.stderr
                    assert not (self.repo / "src/mod.py").exists()
                    self.extra.clear()              # the name the host cannot hold was below the folder just removed
                    raise Killed()
            if box.PULL in args and self.mirrors >= 2:
                self.pulled_after_reset = True
            return super().__call__(*args, **kw)

    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = ResetKilledDocker(tmp_path, task)
    ws = tmp_path / "ws"
    ws.mkdir()
    we.workspace_from(SimpleNamespace(docker=docker), task, "ctr", ws)
    original = (ws / "src/mod.py").read_bytes()
    opts = SimpleNamespace(container="ctr", repo=task.repo, workspace=ws, user="tester", timeout=30, state=tmp_path / "state.json")

    def command(repo):
        (repo / "src/mod.py").unlink()
        (repo / "src/mod.py").mkdir()
        (repo / "src/mod.py/new.py").write_bytes(b"command change\n")
        docker.extra.append(("./src/mod.py/unholdable:name", "file", b"x"))       # a name only Linux holds, as the export gives it
        return b"done\n"

    docker.commands["unholdable tree"] = command
    with pytest.raises(Killed):
        box.call(opts, {"name": "run", "arguments": {"command": "unholdable tree"}}, docker)
    assert (ws / "src/mod.py").read_bytes() == original                         # the refused pull wrote nothing
    assert box.read_state(opts.state)["calls"][-1]["phase"] == "setting back"   # the reset's intent, kept before its first write
    got = box.settle(ws, docker, state=opts.state, container="ctr", repo=task.repo, user="tester", timeout=30)
    assert got["phase"] == "set back" and not getattr(docker, "pulled_after_reset", False), got
    box.mirror(ws, docker, container="ctr", repo=task.repo, user="tester", timeout=30)
    assert (ws / "src/mod.py").is_file(), "recovery imported the reset's transient deletion into the workspace"
    assert (ws / "src/mod.py").read_bytes() == original and snapshot(ws) == snapshot(docker.repo)


@pytest.mark.parametrize("calls", [
    [{"call": 1, "phase": "pulling", "before": {}, "pending": {"a": {"from": "not a mark", "to": None}}}],
    [{"call": 1, "phase": "pulling", "before": {}, "pending": ["a"]}],
    [{"call": 1, "phase": "pulling", "before": {}, "pending": {"a": {"from": None}}}],
    [{"call": 1, "phase": "setting back"}],                                     # a reset with no outcome of the pull it follows
    [{"call": 1, "phase": "setting back", "kept": "all"}],
    [{"call": 1, "phase": "setting back", "kept": "part", "changed": "a", "missed": None}],
])
def test_a_state_whose_intent_cannot_be_read_as_the_box_writes_it_refuses_and_never_guesses(tmp_path, calls):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"calls": calls}))
    with pytest.raises(box.Refused):
        box.read_state(state)
    with pytest.raises(box.Refused):
        box.settle(tmp_path, lambda *a, **kw: pytest.fail("no container step on a state that cannot be read"), state=state,
                   container="c", repo="/r", user="u", timeout=1)


#: Every step that writes the agent's tree in more than one write (replay cohort, repair 2, review 2's findings 1 and 3; class
#: ``state_not_durable``), by the function that makes it and the step, with the intent it keeps, written whole and synced, before
#: its first write, and the points of ``STOPS`` inside it. Built from the code: each call of a step that writes the container's
#: tree (``mirror``, ``set_back``) or the workspace's (``pull``, ``bring_in``), in the box and in the runner.
MULTI = {
    ("box_mcp.py", "call", "mirror"): "'sending', once the workspace is packed (intend): settle sets the container to the workspace "
                                      "again and tells the agent the command never ran (inside the call's mirror)",
    ("box_mcp.py", "call", "set_back"): "'setting back', with the pull's outcome, over 'pulling' (intend): settle sets the container "
                                        "to the workspace again, never pulls it (inside the setting back)",
    ("box_mcp.py", "set_back", "mirror"): "the intent its caller gives (intend)",
    ("box_mcp.py", "settle", "set_back"): "a reset a call was ended inside, finished: the state already names it",
    ("box_mcp.py", "call", "pull"): "'pulling', then each name the pull changes in more than one write (bring_in)",
    ("box_mcp.py", "settle", "pull"): "the same, given the names an earlier pull of the call kept pending (earlier)",
    ("box_mcp.py", "pull", "bring_in"): "each name it changes in more than one write (a folder made a file, a file made a folder, a "
                                        "folder removed, a link or an executable) kept pending in the call's state (intend), and a "
                                        "mode in the workspace's record too, before its first write (while a folder becomes a file, "
                                        "while a file becomes a folder, while a folder is removed; test_box_links' and "
                                        "test_box_modes' points for a link and a mode)",
    ("run.py", "workspace_from", "bring_in"): "none: into an empty workspace before any session, with no call to settle; one that "
                                              "fails or stops ends the run",
    ("run.py", "_run_task.turn", "mirror"): "none: the runner's own step, after settle; one that fails or stops ends the run and its "
                                            "container is removed, so nothing reads what it left",
    ("run.py", "_run_task", "mirror"): "none: the runner's own step before the final capture; one that fails or stops ends the run "
                                       "and its container is removed, so nothing reads what it left",
}
TREE_STEPS = {"mirror", "set_back", "pull", "bring_in"}
#: What writes the workspace's tree in ``bring_in``: each comes after the pull's intent is kept.
TREE_WRITES = {"rmtree", "mkdir", "_write_new"}


def test_every_step_that_writes_a_tree_in_more_than_one_write_keeps_its_intent_before_its_first_write():
    here = ROOT / "validation" / "worked_examples"
    found = {c for c in _crossings(here / "box_mcp.py") | _crossings(here / "run.py") if c[2] in TREE_STEPS}
    assert found == set(MULTI), (sorted(found - set(MULTI)), sorted(set(MULTI) - found))
    fns = {n.name: n for n in ast.walk(ast.parse((here / "box_mcp.py").read_text("utf-8"))) if isinstance(n, ast.FunctionDef)}

    def calls(fn: str, name: str) -> list[ast.Call]:
        return [n for n in ast.walk(fns[fn]) if isinstance(n, ast.Call)
                and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == name]

    def given(call: ast.Call) -> set[str]:
        return {k.arg for k in call.keywords}
    assert calls("call", "mirror") and all("intend" in given(c) for c in calls("call", "mirror") + calls("call", "set_back"))
    assert all("intend" in given(c) for c in calls("set_back", "mirror"))
    assert calls("pull", "bring_in") and all({"intend", "earlier"} <= given(c) for c in calls("pull", "bring_in"))
    assert calls("settle", "pull") and all("earlier" in given(c) for c in calls("settle", "pull"))
    first = min(c.lineno for c in calls("mirror", "intend"))
    assert all(c.lineno > first for c in calls("mirror", "docker")), "the mirror writes the container before its intent is kept"
    first = min(c.lineno for c in calls("bring_in", "intend"))
    writes = [c for name in TREE_WRITES for c in calls("bring_in", name)]
    writes += [c for c in calls("bring_in", "remove") if c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == "path"]
    assert writes and all(c.lineno > first for c in writes), "the pull writes the workspace before its intent is kept"
    assert set(box.RESETS) == {"sending", "setting back"} and set(box.RESETS) < set(box.PHASES)
    assert not set(box.RESETS) & set(box.UNFINISHED), "a reset is settled by a pull"


# --- a pull's outcome is one type, told by one message for each (replay cohort, repair 2, review 2's finding 2) --------------------
def test_review_2_finding_2_a_partial_pull_settled_after_a_restart_never_says_none_of_it_is_kept(monkeypatch, tmp_path):
    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = BoxDocker(tmp_path, task)
    ws = tmp_path / "ws"
    ws.mkdir()
    we.workspace_from(SimpleNamespace(docker=docker), task, "ctr", ws)
    opts = SimpleNamespace(container="ctr", repo=task.repo, workspace=ws, user="tester", timeout=30, state=tmp_path / "state.json")
    docker.commands.update({"edit": box_edits, "true": lambda repo: b""})
    docker.pull_fails.append(Killed())
    with pytest.raises(Killed):
        box.call(opts, {"name": "run", "arguments": {"command": "edit"}}, docker)
    real_write, writes = box._write_new, []

    def fail_second(path, data, **kw):
        if data in (BOX_EDIT, BOX_NEW):
            writes.append(path.name)
            if len(writes) == 2:
                raise OSError(28, "No space left on device")
        return real_write(path, data, **kw)

    monkeypatch.setattr(box, "_write_new", fail_second)
    text = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, docker)["content"][0]["text"]
    assert len(writes) == 2
    assert (ws / "src/by_box.py").read_bytes() == BOX_NEW and (ws / "src/mod.py").read_bytes() != BOX_EDIT
    assert "none of it is kept" not in text, text
    assert "only in part" in text and "kept, as the command left them: src/by_box.py" in text and "not kept: src/mod.py" in text


def _pull_with_outcome(monkeypatch, tmp_path, kept: str, path: str):
    """One call whose pull ends ``kept`` (``box.ALL``, ``PART`` or ``NONE``), made by the call itself (``path`` "direct") or, the box
    ended at the pull, by the next call's settlement ("settled"). Its reply's text, and the call's entry in the box's state."""
    task = SimpleNamespace(name="toy", repo="/w/toy")
    docker = BoxDocker(tmp_path, task)
    ws = tmp_path / "ws"
    ws.mkdir()
    we.workspace_from(SimpleNamespace(docker=docker), task, "ctr", ws)
    opts = SimpleNamespace(container="ctr", repo=task.repo, workspace=ws, user="tester", timeout=30, state=tmp_path / "state.json")

    def edit(repo):
        if kept == box.NONE:
            docker.extra.append(("./held:name", "file", b"x"))        # a name the host cannot hold: nothing is written
        return box_edits(repo)

    def container(*args, **kw):          # the name the host cannot hold is in one export only
        done = docker(*args, **kw)
        if box.PULL in args:
            docker.extra.clear()
        return done

    docker.commands.update({"edit": edit, "true": lambda repo: b""})
    if kept == box.PART:                                              # the second of the command's two files fails to land
        real, writes = box._write_new, []

        def write_new(p, data, **kw):
            if data in (BOX_EDIT, BOX_NEW):
                writes.append(p.name)
                if len(writes) == 2:
                    raise OSError(28, "No space left on device")
            return real(p, data, **kw)
        monkeypatch.setattr(box, "_write_new", write_new)
    if path == "settled":
        docker.pull_fails.append(Killed())
        with pytest.raises(Killed):
            box.call(opts, {"name": "run", "arguments": {"command": "edit"}}, container)
        reply = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, container)
    else:
        reply = box.call(opts, {"name": "run", "arguments": {"command": "edit"}}, container)
    return reply, box.read_state(opts.state)["calls"][0], ws


@pytest.mark.parametrize("kept", ["all", "part", "none"])
def test_each_outcome_of_a_pull_is_one_type_told_by_one_message_whether_the_call_or_its_settlement_made_it(monkeypatch, tmp_path,
                                                                                                           kept):
    got = {}
    for path in ("direct", "settled"):
        got[path] = _pull_with_outcome(monkeypatch, tmp_path / path, kept, path)
        monkeypatch.undo()
    outcome = {path: box.Pulled.of(e) for path, (_, e, _) in got.items()}
    assert {o.kept for o in outcome.values()} == {kept}
    assert outcome["direct"][:3] == outcome["settled"][:3], outcome          # what was kept and what was not, the same
    fixed = [part for part in re.split(r"\{\w+\}", box.TOLD[kept]) if len(part.strip(" .,:;()")) > 2]
    for path, (reply, e, ws) in got.items():
        text = reply["content"][0]["text"]
        assert e["phase"] == ("settled" if kept == "all" else "set back" if path == "direct" else "not settled"), e
        if kept == "all" and path == "direct":                   # nothing to tell: the command's own output, as it always was
            assert reply["isError"] is False and text == "exit code 0\nedited\n", text
            continue
        assert all(part in text for part in fixed), (path, fixed, text)
        exact = (box.told(outcome[path], call=1) if path == "settled" else
                 box.told(outcome[path], after="the container was set back to your working folder"))
        assert exact in text, (exact, text)
    kept_names = {"all": ["src/by_box.py", "src/mod.py"], "part": ["src/by_box.py"], "none": []}[kept]
    assert outcome["direct"].changed == kept_names
    assert outcome["direct"].missed == {"all": [], "part": ["src/mod.py"], "none": None}[kept]
    for path, (reply, e, ws) in got.items():
        text = reply["content"][0]["text"]
        assert ("none of it is kept" in text) is (kept == "none"), text
        if kept == "part":
            assert "kept, as the command left them: src/by_box.py" in text and "not kept: src/mod.py" in text, text
        assert ((ws / "src/by_box.py").is_file() and (ws / "src/by_box.py").read_bytes() == BOX_NEW) is (kept != "none")
        assert ((ws / "src/mod.py").read_bytes() == BOX_EDIT) is (kept == "all")


# --- every file is put in place whole (replay cohort, repair 1, review 1's finding 4) ---------------------------------------------
class FullDisk:
    """A file opened exclusively whose first write lands three bytes, then fails as a full disk does."""

    def __init__(self, real, path):
        self.file = real(path, "xb")

    def __enter__(self):
        return self

    def write(self, data):
        self.file.write(data[:3])
        self.file.flush()
        raise OSError(28, "No space left on device")

    def __exit__(self, *args):
        self.file.close()


def _fill_once(monkeypatch):
    import builtins
    real, done = builtins.open, []

    def open_file(path, mode="r", *args, **kw):
        if mode == "xb" and not done:                # the pull's first file write, wherever it is made
            done.append(Path(path))
            return FullDisk(real, path)
        return real(path, mode, *args, **kw)
    monkeypatch.setattr(builtins, "open", open_file)
    return done


@pytest.mark.parametrize("existing", [False, True], ids=["a new file", "an existing file"])
def test_finding_4_a_write_that_fails_part_way_never_puts_a_part_of_a_file_in_its_place(tmp_path, monkeypatch, existing):
    ws = tmp_path / "ws"
    ws.mkdir()
    dest = ws / "new.txt"
    if existing:
        dest.write_bytes(b"old contents\n")
    before = box.tree_map(ws)
    tree = helpers.tar_of([("./", "dir", b""), ("./new.txt", "file", b"complete contents\n")])
    opened = _fill_once(monkeypatch)
    with pytest.raises(box.Refused):
        box.bring_in(ws, tree, before=before)
    assert opened, "no write was made"
    assert (dest.read_bytes() == b"old contents\n") if existing else not dest.exists(), "a part of a file was put in its place"
    assert list(ws.iterdir()) == ([dest] if existing else []) and not list(box.spare(ws).glob("*"))
    monkeypatch.undo()
    box.bring_in(ws, tree, before=box.tree_map(ws))           # the next pull puts it in place whole
    assert dest.read_bytes() == b"complete contents\n"


def test_a_box_ended_between_a_new_files_write_and_its_put_leaves_nothing_at_its_name_and_the_next_pull_puts_it(monkeypatch,
                                                                                                                tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tree = helpers.tar_of([("./", "dir", b""), ("./new.txt", "file", b"complete contents\n")])

    def ended(temp, path):
        raise Killed

    monkeypatch.setattr(box, "_put_new", ended)
    with pytest.raises(Killed):
        box.bring_in(ws, tree, before={})
    assert not (ws / "new.txt").exists()
    monkeypatch.undo()
    box.bring_in(ws, tree, before={})
    assert (ws / "new.txt").read_bytes() == b"complete contents\n"


def test_a_file_an_ended_pull_left_with_a_second_name_in_the_boxs_own_folder_is_removed_before_the_next_pull(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_bytes(b"a\n")
    box.spare(ws).mkdir()
    os.link(ws / "a.txt", box.spare(ws) / "left.part")          # a box ended between a new file's two names, elsewhere than Windows
    tree = helpers.tar_of([("./", "dir", b""), ("./a.txt", "file", b"a\n"), ("./b.txt", "file", b"b\n")])
    box.bring_in(ws, tree, before={"a.txt": box._mark(b"a\n")})
    assert (ws / "b.txt").read_bytes() == b"b\n" and os.lstat(ws / "a.txt").st_nlink == 1
    assert not list(box.spare(ws).iterdir())
