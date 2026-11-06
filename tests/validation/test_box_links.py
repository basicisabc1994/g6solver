# SPDX-License-Identifier: Apache-2.0
"""The host executor carries symbolic links (PLAN.md, 2026-11-06 evening, ruling 1).

11 replay-cohort bugs were excluded because their trees hold a symbolic link, and the host executor refused any link when it
copied a tree out of the container; a user's repository with a link would be refused the same way. Now one executor carries a
link as git carries one where links cannot be made: in the host's workspace a plain file holding its target, listed in the
workspace's link record (``box_mcp.links_of``) as git's index lists mode 120000 under ``core.symlinks=false``; in the container a
link again. A link is never followed on either side, whatever its target, a tree compares it by its target and kind, and the
kept patch shows it as git does.

Two kinds of test. Fakes (no docker, no Claude Code, no model): the container is a local folder whose links are held beside it
(``LinkDocker``), since this host may not make a link, and the real MIRROR runs on the folder for everything that is not a link.
Real containers (``real``): the box's own scripts, git and GNU tar, in a container with no network from a development bug's
local task image, on a scratch repository made there; nothing of any bug is read, and the container is removed after."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(HERE))

import box_mcp as box  # noqa: E402
import run as we  # noqa: E402

_spec = importlib.util.spec_from_file_location("one_tree_helpers_links", Path(__file__).with_name("test_box_one_tree.py"))
one_tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(one_tree)
helpers = one_tree.helpers

SECRET = b"what a link leaving the repository points to: never read"
FILES = {"docs/notes.md": b"notes\n", "docs/other.md": b"other\n", "same.txt": b"docs/notes.md"}
#: The fake container's links, by name: inside the repository, leaving it (absolute and relative), to a folder, and broken.
LINKS = {"CHANGELOG.md": "docs/notes.md", "out": "/etc/passwd", "up": "../../outside.txt", "lib": "src", "broken": "no/such/file"}
CASES = {"inside": "CHANGELOG.md", "leaving, absolute": "out", "leaving, relative": "up", "to a folder": "lib", "broken": "broken"}


def tar_of(entries: list[tuple[str, str, bytes]]) -> bytes:
    """A tar from (name, kind, data): kind "file", "dir", "sym" (data its target's bytes, any bytes) or "lnk" (a hard link)."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind in ("sym", "lnk"):
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = data.decode("utf-8", "surrogateescape")
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def reparse(path: Path) -> bool:
    """Whether ``path`` is a real link, a junction or another reparse point: something a tool could act through."""
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def entry(rel: str, data: bytes, *, link: bool = False) -> bytes:
    """One name of the fake container's capture (``LinkDocker.capture``)."""
    return b"=== " + rel.encode() + (b" (link)" if link else b"") + b"\n" + data + b"\n=== end\n"


class LinkDocker(one_tree.BoxDocker):
    """The bug's container as a local folder (``BoxDocker``) whose symbolic links are held beside it, by name (``links``), since
    this host may not make one. A mirror's link members are taken from its tar (kept in ``sent``), and the rest of the tar goes
    to the real MIRROR on the folder; every export (``run.workspace_from``'s, the pull's) is the folder's with each link as a link
    member, as GNU tar writes it; a command is a Python step on this container (``commands``); and each capture is the folder's
    files and links against the base's (``capture``)."""

    def __init__(self, root: Path, task, links: dict[str, str], files: dict[str, bytes]) -> None:
        super().__init__(root, task)
        for rel, data in files.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_bytes(data)
        self.base = one_tree.snapshot(self.repo)
        self.links, self.base_links = dict(links), dict(links)
        self.sent: list[dict[str, str]] = []

    def export(self) -> bytes:
        tree = tarfile.open(fileobj=io.BytesIO(helpers.export_of(self.repo)))
        entries = [(m.name, "dir" if m.isdir() else "file", b"" if m.isdir() else tree.extractfile(m).read())
                   for m in tree.getmembers()]
        return tar_of([*entries, *(("./" + rel, "sym", t.encode()) for rel, t in sorted(self.links.items())), *self.extra])

    def tree(self, files: dict[str, bytes], links: dict[str, str]) -> dict[str, tuple[str, bytes]]:
        return {**{rel: ("file", data) for rel, data in files.items()}, **{rel: ("link", t.encode()) for rel, t in links.items()}}

    def capture(self) -> bytes:
        base, now = self.tree(self.base, self.base_links), self.tree(one_tree.snapshot(self.repo), self.links)
        return b"".join(entry(rel, now[rel][1], link=now[rel][0] == "link") if rel in now else entry(rel, b"<removed>")
                        for rel in sorted({*base, *now}) if base.get(rel) != now.get(rel))

    def __call__(self, *args, input=None, timeout=None, env=None, merged=False):
        if box.PULL in args or ("tar" in args and "-c" in args):
            self.calls.append(("pull",) if box.PULL in args else args)
            if box.PULL in args and self.pull_fails:
                raise self.pull_fails.pop(0)
            return helpers.ok(self.export())
        if box.MIRROR in args:
            at = args.index(box.MIRROR)
            assert args[at + 1] == self.task.repo and args[:4] == ("exec", "-i", "-u", "root")
            self.calls.append(args)
            tar = tarfile.open(fileobj=io.BytesIO(input))
            members = tar.getmembers()
            links = {m.name: m.linkname for m in members if m.issym()}
            rest = tar_of([(m.name, "dir" if m.isdir() else "file", b"" if m.isdir() else tar.extractfile(m).read())
                           for m in members if not m.issym()])
            self.sent.append(links)
            done = subprocess.run([sys.executable, "-I", "-c", box.MIRROR, str(self.repo), args[at + 2]], input=rest,
                                  capture_output=True, timeout=60)
            if done.returncode == 0:
                self.links = links
            return done
        if box.SHELL in args:
            self.calls.append(("command", args[-1]))
            return helpers.ok(args[-2].encode() + b"\n" + self.commands[args[-1]](self))
        return super().__call__(*args, input=input, timeout=timeout, env=env, merged=merged)

    def sh(self, ctr, script, *a, **kw):
        if we.PATCH_BYTES in script:
            return helpers.ok(f"{we.PATCH_BYTES}={len(self.capture())}\n".encode())
        saved = re.search(r'git diff --cached --binary .* > "([^"]+)"', script)
        if saved:
            self.files[saved[1]] = self.capture()
            return helpers.ok()
        return helpers.HostDocker.sh(self, ctr, script, *a, **kw)


def scripted(docker: LinkDocker, sessions: list[tuple[str, list[tuple]]], seen: dict):
    """A host session's starter with no model, as ``test_box_one_tree.scripted``: ("write", rel, bytes) as Claude Code's Write tool
    writes; ("run", command) and ("killed", command), a call of the box's ``run`` (the second with the box ended where a test's
    patch sets); ("look", fn), ``fn(workspace, seen)`` while the session runs; ("spoil", bytes), the workspace's link record
    overwritten with those bytes."""
    starts = []

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        sid, steps = sessions[len(starts)]
        starts.append(cwd)
        config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text("utf-8"))["mcpServers"]["box"]
        opts = one_tree.box_options(config["args"][2:])
        ws = Path(cwd)
        for step in steps:
            if step[0] == "write":
                (ws / step[1]).parent.mkdir(parents=True, exist_ok=True)
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
            elif step[0] == "spoil":
                box.links_of(ws).write_bytes(step[1])
        return helpers.Proc(helpers.stream(sid))
    return start


