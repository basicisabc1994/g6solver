# SPDX-License-Identifier: Apache-2.0
"""The host-session author (PLAN.md, 2026-11-04): Claude Code runs on the host, on the subscription's sign-in, which never enters a
container; every command it runs goes through the box (``box_mcp.py``) into the bug's container, which has no network. Fakes only:
no docker, no Claude Code, no network. The mirror script runs for real, with this Python, on local folders."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]
SECRETS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
LIMIT_TEXT = "You've hit your limit · resets 7pm (Europe/London)"
TEST = "def test_owed():\n    assert 0\n"
ON_TREE = we.ProbeRun(1, {"test_critic.py::test_owed": "failed", "test_critic.py::test_ok": "passed"}, {}, "")
AT_PARENT = we.ProbeRun(1, {"test_critic.py::test_owed": "failed", "test_critic.py::test_ok": "passed"}, {}, "")


@pytest.fixture
def box():
    import box_mcp
    return box_mcp


def ok(stdout: bytes = b"", code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")


def write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")


def link(target: Path, at: Path) -> None:
    """A symbolic link, or on a Windows account that may not make one, a junction: both are reparse points."""
    try:
        os.symlink(target, at, target_is_directory=target.is_dir())
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(at))


def tar_of(entries: list[tuple[str, str, bytes]]) -> bytes:
    """A tar from (name, kind, data): kind "file", "dir" or "sym"."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, data.decode()
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def export_of(repo: Path) -> bytes:
    """``tar -c --exclude=.git --exclude=.venv .`` of a local folder, as GNU tar names it."""
    entries = [("./", "dir", b"")]
    for here, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in (".git", ".venv"))
        for name in [*dirs, *sorted(files)]:
            path = Path(here) / name
            rel = "./" + path.relative_to(repo).as_posix()
            entries.append((rel, "dir", b"") if path.is_dir() else (rel, "file", path.read_bytes()))
    return tar_of(entries)


def stream(sid: str, *, cost: float = 0.42, extra: tuple[dict, ...] = ()) -> list[str]:
    lines = [{"type": "system", "subtype": "init", "session_id": sid, "claude_code_version": "2.1.318"},
             {"type": "assistant", "session_id": sid, "message": {"id": f"m-{sid}", "model": SONNET, "content": [],
                                                                  "usage": {"input_tokens": 10, "output_tokens": 5}}},
             *extra,
             {"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": sid,
              "total_cost_usd": cost, "num_turns": 3}]
    return [json.dumps(x) for x in lines]


class Stdin(io.BytesIO):
    def close(self) -> None:
        self.text = self.getvalue().decode("utf-8")
        super().close()


class Proc:
    pid = 4242

    def __init__(self, lines: list[str]) -> None:
        self.stdin, self.stderr = Stdin(), io.BytesIO(b"")
        self.stdout = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def starter(sessions: list[tuple[str, str, list[str] | None]]):
    """A fake process starter: session i is (its id, what it writes in src/mod.py or "", its stream or None for a plain one)."""
    starts: list[dict] = []

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        sid, body, lines = sessions[len(starts)]
        mod = Path(cwd) / "src" / "mod.py"
        starts.append({"argv": list(argv), "cwd": cwd, "env": dict(env), "before": mod.read_text("utf-8") if mod.is_file() else None})
        if body:
            mod.write_text(body, encoding="utf-8")
        proc = Proc(lines if lines is not None else stream(sid))
        starts[-1]["proc"] = proc
        return proc
    return start, starts


