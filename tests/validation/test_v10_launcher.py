# SPDX-License-Identifier: Apache-2.0
"""The session launcher (``validation/patch_review_search/launcher.py``): Claude Code sessions for patch generation,
V9's arm 2 and V10's arm R, run as V7's runner ran them, under one persisted dollar stop, with V9's session records.

No model is called and no container is started: Docker is a fake that records each command and plays back a stream.
The key here is a made-up string, never a real one."""
from __future__ import annotations

import hashlib
import json

import pytest

from patch_review_search import arm_r, launcher
from patch_review_utility import self_review

KEY = "sk-ant-made-up-not-a-key-0000"


class FakeProcess:
    def __init__(self, lines):
        import io
        self.stdin, self.stdout, self.stderr = io.BytesIO(), iter_lines(lines), io.BytesIO(b"")
        self.returncode = 0

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class iter_lines:
    def __init__(self, lines):
        self.lines = [(json.dumps(line) + "\n").encode("utf-8") for line in lines]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


class FakeDocker:
    """Records every command; ``popen`` plays back the stream given."""

    def __init__(self, stream=()):
        self.commands, self.stream, self.envs = [], list(stream), []

    def __call__(self, *args, input=None, timeout=None, env=None):
        import subprocess
        self.commands.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, b"", b"")

    def sh(self, ctr, script, *args, **kwargs):
        return self("exec", ctr, "bash", "-c", script, *args)

    def popen(self, *args, env=None):
        self.commands.append(list(args))
        self.envs.append(dict(env or {}))
        return FakeProcess(self.stream)


def stream(answer="All good.\nVERDICT: ACCEPT", usage=None, session="sess-1", leak=False):
    usage = usage or {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 5000,
                      "cache_creation_input_tokens": 100}
    text = f"the key is {KEY}" if leak else "working"
    return [{"type": "system", "subtype": "init", "session_id": session, "tools": ["Bash", "Edit"], "mcp_servers": []},
            {"type": "assistant", "session_id": session,
             "message": {"id": "m1", "usage": usage, "content": [{"type": "text", "text": text}]}},
            {"type": "result", "subtype": "success", "session_id": session, "result": answer, "is_error": False,
             "num_turns": 3, "duration_ms": 4000, "total_cost_usd": 0.0123}]


def test_the_ledger_stops_before_a_session_that_could_pass_the_cap_and_persists(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = launcher.Ledger(path, cap=15.0)
    first = ledger.reserve("patch:t1", 10.0)
    with pytest.raises(launcher.Stopped, match="cap"):
        ledger.reserve("patch:t2", 5.5)                        # 10 open + 5.5 would pass 15
    ledger.settle(first, 0.25, {"model_calls": 2})
    again = launcher.Ledger(path, cap=15.0)                    # a later step counts what earlier ones spent
    assert again.spent == pytest.approx(0.25) and again.open_dollars() == 0
    held = again.reserve("review_r:t1", 2.0)
    assert launcher.Ledger(path, cap=15.0).open_dollars() == pytest.approx(2.0)   # an unsettled one still counts
    again.settle(held, 0.5, {})
    with pytest.raises(launcher.Stopped, match="raise"):
        launcher.Ledger(path, cap=20.0)                        # the cap can be lowered, never raised


def test_a_session_is_claude_code_headless_with_the_registered_flags_and_the_key_only_in_its_environment(tmp_path):
    docker = FakeDocker(stream())
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    box = launcher.v7.Box(docker, "agent-1", repo="/app", net=launcher.v7.Net(docker, "net", "proxy"))
    patch = "diff --git a/x.py b/x.py\n"
    got = launcher.run_session("self_review", prompt="Review it.", docker=docker, box=box, key=KEY,
                               ledger=ledger, record_dir=tmp_path / "s", task_id="t1", patch=patch,
                               resume="sess-0", caps=launcher.CAPS["self_review"])
    command = docker.commands[-1]
    claude = command[command.index(launcher.v7.CLAUDE_IN):]
    assert claude[:2] == [launcher.v7.CLAUDE_IN, "-p"] and claude[claude.index("--model") + 1] == launcher.MODEL
    assert claude[claude.index("--effort") + 1] == launcher.EFFORT and "--strict-mcp-config" in claude
    assert claude[claude.index("--resume") + 1] == "sess-0" and "WebFetch" in claude and "WebSearch" in claude
    assert claude[claude.index("--max-budget-usd") + 1] == "0.75"
    assert "ANTHROPIC_API_KEY" in command and KEY not in json.dumps(command)       # passed by name only
    assert docker.envs[-1]["ANTHROPIC_API_KEY"] == KEY
    written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "s").rglob("*") if p.is_file())
    assert KEY not in written
    dollars = (1000 * 2.00 + 200 * 10.00 + 5000 * 0.20 + 100 * 4.00) / 1e6
    assert got["dollars"] == pytest.approx(dollars) and ledger.spent == pytest.approx(dollars)
    assert got["model"] == launcher.MODEL and got["ended"] == "completed" and got["model_calls"] == 1
    assert got["patch_sha256"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()
    assert got["tokens"] == 6300 and got["session_id"] == "sess-1" and got["transcript"] == "t1.jsonl"


def test_a_stream_line_that_holds_the_key_is_written_with_the_key_masked(tmp_path):
    docker = FakeDocker(stream(leak=True))
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    box = launcher.v7.Box(docker, "agent-1", repo="/app", net=launcher.v7.Net(docker, "net", "proxy"))
    launcher.run_session("patch", prompt="Do it.", docker=docker, box=box, key=KEY, ledger=ledger,
                         record_dir=tmp_path / "s", task_id="t1", patch="", caps=launcher.CAPS["patch"])
    transcript = (tmp_path / "s" / "t1.jsonl").read_text(encoding="utf-8")
    assert KEY not in transcript and launcher.MASK in transcript


def test_the_dollar_stop_starts_no_session_it_cannot_cover(tmp_path):
    docker = FakeDocker(stream())
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=1.0)
    box = launcher.v7.Box(docker, "agent-1", repo="/app", net=launcher.v7.Net(docker, "net", "proxy"))
    with pytest.raises(launcher.Stopped):
        launcher.run_session("patch", prompt="Do it.", docker=docker, box=box, key=KEY, ledger=ledger,
                             record_dir=tmp_path / "s", task_id="t1", patch="", caps=launcher.CAPS["patch"])
    assert docker.commands == [] and docker.envs == []