def run_links(monkeypatch, tmp_path, sessions, *, commands=None, links=LINKS, files=FILES, extra=(), pull_fails=(), arm="A"):
    """One host run of ``arm`` (``run._run_task``), its sessions scripted (``scripted``), on a ``LinkDocker``."""
    task = we.load_tasks()[0]
    (tmp_path / "outside.txt").write_bytes(SECRET)          # what "up" names from the fake container's repository
    docker = LinkDocker(tmp_path, task, links, files)
    docker.commands.update(commands or {})
    docker.extra.extend(extra)
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
    """What the session finds in its workspace: every file's bytes, the link record, the tree's map and the pack's link members."""
    seen["files"] = {p.relative_to(ws).as_posix(): p.read_bytes() for p in ws.rglob("*") if p.is_file()}
    seen["reparse"] = [p.relative_to(ws).as_posix() for p in ws.rglob("*") if reparse(p)]
    seen["record"] = box.read_links(ws)
    seen["record_path"] = box.links_of(ws)
    seen["map"] = box.tree_map(ws)
    seen["packed"] = {m.name: (m.type, m.linkname) for m in tarfile.open(fileobj=io.BytesIO(box.pack(ws))).getmembers()}


def link_mark(target: bytes) -> str:
    return box.LINK + hashlib.sha256(target).hexdigest()