class HostDocker:
    """The bug's container as a local folder: the export and the mirror really run on it; everything else answers as docker
    would, and nothing starts Claude Code in it."""
    exe = "docker"

    def __init__(self, root: Path, task) -> None:
        self.task, self.calls = task, []
        self.repo = root / "container" / task.name
        write(self.repo, {"src/mod.py": "def f():\n    return 0\n", "tests/test_mod.py": "def test_f():\n    pass\n",
                          ".git/HEAD": "ref: refs/heads/main\n", ".venv/bin/python": "#!/bin/sh\n"})

    def __call__(self, *args, input=None, timeout=None, env=None):
        import box_mcp
        self.calls.append(args)
        if "tar" in args and "-c" in args:
            return ok(export_of(self.repo))
        if box_mcp.MIRROR in args:
            at = args.index(box_mcp.MIRROR)
            assert args[at + 1] == self.task.repo and args[:4] == ("exec", "-i", "-u", "root")
            return subprocess.run([sys.executable, "-I", "-c", box_mcp.MIRROR, str(self.repo), args[at + 2]], input=input,
                                  capture_output=True, timeout=60)
        if args[0] == "cp" and args[1].endswith("/final.patch"):
            Path(args[2]).write_text((self.repo / "src" / "mod.py").read_text("utf-8"), encoding="utf-8")
            return ok()
        if args[0] == "cp":
            return ok(code=1)
        return ok()

    def sh(self, ctr, script, *a, **kw):
        self.calls.append(("sh", script))
        if script == we.TREE_STATE:
            return ok(json.dumps({"head": self.task.parent, "tree": "tree", "dirty": 0, "commits": 1}).encode())
        if script == we.ARM_STATE:
            return ok(json.dumps({"mcp_servers": [], "skills": []}).encode())
        if script.startswith("git status --porcelain"):
            return ok((self.repo / "src" / "mod.py").read_bytes())
        return ok()

    def popen(self, *a, **kw):
        raise AssertionError("no Claude Code runs in the container")


def host_run(monkeypatch, tmp_path, arm, sessions, *, environ=None):
    task = we.load_tasks()[0]
    docker = HostDocker(tmp_path, task)
    start, starts = starter(sessions)
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
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: pytest.fail("no key is read"),
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, model=SONNET, environ=environ or {"PATH": "p"}, executor="host", starter=start)
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    return record, docker, starts, ctx, task, workspaces


def mirrors(docker) -> list[int]:
    import box_mcp
    return [i for i, call in enumerate(docker.calls) if box_mcp.MIRROR in call]


# --- the command line and the environment ---------------------------------------------------------------------------------
def test_the_host_command_line_is_exactly_the_validated_flags_and_resumes_only_on_a_repair(tmp_path):
    start, starts = starter([("s1", "", None), ("s1", "", None)])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=lambda: pytest.fail("no key"), issue_reader=None,
                     g6={}, variant=we.ROUND3, arm="K1", model=SONNET, environ={"PATH": "p"}, executor="host", starter=start)
    run, ws = ctx.archive.start("run", "t"), tmp_path / "ws"
    ws.mkdir()
    mcp, settings = tmp_path / "mcp.json", tmp_path / "settings.json"
    for name, resume in (("stream.jsonl", ""), ("repair_stream_1.jsonl", "s1")):
        we.host_session(ctx, run, ws, mcp, settings, "fix it", name, resume=resume, seconds=60.0, tally=we.c.Tally(), stop_usd=10.5)
    flags = ["-p", "--restricted", "--tools", "Read,Edit,Write,Glob,Grep", "--mcp-config", str(mcp), "--strict-mcp-config",
             "--allowedTools", "mcp__box__run", "--settings", str(settings), "--permission-mode", "acceptEdits",
             "--permission-prompts", "none", "--output-format", "stream-json", "--verbose", "--model", SONNET, "--max-turns",
             str(we.c.MAX_TURNS)]
    first, repair = starts[0]["argv"], starts[1]["argv"]
    assert Path(first[0]).stem.lower() == "claude" and first[1:] == flags and repair[1:] == [*flags, "--resume", "s1"]
    assert "--resume" not in first and not any(t in a for a in first for t in ("Bash", "PowerShell"))
    assert first[first.index("--allowedTools") + 1] == "mcp__box__run" and first.count("--allowedTools") == 1
    assert "--dangerously-skip-permissions" not in first and "--continue" not in repair
    assert starts[0]["cwd"] == str(ws) and starts[0]["proc"].stdin.text == "fix it"
    assert (run.path / "stream.jsonl").is_file() and (run.path / "repair_stream_1.jsonl").is_file()


