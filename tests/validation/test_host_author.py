# SPDX-License-Identifier: Apache-2.0
"""The host-session author (PLAN.md, 2026-11-04): Claude Code runs on the host, on the subscription's sign-in, which never enters a
container; every command it runs goes through the box (``box_mcp.py``) into the bug's container, which has no network. Fakes only:
no docker, no Claude Code, no network. The mirror script runs for real, with this Python, on local folders."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
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


TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "mcp__box__run"]     # exactly what a host session may have, and no more


def stream(sid: str, *, cost: float = 0.42, extra: tuple[dict, ...] = (), tools: list[str] = TOOLS,
           servers: tuple[str, ...] = ("box",)) -> list[str]:
    lines = [{"type": "system", "subtype": "init", "session_id": sid, "claude_code_version": "2.1.318", "tools": tools,
              "mcp_servers": [{"name": s, "status": "connected"} for s in servers]},
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
    pid = None          # no real process: ``end_session`` is a fake wherever a fake process runs
    ended = None        # the workspace ``end_session`` was given for this session's processes

    def __init__(self, lines: list[str], code: int = 0, hangs: bool = False) -> None:
        self.stdin, self.stderr, self.code, self.hangs = Stdin(), io.BytesIO(b""), code, hangs
        self.stdout = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))

    def poll(self) -> int | None:
        return None if self.hangs and self.ended is None else self.code     # one that hangs runs until it is ended

    def wait(self, timeout: float | None = None) -> int:
        if self.poll() is None:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.code


def fake_end(monkeypatch) -> None:
    """The process tree for fake processes: ``host_start`` starts the fake as it is, and ``end_session`` records the workspace it
    was given and stops nothing."""
    monkeypatch.setattr(we, "host_start", lambda start, argv, **kw: SimpleNamespace(proc=start(argv, **kw)), raising=False)
    monkeypatch.setattr(we, "end_session", lambda proc, workspace, tree=None: setattr(proc, "ended", workspace), raising=False)


def starter(sessions: list[tuple]):
    """A fake process starter: session i is (its id, what it writes in src/mod.py or "", its stream or None for a plain one), and
    optionally its exit code (0) and whether it hangs after its stream until it is ended (False)."""
    starts: list[dict] = []

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        sid, body, lines, *code = sessions[len(starts)]
        mod = Path(cwd) / "src" / "mod.py"
        starts.append({"argv": list(argv), "cwd": cwd, "env": dict(env), "before": mod.read_text("utf-8") if mod.is_file() else None})
        if body:
            mod.write_text(body, encoding="utf-8")
        proc = Proc(lines if lines is not None else stream(sid), *code)
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
    fake_end(monkeypatch)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: pytest.fail("no key is read"),
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, model=SONNET, environ=environ or {"PATH": "p"}, executor="host", starter=start)
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    return record, docker, starts, ctx, task, workspaces


def mirrors(docker) -> list[int]:
    import box_mcp
    return [i for i, call in enumerate(docker.calls) if box_mcp.MIRROR in call]


# --- the command line and the environment ---------------------------------------------------------------------------------
def test_the_host_command_line_is_exactly_the_validated_flags_and_resumes_only_on_a_repair(monkeypatch, tmp_path):
    fake_end(monkeypatch)
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


FIXED = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1", "DISABLE_AUTOUPDATER": "1",
         "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
         "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
SESSION_OWN = ("CLAUDE_CODE_AGENT", "CLAUDE_CODE_ALT_SCREEN_FULL_REPAINT", "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
               "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_HOST_SHORT_TEMP_DIRS", "CLAUDE_CODE_MESSAGING_SOCKET",
               "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_JOB_DIR", "CLAUDE_PID",
               "CLAUDECODE", "ClaudeCode_Lower", "anthropic_base_url")


def test_the_host_sessions_fixed_environment_is_pinned():
    assert we.HOST_FIXED_ENV == FIXED


def test_the_host_environment_is_an_allowlist_without_any_claude_or_anthropic_name_but_the_fixed_ones():
    kept = {"PATH": "p", "PATHEXT": ".EXE", "SystemRoot": "r", "USERPROFILE": "u", "APPDATA": "a", "ProgramFiles(x86)": "pf",
            "TEMP": "t", "LC_ALL": "C", "PROCESSOR_ARCHITECTURE": "AMD64", "http_proxy": "h", "NO_PROXY": "n",
            "NODE_EXTRA_CA_CERTS": "ca", "DOCKER_HOST": "d", "HOME": "hm"}
    dropped = {"ANTHROPIC_API_KEY": "sk-ant-api03-notakey000000", "ANTHROPIC_AUTH_TOKEN": "tok", "CLAUDE_CODE_OAUTH_TOKEN": "oat",
               "anthropic_api_key": "lower", "PYTHONPATH": "x", "SECRET_THING": "s", "G6_WE_ARCHIVE": "g",
               "DISABLE_AUTOUPDATER": "0", **dict.fromkeys(SESSION_OWN, "v")}
    got = we.host_env({**kept, **dropped})
    assert got == {**kept, **FIXED}
    assert not {k for k in got if k.upper().startswith(("CLAUDE", "ANTHROPIC"))} - set(FIXED)


def test_no_key_or_token_reaches_the_host_session_even_when_set(monkeypatch, tmp_path):
    fake_end(monkeypatch)
    environ = {"ANTHROPIC_API_KEY": "sk-ant-api03-notakey000000", "ANTHROPIC_AUTH_TOKEN": "tok", "CLAUDE_CODE_OAUTH_TOKEN": "oat",
               "anthropic_api_key": "lower", "PATH": "p", "USERPROFILE": "u", **dict.fromkeys(SESSION_OWN, "v")}
    assert we.host_env(environ) == {"PATH": "p", "USERPROFILE": "u", **FIXED}
    start, starts = starter([("s1", "", None)])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=lambda: pytest.fail("no key"), issue_reader=None,
                     g6={}, variant=we.ROUND3, arm="K1", model=SONNET, environ=environ, executor="host", starter=start)
    (tmp_path / "ws").mkdir()
    we.host_session(ctx, ctx.archive.start("run", "t"), tmp_path / "ws", tmp_path / "m.json", tmp_path / "s.json", "go",
                    "stream.jsonl", resume="", seconds=60.0, tally=we.c.Tally(), stop_usd=10.5)
    env = starts[0]["env"]
    assert env == {"PATH": "p", "USERPROFILE": "u", **FIXED} and not {k.upper() for k in env} & set(SECRETS)


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
        return ok(args[-2].encode() + b"\n" + b"x" * 25000 + b"END", code=3)     # the call's start line, as its shell prints it

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
    assert command[:6] == ("exec", "-u", "tester", "-w", "/w/r", "ctr") and command[-5:-2] == ("sh", "-c", box.SHELL) and merged
    assert command[-2].startswith(box.START + " ") and command[-1] == "pytest -q"


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
    made = we.c.Archive(tmp_path).start("run", "t", arm="K1", executor="host")    # the run as the archive holds it
    made.finish({"executor": "host", "agent_started": True, "cost_usd": 0.0}, "limited")
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps(
        {"status": "limited", "run": made.rel, "limit": {"resets": "resets 7pm"}}) if command == "run"
        else pytest.fail("a limited run is never checked"))
    with pytest.raises(SystemExit, match="limited"):
        loop.one(tmp_path, "g6", "K1", "t", 1)
    assert not (tmp_path / "loop").exists()
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K1-t-1.json").write_text(json.dumps({"arm": "K1", "task": "t", "repeat": 1, "run": made.rel,
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


@pytest.mark.parametrize(("tools", "servers", "named"), [
    (TOOLS, ("box", "claude.ai Gmail"), "claude.ai Gmail"),
    ([*TOOLS, "mcp__claude_ai_Gmail__send_message"], ("box",), "mcp__claude_ai_Gmail__send_message")])
def test_a_session_whose_init_names_another_mcp_server_or_tool_is_stopped_and_the_run_refused(monkeypatch, tmp_path, tools, servers,
                                                                                              named):
    lines = stream("s1", tools=tools, servers=servers)
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: pytest.fail("a refused run is not reviewed"))
    record, _, starts, ctx, _, _ = host_run(monkeypatch, tmp_path, "K1", [("s1", "", lines)])
    assert record["status"] == "refused" and named in record["why"] and len(starts) == 1
    assert record["host_tools"] == tools and record["host_mcp_servers"] == list(servers) and "repairs" not in record
    saved = (ctx.archive.root / record["run"] / "stream.jsonl").read_text("utf-8").splitlines()
    assert len(saved) == 1 and json.loads(saved[0])["subtype"] == "init"       # stopped at once, at the init
    import loop
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps(record))
    with pytest.raises(SystemExit, match="refused"):
        loop.one(ctx.archive.root, "g6", "K1", "t", 1)       # the archive the run was made in, which the admission rule reads


def test_a_model_refusal_fallback_is_recorded_with_the_models_that_wrote(monkeypatch, tmp_path):
    fallback = {"type": "system", "subtype": "model_refusal_fallback", "originalModel": SONNET, "fallbackModel": "claude-opus-4-8",
                "apiRefusalCategory": "cyber", "direction": "retry", "session_id": "s1"}
    other = {"type": "assistant", "session_id": "s1", "message": {"id": "m2", "model": "claude-opus-4-8", "content": []}}
    record, _, starts, _, _, _ = host_run(monkeypatch, tmp_path, "A", [("s1", "", stream("s1", extra=(fallback, other)))])
    assert record["model_refusals"] == [{"subtype": "model_refusal_fallback", "apiRefusalCategory": "cyber", "originalModel": SONNET,
                                         "fallbackModel": "claude-opus-4-8"}]
    assert record["models_seen"] == sorted([SONNET, "claude-opus-4-8"]) and record["model_switched"] is True
    assert len(starts) == 1                                                  # nothing is retried, rephrased or resent


def test_a_limited_or_refused_run_may_be_made_again(tmp_path):
    archive = we.c.Archive(tmp_path / "r3")
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K1", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="")
    for task, status in zip(we.load_tasks(we.TASKS_STAGE4)[:2], ("limited", "refused")):
        archive.start("run", task.id, model=SONNET, arm="K1", repeat=1).finish({"agent_started": True, "cost_usd": 0.0}, status)
        assert "already made" not in we.r3_refusal(ctx, task)[0]


def test_loop_threads_its_executor_to_every_runner_call(monkeypatch, tmp_path):
    import loop
    seen = []
    monkeypatch.setattr(loop, "EXECUTOR", "container")
    monkeypatch.setattr(loop.subprocess, "run", lambda argv, **kw: (seen.append(argv), SimpleNamespace(stdout="{}"))[1])
    monkeypatch.setattr(loop, "rescore", lambda root, g6: [loop.tool("check", "--task", "t")])
    monkeypatch.setenv("G6_WE_ARCHIVE", str(tmp_path))
    loop.main(["--rescore"])
    loop.tool("run", "--task", "t")
    loop.main(["--executor", "host", "--rescore"])
    loop.tool("run", "--task", "t")
    assert [argv[argv.index("--executor") + 1] for argv in seen] == ["container", "container", "host", "host"]
    assert all(argv[2] in ("check", "run") for argv in seen)


@pytest.mark.parametrize("end", ["limited", "refused"])
def test_a_repair_that_meets_the_limit_or_is_refused_ends_arm_ks_loop(monkeypatch, end):
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
                                 session=lambda text, name: (asked.append(name), end)[1])
    assert asked == ["repair_stream_1.jsonl"] and got["status"] == "inconclusive" and end[:5] in got["reason"]


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
    assert record["host_tools"] == TOOLS and record["host_mcp_servers"] == ["box"]          # the six tools, and the box alone
    assert record["models_seen"] == [SONNET] and record["model_switched"] is False and record["model_refusals"] == []
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
    assert record["repairs"]["finding"] == "parent" and len(mirrors(docker)) == 3     # after each session, and before the patch
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


# --- the review of the host executor (2026-11-04): each class of its findings removed where it arises -----------------------
# R1: a host session's ending is decided once, from all of its evidence, and carried to the run's own status.
REFUSAL = {"type": "system", "subtype": "model_refusal", "apiRefusalCategory": "cyber", "originalModel": SONNET, "session_id": "s1"}


def with_a_later_init(**other) -> list[str]:
    """A stream whose first init is exactly the box's and the five file tools, and whose second is not."""
    lines = stream("s1")
    return [lines[0], stream("s1", **other)[0], *lines[1:]]