# --- a tracked link, inside the repository, leaving it, to a folder, broken ------------------------------------------------------
@pytest.mark.parametrize("case", CASES)
def test_a_link_reaches_the_workspace_as_git_holds_it_and_the_container_as_the_same_link(monkeypatch, tmp_path, case):
    rel = CASES[case]
    target = LINKS[rel].encode()
    got = run_links(monkeypatch, tmp_path, [("s1", [("look", look)])])
    assert got.record["status"] == "finished", got.record.get("error")
    seen = got.seen
    assert seen["files"][rel] == target, "the workspace holds a link as a plain file holding its target"
    assert seen["reparse"] == [], "the workspace holds a real link or junction, which a tool could act through"
    assert rel in seen["record"]["links"] and seen["map"][rel] == link_mark(target)
    assert seen["packed"][rel] == (tarfile.SYMTYPE, LINKS[rel]), "the pack sends the link as a link with its target"
    assert not any(name.startswith(rel + "/") for name in seen["packed"]), "a link to a folder was walked"
    assert SECRET not in b"".join(seen["files"].values()), "a link leaving the repository was followed"
    assert seen["record_path"].parent == got.workspaces[0].parent and not seen["record_path"].is_relative_to(got.workspaces[0])
    assert len(got.docker.sent) >= 2 and all(sent.get(rel) == LINKS[rel] for sent in got.docker.sent)
    assert got.docker.links == LINKS and got.final == b"", "a run that changed nothing kept a change to a link"
    assert (tmp_path / "outside.txt").read_bytes() == SECRET
    assert not box.links_of(got.workspaces[0]).exists(), "the link record outlived the run's workspace"


# --- a link changed by a command, replaced by a file, and the reverse -----------------------------------------------------------
def relink(docker) -> bytes:
    docker.links["CHANGELOG.md"] = "docs/other.md"            # ln -sfn docs/other.md CHANGELOG.md
    return b""


def test_a_link_a_command_changes_comes_back_to_the_workspace_and_into_the_kept_patch(monkeypatch, tmp_path):
    got = run_links(monkeypatch, tmp_path, [("s1", [("run", "ln -sfn"), ("look", look)])], commands={"ln -sfn": relink})
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["replies"][0]["isError"] is False, got.seen["replies"]
    assert got.seen["files"]["CHANGELOG.md"] == b"docs/other.md" and "CHANGELOG.md" in got.seen["record"]["links"]
    assert got.docker.sent[-1]["CHANGELOG.md"] == "docs/other.md"
    assert got.final == entry("CHANGELOG.md", b"docs/other.md", link=True), got.final


def link_to_file(docker) -> bytes:
    del docker.links["CHANGELOG.md"]                             # rm CHANGELOG.md && echo '# notes' > CHANGELOG.md
    (docker.repo / "CHANGELOG.md").write_bytes(b"# notes\n")
    return b""


def file_to_link(docker) -> bytes:
    (docker.repo / "src" / "mod.py").unlink()                    # rm src/mod.py && ln -s ../tests/test_mod.py src/mod.py
    docker.links["src/mod.py"] = "../tests/test_mod.py"
    return b""


def file_to_a_link_of_its_bytes(docker) -> bytes:
    (docker.repo / "same.txt").unlink()                          # a file holding "docs/notes.md" made a link to docs/notes.md
    docker.links["same.txt"] = "docs/notes.md"
    return b""


@pytest.mark.parametrize(("change", "rel", "data", "link"), [
    (link_to_file, "CHANGELOG.md", b"# notes\n", False),
    (file_to_link, "src/mod.py", b"../tests/test_mod.py", True),
    (file_to_a_link_of_its_bytes, "same.txt", b"docs/notes.md", True),
])
def test_a_link_a_command_makes_a_file_or_a_file_it_makes_a_link_is_that_in_the_workspace_and_the_kept_patch(monkeypatch, tmp_path,
                                                                                                             change, rel, data,
                                                                                                             link):
    got = run_links(monkeypatch, tmp_path, [("s1", [("run", "swap"), ("look", look)])], commands={"swap": change})
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["files"][rel] == data and (rel in got.seen["record"]["links"]) is link
    assert got.seen["map"][rel] == (link_mark(data) if link else hashlib.sha256(data).hexdigest())
    assert (rel in got.docker.sent[-1]) is link and (rel in got.docker.links) is link
    assert got.final == entry(rel, data, link=link), got.final


