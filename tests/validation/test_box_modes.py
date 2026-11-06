# SPDX-License-Identifier: Apache-2.0
"""A tree carries what git carries (replay cohort, repair 1, review 1's finding 2): every crossing between the host's workspace
and the container carries each entry's kind and mode, git's 100644, 100755 and 120000. The host holds no executable bit, so the
workspace's record lists the executables (``box_mcp.links_of``), as git's index keeps mode 100755 under ``core.fileMode=false``;
the tar carries the mode, the tree map's mark carries it (``box_mcp.EXEC``), and the container's file has it again.

Fakes (no docker, no Claude Code, no model): the container is a local folder whose executable bits are held beside it
(``ModeDocker``), since this host holds none, and the real MIRROR runs on the folder; its own handling of a mode is read by
recording each ``fchmod`` it makes (``test_the_mirror_sets_each_files_mode_from_its_tar_member``)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(HERE))

import box_mcp as box  # noqa: E402
import run as we  # noqa: E402

_spec = importlib.util.spec_from_file_location("one_tree_helpers_modes", Path(__file__).with_name("test_box_one_tree.py"))
one_tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(one_tree)
helpers = one_tree.helpers

EXECUTABLES = {"run.sh": b"#!/bin/sh\nexit 0\n", "bin/tool": b"#!/bin/sh\necho tool\n"}


def tar_of(entries: list[tuple[str, str, bytes, int]]) -> bytes:
    """A tar from (name, kind, data, mode), kind "file", "dir" or "lnk" (a hard link to the name ``data``)."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, kind, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "lnk":
                info.type, info.linkname = tarfile.LNKTYPE, data.decode()
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def entry(rel: str, data: bytes, mode: str = "100644") -> bytes:
    """One name of the fake container's capture (``ModeDocker.capture``), with its git mode."""
    return b"=== " + rel.encode() + f" ({mode})".encode() + b"\n" + data + b"\n=== end\n"


class ModeDocker(one_tree.BoxDocker):
    """The bug's container as a local folder (``BoxDocker``) whose executable bits are held beside it (``modes``), since this host
    holds none. A mirror's members' modes are kept (``sent``) and the container's bits set from them, as the real MIRROR sets a
    file's mode from its member, and its tar goes to the real MIRROR on the folder; every export carries each file's mode; a
    command is a Python step on this container (``commands``); each capture is the folder's files and modes against the base's."""

    def __init__(self, root: Path, task) -> None:
        super().__init__(root, task)
        for rel, data in EXECUTABLES.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_bytes(data)
        self.base = one_tree.snapshot(self.repo)
        self.modes, self.base_modes = set(EXECUTABLES), set(EXECUTABLES)
        self.sent: list[set[str]] = []
        self.extra4: list[tuple[str, str, bytes, int]] = []

    def export(self) -> bytes:
        tree = tarfile.open(fileobj=io.BytesIO(helpers.export_of(self.repo)))
        return tar_of([(m.name, "dir" if m.isdir() else "file", b"" if m.isdir() else tree.extractfile(m).read(),
                        0o755 if m.isdir() or m.name[2:] in self.modes else 0o644) for m in tree.getmembers()] + self.extra4)

    def capture(self) -> bytes:
        base, now = self.base, one_tree.snapshot(self.repo)

        def mode(rel, held):
            return box.EXEC_MODE if rel in held else box.FILE_MODE
        return b"".join(entry(rel, now[rel], mode(rel, self.modes)) if rel in now else entry(rel, b"<removed>")
                        for rel in sorted({*base, *now})
                        if base.get(rel) != now.get(rel) or (rel in now and mode(rel, self.modes) != mode(rel, self.base_modes)))

    def __call__(self, *args, input=None, timeout=None, env=None, merged=False):
        if box.PULL in args or ("tar" in args and "-c" in args):
            self.calls.append(("pull",) if box.PULL in args else args)
            if box.PULL in args and self.pull_fails:
                raise self.pull_fails.pop(0)
            return helpers.ok(self.export())
        if box.MIRROR in args:
            self.calls.append(args)
            members = tarfile.open(fileobj=io.BytesIO(input)).getmembers()
            done = subprocess.run([sys.executable, "-I", "-c", box.MIRROR, str(self.repo), args[args.index(box.MIRROR) + 2]],
                                  input=input, capture_output=True, timeout=60)
            sent = {m.name for m in members if m.isfile() and m.mode & 0o100}
            self.sent.append(sent)
            if done.returncode == 0:
                self.modes = sent
            return done
        if box.SHELL in args:
            self.calls.append(("command", args[-1]))
            return helpers.ok(args[-2].encode() + b"\n" + self.commands[args[-1]](self))
        return super().__call__(*args, input=input, timeout=timeout, env=env, merged=merged)

    def sh(self, ctr, script, *a, **kw):
        saved = re.search(r'git diff --cached --binary .* > "([^"]+)"', script)
        if saved:
            self.files[saved[1]] = self.capture()
            return helpers.captured()
        return helpers.HostDocker.sh(self, ctr, script, *a, **kw)