@pytest.mark.parametrize(("lines", "code", "status"), [
    (stream("s1")[1:], 0, "refused"),                         # no init: what tools the session had is not known
    (with_a_later_init(servers=("box", "other")), 0, "refused"),   # every init is read: a later one with another server
    (stream("s1", tools=[*TOOLS, "Bash"]), 0, "refused"),     # a built-in tool more than the six
    (stream("s1", tools=TOOLS[:-1]), 0, "refused"),           # one less: no box
    (stream("s1", extra=(REFUSAL,)), 0, "model_refused"),     # a request the model refused: a run made, not refused
    (stream("s1")[:-1], 0, "error"),                          # no result
    (stream("s1"), 17, "error"),                              # an exit code other than 0
    (stream("s1"), 0, "finished")])
def test_a_host_sessions_ending_is_decided_once_from_its_init_result_exit_code_and_refusals(monkeypatch, tmp_path, lines, code,
                                                                                             status):
    record, _, starts, *_ = host_run(monkeypatch, tmp_path, "A", [("s1", "", lines, code)])
    assert record["status"] == status, record.get("error")
    assert len(starts) == 1 and starts[0]["proc"].ended is not None        # its processes ended, whatever happened
    assert bool(record.get("why")) is (status == "refused")


def k2_reviews(monkeypatch, review) -> None:
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: review(a[-1]))
    monkeypatch.setattr(we, "run_set", lambda *a: ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda *a: AT_PARENT)