# --- the Write tool on a link's file, and on a name a link left ------------------------------------------------------------------
@pytest.mark.parametrize(("rel", "new"), [("CHANGELOG.md", b"docs/other.md"), ("out", b"/etc/hostname"), ("lib", b"tests")])
def test_the_write_tool_writing_a_links_file_changes_its_target_as_git_does_and_never_writes_through_it(monkeypatch, tmp_path, rel,
                                                                                                       new):
    got = run_links(monkeypatch, tmp_path, [("s1", [("write", rel, new), ("look", look)])])
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.seen["packed"][rel] == (tarfile.SYMTYPE, new.decode()) and got.docker.links[rel] == new.decode()
    assert (got.docker.repo / "docs" / "notes.md").read_bytes() == b"notes\n", "the write went through the link to its target"
    assert got.final == entry(rel, new, link=True), got.final


def remove_link(docker) -> bytes:
    del docker.links["CHANGELOG.md"]
    return b""


def test_a_file_the_write_tool_makes_where_a_command_removed_a_link_is_a_file(monkeypatch, tmp_path):
    got = run_links(monkeypatch, tmp_path, [("s1", [("run", "rm CHANGELOG.md"), ("write", "CHANGELOG.md", b"# new\n"),
                                                    ("look", look)])], commands={"rm CHANGELOG.md": remove_link})
    assert got.record["status"] == "finished", got.record.get("error")
    assert "CHANGELOG.md" not in got.seen["record"]["links"] and got.seen["packed"]["CHANGELOG.md"][0] == tarfile.REGTYPE
    assert got.final == entry("CHANGELOG.md", b"# new\n"), got.final


# --- a stop between every two steps where the pull writes a link ----------------------------------------------------------------
def three_changes(docker) -> bytes:
    relink(docker)
    file_to_link(docker)
    docker.links["new"] = "docs/notes.md"
    return b""


@pytest.mark.parametrize("at", ["after the record's pending", "after a link's file", "before the record is kept"])
def test_a_box_ended_while_its_pull_brings_links_back_leaves_each_a_link_in_the_kept_patch(monkeypatch, tmp_path, at):
    real_links, real_write, ran, killed = box.write_links, box._write_new, [], []

    def write_links(workspace, links, pending=None):        # the pull after the command: never the export at the start
        if ran and not killed and ((at == "after the record's pending" and pending) or (at == "before the record is kept"
                                                                                       and not pending)):
            killed.append(at)
            if pending:
                real_links(workspace, links, pending)
            raise one_tree.Killed
        return real_links(workspace, links, pending)

    def write_new(path, data, **kw):
        if ran and not killed and at == "after a link's file" and data == b"docs/other.md":
            killed.append(at)
            real_write(path, data, **kw)
            raise one_tree.Killed
        return real_write(path, data, **kw)

    monkeypatch.setattr(box, "write_links", write_links)
    monkeypatch.setattr(box, "_write_new", write_new)
    got = run_links(monkeypatch, tmp_path, [("s1", [("killed", "three")])],
                    commands={"three": lambda docker: (ran.append(1), three_changes(docker))[1]})
    assert killed == [at] and got.seen["replies"] == ["killed"]
    assert got.record["status"] == "finished", got.record.get("error")
    assert [s["phase"] for s in got.record["box_settled"]] == ["settled"]
    assert got.final == (entry("CHANGELOG.md", b"docs/other.md", link=True) + entry("new", b"docs/notes.md", link=True)
                         + entry("src/mod.py", b"../tests/test_mod.py", link=True)), got.final


def test_a_pull_that_fails_part_way_through_links_sets_the_record_to_what_the_workspace_holds(monkeypatch, tmp_path):
    real_write, ran, writes = box._write_new, [], []

    def write_new(path, data, **kw):                         # the pull's second link written fails: CHANGELOG.md's landed
        if ran and data in (b"docs/other.md", b"docs/notes.md"):
            writes.append(path)
            if len(writes) == 2:
                raise OSError(28, "No space left on device")
        return real_write(path, data, **kw)

    monkeypatch.setattr(box, "_write_new", write_new)
    got = run_links(monkeypatch, tmp_path, [("s1", [("run", "three"), ("look", look)])],
                    commands={"three": lambda docker: (ran.append(1), three_changes(docker))[1]})
    text = got.seen["replies"][0]["content"][0]["text"]
    assert got.seen["replies"][0]["isError"] is True and "only in part" in text and "set back" in text, text
    assert got.seen["record"] == {"links": set(LINKS), "pending": {}}, got.seen["record"]
    assert got.seen["files"]["CHANGELOG.md"] == b"docs/other.md" and "new" not in got.seen["files"]
    assert got.docker.sent[-1] == {**LINKS, "CHANGELOG.md": "docs/other.md"}
    assert got.final == entry("CHANGELOG.md", b"docs/other.md", link=True), got.final