def scripted(docker: ModeDocker, sessions, seen: dict):
    """A host session's starter with no model: ("write", rel, bytes) as Claude Code's Write tool; ("run", command) and ("killed",
    command), a call of the box's ``run``; ("look", fn), ``fn(workspace, seen)``."""
    starts = []

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        sid, steps = sessions[len(starts)]
        starts.append(cwd)
        config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text("utf-8"))["mcpServers"]["box"]
        opts = one_tree.box_options(config["args"][2:])
        ws = Path(cwd)
        for step in steps:
            if step[0] == "write":
                (ws / step[1]).write_bytes(step[2])
            elif step[0] == "run":
                seen.setdefault("replies", []).append(box.call(opts, {"name": "run", "arguments": {"command": step[1]}}, docker))
            elif step[0] == "killed":
                try:
                    box.call(opts, {"name": "run", "arguments": {"command": step[1]}}, docker)
                except one_tree.Killed:
                    seen.setdefault("replies", []).append("killed")
            elif step[0] == "look":
                step[1](ws, seen)
        return helpers.Proc(helpers.stream(sid))
    return start


def run_modes(monkeypatch, tmp_path, sessions, *, commands=None, arm="A", pull_fails=()):
    task = we.load_tasks()[0]
    docker = ModeDocker(tmp_path, task)
    docker.commands.update(commands or {})
    docker.pull_fails.extend(pull_fails)
    seen: dict = {}
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
    helpers.fake_end(monkeypatch)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: pytest.fail("no key is read"),
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, model=helpers.SONNET, environ={"PATH": "p"}, executor="host",
                     starter=scripted(docker, sessions, seen))
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    final = ctx.archive.root / record["run"] / "final.patch"
    return SimpleNamespace(record=record, docker=docker, seen=seen, workspaces=workspaces,
                           final=final.read_bytes() if final.is_file() else None)


def look(ws: Path, seen: dict) -> None:
    seen["record"] = box.read_links(ws)
    seen["map"] = box.tree_map(ws)
    seen["packed"] = {m.name: m.mode for m in tarfile.open(fileobj=io.BytesIO(box.pack(ws))).getmembers() if m.isfile()}


def exec_mark(data: bytes) -> str:
    return box.EXEC + hashlib.sha256(data).hexdigest()