def test_a_repair_that_does_not_end_cleanly_ends_the_run_with_its_own_status(monkeypatch, tmp_path):
    k2_reviews(monkeypatch, lambda n: {"verdict": "defect", "rule": "r", "why": "w", "test": TEST} if n == 1
               else pytest.fail("no review after a repair that did not end cleanly"))
    record, _, starts, *_ = host_run(monkeypatch, tmp_path, "K2", [("s1", "def f():\n    return 1\n", None),
                                                                     ("s1", "def f():\n    return 2\n", stream("s1")[:-1])])
    assert record["status"] == "error" and record["repairs"]["ended"] == "error" and len(starts) == 2
    assert all(s["proc"].ended is not None for s in starts)


def test_a_model_refusal_is_never_resumed_with_a_repair(monkeypatch, tmp_path):
    k2_reviews(monkeypatch, lambda n: pytest.fail("a run a refusal ended starts no review and no repair"))
    record, _, starts, *_ = host_run(monkeypatch, tmp_path, "K2", [("s1", "", stream("s1", extra=(REFUSAL,))), ("s1", "", None)])
    assert record["status"] == "model_refused" and len(starts) == 1 and record["model_refusals"][0]["subtype"] == "model_refusal"
    assert we.c.scoreable(record) and "why" not in record


def test_a_model_refusal_in_a_repair_ends_the_run_on_the_workspaces_patch_and_resends_nothing(monkeypatch, tmp_path):
    k2_reviews(monkeypatch, lambda n: {"verdict": "defect", "rule": "r", "why": "w", "test": TEST} if n == 1
               else pytest.fail("no review after a repair a refusal ended"))
    record, _, starts, ctx, *_ = host_run(monkeypatch, tmp_path, "K2", [
        ("s1", "def f():\n    return 1\n", None), ("s1", "def f():\n    return 2\n", stream("s1", extra=(REFUSAL,))),
        ("s1", "", None)])
    assert record["status"] == "model_refused" and record["repairs"]["ended"] == "model_refused" and len(starts) == 2
    assert (ctx.archive.root / record["run"] / "final.patch").read_text("utf-8") == "def f():\n    return 2\n"


def test_a_run_a_refusal_ended_is_scored_on_its_patch_and_never_made_again_while_an_init_refusal_may_be(monkeypatch, tmp_path):
    import compare
    import loop
    asked = []
    ran = we.c.Archive(tmp_path).start("run", "t", arm="K1", executor="host")     # the run as the archive holds it
    ran.finish({"executor": "host", "agent_started": True, "cost_usd": 0.0}, "model_refused")

    def tool(command, *args, **kw):
        asked.append(command)
        if command == "run":
            return json.dumps({"status": "model_refused", "run": ran.rel, "cost_usd": 0.0})
        return json.dumps({"checks": [{"patch": "final", "passes": True, "failing": 0, "fail_to_pass_failing": []}]})

    monkeypatch.setattr(loop, "tool", tool)
    row = loop.one(tmp_path, "g6", "K1", "t", 1)
    assert asked == ["run", "check"] and row["status"] == "model_refused" and row["passes"] is True
    monkeypatch.setattr(we, "amendment", lambda task: None)
    assert compare.saved(tmp_path)[("K1", "t", 1)]["maintainers"] is True
    assert we.c.scoreable({"status": "model_refused"}) and not we.c.scoreable({"status": "refused"})
    archive = we.c.Archive(tmp_path / "r3")
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K1", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="")
    made, infra = we.load_tasks(we.TASKS_STAGE4)[:2]
    archive.start("run", made.id, model=SONNET, arm="K1", repeat=1).finish({"agent_started": True, "cost_usd": 0.0}, "model_refused")
    archive.start("run", infra.id, model=SONNET, arm="K1", repeat=1).finish({"agent_started": True, "cost_usd": 0.0}, "refused")
    assert "already made" in we.r3_refusal(ctx, made)[0] and "already made" not in we.r3_refusal(ctx, infra)[0]


@pytest.mark.parametrize(("lines", "code", "hangs", "status"), [
    (stream("s1"), -9, True, "finished"),       # a clean result, then G6 had to end the process: the result's ending
    (stream("s1")[:-1], -9, True, "error"),     # G6 ended it before any result was read
    (stream("s1"), 17, False, "error")])        # it ended by itself with a code other than 0
def test_a_session_g6_ended_after_its_result_keeps_that_results_ending(monkeypatch, tmp_path, lines, code, hangs, status):
    record, _, starts, *_ = host_run(monkeypatch, tmp_path, "A", [("s1", "", lines, code, hangs)])
    assert record["status"] == status and record["ended_by_g6"] is hangs
    saved = json.loads((tmp_path / "archive" / record["run"] / "stream_exit.json").read_text("utf-8"))
    assert saved == {"exit_code": code, "ended_by_g6": hangs, "not_ended": ""}


def test_no_repair_starts_once_the_runs_clock_is_spent(monkeypatch, tmp_path):
    now = [we.time.monotonic()]
    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])

    def review(n):
        now[0] += we.c.WALL_CLOCK_S          # the first review takes the rest of the run's time
        return {"verdict": "defect", "rule": "r", "why": "w", "test": TEST}

    k2_reviews(monkeypatch, review)
    record, _, starts, *_ = host_run(monkeypatch, tmp_path, "K2", [("s1", "def f():\n    return 1\n", None), ("s1", "", None)])
    assert len(starts) == 1 and record["status"] == "timeout" and record["repairs"]["ended"] == "timeout"


@pytest.mark.parametrize("status", ["limited", "refused"])
def test_every_scoring_entry_point_asks_one_rule_and_gives_no_verdict_to_a_limited_or_refused_run(monkeypatch, tmp_path, status):
    import compare
    assert not we.c.scoreable({"status": status}) and we.c.scoreable({"status": "timeout"}) and not we.c.scoreable({})
    task = we.load_tasks()[0]
    archive = we.c.Archive(tmp_path / "a")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test.py::test_bug"], "pass_to_pass": [], "ok": True}, "finished")
    run = archive.start("run", task.id, executor="host")
    run.finish({"executor": "host", "agent_started": True, "cost_usd": 0.0}, status)    # a host run's record, as its start says
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a run with no verdict is never checked"))
    with pytest.raises(SystemExit, match=status):
        we.check(SimpleNamespace(archive=archive), task, run.rel)          # the run named by --run, too
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K1-t-1.json").write_text(json.dumps({"arm": "K1", "task": "t", "repeat": 1, "run": "run/t/1",
                                                               "status": status, "passes": False, "failing": 3}), "utf-8")
    monkeypatch.setattr(we, "amendment", lambda task: None)
    assert compare.saved(tmp_path)[("K1", "t", 1)]["maintainers"] is None