# --- a record that cannot be read, and a target the container cannot make -------------------------------------------------------
@pytest.mark.parametrize("bad", [b"{not json", b'{"links": "CHANGELOG.md", "pending": {}}',
                                 b'{"links": [], "pending": {"a": {"from": null, "to": "zz"}}}'])
def test_a_link_record_that_cannot_be_read_refuses_the_call_and_the_run_and_never_guesses(monkeypatch, tmp_path, bad):
    ws = tmp_path / "ws"
    helpers.write(ws, {"CHANGELOG.md": "docs/notes.md"})
    box.links_of(ws).write_bytes(bad)
    calls = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "ls"}}, lambda *a, **kw: calls.append(a))
    assert got["isError"] is True and calls == [] and "link record" in got["content"][0]["text"], got
    (tmp_path / "run").mkdir()
    run = run_links(monkeypatch, tmp_path / "run", [("s1", [("spoil", bad)])])
    assert run.record["status"] == "error" and "link record" in run.record.get("error", ""), run.record


@pytest.mark.parametrize("data", [b"", b"docs/\x00notes.md", b"x" * box.PATH_MAX, b"\xff\xfe"])
def test_a_links_file_whose_target_the_container_cannot_make_sends_nothing(tmp_path, data):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_bytes(b"x\n")
    (ws / "L").write_bytes(data)
    box.write_links(ws, {"L"})
    calls = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "ls"}}, lambda *a, **kw: calls.append(a))
    assert got["isError"] is True and calls == [] and "nothing was mirrored or run" in got["content"][0]["text"], got


# --- the export's one rule: a link is carried, what the host cannot hold is refused before anything is written ------------------
@pytest.mark.parametrize("bad", [[("./h", "lnk", b"a.py")], [("./t", "sym", b"")], [("./t", "sym", b"x" * box.PATH_MAX)],
                                 [("./t", "sym", b"\xff\xfe")], [("./d", "sym", b"a.py"), ("./d/x.py", "file", b"x")]])
def test_the_export_refuses_a_hard_link_a_target_the_container_cannot_make_or_a_name_inside_a_link_and_writes_nothing(tmp_path, bad):
    tree = tar_of([("./", "dir", b""), ("./a.py", "file", b"x\n"), *bad])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(RuntimeError):
        we.workspace_from(SimpleNamespace(docker=lambda *a, **kw: helpers.ok(tree)), SimpleNamespace(repo="/w/r"), "agent", dest)
    assert os.listdir(dest) == [] and not box.links_of(dest).exists()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_bytes(b"x\n")
    with pytest.raises(box.Refused):
        box.bring_in(ws, tree, before=box.tree_map(ws))
    assert one_tree.snapshot(ws) == {"a.py": b"x\n"} and not box.links_of(ws).exists()


def test_a_tree_with_no_link_keeps_no_link_record(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    box.bring_in(ws, tar_of([("./", "dir", b""), ("./src", "dir", b""), ("./src/a.py", "file", b"x\n")]))
    assert one_tree.snapshot(ws) == {"src/a.py": b"x\n"} and not box.links_of(ws).exists()


def hard_link(docker) -> bytes:
    docker.extra.append(("./alias.py", "lnk", b"src/mod.py"))   # ln src/mod.py alias.py: a name the host cannot hold as named
    (docker.repo / "src" / "by_box.py").write_bytes(b"made = 1\n")
    return b""


def test_a_pull_refused_with_links_in_the_tree_sets_the_container_back_with_its_links(monkeypatch, tmp_path):
    got = run_links(monkeypatch, tmp_path, [("s1", [("run", "ln")])], commands={"ln": hard_link})
    reply = got.seen["replies"][0]
    assert reply["isError"] is True and "set back" in reply["content"][0]["text"], reply
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.docker.links == LINKS and all(sent == LINKS for sent in got.docker.sent)
    assert got.final == b"", "a change the agent was told was not kept reached the patch"


def test_arm_ks_check_that_a_repair_changed_the_tree_sees_a_repair_that_changes_only_a_link(monkeypatch, tmp_path):
    monkeypatch.setattr(we, "run_critic", lambda ctx, task, run, agent, base, title, text, n=1:
                        one_tree.DEFECT if n == 1 else one_tree.CLEAN)
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: helpers.ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: helpers.AT_PARENT)
    got = run_links(monkeypatch, tmp_path, [("s1", [("write", "src/mod.py", b"def f():\n    return 1\n")]),
                                            ("s1", [("write", "CHANGELOG.md", b"docs/other.md")])], arm="K2")
    assert got.record["status"] == "finished", got.record.get("error")
    assert got.record["repairs"]["reason"] != "the agent left the code as it was" and got.record["repairs"]["rounds"] == 2
    assert entry("CHANGELOG.md", b"docs/other.md", link=True) in got.final, got.final


