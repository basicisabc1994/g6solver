# SPDX-License-Identifier: Apache-2.0
"""V7, design D, on its own logic: no network, no Docker, no model, no dataset (the committed split ids only)."""
from __future__ import annotations

import asyncio
import io
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from v7_swebench import analyse, egress_proxy, g6check, run_arm, sample

c = run_arm.c          # the scripts import their shared module as ``v7common``; use that same object
shadow = run_arm.shadow   # and ``shadow`` as run_arm imports it (``v7_swebench.shadow`` would be another module)

OSS = Path(__file__).resolve().parents[2]
SPLITS = OSS / "docs" / "validation" / "results" / "V7" / "splits.json"
KEY = "sk-ant-api03-" + "Zq9" * 30
LEAD_SELF_CHECK = "Check your work: reproduce the issue, run the tests you can, and fix anything that is wrong."


# --- the draw and the scored order ---------------------------------------------------------------------------------------
def _pool():
    repos = {"o/py1": "python", "o/py2": "python", "o/go1": "go", "o/go2": "go", "o/js1": "js", "o/ts1": "ts",
             "o/js2": "js"}
    return [{"instance_id": f"{r}-{i}", "repo": r, "repo_language": lang} for r, lang in repos.items() for i in range(9)]


def test_the_draw_is_disjoint_by_repository_and_fixed_by_the_seed():
    a, b = sample.draw(_pool(), scored=40), sample.draw(list(reversed(_pool())), scored=40)
    assert a == b                                  # the row order does not matter; the seed decides
    assert sample.check_disjoint(a) == []
    assert len(a["dev"]) == 10 and len(a["eval"]) == 20
    bad = {**a, "eval": a["eval"][:-1] + [a["dev"][0]]}
    assert sample.check_disjoint(bad)              # removing the repository check in check_disjoint passes this


def test_the_draw_reads_only_the_three_id_columns():
    rows = [{"instance_id": r["instance_id"], "repo": r["repo"], "repo_language": r["repo_language"]} for r in _pool()]
    assert sample.draw(rows, scored=40)["dev"]
    with pytest.raises(ValueError):
        sample.draw([{"instance_id": "x", "repo": "r", "repo_language": "rust"}])


def test_the_scored_order_is_fixed_and_extends_the_registered_20():
    split = json.loads(SPLITS.read_text(encoding="utf-8"))
    scored = split["scored"]
    assert scored[:20] == split["eval"] and len(scored) == 60 == len(set(scored))
    assert not set(scored) & set(split["dev"]) and split["pilot"] == split["dev"][:4]
    assert sample.check_disjoint(split) == [] and split["dataset"]["sha256"] == c.DATASET["sha256"]
    a = sample.draw(_pool(), scored=40)
    assert a["scored"][:20] == a["eval"] and not set(a["scored"]) & set(a["dev"]) and len(set(a["scored"])) == 40
    # the continuation picks up where the evaluation round-robin stopped (not a fresh round)
    repo = {r["instance_id"]: r["repo"] for r in _pool()}
    rotation = a["eval_repos"] + a["dev_repos"]
    after = rotation.index(repo[a["eval"][-1]]) + 1
    assert repo[a["scored"][20]] == rotation[after % len(rotation)]


def test_no_scored_task_is_held_at_intake_by_its_goal_text():
    from g6.harness import Check, Memory, Standard, run

    split = json.loads(SPLITS.read_text(encoding="utf-8"))
    for task in split["dev"] + split["scored"]:
        got = run(Standard(goal=f"Resolve SWE-bench Pro task {task} in its repository.",
                           checks=(Check("ok", lambda o: True),), family="v7"),
                  lambda contract: "x", memory=Memory(":memory:"))
        assert got["completion_state"] != "blocked-escalated", task


# --- spend --------------------------------------------------------------------------------------------------------------
def _ledger(archive: c.Archive, rows: list[tuple[str, float, str]]) -> None:
    for i, (phase, cost, status) in enumerate(rows):
        run = archive.start(phase, f"t{i}", "A")
        if status != "running":
            run.finish({"cost_usd": cost, "agent_started": True}, status)


def test_the_cap_defaults_to_180_and_is_never_raised():
    assert c.cap_usd({}) == 180.0
    assert c.cap_usd({"G6_BENCH_MAX_USD": "300"}) == 180.0     # removing min(..., MAX_USD_CEILING) breaks this
    assert c.cap_usd({"G6_BENCH_MAX_USD": "60"}) == 60.0
    assert c.cap_usd({"G6_BENCH_MAX_USD": "lots"}) == 0.0 and c.cap_usd({"G6_BENCH_MAX_USD": "-5"}) == 0.0


def test_a_task_is_reserved_at_all_three_runs_caps_and_its_lesson_and_runs_in_flight_count_too(tmp_path):
    assert c.RESERVE_PER_RUN >= c.CAPS["max_usd_per_run"] and c.RESERVE_STAGE2 >= c.CAPS_STAGE2["max_usd_per_run"]
    assert c.TASK_RESERVE == pytest.approx(c.RESERVE_PER_RUN + 2 * c.RESERVE_STAGE2 + c.RESERVE_LESSON) == 8.6
    archive = c.Archive(tmp_path)
    _ledger(archive, [("eval", 180.0 - 8.6 + 0.01, "finished")])
    assert not c.may_start(c.spend(archive.records()), "eval", need=c.TASK_RESERVE)[0]   # one cent short
    archive2 = c.Archive(tmp_path / "b")
    _ledger(archive2, [("eval", 180.0 - 8.6, "finished")])
    assert c.may_start(c.spend(archive2.records()), "eval", need=c.TASK_RESERVE)[0]
    archive3 = c.Archive(tmp_path / "c")                       # a run another process has in flight counts at $4.50
    _ledger(archive3, [("eval", 180.0 - 8.6 - 4.5 + 0.01, "finished"), ("pilot", 0.0, "running")])
    assert not c.may_start(c.spend(archive3.records()), "eval", need=c.TASK_RESERVE)[0]
    assert not c.may_start(c.spend([]), "eval", environ={"G6_BENCH_MAX_USD": "2"})[0]


