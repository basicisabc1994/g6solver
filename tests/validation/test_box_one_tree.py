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
    ("box_mcp.py", "call", "tree_map"): "the tree the call mirrored, kept in its state for the three-way pull",
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
    ("box_mcp.py", "call", "tree_map"): _ENDED,
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