@pytest.mark.parametrize("code", [-9, None])
def test_a_docker_client_ended_by_a_signal_is_a_tool_error(tmp_path, box, code):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}},
                   lambda *a, **kw: ok(b"{}") if box.MIRROR in a else ok(b"", code=code))
    assert got["isError"] is True


# R2: a producer's text is trusted only where Claude Code itself marks it.
def test_a_limit_is_read_only_where_claude_code_marks_its_own_words_and_a_model_counts_only_by_its_own_name(tmp_path):
    said = {"type": "result", "subtype": "success", "is_error": False, "result": f"The fixture prints: {LIMIT_TEXT}"}
    assert we.limit_of(json.dumps(said)) is None                                  # a successful result's text is the model's
    assert we.limit_of(json.dumps({"type": "result", "subtype": "error_during_execution", "result": LIMIT_TEXT})) == \
        "resets 7pm (Europe/London)"
    for n, (seen, switched) in enumerate([(SONNET, False), (SONNET + "-20260101", False), (SONNET + "-2026010", True),
                                           (SONNET + "-different-model", True), (SONNET[:-1], True)]):
        lines = stream("s1")
        message = json.loads(lines[1])
        message["message"]["model"] = seen
        path = tmp_path / f"stream-{n}.jsonl"
        path.write_text("\n".join([lines[0], json.dumps(message), lines[-1]]), encoding="utf-8")
        assert we.host_facts(path, SONNET)["switched"] is switched, seen


# R3: nothing the agent started runs while the mirror checks and acts, and no check is apart from what it guards.
def mirror_in(box, repo: Path, data: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-c", box.MIRROR, str(repo), "tester"], input=data, capture_output=True,
                          timeout=60)


def unlink(at: Path) -> None:
    if os.name == "nt":
        os.rmdir(at)
    else:
        os.remove(at)


def test_the_mirror_writes_a_changed_file_fresh_and_refuses_a_repository_root_that_is_a_link(tmp_path, box):
    ws, repo, elsewhere = tmp_path / "ws", tmp_path / "repo", tmp_path / "elsewhere"
    write(ws, {"a.py": "agent\n", "b.py": "same\n"})
    write(repo, {"b.py": "same\n"})
    write(elsewhere, {"a.py": "elsewhere\n", "kept.txt": "k\n"})
    os.link(elsewhere / "a.py", repo / "a.py")            # the repository's a.py is another name of a file outside it
    done = mirror_in(box, repo, box.pack(ws))
    assert done.returncode == 0, done.stderr
    assert (elsewhere / "a.py").read_text() == "elsewhere\n" and (repo / "a.py").read_text() == "agent\n"
    assert os.stat(repo / "a.py").st_nlink == 1
    repo.rename(tmp_path / "moved")
    link(elsewhere, repo)                                  # the repository's root itself replaced by a link
    try:
        done = mirror_in(box, repo, box.pack(ws))
        assert done.returncode != 0 and b"refused" in done.stderr
        assert sorted(p.name for p in elsewhere.iterdir()) == ["a.py", "kept.txt"] and (elsewhere / "a.py").read_text() == "elsewhere\n"
    finally:
        unlink(repo)


def test_the_mirror_refuses_before_any_change_to_remove_a_folder_that_holds_git_or_venv_at_any_depth(tmp_path, box):
    ws, repo = tmp_path / "ws", tmp_path / "repo"
    write(ws, {"a.py": "new\n", "pkg": "now a file\n"})
    write(repo, {"a.py": "old\n", "gone.py": "g\n", "pkg/deep/.venv/bin/python": "v\n"})
    done = mirror_in(box, repo, box.pack(ws))
    assert done.returncode != 0 and b"refused" in done.stderr
    assert (repo / "a.py").read_text() == "old\n" and (repo / "gone.py").is_file() and (repo / "pkg/deep/.venv/bin/python").is_file()


def test_the_mirror_first_stops_every_process_of_the_user_but_the_containers_first(box):
    procs = {"1": ("1000", "S"), "7": ("1000", "R"), "8": ("1000", "Z"), "9": ("0", "S"), "self": None}
    killed = []

    class Os:
        @staticmethod
        def listdir(path):
            assert path == "/proc"
            return list(procs)

        @staticmethod
        def getpid():
            return 99

        @staticmethod
        def kill(pid, sig):
            killed.append((pid, sig))
            procs.pop(str(pid))

    def status(path, *a, **kw):
        uid, state = procs[path.split("/")[2]]
        return io.StringIO(f"Name:\tx\nState:\t{state} (x)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")

    space = {"os": Os, "signal": SimpleNamespace(SIGKILL=9), "sys": sys, "time": SimpleNamespace(sleep=lambda s: None), "open": status}
    exec(box.STOP, space)
    space["stop"](1000)
    assert killed == [(7, 9)]                   # not the container's first process, a zombie, or another user's
    procs["7"] = ("1000", "R")
    Os.kill = staticmethod(lambda pid, sig: killed.append((pid, sig)))         # one that does not end
    with pytest.raises(SystemExit, match="refused"):
        space["stop"](1000)
    assert box.STOP in box.MIRROR


def test_the_box_refuses_a_workspace_that_is_a_link_and_a_file_that_is_not_the_one_it_checked(monkeypatch, tmp_path, box):
    real = tmp_path / "real"
    write(real, {"a.py": "x\n"})
    link(real, tmp_path / "ws")
    try:
        with pytest.raises(box.Refused, match="workspace"):
            box.pack(tmp_path / "ws")
    finally:
        unlink(tmp_path / "ws")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    checked = box._plain

    def swapped(path, root):          # the name given to a file outside right after its check
        st = checked(path, root)
        if path.name == "a.py":
            path.unlink()
            os.link(outside, path)
        return st

    monkeypatch.setattr(box, "_plain", swapped)
    with pytest.raises(box.Refused, match="a.py"):
        box.pack(real)


# R4: a host run is counted by the API's rules as what it is: no reservation, no stop.
def test_a_host_run_reserves_and_stops_nothing_of_the_apis_money(monkeypatch, tmp_path):
    started = {"event": "started", "run": "run/t/1", "kind": "run"}
    assert we.c.spent([{**started, "stop_usd": 0}]) == 0 and we.c.spent([{**started, "executor": "host", "stop_usd": 10.5}]) == 0
    assert we.c.spent([started]) == we.c.RUN_STOP_USD
    record, _, _, ctx, *_ = host_run(monkeypatch, tmp_path, "A", [("s1", "", stream("s1", cost=50.0))])
    assert record["status"] == "finished" and record["cost_usd"] == 0.0 and record["subscription_usd_estimate"] == 50.0
    start = [r for r in ctx.archive.rows() if r.get("event") == "started" and r.get("kind") == "run"][-1]
    assert start["executor"] == "host" and start["stop_usd"] == 0.0