# --- the archive -------------------------------------------------------------------------------------------------------
def test_the_archive_is_write_once(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = archive.start("eval", "t1", "A")
    run.write("prompt.txt", "hello")
    with pytest.raises(c.ArchiveError):
        run.write("prompt.txt", "again")                   # a file is created once
    with pytest.raises(c.ArchiveError):
        run.write("run.json", "{}")                        # run.json is the index, written only by finish
    with pytest.raises(c.ArchiveError):
        run.write("../escape.txt", "x")
    monkeypatch.setattr(c, "_stamp", lambda: "20261023T000000.000000Z")
    archive.start("eval", "t2", "A")
    with pytest.raises(c.ArchiveError):
        archive.start("eval", "t2", "A")                   # a run folder is created once
    run.finish({"cost_usd": 0.1}, "finished")
    with pytest.raises(c.ArchiveError):
        run.finish({"cost_usd": 0.1}, "finished")
    assert (run.path / "prompt.txt").read_text() == "hello"


def test_the_key_scan_withholds_a_planted_key_and_flags_the_run(tmp_path):
    archive = c.Archive(tmp_path, c.Redactor(KEY))
    run = archive.start("eval", "t1", "A")
    planted = run.fresh_path("model.patch")                # written by an outside program, not redacted on the way
    # the second key is built at runtime so no key-shaped literal sits in this file (the export scan's anthropic-key rule)
    other = "sk-ant-" + "admin01-abcdefghijklmnop"
    planted.write_text(f"+TOKEN = '{KEY}'\n+OTHER = '{other}'\n", encoding="utf-8")
    run.write("prompt.txt", f"key {KEY}")                  # redacted before it is written
    done = run.finish({"cost_usd": 0.0}, "finished")
    assert done["flagged"] is True and done["redactions"] == [{"file": "model.patch", "replaced": 2}]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert KEY not in text and "sk-ant-admin01" not in text, path.name
    assert c.WITHHELD in planted.read_text(encoding="utf-8")
    assert done["files"]["model.patch"]["sha256"] == c.sha256_file(planted)   # the index hashes the redacted file


def test_the_manifest_hashes_match_the_files(tmp_path):
    archive = c.Archive(tmp_path / "archive")
    run = archive.start("eval", "t1", "A")
    run.write("stream.jsonl", '{"type": "system"}\n')
    run.finish({"cost_usd": 1.0}, "finished")
    found = analyse.write_manifest(archive, tmp_path / "results")
    manifest = json.loads((tmp_path / "results" / "archive_manifest.json").read_text())
    assert found["files"] == manifest["count"] == 3        # the stream, run.json and runs.jsonl
    for rel, entry in manifest["files"].items():
        path = archive.root / rel
        assert entry["sha256"] == c.sha256_file(path) and entry["bytes"] == path.stat().st_size


# --- a fake docker for design D: every command the runner sends, answered without a daemon -----------------------------
class FakeProc:
    def __init__(self, lines: list[bytes]):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout = io.BytesIO(b"".join(lines))
        self.stderr = io.BytesIO(b"a line of stderr\n")
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _stream(text_with_key: bool = False, refusal: bool = False, cost: float = 0.5, subtype: str = "success",
            result: bool = True) -> list[dict]:
    leak = f"ANTHROPIC_API_KEY={KEY}" if text_with_key else "ok"
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "assistant", "session_id": "s1", "message": {
            "id": "m1", "stop_reason": "refusal" if refusal else "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "env"}}]}},
        {"type": "user", "session_id": "s1", "message": {"content": [{"type": "tool_result", "content": leak}]}},
    ]
    if result:
        lines.append({"type": "result", "subtype": subtype, "is_error": refusal or subtype != "success", "num_turns": 1,
                      "total_cost_usd": cost, "duration_ms": 1000, "session_id": "s1", "result": "done"})
    return lines


class DockerD(run_arm.Docker):
    def __init__(self, *, flag: bool = False, oracle: bytes = b"clean 0", stream: list[dict] | None = None,
                 patch: str = "diff --git a/x.py b/x.py\n+++ b/x.py\n+ok\n", events: list | None = None,
                 fail_commit: bool = False):
        super().__init__(runner=self._run)
        self.flag, self.oracle, self.patch, self.fail_commit = flag, oracle, patch, fail_commit
        self.stream = stream if stream is not None else _stream()
        self.events = events if events is not None else []
        self.calls, self.popens, self.procs = [], [], []

    def _run(self, argv, input=None, capture_output=True, timeout=None, env=None):
        self.calls.append(argv)
        args = argv[1:]
        stdout, code = b"", 0
        if args[:2] == ["image", "inspect"]:
            stdout = b'["ghcr.io/x@sha256:abc"]'
        elif args[0] == "commit":
            self.events.append(("snapshot",))
            stdout, code = (b"", 1) if self.fail_commit else (b"sha256:snap\n", 0)
        elif args[0] == "cp" and ":" in args[1] and not Path(args[1]).drive:
            dest = Path(args[2])
            if args[1].endswith("v7_model.patch"):
                dest.write_text(self.patch, encoding="utf-8")
            elif args[1].endswith("egress.jsonl"):
                dest.write_text('{"host": "api.anthropic.com", "port": 443, "allowed": true}\n', encoding="utf-8")
        elif args[0] == "exec" and any("g6check.py" in a for a in args):
            self.events.append(("g6_check",))
            findings = [{"check": "visible_tests", "said": "tests/test_x.py fails"}] if self.flag else []
            stdout = json.dumps({"flag": self.flag, "findings": findings, "tests": ["tests/test_x.py"],
                                 "replies": {"workforce.repo_tests.run": {"verdict": "failed" if self.flag else
                                                                          "passed"}}}).encode()
        elif args[0] == "exec" and "bash" in args and "-c" in args:
            stdout = self._script(args[args.index("-c") + 1])
        return subprocess.CompletedProcess(argv, code, stdout, b"")

    def _script(self, script: str) -> bytes:
        if "v7-oracle-check" in script:
            self.events.append(("oracle_check",))
            return self.oracle
        if "rev-parse --show-toplevel" in script and "git add" not in script:
            return b"/app"
        if "rev-list --all" in script:
            return b"all=5\nhead=5\nstash=0\nrefs=1\ndirty=0\nsha=abc"
        if "ldd" in script:
            return b"glibc"
        if "--version" in script and "claude" in script:
            return b"2.1.309 (Claude Code)"
        if "git add -A" in script:
            return str(len(self.patch)).encode()
        if "import g6" in script:
            return b"0.1.0.dev0"
        if "command -v python3" in script:
            return b"/usr/bin/python3"
        return b""

    def popen(self, *args, env=None):
        self.events.append(("claude", "--resume" in args))
        self.popens.append((list(args), dict(env or {})))
        proc = FakeProc([(json.dumps(o) + "\n").encode() for o in self.stream])
        self.procs.append(proc)
        return proc


def _harness_tree(root: Path, task: str) -> None:
    folder = root / "tasks" / task
    (folder / "environment").mkdir(parents=True)
    (folder / "tests").mkdir()
    (folder / "environment" / "Dockerfile").write_text("FROM ghcr.io/x:1\n", encoding="utf-8")
    (folder / "task.toml").write_text('[environment]\ndocker_image = "ghcr.io/x:1"\ncpus = 1\nmemory_mb = 4096\n'
                                      '[agent]\ntimeout_sec = 3000.0\n[verifier]\ntimeout_sec = 3000.0\n',
                                      encoding="utf-8")
    (folder / "instruction.md").write_text("Fix the bug.", encoding="utf-8")
    (folder / "tests" / "test_patch.patch").write_text("--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1 @@\n+x\n",
                                                       encoding="utf-8")


WHEEL = {"ref": "release/v0.1", "commit": "c0ffee" * 6 + "c0ff", "sha256": "ab" * 32}


class FakeAnthropic:
    """The Anthropic SDK client's ``messages.create``, as the lesson writer's AnthropicCaller calls it: no network."""

    def __init__(self, replies: list | None = None, events: list | None = None, usage: dict | None = None):
        self.replies = list(replies) if replies is not None else [
            '{"failure_code": "VISIBLE_TESTS_LEFT_FAILING", "instruction": "Run the tests next to every file you '
            'change before you finish.", "rationale": "G6 found the touched tests failing."}']
        self.events = events if events is not None else []
        self.usage = usage or {"input_tokens": 1500, "output_tokens": 120, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}
        self.calls, self.keys = [], []
        self.messages = self

    def __call__(self, key: str) -> FakeAnthropic:   # the context's factory: the key -> a client
        self.keys.append(key)
        return self

    def create(self, **kwargs):
        self.events.append(("lesson",))
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        dump = {"id": f"msg_{len(self.calls)}", "model": kwargs["model"], "stop_reason": "end_turn",
                "content": [{"type": "text", "text": reply}], "usage": dict(self.usage)}
        block = type("Block", (), {"type": "text", "text": reply})()
        usage = type("Usage", (), dict(self.usage))()

        class Response:
            content, stop_reason = [block], "end_turn"

            def model_dump(self, mode="json"):
                return dump
        Response.usage = usage
        return Response()


def _ctx(tmp_path: Path, docker, *, environ=None, key_reader=None, lessons=None) -> run_arm.RunContext:
    harness = tmp_path / "harness"
    for task in ("t-dev", "t-eval"):
        _harness_tree(harness, task)
    for name in ("claude", "py.tar.gz", "g6.whl"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "wheels").mkdir()
    assets = run_arm.Assets(harness, tmp_path / "claude", tmp_path / "py.tar.gz", tmp_path / "wheels",
                            wheel=tmp_path / "g6.whl")
    splits = {"dev": ["t-dev"], "eval": ["t-eval"], "scored": ["t-eval"], "pilot": ["t-dev"],
              "repo_of": {"t-dev": "o/a", "t-eval": "o/b"}}
    return run_arm.RunContext(c.Archive(tmp_path / "archive"), assets, splits, docker=docker, environ=environ or {},
                              key_reader=key_reader or (lambda: KEY), wheel=dict(WHEEL),
                              lesson_client=lessons if lessons is not None else FakeAnthropic())


def _run_of(ctx, arm: str, phase: str = "eval", task: str = "t-eval") -> dict:
    return ctx.archive.latest(phase, task, arm)


# --- design D: the pipeline --------------------------------------------------------------------------------------------
def test_the_self_check_prompt_is_the_lead_s_exact_words_for_every_task(tmp_path):
    assert c.SELF_CHECK == LEAD_SELF_CHECK
    seen = []
    for name in ("one", "two"):
        docker = DockerD(flag=False)
        ctx = _ctx(tmp_path / name, docker)
        (ctx.assets.harness / "tasks" / "t-eval" / "instruction.md").write_text(f"Task {name}.", encoding="utf-8")
        run_arm.run_task(ctx, "eval", "t-eval")
        prompts = [p.stdin.getvalue() for p in docker.procs]
        assert prompts[0] == f"Task {name}.".encode()       # stage 1: the task's own text
        seen.append(prompts[1])                              # stage 2, A2
        assert (_run_of(ctx, "A2")["path"] / "prompt.txt").read_text(encoding="utf-8") == LEAD_SELF_CHECK
    assert seen == [LEAD_SELF_CHECK.encode()] * 2           # the same words, whatever the task


def test_g_spends_nothing_and_keeps_the_stage_1_patch_when_g6_does_not_flag(tmp_path):
    docker = DockerD(flag=False)
    ctx = _ctx(tmp_path, docker)
    task = run_arm.run_task(ctx, "eval", "t-eval")
    assert [e for e in docker.events if e[0] == "claude"] == [("claude", False), ("claude", True)]  # A and A2 only
    g = _run_of(ctx, "G")
    assert g["record"]["acted"] is False and g["record"]["cost_usd"] == 0.0 and g["record"]["status"] == "kept"
    assert task["g6_flag"] is False and json.loads((g["path"] / "g6_check.json").read_text())["flag"] is False
    assert (g["path"] / "model.patch").read_bytes() == (_run_of(ctx, "A")["path"] / "model.patch").read_bytes()


def test_both_stage_2_passes_resume_the_same_session_with_equal_caps(tmp_path):
    docker = DockerD(flag=True)
    ctx = _ctx(tmp_path, docker)
    run_arm.run_task(ctx, "eval", "t-eval")
    runs = [args for args, _ in docker.popens]
    assert len(runs) == 3
    a2, g = (r[r.index(run_arm.CLAUDE_IN):] for r in runs[1:])
    assert a2 == g                                           # the same flags and caps; only the words differ
    caps = c.CAPS_STAGE2
    assert (caps["max_turns"], caps["max_usd_per_run"], caps["wall_clock_s"]) == (30, 1.50, 900)
    assert a2[a2.index("--max-turns") + 1] == "30" and a2[a2.index("--max-budget-usd") + 1] == "1.50"
    assert a2[a2.index("--resume") + 1] == "s1"             # the stage-1 session
    assert "--mcp-config" not in g                           # G gets G6's findings, not G6's tools
    assert docker.procs[2].stdin.getvalue().startswith(b"G6's pre-finish check")
    assert json.loads((_run_of(ctx, "G")["path"] / "command.json").read_text())["caps"] == dict(caps)


def test_no_grading_happens_before_both_stage_2_passes_finish(tmp_path):
    events = []
    docker = DockerD(flag=True, events=events)
    ctx = _ctx(tmp_path, docker)
    run_arm.run_task(ctx, "eval", "t-eval", grade=lambda phase, arm, task: events.append(("grade", arm)))
    names = [e[0] for e in events]
    last_stage2 = max(i for i, e in enumerate(events) if e == ("claude", True))
    assert [e for e in events if e[0] == "grade"] == [("grade", "A"), ("grade", "A2"), ("grade", "G")]
    assert min(i for i, n in enumerate(names) if n == "grade") > last_stage2
    run = ctx.archive.start("eval", "t-x", "task")
    run.finish({"stage2_done": False}, "error")
    with pytest.raises(RuntimeError):                        # a task whose stage 2 did not finish is never graded
        run_arm.grade_task(ctx, "eval", "t-x", evaluate=lambda *a: None)


# --- shadow learning (owner, 2026-10-24): a lesson per task from G6's own check, never given to Claude Code ---------
def _lesson(ctx, phase: str = "eval", task: str = "t-eval") -> dict:
    return ctx.archive.latest(phase, task, shadow.ARM)


def test_a_lesson_is_written_after_both_stage_2_passes_and_before_any_grade(tmp_path):
    events = []
    docker = DockerD(flag=True, events=events)
    ctx = _ctx(tmp_path, docker, lessons=FakeAnthropic(events=events))
    run_arm.run_task(ctx, "eval", "t-eval", grade=lambda phase, arm, task: events.append(("grade", arm)))
    names = [e[0] for e in events]
    # fails if the lesson is written before G's pass (it could then reach Claude Code) or after a grade exists
    assert names.count("lesson") == 1
    assert max(i for i, e in enumerate(events) if e == ("claude", True)) < names.index("lesson") < names.index("grade")
    assert _lesson(ctx)["record"]["status"] == "finished"


def test_a_lesson_sees_g6s_findings_and_gs_outcome_and_nothing_of_a_grade_or_the_task_text(tmp_path):
    finding = {"flag": True, "findings": [{"check": "visible_tests", "said": "tests/test_x.py fails"}],
               "findings_text": "- tests/test_x.py fails", "reward": "PLANTED-GRADE", "replies": {"x": "PLANTED-REPLY"}}
    g = {"status": "finished", "acted": True, "patch_sha256": "b" * 64, "resolved": "PLANTED-GRADE"}
    given = shadow.lesson_input("t-eval", finding, g, stage1_patch_sha256="a" * 64)
    # fails if a grade, the grader's output or G6's raw replies are passed on: the lesson sees G6's words only
    assert set(given) == set(shadow.INPUT_FIELDS)
    assert "PLANTED" not in json.dumps(given)
    assert given["g_changed_patch"] is True and given["checks"] == ["visible_tests"]
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=lessons)
    run_arm.run_task(ctx, "eval", "t-eval")
    sent = json.dumps(lessons.calls[0]["messages"])
    assert "tests/test_x.py fails" in sent and "Fix the bug." not in sent   # findings in; the task's own text out