# --- finding 2, as the review gave it, and the marks -----------------------------------------------------------------------------
def test_finding_2_an_executable_survives_the_export_into_the_workspace_and_the_pack_back(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    box.bring_in(ws, tar_of([("run.sh", "file", b"#!/bin/sh\nexit 0\n", 0o755), ("plain.txt", "file", b"x\n", 0o644)]))
    with tarfile.open(fileobj=io.BytesIO(box.pack(ws))) as replay:
        assert replay.getmember("run.sh").mode == 0o755 and replay.getmember("plain.txt").mode == 0o644
    assert box.read_links(ws)["executable"] == {"run.sh"} and box.tree_map(ws)["run.sh"] == exec_mark(b"#!/bin/sh\nexit 0\n")


def test_a_change_of_mode_alone_is_a_change_and_the_three_kinds_differ():
    data = b"same bytes\n"
    marks = {box.FILE_MODE: box._mark(data), box.EXEC_MODE: box._mark(data, executable=True),
             box.LINK_MODE: box._mark(data, link=True)}
    assert len(set(marks.values())) == 3 and all(box._is_mark(m) for m in marks.values())
    assert {box._mode_of(mark): mode for mode, mark in marks.items()} == {mode: mode for mode in marks}
    assert box._mode_of(box._mark(None)) is None and box._marked(data, box.EXEC_MODE) == marks[box.EXEC_MODE]


# --- each crossing carries the mode ---------------------------------------------------------------------------------------------
def test_an_executable_reaches_the_workspace_as_git_holds_it_and_the_container_with_its_mode(monkeypatch, tmp_path):
    got = run_modes(monkeypatch, tmp_path, [("s1", [("look", look)])])
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["record"]["executable"] == set(EXECUTABLES) and got.seen["record"]["links"] == set()
    assert all(got.seen["map"][rel] == exec_mark(data) for rel, data in EXECUTABLES.items())
    assert {rel: got.seen["packed"][rel] for rel in EXECUTABLES} == {rel: 0o755 for rel in EXECUTABLES}
    assert got.seen["packed"]["src/mod.py"] == 0o644
    assert len(got.docker.sent) >= 2 and all(sent == set(EXECUTABLES) for sent in got.docker.sent)
    assert got.docker.modes == set(EXECUTABLES) and got.final == b"", "a run that changed nothing kept a change of mode"


def chmod(docker) -> bytes:
    docker.modes.add("src/mod.py")                 # chmod +x src/mod.py && chmod -x run.sh
    docker.modes.discard("run.sh")
    return b""


def test_a_mode_a_command_changes_comes_back_to_the_workspace_and_into_the_kept_patch(monkeypatch, tmp_path):
    got = run_modes(monkeypatch, tmp_path, [("s1", [("run", "chmod"), ("look", look)])], commands={"chmod": chmod})
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["replies"][0]["isError"] is False, got.seen["replies"]
    assert got.seen["record"]["executable"] == {"bin/tool", "src/mod.py"}
    assert got.docker.sent[-1] == {"bin/tool", "src/mod.py"}
    assert got.final == (entry("run.sh", EXECUTABLES["run.sh"]) + entry("src/mod.py", got.docker.base["src/mod.py"], box.EXEC_MODE)), \
        got.final


def test_the_write_tool_writing_an_executables_file_keeps_its_mode(monkeypatch, tmp_path):
    got = run_modes(monkeypatch, tmp_path, [("s1", [("write", "run.sh", b"#!/bin/sh\nexit 1\n"), ("look", look)])])
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["packed"]["run.sh"] == 0o755 and got.docker.modes == set(EXECUTABLES)
    assert got.final == entry("run.sh", b"#!/bin/sh\nexit 1\n", box.EXEC_MODE), got.final


@pytest.mark.parametrize("restart", ["the next call", "the runner"])
@pytest.mark.parametrize("at", ["after the command", "after the record's pending", "before the record is kept"])
def test_a_box_ended_while_its_pull_brings_a_mode_back_keeps_it_in_the_kept_patch(monkeypatch, tmp_path, at, restart):
    real_links, ran, killed = box.write_links, [], []

    def write_links(workspace, links, pending=None, **kw):
        if ran and not killed and ((at == "after the record's pending" and pending)
                                   or (at == "before the record is kept" and not pending)):
            killed.append(at)
            if pending:
                real_links(workspace, links, pending, **kw)
            raise one_tree.Killed
        return real_links(workspace, links, pending, **kw)

    monkeypatch.setattr(box, "write_links", write_links)
    steps = [("killed", "chmod")] + ([("run", "true")] if restart == "the next call" else [])
    got = run_modes(monkeypatch, tmp_path, [("s1", steps)], pull_fails=[one_tree.Killed()] if at == "after the command" else [],
                    commands={"chmod": lambda docker: (ran.append(1), chmod(docker))[1], "true": lambda docker: b""})
    assert got.seen["replies"][0] == "killed" and killed == ([] if at == "after the command" else [at])
    assert got.record["status"] == "finished", got.record.get("error")
    if restart == "the next call":
        assert got.seen["replies"][1]["isError"] is False and "came back now" in got.seen["replies"][1]["content"][0]["text"]
        assert "box_settled" not in got.record
    else:
        assert [s["phase"] for s in got.record["box_settled"]] == ["settled"]
    assert got.final == (entry("run.sh", EXECUTABLES["run.sh"]) + entry("src/mod.py", got.docker.base["src/mod.py"], box.EXEC_MODE)), \
        got.final


def hard_link_and_chmod(docker) -> bytes:
    chmod(docker)
    docker.extra4.append(("./alias.py", "lnk", b"src/mod.py", 0o644))     # a name the host cannot hold: the pull is refused
    return b""


def test_a_pull_refused_with_modes_in_the_tree_sets_the_container_back_with_its_modes(monkeypatch, tmp_path):
    got = run_modes(monkeypatch, tmp_path, [("s1", [("run", "ln")])], commands={"ln": hard_link_and_chmod})
    reply = got.seen["replies"][0]
    assert reply["isError"] is True and "set back" in reply["content"][0]["text"], reply
    assert got.docker.modes == set(EXECUTABLES) and got.docker.sent[-1] == set(EXECUTABLES)
    assert got.final == b"", "a change the agent was told was not kept reached the patch"


def test_a_reset_the_box_was_ended_inside_is_finished_with_its_modes_and_never_pulled(monkeypatch, tmp_path):
    real, ran, stopped = ModeDocker.__call__, [], []

    def call(self, *args, **kw):          # the reset after the refused pull: the box is ended as its mirror starts
        if box.MIRROR in args and ran and not stopped:
            stopped.append(1)
            raise one_tree.Killed
        if box.PULL in args and stopped:
            ran.append("pulled after the reset")
        return real(self, *args, **kw)

    monkeypatch.setattr(ModeDocker, "__call__", call)
    got = run_modes(monkeypatch, tmp_path, [("s1", [("killed", "ln")])],
                    commands={"ln": lambda docker: (ran.append(1), hard_link_and_chmod(docker))[1]})
    assert stopped and got.seen["replies"] == ["killed"] and "pulled after the reset" not in ran
    assert got.record["status"] == "finished", got.record.get("error")
    assert [s["phase"] for s in got.record["box_settled"]] == ["set back"]
    assert got.docker.modes == set(EXECUTABLES) and got.docker.sent[-1] == set(EXECUTABLES)
    assert got.final == b"", "a change the agent was told was not kept reached the patch"


def test_a_pull_that_fails_part_way_through_modes_sets_the_record_to_what_the_workspace_holds(monkeypatch, tmp_path):
    real_write, ran, writes = box._write_new, [], []

    def write_new(path, data, **kw):
        if ran:
            writes.append(path)
            if len(writes) == 2:
                raise OSError(28, "No space left on device")
        return real_write(path, data, **kw)

    def change(docker) -> bytes:
        ran.append(1)
        chmod(docker)                                                 # src/mod.py made executable: the record carries it
        (docker.repo / "bin" / "tool").write_bytes(b"#!/bin/sh\necho new\n")
        (docker.repo / "src" / "by_box.py").write_bytes(b"new = 1\n")
        return b""

    monkeypatch.setattr(box, "_write_new", write_new)
    got = run_modes(monkeypatch, tmp_path, [("s1", [("run", "change"), ("look", look)])], commands={"change": change})
    text = got.seen["replies"][0]["content"][0]["text"]
    assert got.seen["replies"][0]["isError"] is True and "only in part" in text, text
    held = {rel for rel, mark in got.seen["map"].items() if mark.startswith(box.EXEC)}
    assert got.seen["record"]["executable"] == held and got.seen["record"]["pending"] == {}
    assert got.docker.sent[-1] == held


def test_arm_ks_check_that_a_repair_changed_the_tree_sees_a_repair_that_changes_only_a_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(we, "run_critic", lambda ctx, task, run, agent, base, title, text, n=1:
                        one_tree.DEFECT if n == 1 else one_tree.CLEAN)
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: helpers.ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: helpers.AT_PARENT)
    got = run_modes(monkeypatch, tmp_path, [("s1", [("write", "src/mod.py", b"def f():\n    return 1\n")]),
                                            ("s1", [("run", "chmod")])], commands={"chmod": chmod}, arm="K2")
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.record["repairs"]["reason"] != "the agent left the code as it was" and got.record["repairs"]["rounds"] == 2
    assert entry("src/mod.py", b"def f():\n    return 1\n", box.EXEC_MODE) in got.final, got.final