# R5: on the host the agent's tree is the workspace, by its bytes.
def test_k_on_the_host_compares_the_workspaces_bytes_and_never_gits_display(monkeypatch):
    class Docker:
        def sh(self, *a, **kw):
            raise AssertionError("git's display is not read when the tree is given")

    trees = iter(["a", "b", "b", "b"])
    monkeypatch.setattr(we, "run_set", lambda *a: ON_TREE)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker()), SimpleNamespace(repo="/w/r"), SimpleNamespace(write_json=lambda *a: None),
                                 "agent", "base", [], "", None, 1.0, 0.0,
                                 lambda n: {"verdict": "defect", "rule": "r", "why": "w", "test": TEST},
                                 session=lambda note, name: "", tree=lambda: next(trees))
    assert got["rounds"] == 2 and got["reason"] == "the agent left the code as it was"


def test_the_final_patch_is_the_workspace_mirrored_again_after_a_reviewers_test_changed_the_container(monkeypatch, tmp_path):
    def run_set(ctx, task, agent, where):
        (ctx.docker.repo / "src" / "mod.py").write_text("the reviewer's test wrote this\n", encoding="utf-8")
        return we.ProbeRun(0, {"test_critic.py::test_owed": "passed"}, {}, "")

    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: {"verdict": "defect", "rule": "r", "why": "w", "test": TEST})
    monkeypatch.setattr(we, "run_set", run_set)
    record, docker, _, ctx, _, _ = host_run(monkeypatch, tmp_path, "K2", [("s1", "def f():\n    return 1\n", None)])
    assert record["status"] == "finished", record.get("error")
    assert (ctx.archive.root / record["run"] / "final.patch").read_text("utf-8") == "def f():\n    return 1\n"
    diff = next(i for i, call in enumerate(docker.calls) if call[0] == "sh" and f"{we.WE}/final.patch" in call[1])
    assert mirrors(docker)[-1] == diff - 1


# R6: the session's clock covers its prompt, and every process a session started ends with it.
def test_the_prompt_goes_in_while_the_sessions_clock_runs(monkeypatch, tmp_path):
    stopped = threading.Event()

    class Unread(Stdin):
        def write(self, data):
            stopped.wait(30)          # a process that takes no input until it is stopped
            return super().write(data)

    class Quiet:
        lines = [(stream("s1")[0] + "\n").encode()]

        def readline(self):
            if self.lines:
                return self.lines.pop()
            stopped.wait(30)
            return b""

    proc = Proc([])
    proc.stdin, proc.stdout = Unread(), Quiet()
    fake_end(monkeypatch)
    monkeypatch.setattr(we, "end_session", lambda p, ws, tree=None: (setattr(p, "ended", ws), stopped.set())[1])
    ctx = SimpleNamespace(starter=lambda argv, **kw: proc, model=SONNET, environ={}, archive=we.c.Archive(tmp_path / "a"))
    begun = time.monotonic()
    ended = we.host_session(ctx, ctx.archive.start("run", "t"), tmp_path, tmp_path / "m.json", tmp_path / "s.json", "go",
                            "stream.jsonl", seconds=0.3, tally=we.c.Tally())
    assert ended == "timeout" and time.monotonic() - begun < 5 and proc.ended == tmp_path and proc.stdin.text == "go"


@pytest.mark.skipif(os.name != "nt", reason="a real Windows process tree: its Job Object")
def test_a_session_starts_in_a_process_tree_of_its_own_whose_end_ends_a_child_its_launcher_left(monkeypatch, tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes, kernel.CloseHandle.argtypes = [wintypes.HANDLE, wintypes.UINT], [wintypes.HANDLE]
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import subprocess, sys\nchild = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'], "
                        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                        "print(child.pid, flush=True)\n", encoding="utf-8")
    swept = []
    monkeypatch.setattr(we.subprocess, "run", lambda *a, **kw: swept.append(a))       # nothing is found by a sweep or by text
    tree = we.host_start(subprocess.Popen, [sys.executable, "-I", str(launcher)], cwd=str(tmp_path), env=dict(os.environ),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    said = tree.proc.stdout.readline()
    assert isinstance(said, bytes)                                                  # the session's pipes carry bytes
    handle = kernel.OpenProcess(0x00100000 | 0x0001, False, int(said))
    assert handle, ctypes.get_last_error()
    try:
        assert tree.proc.wait(timeout=30) == 0 and kernel.WaitForSingleObject(handle, 0) == 258   # the launcher ended first
        assert we.end_session(tree.proc, tmp_path, tree) == "" and swept == []
        assert kernel.WaitForSingleObject(handle, 5000) == 0, "the child the launcher left still runs"
    finally:
        kernel.TerminateProcess(handle, 0)
        kernel.CloseHandle(handle)
    alone = SimpleNamespace(poll=lambda: 0)
    assert "not started in a process tree" in we.end_session(alone, tmp_path)      # said, never taken for a confirmed end


def test_processes_not_confirmed_ended_make_a_sessions_ending_error_and_never_more_scoreable(monkeypatch, tmp_path):
    fake_end(monkeypatch)
    monkeypatch.setattr(we, "end_session", lambda proc, ws, tree=None: (setattr(proc, "ended", ws), "the job still has members")[1])
    limited = stream("s1", extra=({"type": "assistant", "message": {"model": "<synthetic>", "content": [
        {"type": "text", "text": LIMIT_TEXT}]}},))
    for n, (lines, status) in enumerate([(stream("s1"), "error"), (stream("s1", extra=(REFUSAL,)), "error"), (limited, "limited")]):
        start, _ = starter([("s1", "", lines)])
        ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=None, issue_reader=None, g6={},
                         variant=we.ROUND3, arm="A", model=SONNET, environ={"PATH": "p"}, executor="host", starter=start)
        run = ctx.archive.start("run", f"t{n}", executor="host")
        ended = we.host_session(ctx, run, tmp_path, tmp_path / "m.json", tmp_path / "s.json", "go", "stream.jsonl", seconds=60.0,
                                tally=we.c.Tally())
        assert ended == status
        assert json.loads((run.path / "stream_exit.json").read_text("utf-8"))["not_ended"] == "the job still has members"
        assert we.c.ending(ctx.archive.root, run.rel)["status"] == status