class Captured(FakeDocker):
    """A fake whose ``docker cp`` out of the container writes the patch the session left."""

    def __init__(self, streams, patch):
        super().__init__()
        self.streams, self.patch = list(streams), patch

    def __call__(self, *args, input=None, timeout=None, env=None):
        from pathlib import Path
        if args and args[0] == "cp" and str(args[1]).endswith(":/tmp/v7_model.patch"):
            Path(args[2]).write_text(self.patch, encoding="utf-8")
        return super().__call__(*args, input=input, timeout=timeout, env=env)

    def popen(self, *args, env=None):
        self.stream = self.streams.pop(0)
        return super().popen(*args, env=env)


def test_a_task_gets_its_patch_session_then_arm_2_in_the_same_session_with_v9s_prompt(tmp_path, monkeypatch):
    patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
    docker = Captured([stream(answer="Done.", session="sess-patch"), stream(session="sess-patch")], patch)
    monkeypatch.setattr(launcher, "open_box", lambda *a, **k: launcher.v7.Box(
        docker, "agent-1", repo="/app", net=launcher.v7.Net(docker, "net", "proxy")))
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    requirements = [{"id": "R1", "text": "add returns the sum"}]
    got = launcher.patch_and_self_review(
        "t1", statement="add returns the difference.", requirements=requirements, source=tmp_path,
        docker=docker, assets=None, image="img", key=KEY, ledger=ledger, out=tmp_path / "out",
        time_limit_s=1800, token_cap=200_000)
    assert (tmp_path / "out" / "patches" / "t1.diff").read_text(encoding="utf-8") == patch
    review = json.loads((tmp_path / "out" / "sessions" / "t1.json").read_text(encoding="utf-8"))
    assert review["patch_sha256"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()
    prompts = [c for c in docker.commands if launcher.v7.CLAUDE_IN in c]
    assert "--resume" not in prompts[0] and prompts[1][prompts[1].index("--resume") + 1] == "sess-patch"
    prepared = self_review.prepare("t1", requirements, patch=patch, time_limit_s=1800, token_cap=200_000)
    assert self_review.disposition(prepared, review)["accepted"] is True
    assert got["patch"]["model_calls"] == 1 and got["self_review"]["dollars"] > 0


def test_arm_r_reviews_in_its_copy_with_the_diff_beside_the_prompt_and_its_tests_come_back(tmp_path, monkeypatch):
    patch = "diff --git a/calc.py b/calc.py\n"
    docker = Captured([stream(answer="VERDICT: REJECT", session="sess-r")], patch)
    monkeypatch.setattr(launcher, "open_box", lambda *a, **k: launcher.v7.Box(
        docker, "agent-2", repo="/app", net=launcher.v7.Net(docker, "net", "proxy")))
    prepared = arm_r.prepare("t1", [{"id": "R1", "text": "x"}], patch=patch, time_limit_s=1800, token_cap=200_000)
    copy = tmp_path / "copy"
    copy.mkdir()
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    got = launcher.review_r("t1", prepared=prepared, copy=copy, docker=docker, assets=None, image="img", key=KEY,
                            ledger=ledger, sessions=tmp_path / "r-sessions")
    claude = next(c for c in docker.commands if launcher.v7.CLAUDE_IN in c)
    beside = claude[claude.index("--append-system-prompt") + 1]
    assert patch in beside and "--resume" not in claude
    assert ["cp", f"agent-2:/app/{arm_r.FOLDER}/.", str(copy / arm_r.FOLDER)] in docker.commands
    assert got["ended"] == "completed" and (tmp_path / "r-sessions" / "t1.json").is_file()


def test_the_records_are_the_ones_arm_2_and_arm_r_read(tmp_path):
    docker = FakeDocker(stream(answer="Found a defect.\nVERDICT: REJECT"))
    ledger = launcher.Ledger(tmp_path / "ledger.json", cap=15.0)
    box = launcher.v7.Box(docker, "agent-1", repo="/app", net=launcher.v7.Net(docker, "net", "proxy"))
    patch = "diff --git a/x.py b/x.py\n"
    got = launcher.run_session("review_r", prompt="Review it.", docker=docker, box=box, key=KEY, ledger=ledger,
                               record_dir=tmp_path / "r", task_id="t1", patch=patch, caps=launcher.CAPS["review_r"])
    prepared = arm_r.prepare("t1", [{"id": "R1", "text": "x"}], patch=patch, time_limit_s=1800, token_cap=200_000)
    assert arm_r.disposition(prepared, got)["category"] == "defect_found"
    assert self_review.disposition(prepared, {**got, "answer": "VERDICT: ACCEPT"})["accepted"] is True
    assert arm_r.cost_cents(got, launcher.PRICES) == pytest.approx(got["dollars"] * 100)