# --- the inventory: every rule that sorts a tree's entries by kind, and how it takes a link ---------------------------------------
#: Each place in the box (its functions and its two container scripts) and in the runner's two crossings that decides what an
#: entry is, with how it takes a symbolic link. A new place fails this test until it is named, so no rule that copies a tree
#: can meet a link it does not list.
KINDS = {
    ("box_mcp.py", "_plain"): "a real link or reparse point in the workspace is refused: the workspace's links are plain files",
    ("box_mcp.py", "_read"): "a file read is the regular file checked; a link's file is read as a file, its bytes the target",
    ("box_mcp.py", "_same_folder"): "a folder that became a link while packed refuses the pack",
    ("box_mcp.py", "_root"): "a workspace that is a link refuses",
    ("box_mcp.py", "_tree"): "a folder is walked; a file is a link when its record lists it (_is_link)",
    ("box_mcp.py", "pack"): "a link's file is sent as a SYMTYPE member with its bytes as the target",
    ("box_mcp.py", "written"): "a link's file is checked by name and file identity, as a file is",
    ("box_mcp.py", "export_members"): "a SYMTYPE member is carried, its target checked (_link_target); a hard link is refused",
    ("box_mcp.py", "bring_in"): "a SYMTYPE member is written as a plain file holding its target and listed in the record",
    ("MIRROR", "<script>"): "a SYMTYPE member is made a link fresh (never through a name), the same link left; others removed",
    ("MIRROR", "kind"): "a link in the container is 'other': cleared, never written through or walked",
    ("run.py", "workspace_from"): "the clean-up of a refused export removes a link itself, never what it points to",
    ("run.py", "_run_task"): "the clean-up of the workspace's siblings removes a link itself, never what it points to",
}
KIND_CALLS = {"isfile", "isdir", "issym", "islnk", "isreg", "is_dir", "is_file", "is_symlink", "islink", "S_ISLNK", "S_ISREG",
              "S_ISDIR", "S_ISCHR", "S_ISBLK", "S_ISFIFO", "S_ISSOCK", "is_junction"}
KIND_NAMES = {"SYMTYPE", "LNKTYPE", "DIRTYPE", "REGTYPE", "FILE_ATTRIBUTE_REPARSE_POINT"}