# R7: no prefix admits a name by itself.
def test_no_prefix_admits_a_name_by_itself_and_no_name_of_a_key_or_token_passes():
    environ = {"PATH": "p", "DOCKER_HOST": "d", "DOCKER_CONTEXT": "c", "DOCKER_CERT_PATH": "cp", "DOCKER_TLS_VERIFY": "1",
               "DOCKER_AUTH_TOKEN": "t", "DOCKER_CONFIG": "cfg", "docker_password": "p", "LC_ALL": "C", "LC_API_KEY": "k",
               "PROCESSOR_SECRET": "s", "PROCESSOR_ARCHITECTURE": "AMD64", "HTTPS_PROXY": "h", "Http_Proxy_Credential": "x"}
    assert we.host_env(environ) == {"PATH": "p", "DOCKER_HOST": "d", "DOCKER_CONTEXT": "c", "DOCKER_CERT_PATH": "cp",
                                    "DOCKER_TLS_VERIFY": "1", "LC_ALL": "C", "PROCESSOR_ARCHITECTURE": "AMD64", "HTTPS_PROXY": "h",
                                    **FIXED}


# --- the second review of the host executor (2026-11-04): each class of its findings removed at its root -----------------------
# A: a run's ending is written once, the moment it is decided, and every entry point reads it through one function.
LIMITED = stream("s1", extra=({"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": LIMIT_TEXT}]}},))


@pytest.mark.parametrize(("lines", "failing", "status"), [
    (LIMITED, 1, "limited"),                                  # the mirror after the session fails: limited stays limited
    (stream("s1", tools=[*TOOLS, "Bash"]), 1, "refused"),     # and refused stays refused
    (stream("s1"), 2, "error")])                              # a finished run whose final mirror fails is "error"
def test_a_runs_ending_is_written_when_decided_and_a_failure_after_it_never_makes_it_more_scoreable(monkeypatch, tmp_path, lines,
                                                                                                   failing, status):
    import box_mcp
    real, seen = box_mcp.mirror, []

    def mirror(workspace, docker, **kw):
        folder = next((tmp_path / "archive" / "run").glob("*/*"))
        seen.append(we.c.ending(tmp_path / "archive", folder.relative_to(tmp_path / "archive").as_posix()))
        if len(seen) == failing:
            raise box_mcp.BoxError("the mirror failed (1)")
        return real(workspace, docker, **kw)

    monkeypatch.setattr(box_mcp, "mirror", mirror)
    record, *_ = host_run(monkeypatch, tmp_path, "A", [("s1", "", lines)])
    run = tmp_path / "archive" / record["run"]
    decided = "finished" if status == "error" else status
    assert record["status"] == status and "the mirror failed" in record["error"]
    assert seen[failing - 1]["status"] == decided                    # on record before the mirror that failed
    assert json.loads((run / "ending.json").read_text("utf-8"))["status"] == decided and (run / "failed_after_ending.json").is_file()
    assert we.c.ending(tmp_path / "archive", record["run"])["status"] == status and we.c.scoreable(record) is (status == "error")


def test_every_entry_point_reads_a_runs_ending_from_its_folder_and_a_host_run_without_one_is_neither_scored_nor_made(monkeypatch,
                                                                                                                   tmp_path):
    import compare
    import loop
    task, other = we.load_tasks(we.TASKS_STAGE4)[:2]
    archive = we.c.Archive(tmp_path)
    archive.start("validate", task.id).finish({"fail_to_pass": ["t::a"], "pass_to_pass": [], "ok": True}, "finished")
    made = archive.start("run", task.id, model=SONNET, arm="K1", repeat=1, executor="host")
    made.end("model_refused")                    # its ending was written; its record and the index's end of it never were
    bare = archive.start("run", task.id, model=SONNET, arm="K1", repeat=2, executor="host")
    (bare.path / "final.patch").write_text("a patch", encoding="utf-8")            # stopped before any ending
    limited = archive.start("run", other.id, model=SONNET, arm="K1", repeat=1, executor="host")
    limited.end("limited")
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K1", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="",
                          executor="host")
    assert "already made (model_refused)" in we.r3_refusal(ctx, task)[0] and we.r3_refusal(ctx, other)[0] == ""
    ctx.repeat = 2
    assert we.r3_refusal(ctx, task)[0] == ""
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a run that may not be scored is never checked"))
    with pytest.raises(SystemExit, match="no ending"):
        we.check(SimpleNamespace(archive=archive), task, bare.rel)
    diff = tmp_path / "amendment.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda name: diff)
    (tmp_path / "loop" / "amended").mkdir(parents=True)
    keys = [("K1", task.id, 2), ("K1", other.id, 1)]
    for (arm, name, repeat), rel in zip(keys, (bare.rel, limited.rel)):     # rows that say a pass, each with an amended pass kept
        saved = f"{arm}-{name}-{repeat}.json"
        (tmp_path / "loop" / saved).write_text(json.dumps({"arm": arm, "task": name, "repeat": repeat, "run": rel,
                                                           "status": "finished", "passes": True, "failing": 0}), "utf-8")
        (tmp_path / "loop" / "amended" / saved).write_text(json.dumps({"run": rel, "amendment_sha256": we.c.sha256_file(diff),
                                                                       "amended_passes": True}), "utf-8")
    scores = compare.saved(tmp_path)
    assert all((scores[key]["maintainers"], scores[key]["amended"]) == (None, None) for key in keys)
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps({"status": "finished", "run": limited.rel,
                                                                           "executor": "host"}) if command == "run"
                        else pytest.fail("a run that may not be scored is never checked"))
    assert loop.rescore(tmp_path, "g6") == []
    with pytest.raises(SystemExit, match="limited"):
        loop.one(tmp_path, "g6", "K1", "another", 1)                 # the run's record says finished; its ending, limited


# B: one figure gives a run's API need, and a host run's is nothing wherever it is asked.
def test_a_host_runs_api_need_is_nothing_at_admission_at_its_start_in_the_spend_and_at_its_sessions_cost_stop(monkeypatch, tmp_path):
    assert we.c.api_need({"executor": "host", "stop_usd": 10.5}) == 0.0 and we.c.api_need({"stop_usd": 10.5}) == 10.5
    assert we.c.api_need({"stop_usd": 0}) == 0.0 and we.c.api_need({}) == we.c.RUN_STOP_USD
    task = we.load_tasks(we.TASKS_STAGE4)[0]
    archive = we.c.Archive(tmp_path / "r3")
    archive.start("run", "prior-api").finish({"cost_usd": we.R3_CAP_USD}, "finished")       # the API's whole round spent
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K1", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="",
                          executor="host")
    assert we.r3_refusal(ctx, task)[0] == ""
    ctx.executor = "container"
    assert "the round's $180 stop" in we.r3_refusal(ctx, task)[0]
    fake_end(monkeypatch)
    start, _ = starter([("s1", "", stream("s1", cost=50.0))])
    session = SimpleNamespace(starter=start, model=SONNET, environ={}, archive=archive)
    tally = we.c.Tally(count_reported=True)
    assert we.host_session(session, archive.start("run", "t"), tmp_path, tmp_path / "m.json", tmp_path / "s.json", "go", "stream.jsonl",
                           seconds=60.0, tally=tally, stop_usd=we.c.api_need({"executor": "host"})) == ""
    assert tally.cost_usd == 50.0                                    # the subscription's estimate, which stopped nothing