def test_a_new_failure_becomes_a_candidate_and_a_recurring_one_adds_none(tmp_path):
    ctx = _ctx(tmp_path, DockerD(flag=True))
    run_arm.run_task(ctx, "eval", "t-eval")
    run_arm.run_task(ctx, "eval", "t-dev")
    first, second = _lesson(ctx)["record"], _lesson(ctx, task="t-dev")["record"]
    with shadow.store(ctx.archive, "eval") as theories:
        kept = theories.theories()
    # fails if every lesson adds a theory (no formation across tasks) or if a lesson credits or activates one
    assert len(kept) == 1 and kept[0].status == "candidate" and kept[0].confirmations == 0
    assert kept[0].source_instances == ["t-eval"] and kept[0].failure_code == "VISIBLE_TESTS_LEFT_FAILING"
    assert first["added"] == kept[0].theory_id and second["recurs"] == kept[0].theory_id and not second.get("added")
    snapshot = json.loads((_lesson(ctx, task="t-dev")["path"] / "theories.json").read_text(encoding="utf-8"))
    assert [t["theory_id"] for t in snapshot] == [kept[0].theory_id]
    # the next lesson is shown the store's lessons so far, so it can name a recurrence
    assert "VISIBLE_TESTS_LEFT_FAILING" in json.dumps(ctx.lesson_client.calls[1]["messages"])