def _kind_places(source: str, label: str, only: set[str] | None = None) -> set[tuple[str, str]]:
    """Each (``label``, top-level function, or "<script>" for a container script's own lines) whose code decides what an entry
    is: a call of a kind test (``KIND_CALLS``) or a kind's name (``KIND_NAMES``)."""
    found: set[tuple[str, str]] = set()
    top = "<module>" if label.endswith(".py") else "<script>"

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name if where == top else where)
                continue
            if isinstance(child, ast.Call):
                hit = (child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")) in KIND_CALLS
            else:
                hit = isinstance(child, ast.Attribute) and child.attr in KIND_NAMES
            if hit and (only is None or where in only):
                found.add((label, where))
            visit(child, where)
    visit(ast.parse(source), top)
    return found


def test_every_rule_that_sorts_a_trees_entries_by_kind_is_named_with_how_it_takes_a_link():
    found = (_kind_places((HERE / "box_mcp.py").read_text("utf-8"), "box_mcp.py") | _kind_places(box.MIRROR, "MIRROR")
             | _kind_places(box.PULL, "PULL")
             | _kind_places((HERE / "run.py").read_text("utf-8"), "run.py", only={"workspace_from", "_run_task"}))
    assert found == set(KINDS), (sorted(found - set(KINDS)), sorted(set(KINDS) - found))


# --- real containers: the box's own scripts, git and GNU tar ---------------------------------------------------------------------
REAL_IMAGE = "g6we-task:click-3769-c53d310d65a5-r3"     # a development bug's task image: only its python3, git, tar and user
REPO = f"{we.HOME}/linkrepo"
OUTSIDE = f"{we.HOME}/outside.txt"
SETUP = f"""set -e
printf '%s' '{SECRET.decode()}' > {OUTSIDE}
mkdir -p {REPO} && cd {REPO}
git -c init.defaultBranch=main init -q
mkdir -p docs src
printf 'notes\\n' > docs/notes.md
printf 'other\\n' > docs/other.md
printf 'def f():\\n    return 0\\n' > src/mod.py
printf 'plain\\n' > tolink.txt
ln -s docs/notes.md CHANGELOG.md
ln -s /etc/passwd out
ln -s ../outside.txt up
ln -s src lib
ln -s no/such/file broken
ln -s docs/notes.md gone
ln -s docs/notes.md tofile
git add -A
git -c user.email=t@example.invalid -c user.name=t commit -qm base
git rev-parse HEAD
"""
REAL_LINKS = {"CHANGELOG.md": "docs/notes.md", "out": "/etc/passwd", "up": "../outside.txt", "lib": "src", "broken": "no/such/file",
              "gone": "docs/notes.md", "tofile": "docs/notes.md"}
#: What the known patch does to the scratch repository: a link added, one changed, one removed, one renamed (git reads it so), a
#: link made a file, a file made a link, and a plain edit.
KNOWN_CHANGES = """set -e
cd /tmp/known
ln -s src new_link
ln -sfn docs/other.md CHANGELOG.md
rm gone
mv broken broken_moved
rm tofile && printf 'now a file\\n' > tofile
rm tolink.txt && ln -s docs/other.md tolink.txt
printf 'def f():\\n    return 1\\n' > src/mod.py
"""
#: Each name's kind and target, or a file's SHA-256, in the scratch repository, and the SHA-256 of what the links leaving it name.
STATE = f"""cd {REPO} && for n in CHANGELOG.md out up lib broken gone tofile tolink.txt new_link; do
  if [ -L "$n" ]; then printf '%s link %s\\n' "$n" "$(readlink "$n")";
  elif [ -f "$n" ]; then printf '%s file %s\\n' "$n" "$(sha256sum < "$n" | cut -c1-16)";
  elif [ -e "$n" ]; then printf '%s other\\n' "$n"; else printf '%s none\\n' "$n"; fi; done
printf 'passwd %s\\n' "$(sha256sum < /etc/passwd | cut -c1-16)"
printf 'hostname %s\\n' "$(sha256sum < /etc/hostname | cut -c1-16)"
printf 'outside %s\\n' "$(sha256sum < {OUTSIDE} | cut -c1-16)"
printf 'notes %s\\n' "$(sha256sum < docs/notes.md | cut -c1-16)"
ls src | tr '\\n' ' '; echo
"""


@pytest.fixture
def real(tmp_path):
    """A container from ``REAL_IMAGE`` with no network, its scratch repository made, removed after the test. Each container step
    waits while less than 6.5 GB of memory is available (``cohorts.Floored``)."""
    import cohorts
    if shutil.which("docker") is None:
        pytest.skip("no docker on this machine")
    docker = cohorts.Floored()
    if docker("image", "inspect", REAL_IMAGE, "--format", "{{.Id}}", timeout=60).returncode != 0:
        pytest.skip(f"docker holds no {REAL_IMAGE}")
    name = f"g6we-linktest-{uuid.uuid4().hex[:10]}"
    try:
        we.checked(docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", "--cpus", "1", "--memory", "1g",
                          "-u", we.USER, "-e", f"HOME={we.HOME}", REAL_IMAGE, "tail", "-f", "/dev/null", timeout=300),
                   "the test's container")
        base = we.checked(docker.sh(name, SETUP, timeout=120), "the scratch repository").splitlines()[-1]
        yield SimpleNamespace(docker=docker, name=name, base=base, ws=tmp_path / "ws", tmp=tmp_path)
    finally:
        docker("rm", "-f", name, timeout=120)


def state(real) -> dict[str, str]:
    lines = we.checked(real.docker.sh(real.name, STATE, timeout=60), "the repository's state").splitlines()
    return {line.split(" ", 1)[0]: line.split(" ", 1)[1] for line in lines[:-1]} | {"src": lines[-1].strip()}