# --- the mirror script itself: a file's mode is its tar member's -----------------------------------------------------------------
#: Run before MIRROR on the host, which has no ``fchmod``: every mode the script sets is printed with the name it set it on.
FCHMOD = r'''import os, sys
_paths, _open = {}, os.open
def _opened(path, *a, **kw):
    fd = _open(path, *a, **kw)
    _paths[fd] = os.path.relpath(path, sys.argv[1]).replace(os.sep, "/")
    return fd
def _fchmod(fd, mode):
    print("FCHMOD", _paths.get(fd), oct(mode), file=sys.stderr)
os.open, os.fchmod = _opened, _fchmod
'''


def test_the_mirror_sets_each_files_mode_from_its_tar_member(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, data in {"same.txt": b"same\n", "gains.sh": b"same\n", "edited.txt": b"old\n"}.items():
        (repo / rel).write_bytes(data)
    old = stat.S_IMODE((repo / "edited.txt").stat().st_mode)
    tar = tar_of([("same.txt", "file", b"same\n", 0o644), ("gains.sh", "file", b"same\n", 0o755),
                  ("edited.txt", "file", b"new\n", 0o644), ("new.sh", "file", b"#!/bin/sh\n", 0o755),
                  ("new.txt", "file", b"plain\n", 0o644)])
    done = subprocess.run([sys.executable, "-I", "-c", FCHMOD + box.MIRROR, str(repo), "tester"], input=tar, capture_output=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    set_ = {line.split()[1]: line.split()[2] for line in done.stderr.decode().splitlines() if line.startswith("FCHMOD")}
    assert set_["gains.sh"] == "0o755", "the same bytes whose executable bit changed kept the old mode"
    assert set_["new.sh"] == "0o755" and set_["new.txt"] == "0o644"
    assert "same.txt" not in set_, "a file with the same bytes and the same mode was touched"
    assert set_["edited.txt"] == oct(old), "a replaced file whose executable bit is the tar's did not keep its mode"
    assert (repo / "new.sh").read_bytes() == b"#!/bin/sh\n" and (repo / "edited.txt").read_bytes() == b"new\n"