# D: the filesystem's identity, not a name's rendering, says what a path is.
def test_the_mirror_refuses_a_repository_reached_through_an_ancestor_that_is_a_link_and_changes_nothing(tmp_path, box):
    ws, outside, parent = tmp_path / "ws", tmp_path / "outside", tmp_path / "parent"
    write(ws, {"a.py": "agent\n"})
    write(outside / "repo", {"a.py": "outside\n", "kept.txt": "k\n"})
    link(outside, parent)                           # the repository's own folder is plain; the folder above it is a link
    try:
        done = mirror_in(box, parent / "repo", box.pack(ws))
        assert done.returncode != 0 and b"refused" in done.stderr
        assert sorted(p.name for p in (outside / "repo").iterdir()) == ["a.py", "kept.txt"]
        assert (outside / "repo" / "a.py").read_text() == "outside\n"
    finally:
        unlink(parent)
    write(parent / "repo", {"a.py": "old\n"})
    assert mirror_in(box, parent / "repo", box.pack(ws)).returncode == 0 and (parent / "repo" / "a.py").read_text() == "agent\n"


def test_an_extracted_export_is_checked_by_exact_name_and_by_file_identity(tmp_path):
    dest = tmp_path / "dest"
    write(dest, {"a.txt": "a"})
    os.link(dest / "a.txt", dest / "b.txt")
    with pytest.raises(RuntimeError, match="one file"):
        we.written(dest, [tarfile.TarInfo("./a.txt"), tarfile.TarInfo("./b.txt")])
    with pytest.raises(RuntimeError, match="own name"):
        we.written(dest, [tarfile.TarInfo("./A.TXT")])
    we.written(dest, [tarfile.TarInfo("./"), tarfile.TarInfo("./a.txt")])


@pytest.mark.skipif(os.name != "nt", reason="NTFS short names")
def test_the_export_refuses_a_short_name_alias_and_leaves_the_workspace_empty_before_the_agent_starts(tmp_path):
    import ctypes
    write(tmp_path / "probe", {"longfilename.py": "x"})
    buffer = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.kernel32.GetShortPathNameW(str(tmp_path / "probe" / "longfilename.py"), buffer, len(buffer))
    alias = Path(buffer.value).name
    if alias.casefold() == "longfilename.py":
        pytest.skip("this volume gives no short names")
    archive = tar_of([("./", "dir", b""), ("./longfilename.py", "file", b"first"), ("./" + alias, "file", b"second")])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="nothing of it kept"):
        we.workspace_from(SimpleNamespace(docker=lambda *a, **kw: ok(archive)), SimpleNamespace(repo="/w/r"), "agent", dest)
    assert list(dest.iterdir()) == []


# E: a docker failure is told from the command's own exit by a start line only the command's shell prints.
@pytest.mark.parametrize("code", [0, 1, 3, 125])
def test_a_reply_without_the_calls_own_start_line_is_a_tool_error_whatever_its_exit(tmp_path, box, code):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    starts = []

    def docker(head):
        def reply(*args, **kw):
            if box.MIRROR in args:
                return ok(b"{}")
            starts.append(args[-2])
            return ok(head(args[-2]) + b"Error response from daemon: No such container: ctr\n", code=code)
        return reply

    for head in (lambda start: b"", lambda start: f"{box.START} {'0' * 32}\n".encode()):     # none, or another call's
        got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, docker(head))
        assert got["isError"] is True and "never ran" in got["content"][0]["text"]
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, docker(lambda start: start.encode() + b"\n"))
    assert got["isError"] is False and got["content"][0]["text"].startswith(f"exit code {code}") and box.START not in got["content"][0]["text"]
    assert len(set(starts)) == 3 and all(s.startswith(box.START + " ") for s in starts)       # a nonce of its own for each call


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh on this host")
def test_the_box_shell_prints_the_start_line_and_then_runs_the_command(box):
    done = subprocess.run(["sh", "-c", box.SHELL, f"{box.START} abc", "echo ran; exit 3"], capture_output=True, timeout=60)
    assert done.returncode == 3 and done.stdout.replace(b"\r\n", b"\n") == f"{box.START} abc\nran\n".encode()


# --- the third review of the host executor (2026-11-04): each class of its findings removed at its root -------------------------
# A: a run's durable ending comes first: one function gives a run's verdict on either score, and a verdict saved under another
# ending is void.
def check_says(passes: bool):
    return lambda command, *a, **kw: json.dumps({"checks": [{"patch": "final", "passes": passes, "failing": 0,
                                                             "fail_to_pass_failing": []}]})


def test_a_saved_verdict_holds_only_under_the_ending_it_was_made_under_on_either_score(monkeypatch, tmp_path):
    import compare
    import loop
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "t", executor="host")
    run.end("finished")
    diff = tmp_path / "amendment.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps({"status": "finished", "run": run.rel, "executor": "host"})
                        if command == "run" else check_says(True)(command))
    made = we.c.ending_sha256(tmp_path, run.rel)
    assert made and loop.one(tmp_path, "g6", "K1", "t", 1)["ending_sha256"] == made
    assert loop.rescore(tmp_path, "g6")[0]["amended_passes"] is True
    scores = compare.saved(tmp_path)[("K1", "t", 1)]
    assert (scores["maintainers"], scores["amended"]) == (True, True)
    run.failed("final patch capture failed", agent_started=True)       # the run's ending is "error" now
    assert we.c.ending_sha256(tmp_path, run.rel) != made
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("an error run's verdict needs no run and no check"))
    scores = compare.saved(tmp_path)[("K1", "t", 1)]
    assert (scores["maintainers"], scores["amended"]) == (False, False)
    assert loop.rescore(tmp_path, "g6")[0]["amended_passes"] is False
    row = loop.one(tmp_path, "g6", "K1", "t", 1)                        # given again from the ending it has now
    assert (row["status"], row["passes"], row["ending_sha256"]) == ("error", False, we.c.ending_sha256(tmp_path, run.rel))
    container = archive.start("run", "c")                               # a container run has no ending file: its row holds as before
    container.finish({"agent_started": True}, "finished")              # on its own record, which the admission rule reads
    kept = {"arm": "A", "task": "c", "repeat": 1, "run": container.rel, "status": "finished", "passes": True, "failing": 0}
    (tmp_path / "loop" / "A-c-1.json").write_text(json.dumps(kept), "utf-8")
    assert loop.one(tmp_path, "g6", "A", "c", 1) == kept and compare.saved(tmp_path)[("A", "c", 1)]["maintainers"] is True