def test_no_key_or_token_reaches_the_host_session_even_when_set(tmp_path):
    environ = {"ANTHROPIC_API_KEY": "sk-ant-api03-notakey000000", "ANTHROPIC_AUTH_TOKEN": "tok", "CLAUDE_CODE_OAUTH_TOKEN": "oat",
               "anthropic_api_key": "lower", "PATH": "p", "USERPROFILE": "u"}
    assert we.host_env(environ) == {"PATH": "p", "USERPROFILE": "u"}
    start, starts = starter([("s1", "", None)])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=lambda: pytest.fail("no key"), issue_reader=None,
                     g6={}, variant=we.ROUND3, arm="K1", model=SONNET, environ=environ, executor="host", starter=start)
    (tmp_path / "ws").mkdir()
    we.host_session(ctx, ctx.archive.start("run", "t"), tmp_path / "ws", tmp_path / "m.json", tmp_path / "s.json", "go",
                    "stream.jsonl", resume="", seconds=60.0, tally=we.c.Tally(), stop_usd=10.5)
    env = starts[0]["env"]
    assert env == {"PATH": "p", "USERPROFILE": "u"} and not {k.upper() for k in env} & set(SECRETS)


# --- the workspace's export -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [("./evil", "sym", b"/etc/passwd"), ("/abs.py", "file", b"x"), ("./../up.py", "file", b"x"),
                                 ("./a/../../up.py", "file", b"x"), ("./A.py", "file", b"y")])
def test_the_workspace_export_refuses_a_link_an_absolute_name_a_dotdot_part_or_a_case_clash_and_writes_nothing(tmp_path, bad):
    task = SimpleNamespace(repo="/home/tester/work/r")
    archive = tar_of([("./", "dir", b""), ("./a.py", "file", b"x"), bad])
    ctx = SimpleNamespace(docker=lambda *a, **kw: ok(archive))
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(RuntimeError):
        we.workspace_from(ctx, task, "agent", dest)
    assert list(dest.iterdir()) == [] and not (tmp_path / "up.py").exists()


def test_the_workspace_export_brings_out_the_tree_without_git_or_venv(tmp_path):
    seen = []
    archive = tar_of([("./", "dir", b""), ("./src", "dir", b""), ("./src/m.py", "file", b"print(1)\n"), ("./README", "file", b"r")])
    ctx = SimpleNamespace(docker=lambda *a, **kw: (seen.append(a), ok(archive))[1])
    task = SimpleNamespace(repo="/home/tester/work/r")
    assert we.workspace_from(ctx, task, "agent", tmp_path) == 2
    assert (tmp_path / "src" / "m.py").read_bytes() == b"print(1)\n"
    assert seen == [("exec", "-u", we.USER, "-w", task.repo, "agent", "tar", "-c", "--exclude=.git", "--exclude=.venv", ".")]


def test_a_workspace_under_a_claude_folder_is_refused(tmp_path):
    ctx = SimpleNamespace(archive=SimpleNamespace(root=tmp_path / ".claude" / "archive"))
    with pytest.raises(RuntimeError, match=".claude"):
        we.host_workspace(ctx, "tag")


# --- the mirror ----------------------------------------------------------------------------------------------------------------
def test_the_mirror_copies_new_and_changed_files_removes_the_rest_and_keeps_git_and_venv(tmp_path, box):
    ws, repo = tmp_path / "ws", tmp_path / "repo"
    write(ws, {"a.py": "new\n", "sub/b.py": "same\n", "new.py": "n\n", ".venv/ignored.py": "x", ".git/config": "x"})
    (ws / "emptydir").mkdir()
    write(repo, {"a.py": "old\n", "sub/b.py": "same\n", "gone.py": "g\n", "sub/gone2.py": "g\n", "olddir/x.py": "x\n",
                 ".git/HEAD": "ref\n", ".venv/lib/x.py": "v\n", "sub/.venv/y.py": "nested\n"})
    stamp = (repo / "sub" / "b.py").stat().st_mtime_ns
    names = tarfile.open(fileobj=io.BytesIO(box.pack(ws))).getnames()
    assert not any(part in (".git", ".venv") for name in names for part in name.split("/"))
    done = subprocess.run([sys.executable, "-I", "-c", box.MIRROR, str(repo), "tester"], input=box.pack(ws), capture_output=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert (repo / "a.py").read_text() == "new\n" and (repo / "new.py").read_text() == "n\n" and (repo / "emptydir").is_dir()
    assert not (repo / "gone.py").exists() and not (repo / "sub" / "gone2.py").exists() and not (repo / "olddir").exists()
    assert (repo / ".git" / "HEAD").read_text() == "ref\n" and (repo / ".venv" / "lib" / "x.py").read_text() == "v\n"
    assert (repo / "sub" / ".venv" / "y.py").read_text() == "nested\n"
    assert (repo / "sub" / "b.py").stat().st_mtime_ns == stamp          # an unchanged file is not written again
    assert json.loads(done.stdout) == {"written": 2, "removed": 4}


def test_the_mirror_script_refuses_a_tar_that_climbs_out_and_changes_nothing(tmp_path, box):
    repo = tmp_path / "repo"
    write(repo, {"a.py": "old\n"})
    bad = tar_of([("a.py", "file", b"new\n"), ("../out.py", "file", b"x")])
    done = subprocess.run([sys.executable, "-I", "-c", box.MIRROR, str(repo), "tester"], input=bad, capture_output=True, timeout=60)
    assert done.returncode != 0 and (repo / "a.py").read_text() == "old\n" and not (tmp_path / "out.py").exists()


def test_the_box_refuses_a_workspace_holding_a_link_and_syncs_nothing(tmp_path, box):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})
    (tmp_path / "outside").mkdir()
    link(tmp_path / "outside", ws / "escape")
    try:
        with pytest.raises(box.Refused):
            box.pack(ws)
        calls = []
        opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
        got = box.call(opts, {"name": "run", "arguments": {"command": "ls"}}, lambda *a, **kw: calls.append(a))
        assert got["isError"] is True and calls == [] and "escape" in got["content"][0]["text"]
    finally:
        if os.name == "nt":
            os.rmdir(ws / "escape")
        else:
            os.remove(ws / "escape")