def test_real_the_export_brings_each_link_as_git_holds_it_and_the_mirror_makes_each_again_writing_through_none(real):
    before = state(real)
    real.ws.mkdir()
    we.workspace_from(SimpleNamespace(docker=real.docker), SimpleNamespace(repo=REPO), real.name, real.ws)
    ws = real.ws
    assert {rel: (ws / rel).read_bytes() for rel in REAL_LINKS} == {rel: t.encode() for rel, t in REAL_LINKS.items()}
    assert box.read_links(ws)["links"] == set(REAL_LINKS) and not any(reparse(p) for p in ws.rglob("*"))
    assert (ws / "lib").is_file() and SECRET not in b"".join(p.read_bytes() for p in ws.rglob("*") if p.is_file())
    (ws / "CHANGELOG.md").write_bytes(b"docs/other.md")      # the Write tool on a link's file: its target, as git reads it
    (ws / "out").write_bytes(b"/etc/hostname")                # a link leaving the repository, pointed elsewhere
    links = box.read_links(ws)["links"] - {"up"}
    box.write_links(ws, links)                                # "up" made a plain file: the mirror must not write through it
    (ws / "up").write_bytes(b"over the link\n")
    box.mirror(ws, real.docker, container=real.name, repo=REPO, user=we.USER, timeout=120)
    after = state(real)
    assert after["CHANGELOG.md"] == "link docs/other.md" and after["out"] == "link /etc/hostname"
    assert after["up"] == "file " + hashlib.sha256(b"over the link\n").hexdigest()[:16]
    assert {k: after[k] for k in ("lib", "broken", "gone", "tofile")} == {k: before[k] for k in ("lib", "broken", "gone", "tofile")}
    assert after["lib"] == "link src" and after["src"] == before["src"]
    for name in ("passwd", "hostname", "outside", "notes"):     # nothing a link names was written, as root or as the user
        assert after[name] == before[name], name


def test_real_a_known_patch_that_adds_changes_and_removes_links_comes_back_byte_for_byte_through_the_box(real):
    import scripted_session
    we.checked(real.docker.sh(real.name, f"cp -a {REPO} /tmp/known", timeout=120), "the known patch's copy")
    we.checked(real.docker.sh(real.name, KNOWN_CHANGES, timeout=120), "the known patch's changes")
    we.checked(real.docker.sh(real.name, we.diff_script("/tmp/known", real.base, "/tmp/known.patch"), timeout=120),
               "the known patch's capture")
    known_bytes = real.tmp / "known.patch"
    assert we.copy_out(real.docker, real.name, "/tmp/known.patch", known_bytes)
    patch = known_bytes.read_bytes()
    assert patch.count(b"new file mode 120000") == 2 and patch.count(b"deleted file mode 120000") == 2, patch
    assert b"-docs/notes.md\n\\ No newline at end of file\n+docs/other.md\n\\ No newline at end of file" in patch
    assert b"rename from broken\nrename to broken_moved" in patch, patch
    real.ws.mkdir()
    we.workspace_from(SimpleNamespace(docker=real.docker), SimpleNamespace(repo=REPO), real.name, real.ws)
    (real.ws / scripted_session.KNOWN).write_bytes(patch)    # the Write tool's way
    opts = SimpleNamespace(container=real.name, repo=REPO, workspace=str(real.ws), user=we.USER, timeout=120.0,
                           state=str(real.tmp / "ws.box.json"))
    got = box.call(opts, {"name": "run", "arguments": {"command": scripted_session.APPLY}}, box.docker_runner("docker"))
    assert got["isError"] is False, got
    ws, record = real.ws, box.read_links(real.ws)
    assert (ws / "new_link").read_bytes() == b"src" and (ws / "CHANGELOG.md").read_bytes() == b"docs/other.md"
    assert (ws / "tofile").read_bytes() == b"now a file\n" and (ws / "tolink.txt").read_bytes() == b"docs/other.md"
    assert (ws / "broken_moved").read_bytes() == b"no/such/file" and not (ws / "broken").exists()
    assert not (ws / "gone").exists() and not (ws / scripted_session.KNOWN).exists()
    assert record == {"links": set(REAL_LINKS) - {"gone", "tofile", "broken"} | {"new_link", "tolink.txt", "broken_moved"},
                      "pending": {}}
    box.mirror(ws, real.docker, container=real.name, repo=REPO, user=we.USER, timeout=120)    # the runner's last mirror
    we.checked(real.docker.sh(real.name, we.diff_script(REPO, real.base, "/tmp/final.patch"), timeout=120), "the capture")
    final = real.tmp / "final.patch"
    assert we.copy_out(real.docker, real.name, "/tmp/final.patch", final)
    assert final.read_bytes() == patch, "the kept patch is not the known patch, byte for byte"