def test_no_finding_means_no_lesson_no_model_call_and_no_spend(tmp_path):
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=False), lessons=lessons)
    task = run_arm.run_task(ctx, "eval", "t-eval")
    # fails if a model is called when G6's check found nothing to learn from
    assert lessons.calls == [] and lessons.keys == [] and _lesson(ctx) is None
    assert task["lesson"]["status"] == "skipped"


def test_the_lesson_run_keeps_the_full_api_response_and_its_cost_counts_toward_the_cap(tmp_path):
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=lessons)
    run_arm.run_task(ctx, "eval", "t-eval")
    run = _lesson(ctx)
    for name in ("lesson_input.json", "prompt.txt", "response.json", "lesson.json", "theories.json", "run.json"):
        assert (run["path"] / name).is_file(), name
    response = json.loads((run["path"] / "response.json").read_text(encoding="utf-8"))
    assert response["usage"] == lessons.usage and response["id"] == "msg_1"
    cost = c.usage_cost(lessons.usage)
    # fails if the lesson's spend is not recorded, so the cap would not count it
    assert cost > 0 and run["record"]["cost_usd"] == pytest.approx(cost) and not run["record"]["agent_started"]
    assert c.spend(ctx.archive.records()).total == pytest.approx(0.5 * 3 + cost)   # stage 1, A2 and G at $0.50 each
    assert run["record"]["model"] == c.MODEL and lessons.keys == [KEY]
    assert lessons.calls[0]["max_tokens"] == shadow.MAX_TOKENS


def test_a_lesson_never_reaches_claude_code(tmp_path):
    docker = DockerD(flag=True)
    ctx = _ctx(tmp_path, docker)
    with shadow.store(ctx.archive, "eval") as theories:
        theories.add_candidates([shadow.Theory.new("EARLIER", "SENTINEL-LESSON-TEXT", source_instances=["t-old"])])
    run_arm.run_task(ctx, "eval", "t-eval")
    # fails if a lesson is ever put in what Claude Code is given (a prompt, its flags or its environment)
    for proc, (args, env) in zip(docker.procs, docker.popens):
        assert b"SENTINEL" not in proc.stdin.getvalue() and "SENTINEL" not in json.dumps([args, env])
    given = (_run_of(ctx, "G")["path"] / "prompt.txt").read_text(encoding="utf-8")
    assert given == run_arm.REPAIR_PROMPT.format(failures="- tests/test_x.py fails")


@pytest.mark.parametrize("reply, status", [(RuntimeError("the model is down"), "error"), ("not json", "unparsed"),
                                           ('{"failure_code": "x", "instruction": ""}', "unparsed")])
def test_a_failed_or_unreadable_lesson_never_changes_the_task_and_adds_nothing(tmp_path, reply, status):
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=FakeAnthropic(replies=[reply]))
    task = run_arm.run_task(ctx, "eval", "t-eval")
    # fails if a lesson's failure stops or changes the task, or a malformed lesson enters the store
    assert task["status"] == "finished" and task["stage2_done"] is True
    assert _lesson(ctx)["record"]["status"] == status and task["lesson"]["status"] == status
    with shadow.store(ctx.archive, "eval") as theories:
        assert theories.theories() == []


def test_a_lesson_cannot_cost_more_than_its_reserve(tmp_path):
    wide = "測試失敗" * 20_000                                  # three UTF-8 bytes a character
    findings = [{"check": "visible_tests", "said": wide}]
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=lessons)
    with shadow.store(ctx.archive, "eval") as theories:
        theories.add_candidates([shadow.Theory.new(f"CODE_{i}", wide[:2000]) for i in range(80)])
    given = shadow.lesson_input("t", {"findings": findings, "findings_text": "- " + wide}, {}, "")
    with shadow.store(ctx.archive, "eval") as theories:
        text = shadow.SYSTEM + shadow.user_text(given, theories.theories())
    # fails if the findings or the store's lessons are cut by characters, not bytes, or passed uncut: a token covers
    # at least one UTF-8 byte, so the input is bounded by its bytes, and the output by MAX_TOKENS
    worst = len(text.encode("utf-8")) * c.PRICES["input"] / 1e6 + shadow.MAX_TOKENS * c.PRICES["output"] / 1e6
    assert worst <= c.RESERVE_LESSON, worst


@pytest.mark.parametrize("breaks", ["input", "start"])
def test_a_lesson_that_cannot_even_start_never_costs_the_task_its_grading(tmp_path, monkeypatch, breaks):
    graded = []
    ctx = _ctx(tmp_path, DockerD(flag=True))
    if breaks == "input":
        monkeypatch.setattr(shadow, "lesson_input", lambda *a, **k: (_ for _ in ()).throw(TypeError("not a dict")))
    else:
        start = ctx.archive.start

        def refuse(phase, task, arm, **meta):
            if arm == shadow.ARM:
                raise c.ArchiveError("the disk is full")
            return start(phase, task, arm, **meta)
        monkeypatch.setattr(ctx.archive, "start", refuse)
    task = run_arm.run_task(ctx, "eval", "t-eval", grade=lambda phase, arm, t: graded.append(arm))
    # fails if a lesson's failure outside its own run reaches run_task, which would end the task as an error after
    # all three arms finished and skip its grading
    assert task["status"] == "finished" and graded == ["A", "A2", "G"] and task["lesson"]["status"] == "error"