# --- the box as an MCP server ------------------------------------------------------------------------------------------------
def _session(box, ws, docker, *messages):
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    stdout = io.BytesIO()
    box.serve(stdin, stdout, opts, docker)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_the_box_answers_initialize_lists_one_tool_and_runs_a_command_after_the_mirror(tmp_path, box):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})
    calls = []

    def docker(*args, input=None, timeout=None, merged=False):
        calls.append((args, merged))
        if box.MIRROR in args:
            return ok(b'{"written": 1, "removed": 0}')
        return ok(b"x" * 25000 + b"END", code=3)

    replies = _session(box, ws, docker,
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run", "arguments": {"command": "pytest -q"}}},
                       {"jsonrpc": "2.0", "id": 4, "method": "ping"})
    assert [r["id"] for r in replies] == [1, 2, 3, 4] and all(r["jsonrpc"] == "2.0" for r in replies)
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"] and init["serverInfo"]["name"] == "box"
    tools = replies[1]["result"]["tools"]
    assert [t["name"] for t in tools] == ["run"] and tools[0]["inputSchema"]["required"] == ["command"]
    assert "test environment" in tools[0]["description"]
    result = replies[2]["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is False and text.startswith("exit code 3") and text.endswith("END")
    assert len(text.split("\n", 1)[1]) == 20000
    assert replies[3]["result"] == {}
    (mirror, _), (command, merged) = calls
    assert mirror[:9] == ("exec", "-i", "-u", "root", "ctr", "python3", "-I", "-c", box.MIRROR) and mirror[9:] == ("/w/r", "tester")
    assert command[:6] == ("exec", "-u", "tester", "-w", "/w/r", "ctr") and command[-3:] == ("sh", "-c", "pytest -q") and merged


@pytest.mark.parametrize("failure", ["mirror", "exec", "raises"])
def test_a_docker_failure_is_a_tool_error(tmp_path, box, failure):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})

    def docker(*args, input=None, timeout=None, merged=False):
        if failure == "raises":
            raise OSError("docker is not there")
        if box.MIRROR in args:
            return ok(code=1) if failure == "mirror" else ok(b"{}")
        return ok(b"Error response from daemon: container ctr is not running", code=125)

    replies = _session(box, ws, docker, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                         "params": {"name": "run", "arguments": {"command": "true"}}})
    assert replies[0]["id"] == 7 and replies[0]["result"]["isError"] is True and replies[0]["result"]["content"][0]["text"]