@pytest.mark.parametrize("status", ["limited", "refused"])
def test_a_saved_pass_for_a_run_that_may_not_be_scored_is_never_returned_or_run_again(monkeypatch, tmp_path, status):
    import compare
    import loop
    run = we.c.Archive(tmp_path).start("run", "t", executor="host")
    run.end(status)
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K1-t-1.json").write_text(json.dumps({"arm": "K1", "task": "t", "repeat": 1, "run": run.rel,
                                                               "status": "finished", "passes": True, "failing": 0}), "utf-8")
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a saved row's check starts nothing"))
    with pytest.raises(SystemExit, match=status):
        loop.one(tmp_path, "g6", "K1", "t", 1)
    monkeypatch.setattr(we, "amendment", lambda task: None)
    assert compare.saved(tmp_path)[("K1", "t", 1)]["maintainers"] is None


def test_an_empty_or_missing_record_is_never_scoreable_and_the_loop_saves_nothing_without_one(monkeypatch, tmp_path):
    import loop
    assert not any(we.c.scoreable(x) for x in (None, {}, {"status": None}, {"status": ""}, {"status": "unheard of"}))
    assert all(we.c.scoreable({"status": s}) for s in ("finished", "model_refused", "error", "timeout", "cap_turns", "cap_cost"))
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: "" if command == "run" else pytest.fail("no record, no check"))
    with pytest.raises(SystemExit, match="no record"):
        loop.one(tmp_path, "g6", "K1", "t", 1)
    assert not (tmp_path / "loop").exists()


@pytest.mark.parametrize("model", [we.R3_MODELS[1], we.R3_MODELS[2]])
def test_a_host_run_is_admitted_whatever_the_api_has_spent_and_asks_no_share_of_fables_budget(tmp_path, model):
    task = we.load_tasks()[0]
    archive = we.c.Archive(tmp_path)
    for key in [k for k in we.r3_schedule([task]) if we.R3_MODELS.index(k[0]) < we.R3_MODELS.index(model)]:
        archive.start("run", key[1], model=key[0], arm=key[2], repeat=key[3]).finish({"agent_started": True, "cost_usd": 0.0}, "finished")
    archive.start("run", "prior-api").finish({"cost_usd": we.R3_CAP_USD + 0.01}, "finished")      # past the round's API stop
    ctx = SimpleNamespace(archive=archive, tasks=[task], model=model, arm="A", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="",
                          executor="container")
    assert "stop" in we.r3_refusal(ctx, task)[0]
    ctx.executor = "host"
    assert we.r3_refusal(ctx, task) == ("", we.R3_RUN_CAP_USD, round(we.R3_RUN_CAP_USD * we.R3_MARGIN, 6))


# B: Claude Code's stream is newline-delimited JSON, split at "\n" alone wherever a host stream or its stderr is read.
@pytest.mark.parametrize("where", ["stream", "stderr"])
def test_a_line_separator_inside_a_json_string_never_splits_a_host_stream_or_its_stderr(monkeypatch, tmp_path, where):
    quoted = json.dumps({"type": "assistant", "session_id": "s1", "message": {"id": "m-quoted", "model": SONNET, "content": [
        {"type": "text", "text": "A quoted example: " + LIMIT_TEXT}]}}, ensure_ascii=False)
    assert we.limit_of(quoted) is None and len(quoted.splitlines()) == 2         # one line, which str.splitlines makes two
    lines = stream("s1")
    if where == "stream":
        lines.insert(2, quoted)
    fake_end(monkeypatch)
    start, _ = starter([("s1", "", lines)])

    def started(argv, **kw):
        proc = start(argv, **kw)
        if where == "stderr":
            proc.stderr = io.BytesIO(quoted.encode("utf-8"))
        return proc

    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=None, issue_reader=None, g6={},
                     variant=we.ROUND3, arm="A", model=SONNET, environ={"PATH": "p"}, executor="host", starter=started)
    run = ctx.archive.start("run", "t", executor="host")
    assert we.host_session(ctx, run, tmp_path, tmp_path / "m.json", tmp_path / "s.json", "go", "stream.jsonl", seconds=60.0,
                           tally=we.c.Tally()) == ""
    assert we.host_facts(run.path / "stream.jsonl", SONNET)["limit"] is None and not (run.path / "ending.json").exists()


# C: everything a session needs before it can be ended is inside the try whose finally ends it.
def test_a_session_whose_transcript_cannot_be_opened_is_still_ended(monkeypatch, tmp_path):
    fake_end(monkeypatch)
    start, starts = starter([("s1", "", None)])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=None, issue_reader=None, g6={},
                     variant=we.ROUND3, arm="A", model=SONNET, environ={"PATH": "p"}, executor="host", starter=start)
    run = ctx.archive.start("run", "t", executor="host")

    def cannot_open(name):
        raise OSError("no room for the transcript")

    monkeypatch.setattr(run, "fresh_path", cannot_open)
    with pytest.raises(OSError, match="no room"):
        we.host_session(ctx, run, tmp_path, tmp_path / "m.json", tmp_path / "s.json", "go", "stream.jsonl", seconds=60.0,
                        tally=we.c.Tally())
    assert len(starts) == 1 and starts[0]["proc"].ended == tmp_path


# D: a folder the walk went through is checked again once it is done.
@pytest.mark.parametrize("into", ["a link", "another folder"])
def test_a_folder_changed_after_its_check_refuses_the_whole_workspace(monkeypatch, tmp_path, box, into):
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    write(ws, {"a.py": "x\n", "pkg/source.py": "original\n"})
    outside.mkdir()
    checked, swapped = box._plain, []

    def swap(path, root):            # the folder's name given to a link, or to another folder, right after its check
        st = checked(path, root)
        if path.name == "pkg" and not swapped:
            (ws / "pkg").rename(tmp_path / "moved")
            if into == "a link":
                link(outside, ws / "pkg")
            else:
                (ws / "pkg").mkdir()
            swapped.append(into)
        return st

    monkeypatch.setattr(box, "_plain", swap)
    sent = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    try:
        got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, lambda *a, **kw: (sent.append(a), ok(b"{}"))[1])
        assert swapped and got["isError"] is True and sent == [] and "pkg" in got["content"][0]["text"]
    finally:
        if swapped == ["a link"]:
            unlink(ws / "pkg")


# E: any failure to pack a workspace is a refused workspace: a tool error, with nothing sent or run.
@pytest.mark.parametrize("error", [UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"), OSError("gone"),
                                   tarfile.TarError("bad member"), ValueError("bad name")])
def test_any_failure_to_pack_the_workspace_is_a_refused_workspace_and_nothing_is_sent(monkeypatch, tmp_path, box, error):
    ws = tmp_path / "ws"
    write(ws, {"a.py": "x\n"})

    def fails(*a):
        raise error

    monkeypatch.setattr(box, "_read", fails)
    with pytest.raises(box.Refused):
        box.pack(ws)
    sent = []
    opts = SimpleNamespace(container="ctr", repo="/w/r", workspace=str(ws), user="tester", timeout=30.0)
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, lambda *a, **kw: (sent.append(a), ok(b"{}"))[1])
    assert got["isError"] is True and sent == [] and got["content"][0]["text"].startswith("nothing was mirrored or run")