def test_a_lesson_s_cost_is_recorded_even_when_archiving_its_response_fails(tmp_path, monkeypatch):
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=lessons)
    write_json = c.RunFolder.write_json

    def refuse(self, name, data):
        if name == "response.json":
            raise c.ArchiveError("the disk is full")
        return write_json(self, name, data)
    monkeypatch.setattr(c.RunFolder, "write_json", refuse)
    run_arm.run_task(ctx, "eval", "t-eval")
    # fails if the cost is worked out only after the response is archived: a billed call would count as free
    assert _lesson(ctx)["record"]["cost_usd"] == pytest.approx(c.usage_cost(lessons.usage))
    assert _lesson(ctx)["record"]["status"] == "error"


def test_an_interrupted_lesson_is_ended_at_its_response_s_cost(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("eval", "t-eval", shadow.ARM)
    usage = {"input_tokens": 4000, "output_tokens": 900}
    run.write_json("response.json", {"id": "msg_1", "usage": usage})       # then the runner crashed
    run_arm.close_interrupted(archive)
    # fails if a crashed lesson is priced from a Claude Code stream it never has, so at $0
    ended = archive.latest("eval", "t-eval", shadow.ARM)["record"]
    assert ended["status"] == "interrupted" and ended["cost_usd"] == pytest.approx(c.usage_cost(usage))


def test_the_lesson_prompt_never_names_the_task_and_says_whether_the_agent_saw_the_findings(tmp_path):
    lessons = FakeAnthropic()
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=lessons)
    run_arm.run_task(ctx, "eval", "t-eval")
    # fails if the public task id is in the prompt: the one input that lets the model recall a memorised task
    assert "t-eval" not in json.dumps(lessons.calls[0]["messages"])
    given = shadow.lesson_input("t", {"findings": [{"check": "change", "said": "no file is changed"}],
                                      "findings_text": "- no file is changed"}, {"acted": False}, "")
    text = shadow.user_text(given, [])
    assert "given to the agent" not in text.split("\n\n")[0] and "not given G6's findings" in text


def test_the_store_lock_is_not_held_while_the_model_is_called(tmp_path):
    seen = []

    class Watching(FakeAnthropic):
        def create(self, **kwargs):
            free = shadow._LOCK.acquire(blocking=False)
            if free:
                shadow._LOCK.release()
            seen.append(free)
            return super().create(**kwargs)
    ctx = _ctx(tmp_path, DockerD(flag=True), lessons=Watching())
    run_arm.run_task(ctx, "eval", "t-eval")
    # fails if a slow model call holds the lock the other task's lesson needs
    assert seen == [True]


def test_the_lesson_client_has_a_short_timeout_and_no_retries_the_archive_would_not_see():
    client = shadow.anthropic_client("sk-ant-not-a-real-key")
    # fails if the SDK's defaults (600 s, two unrecorded retries) are left in place
    assert client.max_retries == 0 and client.timeout == shadow.TIMEOUT_S <= 180
    assert str(client.base_url).rstrip("/") == shadow.ANTHROPIC_URL


def test_a_relative_archive_keeps_the_store_inside_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with shadow.store(c.Archive(Path("arch")), "eval"):
        pass
    # fails if a relative archive path is resolved under G6_HOME instead (open_db's rule for a relative path)
    assert (tmp_path / "arch" / "shadow" / "eval.db").is_file()


def test_the_evaluation_tests_are_absent_from_every_container_before_grading(tmp_path):
    events = []
    docker = DockerD(flag=True, events=events)
    ctx = _ctx(tmp_path, docker)
    run_arm.run_task(ctx, "eval", "t-eval")
    names = [e[0] for e in events]
    assert names.count("oracle_check") == 4                  # before stage 1, A2, G6's check and G
    assert names.index("oracle_check") < names.index("claude")
    calls = [" ".join(a) for a in docker.calls if "v7-oracle-check" in " ".join(a)]
    assert all("/app/tests/test_new.py" in call and "/tests" in call for call in calls)
    assert run_arm.created_paths("--- /dev/null\n+++ b/a/test_n.py\n--- a/b.py\n+++ b/b.py\n") == ["a/test_n.py"]
    found = DockerD(oracle=b"present 1")                     # the grader's material in the container: stop at once
    ctx2 = _ctx(tmp_path / "found", found)
    task = run_arm.run_task(ctx2, "eval", "t-eval")
    assert task["status"] == "infra_error" and "OracleVisible" in task["error"] and not found.popens
    assert _run_of(ctx2, "A")["record"]["status"] == "error"   # the stopped run is kept, with its run.json


def test_every_run_started_ends_with_a_run_json_and_a_ledger_line_whatever_happens(tmp_path):
    for name, docker in (("cap", DockerD(stream=_stream(subtype="error_max_turns"))),
                         ("silent", DockerD(stream=_stream(result=False))),
                         ("crash", DockerD(fail_commit=True))):
        ctx = _ctx(tmp_path / name, docker)
        run_arm.run_task(ctx, "eval", "t-eval")
        ledger = ctx.archive.ledger()
        started = {line["run"] for line in ledger if line["event"] == "started"}
        ended = {line["run"] for line in ledger if line["event"] == "ended"}
        assert started and started == ended, name           # no run is left without its end
        for rel in started:
            index = json.loads((ctx.archive.root / rel / "run.json").read_text())
            assert index["status"] and set(index["files"]) <= {p.relative_to(ctx.archive.root / rel).as_posix()
                                                                for p in (ctx.archive.root / rel).rglob("*")}
    assert _run_of(_ctx_cached(tmp_path / "cap"), "A")["record"]["status"] == "cap_turns"
    assert _run_of(_ctx_cached(tmp_path / "silent"), "A")["record"]["status"] == "error"
    crash = _ctx_cached(tmp_path / "crash")
    assert _run_of(crash, "A")["record"]["status"] == "finished"
    assert _run_of(crash, "task")["record"]["status"] == "error"


def _ctx_cached(folder: Path):
    return run_arm.RunContext(c.Archive(folder / "archive"), None, {})


def test_the_key_reaches_only_the_agent_process_and_never_the_archive(tmp_path):
    docker = DockerD(flag=True, stream=_stream(text_with_key=True), patch=f"+++ b/settings.py\n+TOKEN = '{KEY}'\n")
    ctx = _ctx(tmp_path, docker)
    run_arm.run_task(ctx, "eval", "t-eval")
    for path in ctx.archive.root.rglob("*"):
        if path.is_file():         # removing the Redactor from the stream writer or the scan leaves the key here
            assert KEY not in path.read_text(encoding="utf-8", errors="replace"), path.name
    a = _run_of(ctx, "A")["record"]
    assert a["flagged"] and a["redactions"] == [{"file": "model.patch", "replaced": 1}]
    for args, env in docker.popens:
        assert KEY not in " ".join(args) and "ANTHROPIC_API_KEY" in args and env["ANTHROPIC_API_KEY"] == KEY
    assert all(KEY not in " ".join(map(str, call)) for call in docker.calls)