# --- the usage limit -----------------------------------------------------------------------------------------------------------
def test_a_usage_limit_line_is_read_only_from_claude_codes_own_words():
    synthetic = {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": LIMIT_TEXT}]}}
    model = {"type": "assistant", "message": {"model": SONNET, "content": [{"type": "text", "text": LIMIT_TEXT}]}}
    tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": LIMIT_TEXT}]}}
    result = {"type": "result", "is_error": True, "result": "You've hit your weekly limit"}
    assert we.limit_of(json.dumps(synthetic)) == "resets 7pm (Europe/London)"
    assert we.limit_of(json.dumps(result)) == "" and we.limit_of("You’ve hit your session limit ∙ resets 3am") == "resets 3am"
    assert we.limit_of(json.dumps(model)) is None and we.limit_of(json.dumps(tool)) is None
    assert we.limit_of(json.dumps({"type": "result", "result": "done"})) is None and we.limit_of("plain text") is None


def test_a_run_that_meets_the_usage_limit_ends_limited_with_its_reset_and_no_review(monkeypatch, tmp_path):
    synthetic = {"type": "assistant", "session_id": "s1",
                 "message": {"id": "lim", "model": "<synthetic>", "content": [{"type": "text", "text": LIMIT_TEXT}]}}
    lines = [*stream("s1", extra=(synthetic,))]
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: pytest.fail("a limited run is not reviewed"))
    record, _, starts, ctx, _, _ = host_run(monkeypatch, tmp_path, "K1", [("s1", "", lines)])
    assert record["status"] == "limited" and record["limit"] == {"stream": "stream.jsonl", "resets": "resets 7pm (Europe/London)"}
    assert len(starts) == 1 and "repairs" not in record and record["cost_usd"] == 0.0
    ended = [r for r in ctx.archive.rows() if r.get("event") == "ended"]
    assert ended[-1]["status"] == "limited" and ended[-1]["cost_usd"] == 0.0
    saved = (ctx.archive.root / record["run"] / "stream.jsonl").read_text("utf-8").splitlines()
    assert json.loads(saved[-1])["message"]["model"] == "<synthetic>"      # the run ended at the limit's line


def test_a_limited_run_is_never_scored_and_may_be_made_again(monkeypatch, tmp_path):
    import compare
    import loop
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps(
        {"status": "limited", "run": "run/t/1", "limit": {"resets": "resets 7pm"}}) if command == "run"
        else pytest.fail("a limited run is never checked"))
    with pytest.raises(SystemExit, match="limited"):
        loop.one(tmp_path, "g6", "K1", "t", 1)
    assert not (tmp_path / "loop").exists()
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K1-t-1.json").write_text(json.dumps({"arm": "K1", "task": "t", "repeat": 1, "run": "run/t/1",
                                                               "status": "limited", "passes": False, "failing": None}), "utf-8")
    monkeypatch.setattr(we, "amendment", lambda task: None)
    assert compare.saved(tmp_path)[("K1", "t", 1)]["maintainers"] is None
    task = we.load_tasks(we.TASKS_STAGE4)[0]
    archive = we.c.Archive(tmp_path / "r3")
    run = archive.start("run", task.id, model=SONNET, arm="K1", repeat=1)
    run.finish({"agent_started": True, "cost_usd": 0.0}, "limited")
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K1", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="")
    assert "already made" not in we.r3_refusal(ctx, task)[0]
    archive.start("run", task.id, model=SONNET, arm="K1", repeat=2).finish({"agent_started": True}, "error")
    ctx.repeat = 2
    assert "already made" in we.r3_refusal(ctx, task)[0]                 # an error is made again only with a reason, as before


def test_a_repair_that_meets_the_limit_ends_arm_ks_loop(monkeypatch):
    class Docker:
        def sh(self, *a, **kw):
            return ok(b"tree")
    monkeypatch.setattr(we, "run_set", lambda *a: ON_TREE)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    monkeypatch.setattr(we, "run_session", lambda *a, **kw: pytest.fail("the host's repairs replace the container's"))
    asked = []
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker(), arm="K1"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "", None, 1.0, 0.0,
                                 lambda n: {"verdict": "defect", "rule": "r", "why": "w", "test": TEST, "seconds": 1.0},
                                 session=lambda text, name: (asked.append(name), "limited")[1])
    assert asked == ["repair_stream_1.jsonl"] and got["status"] == "inconclusive" and "limit" in got["reason"]