def test_the_runner_never_reads_the_environment_for_the_key(tmp_path, monkeypatch):
    # built at runtime so no key-shaped literal sits in this file (the export scan's anthropic-key rule)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "from-the-environment-0000000000")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))       # an empty key file: g6.core.keys has no key
    ctx = _ctx(tmp_path, DockerD())
    ctx.key_reader = None
    from g6.core.keys import KeyMissing

    with pytest.raises(KeyMissing):          # reading os.environ instead of g6.core.keys would start the run
        run_arm.run_task(ctx, "eval", "t-eval")


def test_a_task_past_the_cap_is_not_started_and_the_key_is_not_read(tmp_path):
    def no_key():
        raise AssertionError("the key was read for a task that cannot start")

    ctx = _ctx(tmp_path, DockerD(), environ={"G6_BENCH_MAX_USD": "8"}, key_reader=no_key)
    assert run_arm.run_task(ctx, "eval", "t-eval")["status"] == "not_started"      # $8.60 does not fit under $8


def test_the_wheel_is_built_from_a_named_ref_and_every_run_carries_its_commit(tmp_path):
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, b"1234567890abcdef1234567890abcdef12345678\n", b"")
        if "archive" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(_tar_with_pyproject())
        if argv[0] == "uv":
            outdir = Path(argv[argv.index("--out-dir") + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "g6-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(argv, 0, b"built", b"")

    archive = c.Archive(tmp_path / "archive")
    info = run_arm.build_wheel("release/v0.1", archive, repo=tmp_path, runner=fake)
    assert info["ref"] == "release/v0.1" and info["commit"].startswith("12345678")
    assert info["sha256"] == c.sha256_file(Path(info["path"])) and Path(info["path"]).is_relative_to(archive.root)
    assert run_arm.wheel_info(archive)["commit"] == info["commit"]
    assert any("archive" in a and info["commit"] in a for a in seen)   # the archive is of the resolved commit
    ctx = _ctx(tmp_path / "r", DockerD())
    run_arm.run_task(ctx, "eval", "t-eval")
    for arm in ("task", "A", "A2", "G"):
        started = next(line for line in ctx.archive.ledger() if line["event"] == "started" and line["arm"] == arm)
        assert started["g6_wheel"]["commit"] == WHEEL["commit"], arm


def _tar_with_pyproject() -> bytes:
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"[project]\nname='g6'\n"
        info = tarfile.TarInfo("oss/pyproject.toml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_three_runs_per_task_are_reserved_together_and_two_tasks_run_at_a_time(tmp_path):
    live, peak, lock = [], [], threading.Lock()

    def fake_task(ctx, phase, task, *, admitted=False, grade=None):
        with lock:
            live.append(task)
            peak.append(len(live))
        runs = {arm: ctx.archive.start(phase, task, arm) for arm in ("task", "A", "A2", "G")}
        time.sleep(0.1)
        for arm, cost in (("A", 1.0), ("A2", 0.5), ("G", 0.5)):
            runs[arm].finish({"cost_usd": cost, "agent_started": True}, "finished")
        runs["task"].finish({"stage2_done": True}, "finished")
        with lock:
            live.remove(task)
        return {"status": "finished"}

    ids = [f"s{i}" for i in range(7)]
    ctx = run_arm.RunContext(c.Archive(tmp_path / "w1"), None, {}, environ={})
    out = run_arm.batch_tasks(ctx, "eval", ids[:5], run_task=fake_task, jobs=2)
    assert max(peak) == 2 and out["tasks_completed"] == 5
    live, peak = [], []
    ctx = run_arm.RunContext(c.Archive(tmp_path / "w2"), None, {}, environ={"G6_BENCH_MAX_USD": "16.99"})
    run_arm.batch_tasks(ctx, "eval", ids[:3], run_task=fake_task, jobs=2)
    assert max(peak) == 1                                    # two reservations of $8.60 do not fit under $16.99
    # $2 a task under $17: a task is admitted while spent + $8.60 in flight + $8.60 fits, so five complete and two
    # are not reached, and the cut is reported
    ctx = run_arm.RunContext(c.Archive(tmp_path / "w3"), None, {}, environ={"G6_BENCH_MAX_USD": "17"})
    out = run_arm.batch_tasks(ctx, "eval", ids, run_task=fake_task, jobs=2)
    assert c.spend(ctx.archive.records()).total <= 17 and out["stopped"]
    assert out["tasks_completed"] == 5 and out["not_run_tasks"] == ids[5:]


def test_an_interrupted_run_is_ended_at_its_stream_s_cost_and_never_run_again(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("eval", "t1", "A")
    with run.stream("stream.jsonl") as fh:
        fh.write(json.dumps(_stream()[1]) + "\n")
    assert run_arm.attempted(archive, "eval", "t1")          # still running: counted as attempted
    run_arm.close_interrupted(archive)
    rec = archive.latest("eval", "t1", "A")["record"]
    assert rec["status"] == "interrupted" and rec["cost_usd"] == pytest.approx(
        c.usage_cost({"input_tokens": 100, "output_tokens": 50}))
    assert run_arm.attempted(archive, "eval", "t1") and c.spend(archive.records()).in_flight == {}


def test_a_stage_1_the_api_refused_before_any_token_is_not_an_attempt(tmp_path):
    # The pilot of 2026-10-25: Claude Code started, the API refused every request (a key not scoped to a workspace),
    # nothing went in, nothing came out, nothing was spent. No outcome exists to re-roll, so the task may start again.
    archive = c.Archive(tmp_path)
    zero = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    archive.start("pilot", "t1", "A").finish({"agent_started": True, "cost_usd": 0.0, "usage": zero}, "error")
    assert not run_arm.attempted(archive, "pilot", "t1")
    # The model read the task: an attempt, whatever came of it.
    archive.start("pilot", "t2", "A").finish({"agent_started": True, "cost_usd": 0.004,
                                              "usage": {**zero, "input_tokens": 900}}, "error")
    assert run_arm.attempted(archive, "pilot", "t2")
    # No usage on record (an interrupted run): counted, as before.
    archive.start("pilot", "t3", "A").finish({"agent_started": True, "cost_usd": 0.0}, "interrupted")
    assert run_arm.attempted(archive, "pilot", "t3")


# --- the stream ---------------------------------------------------------------------------------------------------------
def test_the_tally_counts_each_message_once_and_prefers_claude_codes_cost():
    t = run_arm.Tally()
    msg = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1000, "output_tokens": 100}}}
    t.feed(msg)
    t.feed(msg)                                    # Claude Code repeats a message per content block
    assert t.model_calls == 1 and t.estimated_usd == pytest.approx((2.0 * 1000 + 10.0 * 100) / 1e6)
    assert t.cost_usd == t.estimated_usd           # no result record: the stream's count
    t.reported = 0.9
    assert t.cost_usd == 0.9


def test_the_stream_stops_at_the_deadline_and_at_the_cost_cap():
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
    clock = lambda: next(ticks, 99.0)  # noqa: E731
    lines = [(json.dumps({"type": "system"}) + "\n").encode()]
    assert run_arm.read_stream(iter(lines * 3), run_arm.Tally(), deadline=5.0, max_turns=9, max_usd=9,
                               write=lambda s: None, clock=clock) in ("timeout", "")
    big = {"type": "assistant", "message": {"id": "m", "usage": {"output_tokens": 2_000_000}}}
    stop = run_arm.read_stream(iter([(json.dumps(big) + "\n").encode()]), run_arm.Tally(), deadline=1e12,
                               max_turns=9, max_usd=4.0, write=lambda s: None)
    assert stop == "cap_cost"                      # removing the estimated_usd check in read_stream returns ""
    assert run_arm.status_of("", run_arm.Tally(result={"subtype": "error_max_turns", "is_error": True})) == "cap_turns"


# --- the network rule ---------------------------------------------------------------------------------------------------
def test_the_proxy_lets_through_only_the_model_endpoint():
    allow = egress_proxy.parse_allow(["api.anthropic.com:443"])
    assert egress_proxy.decide(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n", allow)[0]
    assert not egress_proxy.decide(b"CONNECT github.com:443 HTTP/1.1\r\n\r\n", allow)[0]
    assert not egress_proxy.decide(b"CONNECT api.anthropic.com:80 HTTP/1.1\r\n\r\n", allow)[0]
    assert not egress_proxy.decide(b"GET http://api.anthropic.com/ HTTP/1.1\r\n\r\n", allow)[0]
    assert not egress_proxy.decide(b"garbage\r\n\r\n", allow)[0]


def test_the_proxy_end_to_end_on_this_machine_only(tmp_path):
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    up_port = upstream.getsockname()[1]

    def echo():
        conn, _ = upstream.accept()
        conn.sendall(conn.recv(5))
        conn.close()

    threading.Thread(target=echo, daemon=True).start()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    log = tmp_path / "egress.jsonl"
    loop = asyncio.new_event_loop()
    allow = frozenset({("127.0.0.1", up_port)})
    threading.Thread(target=lambda: loop.run_until_complete(egress_proxy.serve(port, allow, str(log), "127.0.0.1")),
                     daemon=True).start()
    for _ in range(50):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            time.sleep(0.05)
    s.sendall(f"CONNECT 127.0.0.1:{up_port} HTTP/1.1\r\n\r\n".encode())
    assert s.recv(64).startswith(b"HTTP/1.1 200")
    s.sendall(b"hello")
    assert s.recv(5) == b"hello"
    s.close()
    t = socket.create_connection(("127.0.0.1", port), timeout=2)
    t.sendall(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
    assert t.recv(64).startswith(b"HTTP/1.1 403")
    t.close()
    entries = [json.loads(x) for x in log.read_text().splitlines()]
    assert [e["allowed"] for e in entries] == [True, False]   # the server thread is a daemon; it ends with the run


# --- G6's check ---------------------------------------------------------------------------------------------------------
def test_g6s_check_flags_what_its_shipped_checks_find_and_nothing_else(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("from pkg.mod import f\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    passed = lambda repo, tests, **kw: {"verdict": "passed", "tests": tests}  # noqa: E731
    failed = lambda repo, tests, **kw: {"verdict": "failed", "said": "1 failed", "output": "E assert 2 == 1"}  # noqa: E731
    assert g6check.check(repo, run_tests=passed)["findings"][0]["check"] == "change"     # nothing changed
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    clean = g6check.check(repo, run_tests=passed)
    assert clean["flag"] is False and clean["tests"] == ["test_mod.py"]   # G6 derives the visible test itself
    flagged = g6check.check(repo, run_tests=failed)
    assert flagged["flag"] is True and "E assert 2 == 1" in g6check.findings_text(flagged["findings"])
    assert flagged["replies"]["workforce.repo_tests.run"]["verdict"] == "failed"   # G6's reply, as returned
    (repo / "pkg" / "mod.py").write_text("def f(:\n", encoding="utf-8")
    assert any(f["check"] == "parse" for f in g6check.check(repo, run_tests=passed)["findings"])
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "test_mod.py").write_text("from pkg.mod import f\n\ndef test_f():\n    assert True\n", encoding="utf-8")
    shortcut = g6check.check(repo, run_tests=passed)
    assert shortcut["flag"] is True and {f["check"] for f in shortcut["findings"]} & {"shortcut", "weakening"}
    unplaced = lambda repo, tests, **kw: {"verdict": "collection_error"}  # noqa: E731
    (repo / "test_mod.py").write_text("from pkg.mod import f\n\ndef test_f():\n    assert f() == 1\n",
                                      encoding="utf-8")
    assert g6check.check(repo, run_tests=unplaced)["flag"] is False   # an environment G6 cannot place is no flag


# --- the analysis -------------------------------------------------------------------------------------------------------
def test_the_gaming_scan_finds_test_edits_oracle_access_history_and_network(tmp_path):
    archive = c.Archive(tmp_path)
    task_run = archive.start("eval", "t1", "task")
    task_run.write("egress.jsonl", '{"host": "github.com", "port": 443, "allowed": false}\n')
    task_run.finish({"stage2_done": True}, "finished")
    run = archive.start("eval", "t1", "A")
    run.write_json("environment.json", {"git": {"history_clean": True}})
    run.write("model.patch", "+++ b/openlibrary/tests/test_x.py\n+assert True\n")
    uses = [{"name": "Bash", "input": {"command": "git log --all --oneline"}},
            {"name": "Bash", "input": {"command": "cat /tests/config.json"}},
            {"name": "Bash", "input": {"command": "curl https://github.com/x/y/commit/abc.patch"}},
            {"name": "Edit", "input": {"file_path": "/app/src/foo_test.go"}},
            {"name": "WebFetch", "input": {"url": "https://example.com"}}]
    run.write("stream.jsonl", json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", **u} for u in uses]}}) + "\n")
    run.finish({"cost_usd": 1.0}, "finished")
    kinds = {h["kind"] for h in analyse.scan_run(run.path, task_run.path)}
    assert kinds == {"test_edit", "oracle_access", "git_history", "network"}
    assert analyse.cmd_gaming(archive)["runs_with_matches"] == 1


def test_h1_h2_and_holm_on_known_tables():
    assert analyse.fisher_one_sided(3, 1, 1, 3) == pytest.approx(17 / 70)     # the tea-tasting table
    assert analyse.fisher_one_sided(4, 0, 0, 4) == pytest.approx(1 / 70)
    assert analyse.fisher_one_sided(0, 4, 4, 0) == pytest.approx(1.0)
    assert analyse.fisher_one_sided(8, 1, 1, 7) == pytest.approx((9 * 8 + 1) / 24310)   # C(9,8)C(8,1)+1 of C(17,9)
    assert analyse.sign_p(8, 0) == pytest.approx(1 / 256) and analyse.sign_p(7, 1) == pytest.approx(9 / 256)
    assert analyse.holm({"H1": 0.01, "H2": 0.04}) == {"H1": 0.02, "H2": 0.04}
    assert analyse.holm({"H1": 0.03, "H2": 0.02}) == {"H1": 0.04, "H2": 0.04}


def _d_rows(archive: c.Archive, rows: list[tuple[bool, bool, bool, bool]], a_status: str = "finished") -> list[str]:
    """(A resolved, A2 resolved, G resolved, G6 flagged) per completed task, archived as the pipeline archives them."""
    ids = []
    for i, (a, a2, g, flag) in enumerate(rows):
        task = f"s{i:02d}"
        ids.append(task)
        task_run = archive.start("eval", task, "task")
        for arm, ok, cost in (("A", a, 2.0), ("A2", a2, 1.0), ("G", g, 1.0 if flag else 0.0)):
            status = a_status if (arm == "A" and i == 0) else "finished"
            run = archive.start("eval", task, arm)
            run.finish({"cost_usd": cost, "released": True, "agent_started": True, "g6_flag": flag}, status)
            grade = archive.start("eval", task, f"grade-{arm}")
            grade.finish({"resolved": ok}, "finished")
        task_run.finish({"stage2_done": True, "g6_flag": flag}, "finished")
    return ids


def test_the_report_needs_a_person_to_have_reviewed_the_gaming_matches(tmp_path):
    archive = c.Archive(tmp_path)
    with pytest.raises(SystemExit):
        analyse.report_d(archive, tmp_path / "out", {"scored": []})
    c.write_json(tmp_path / "gaming_review.json", {"reviewed_by": "auto:script", "void": []})
    with pytest.raises(SystemExit):
        analyse.report_d(archive, tmp_path / "out", {"scored": []})


def test_the_design_d_report_tests_h1_and_h2_names_the_comparison_and_counts_every_run(tmp_path):
    archive = c.Archive(tmp_path / "archive")
    rows = ([(False, False, True, True)] * 6 + [(False, False, False, True)] * 2 + [(False, False, False, False)]
            + [(True, True, True, False)] * 6 + [(True, True, False, True)] + [(True, False, True, False)])
    ids = _d_rows(archive, rows, a_status="refused")         # task s00's stage 1 refused: still a row
    stopped = archive.start("eval", "s98", "task")            # a task that stopped part-way
    archive.start("eval", "s98", "A").finish({"cost_usd": 4.1, "agent_started": True}, "cap_cost")
    stopped.finish({"stage2_done": False, "error": "x"}, "error")
    ids += ["s98", "s99"]                                     # s99 was never reached
    c.write_json(archive.root / "gaming_review.json", {"reviewed_by": "Dan Busch", "void": []})
    report = analyse.report_d(archive, tmp_path / "out", {"scored": ids, "repo_of": {t: "r" for t in ids}})
    assert report["completed_tasks"] == 17 and report["not_completed"] == ["s98"] and report["not_reached"] == ["s99"]
    h1 = report["H1"]                                        # flagged: 8 of 9 failed stage-1 patches, 1 of 8 passed
    assert (h1["flagged_failed"], h1["failed"], h1["flagged_passed"], h1["passed"]) == (8, 9, 1, 8)
    assert h1["p_one_sided"] == pytest.approx(analyse.fisher_one_sided(8, 1, 1, 7), abs=1e-6)
    h2 = report["H2"]                                        # G resolves and A2 not: 6 + 1; the reverse: 1
    assert (h2["wins"], h2["losses"]) == (7, 1) and h2["p_one_sided"] == pytest.approx(9 / 256, abs=1e-6)
    assert report["holm"] == analyse.holm({"H1": h1["p_one_sided"], "H2": h2["p_one_sided"]})
    assert report["secondary"]["breaks"] == {"A2": 1, "G": 1}
    assert report["secondary"]["false_flag_rate"] == pytest.approx(1 / 8)
    assert report["headline"].startswith(analyse.HEADLINE) and analyse.HEADLINE == (
        "G6's check-and-fix against Claude Code checking itself with the same budget")
    assert ("not significant" in report["headline"]) == (report["holm"]["H2"] > 0.05)
    assert report["runs_started"]["by_status"]["cap_cost"] == 1 and report["runs_started"]["by_status"]["refused"] == 1
    assert report["runs_started"]["total"] == 17 * 7 + 2        # every run started is counted, stopped ones too
    assert report["shadow_learning"]["lessons"] == 0           # no lesson runs in this archive


def test_the_report_summarises_the_lessons_and_tests_nothing_on_them(tmp_path):
    archive = c.Archive(tmp_path)
    for task, body, status in [("t1", {"failure_code": "A_B", "added": "th1", "cost_usd": 0.02}, "finished"),
                               ("t2", {"failure_code": "A_B", "recurs": "th1", "cost_usd": 0.03}, "finished"),
                               ("t3", {"why": "not JSON", "cost_usd": 0.01}, "unparsed"),
                               ("t4", {"failure_code": "C_D", "added": "th2", "cost_usd": 0.02}, "finished")]:
        archive.start("eval", task, shadow.ARM).finish(body, status)
    summary = shadow.summary(archive, "eval", ["t1", "t2", "t3"])   # t4 is not a completed task
    # fails if lessons of tasks outside the analysis are counted, or recurrences are not tallied by failure code
    assert summary["lessons"] == 3 and summary["by_status"] == {"finished": 2, "unparsed": 1}
    assert summary["candidates"] == 1 and summary["recurrences"] == {"A_B": 1}
    assert summary["cost_usd"] == pytest.approx(0.06)
    assert "never given to Claude Code" in summary["note"] and "p_" not in json.dumps(summary)   # descriptive only


def test_the_verdict_follows_the_registered_order():
    rows = {f"t{i}": {"A": i < 10} for i in range(20)}       # stage 1 fails half
    review = {"reviewed_by": "Dan Busch"}
    h1, h2 = {"p_one_sided": 0.001}, {"wins": 9, "losses": 0}
    v = analyse.verdict_d({"H1": 0.002, "H2": 0.004}, h1, h2, rows, review, oracle_ok=True)
    assert v["verdict"] == "mechanism_demonstrated"
    assert analyse.verdict_d({"H1": 0.002, "H2": 0.2}, h1, {"wins": 3, "losses": 1}, rows, review,
                             oracle_ok=True)["verdict"] == "component_validated_no_transfer"
    assert analyse.verdict_d({"H1": 0.002, "H2": 0.004}, h1, h2, rows, review,
                             oracle_ok=False)["verdict"] == "oracle_inadequate"
    assert "provisional" in analyse.verdict_d({"H1": 0.5, "H2": 0.5}, h1, {"wins": 1, "losses": 1}, rows,
                                              review)["why"]
    assert analyse.verdict_d({"H1": 1, "H2": 1}, h1, {"wins": 0, "losses": 9}, rows, review,
                             oracle_ok=True)["verdict"] == "unsafe_or_regressive"
    assert analyse.verdict_d({"H1": 0.002, "H2": 0.004}, h1, h2, {f"t{i}": {"A": True} for i in range(20)}, review,
                             oracle_ok=True)["verdict"] == "insufficient_failure_opportunities"


def test_the_projection_follows_the_batch_rule():
    assert analyse.project(0.0, 0.0, 0.0, 0.0) == 60                 # free runs: every scored task
    every = analyse.project(4.0, 1.5, 1.5, 1.0)                       # $7 a task at the caps, after a $28 pilot
    per = 7.0 + c.LESSON_ESTIMATE_USD                                  # and a lesson for every flagged task
    pilot = 4 * per
    # fails if the projection leaves out the lesson a flagged task also pays for
    assert pilot + every * per <= 180 < pilot + every * per + c.TASK_RESERVE   # it stops when the next cannot fit