# --- the host mode of a run -------------------------------------------------------------------------------------------------------
def test_the_host_mode_runs_claude_code_on_the_host_and_every_command_in_a_container_with_no_network(monkeypatch, tmp_path):
    record, docker, starts, ctx, task, workspaces = host_run(monkeypatch, tmp_path, "A", [("s1", "def f():\n    return 1\n", None)],
                                                             environ={"PATH": "p", "ANTHROPIC_API_KEY": "sk-ant-api03-notakey00000"})
    assert record["status"] == "finished", record.get("error")
    assert not any(we.CLAUDE in call for call in docker.calls)          # no Claude Code in the container
    agent = next(call for call in docker.calls if call[:2] == ("run", "-d"))
    at = agent.index("--network")
    assert agent[at + 1] == "none" and not any("PROXY" in a for call in docker.calls for a in call)
    assert len(starts) == 1 and starts[0]["cwd"] == str(workspaces[0]) and starts[0]["before"] == "def f():\n    return 0\n"
    assert "ANTHROPIC_API_KEY" not in starts[0]["env"]
    diff = next(i for i, call in enumerate(docker.calls) if call[0] == "sh" and f"{we.WE}/final.patch" in call[1])
    assert mirrors(docker) and mirrors(docker)[-1] < diff
    run = ctx.archive.root / record["run"]
    assert (run / "final.patch").read_text("utf-8") == "def f():\n    return 1\n"     # the host's edit reached the container
    assert record["executor"] == "host" and record["cost_usd"] == 0.0 and record["subscription_usd_estimate"] == pytest.approx(0.42)
    assert record["claude_version"] == "2.1.318" and not workspaces[0].exists()
    ended = [r for r in ctx.archive.rows() if r.get("event") == "ended"]
    assert ended[-1]["cost_usd"] == 0.0
    box = json.loads((run / "host_mcp.json").read_text("utf-8"))["mcpServers"]["box"]
    assert box["command"] == sys.executable and box["args"][:2] == ["-I", str(we.HERE / "box_mcp.py")]
    args = box["args"]
    assert {args[args.index(f) + 1] for f in ("--container", "--repo", "--workspace", "--user")} == {
        agent[agent.index("--name") + 1], task.repo, str(workspaces[0]), we.USER}
    assert json.loads((run / "host_settings.json").read_text("utf-8")) == {
        "permissions": {"blockReadsOutsideWorkingDirectories": True, "blockWritesOutsideWorkingDirectories": True},
        "disableAllHooks": True}


def test_k2_on_the_host_repairs_in_a_resumed_host_session_mirrored_before_each_read(monkeypatch, tmp_path):
    reviews = [{"verdict": "defect", "rule": "r", "why": "w", "test": TEST, "seconds": 1.0},
               {"verdict": "none", "rule": "", "why": "", "test": "", "seconds": 1.0}]
    monkeypatch.setattr(we, "run_critic", lambda ctx, task, run, agent, base, title, text, n=1: reviews[n - 1])
    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda ctx, task, found, ins: AT_PARENT)
    record, docker, starts, ctx, _, _ = host_run(monkeypatch, tmp_path, "K2", [("s1", "def f():\n    return 1\n", None),
                                                                                 ("s1", "def f():\n    return 2\n", None)])
    assert record["status"] == "finished", record.get("error")
    assert len(starts) == 2 and "--resume" not in starts[0]["argv"] and starts[1]["argv"][-2:] == ["--resume", "s1"]
    assert "test_owed" in starts[1]["proc"].stdin.text
    assert record["repairs"]["rounds"] == 2 and record["repairs"]["status"] == "no_counterexample_in_domain"
    assert record["repairs"]["finding"] == "parent" and len(mirrors(docker)) == 2
    run = ctx.archive.root / record["run"]
    assert (run / "repair_stream_1.jsonl").is_file() and (run / "final.patch").read_text("utf-8") == "def f():\n    return 2\n"
    assert record["cost_usd"] == 0.0


def test_a_host_run_is_refused_when_dry_or_for_an_arm_with_repairs_outside_arm_k():
    ctx = SimpleNamespace(executor="host", variant=we.ROUND3, arm="K2")
    assert we.host_refusal(ctx, dry=False) == "" and "dry" in we.host_refusal(ctx, dry=True)
    for arm in ("V4", "R", "M9", "B"):
        ctx.arm = arm
        assert we.host_refusal(ctx, dry=False)
    assert we.host_refusal(SimpleNamespace(executor="cloud", variant=we.ROUND3, arm="A"), dry=False)
