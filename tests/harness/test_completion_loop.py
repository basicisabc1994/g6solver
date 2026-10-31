# SPDX-License-Identifier: Apache-2.0
"""The completion loop: its confined runs are replaced by recorded results, which say nothing about confinement, and its
sessions and its critic by fakes. No model is called and nothing a model wrote runs."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from g6.core import host_models, keys
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.workforce import repo_tests

BASE = "def double(x):\n    return x + 2\n"
FIXED = "def double(x):\n    return x * 2\n"
TASK = "double must return twice its argument\n\nIt returns x + 2."
TREE = json.dumps({"goal": "g", "dimensions": {"a": ["1", "2"], "b": ["1", "2"]}, "branches": []})
PROBE = "from mod import double\n\n\ndef test_cross():\n    assert double(2) == 4\n"
CASES = {f"test_cross[{i}]": "failed" for i in range(4)} | {f"test_random[{i}]": "failed" for i in range(300)}
PASSING = dict.fromkeys(CASES, "passed")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class Scouts:
    """Stands for the scouts' sessions: each leaves ``files`` where it may write, and what it was given is kept."""

    def __init__(self, files=None, tree=TREE):
        self.files, self.tree, self.calls = files or {"test_p.py": PROBE}, tree, []

    def __call__(self, prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        self.calls.append({"prompt": prompt, "cwd": cwd, "where": where, "mod": (cwd / "mod.py").read_text(),
                           "history": (cwd / ".git").exists(), "seconds": seconds})
        for name, text in self.files.items():
            (where / "probes" / name).write_text(text, encoding="utf-8")
        (where / "tree.json").write_text(self.tree, encoding="utf-8")
        return "done"


def ran(placed: str, cases: dict[str, str], **more) -> dict:
    """A run of the file at ``placed``, as ``repo_tests`` reports it."""
    failing = [name for name, got in cases.items() if got == "failed"]
    return {"verdict": "failed" if failing else "passed", "said": "", "rootdir": "", "loaded_outside": {},
            "loaded_complete": True, "os_errors": [],
            "test_outcomes": {f"{placed}::{name}": {"setup": "passed", "call": got, "teardown": "passed"}
                              for name, got in cases.items()},
            "failure_messages": {f"{placed}::{name}": f"assert {name}" for name in failing}, **more}


class Runs:
    """Stands for ``_run_check``: what each case gives at the base commit, and on the working tree now."""

    def __init__(self, monkeypatch, base=None, now=None):
        self.base, self.now, self.calls = base or CASES, now or PASSING, []
        monkeypatch.setattr(vs, "_run_check", self)

    def __call__(self, change, placed, source, node, *, at_base, timeout_s, roots=()):
        self.calls.append(("base" if at_base else "patch", placed, source, timeout_s))
        got = self.base if at_base else self.now
        return got(placed) if callable(got) else ran(placed, got)

    def on_patch(self) -> int:
        return sum(call[0] == "patch" for call in self.calls)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A repository with the bug at its base commit, and G6's boundary and final review stood in for."""
    home = tmp_path / "g6"
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))     # the agent's scout's folder, kept to this test
    monkeypatch.setattr(proposer, "boundary", lambda root: "")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mod.py").write_text(BASE, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "mod.py")
    git(root, "commit", "-qm", "base")
    reviews = []

    def review(repo, **asked):
        reviews.append(asked)
        return {"assessment": {"outcome": "insufficient_evidence", "report": "THE REVIEW"}}

    monkeypatch.setattr(loop, "review_patch", review)

    class World:
        repo, base, session, reviewed = root, git(root, "rev-parse", "HEAD"), home / "completion_loop" / "s1", reviews

        def edit(self, text: str = FIXED) -> None:
            (root / "mod.py").write_text(text, encoding="utf-8")

        def advance(self, scouts=None, critic=None, task=TASK):
            return loop.advance(root, task, self.base, self.session, scout=scouts or Scouts(), critic=critic)

        def state(self) -> dict:
            return json.loads((self.session / "state.json").read_text(encoding="utf-8"))

    return World()


def test_nothing_is_tested_or_spent_when_no_python_changed(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    (world.repo / "notes.md").write_text("a note", encoding="utf-8")
    step = world.advance(scouts)
    assert (step.action, step.text) == (loop.STOP, "")
    assert not scouts.calls and not runs.calls


def test_scouts_read_the_base_commit_without_the_fix_or_the_history(world, monkeypatch):
    Runs(monkeypatch)
    scouts = Scouts()
    world.edit()
    world.advance(scouts)
    assert len(scouts.calls) == 3    # the agent's scout and L9's two hidden ones
    assert {call["mod"] for call in scouts.calls} == {BASE}
    assert not any(call["history"] for call in scouts.calls)
    assert len({str(call["cwd"]) for call in scouts.calls}) == 3    # none sees another's copy


def test_a_scout_is_told_the_task_and_never_told_to_run_anything_itself(world, monkeypatch):
    Runs(monkeypatch)
    scouts = Scouts()
    world.edit()
    world.advance(scouts)
    for call in scouts.calls:
        assert "double must return twice its argument" in call["prompt"]
        assert call["where"].as_posix() in call["prompt"]
        assert "/opt/we" not in call["prompt"] and ".venv/bin/python" not in call["prompt"]
        assert "run them from the repository" not in call["prompt"]


def test_hidden_work_is_outside_the_repository_and_named_nowhere_the_agent_reads(world, monkeypatch):
    Runs(monkeypatch, now=dict(CASES))
    scouts = Scouts()
    world.edit()
    step = world.advance(scouts)
    held = [call["where"] for call in scouts.calls[1:]]
    assert step.action == loop.CONTINUE
    assert all(not where.is_relative_to(world.repo) for where in held)
    assert not any(where.as_posix() in step.text or str(where) in step.text for where in held)
    assert scouts.calls[0]["where"].as_posix() in step.text    # the agent's own scout is the one it may read
    assert not scouts.calls[0]["where"].is_relative_to(world.session)    # so nothing beside it leads to the rest


def test_failures_go_back_with_the_repair_text_and_the_fixer_only_once(world, monkeypatch):
    Runs(monkeypatch, now=dict(CASES))
    shipped = loop.harness()
    scouts = Scouts()
    world.edit()
    first = world.advance(scouts)
    assert first.action == loop.CONTINUE
    assert "assert test_cross[0]" in first.text and "A case that passed before your change" in first.text
    assert shipped.fixer[:40] in first.text
    assert "/opt/we" not in first.text and "run them from the repository" not in first.text
    world.edit(FIXED + "# again\n")
    second = world.advance(scouts)
    assert second.action == loop.CONTINUE
    assert shipped.fixer[:40] not in second.text


def test_the_scouts_run_once_and_their_base_run_once(world, monkeypatch):
    runs = Runs(monkeypatch, now=dict(CASES))
    scouts = Scouts()
    world.edit()
    world.advance(scouts)
    world.edit(FIXED + "# again\n")
    world.advance(scouts)
    assert len(scouts.calls) == 3
    assert sum(call[0] == "base" for call in runs.calls) == 3


def test_a_repair_that_holds_stops_the_loop_with_the_report(world, monkeypatch):
    runs = Runs(monkeypatch, now=dict(CASES))
    world.edit()
    assert world.advance().action == loop.CONTINUE
    runs.now = dict(PASSING)
    world.edit(FIXED + "# repaired\n")
    step = world.advance()
    assert step.action == loop.STOP
    assert "THE REVIEW" in step.text
    assert f"no counterexample found in {3 * len(CASES)} probes" in step.text.lower()


def test_the_loop_stops_when_the_agent_leaves_the_code_as_it_was(world, monkeypatch):
    runs = Runs(monkeypatch, now=dict(CASES))
    world.edit()
    assert world.advance().action == loop.CONTINUE
    checks = runs.on_patch()
    step = world.advance()
    assert step.action == loop.STOP
    assert "left the code as it was" in step.text
    assert runs.on_patch() == checks     # no new run: the code is the code that failed


def test_at_most_three_repairs_go_back(world, monkeypatch):
    runs = Runs(monkeypatch, now=dict(CASES))
    for repair in range(loop.MAX_REPAIRS):
        world.edit(FIXED + f"# {repair}\n")
        assert world.advance().action == loop.CONTINUE
    world.edit(FIXED + "# the last\n")
    step = world.advance()
    assert step.action == loop.STOP
    assert runs.on_patch() == 3 * (loop.MAX_REPAIRS + 1)
    assert "still fail" in step.text and "no counterexample" not in step.text.lower()


def test_the_loops_time_is_held_across_hooks(world, monkeypatch):
    Runs(monkeypatch, now=dict(CASES))
    world.edit()
    world.advance()
    state = world.state()
    state["started"] = time.time() - loop.LOOP_S - 1
    (world.session / "state.json").write_text(json.dumps(state), encoding="utf-8")
    world.edit(FIXED + "# late\n")
    step = world.advance()
    assert step.action == loop.STOP
    assert "time ran out" in step.text


def test_once_it_has_stopped_the_loop_is_done_for_the_session(world, monkeypatch):
    runs = Runs(monkeypatch)
    world.edit()
    assert "THE REVIEW" in world.advance().text
    calls = len(runs.calls)
    world.edit(FIXED + "# later\n")
    step = world.advance()
    assert (step.action, step.text) == (loop.STOP, "")
    assert len(runs.calls) == calls


def test_a_set_that_exercised_nothing_on_the_base_is_left_out(world, monkeypatch):
    def base(placed: str) -> dict:
        return ran(placed, dict.fromkeys(CASES, "skipped"))

    runs = Runs(monkeypatch, base=base)
    world.edit()
    step = world.advance()
    assert step.action == loop.STOP
    assert runs.on_patch() == 0
    assert "exercised nothing" in step.text
    assert "no counterexample" not in step.text.lower()     # nothing ran, so nothing can be said


def test_the_probes_never_reach_the_review_as_checks(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    world.advance()
    [asked] = world.reviewed
    assert asked["requirements"] == [{"id": "task", "text": TASK, "checks": []}]
    assert not asked.get("proposed_checks") and not asked.get("template_instances")


def test_the_report_never_says_the_patch_is_right(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    text = world.advance().text
    for word in ("verified", "proved", "proven", "experimental", "correct"):
        assert word not in text.lower()


def test_without_a_boundary_the_stage_is_refused_and_no_model_asked(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    world.edit()
    step = world.advance(scouts)
    assert step.action == loop.STOP
    assert "no backend here" in step.text and "did not run" in step.text
    assert not scouts.calls and not runs.calls


def test_a_run_refused_for_want_of_a_boundary_stops_the_loop_and_says_so(world, monkeypatch):
    def refused(*args, **kwargs):
        raise repo_tests.RepoRefused(ISOLATION_UNAVAILABLE, "no boundary now")

    monkeypatch.setattr(vs, "_run_check", refused)
    world.edit()
    step = world.advance()
    assert step.action == loop.STOP
    assert "no boundary now" in step.text
    assert not world.reviewed


def test_the_scouts_are_not_started_without_the_persons_approval(world, monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {})
    monkeypatch.setenv("G6_LLM_PROVIDER", "claude_code")     # an agent's environment is not the person's approval
    runs = Runs(monkeypatch)
    world.edit()
    step = loop.advance(world.repo, TASK, world.base, world.session)
    assert step.action == loop.STOP
    assert "G6_LLM_PROVIDER=claude_code" in step.text
    assert not runs.calls


def test_the_persons_line_in_the_key_file_approves_the_scouts(monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {"G6_LLM_PROVIDER": "claude_code", "G6_LLM_MODEL": "a-model"})
    host = loop.scout_host()
    assert host.__self__.model == "a-model"


def test_codex_as_the_critic_needs_its_own_line_in_the_key_file(monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {})
    monkeypatch.setenv(loop.CRITIC_LINE, "codex")
    assert loop.critic_host() is None
    monkeypatch.setattr(keys, "read", lambda: {loop.CRITIC_LINE: "codex"})
    assert isinstance(loop.critic_host(), host_models.CodexCaller)


def test_a_refused_scout_is_told_why_once_and_its_probes_still_count(world, monkeypatch):
    Runs(monkeypatch, base=dict.fromkeys(CASES, "passed"))    # reproduces nothing, so the gate refuses it
    scouts = Scouts()
    world.edit()
    world.advance(scouts)
    assert len(scouts.calls) == 6
    assert "The runner checked your work and refused it" in scouts.calls[1]["prompt"]
    assert "none fails: reproduce the issue's symptom" in scouts.calls[1]["prompt"]


def test_a_failure_that_is_a_refused_access_is_never_sent_as_a_failure_of_the_code(world, monkeypatch):
    def now(placed: str) -> dict:
        found = ran(placed, {**PASSING, "test_cross[0]": "failed"})
        return {**found, "os_errors": [f"{placed}::test_cross[0]"]}

    Runs(monkeypatch, now=now)
    world.edit()
    step = world.advance()
    assert step.action == loop.STOP


def test_a_run_of_code_from_outside_its_copy_says_nothing_of_the_patch(world, monkeypatch):
    def now(placed: str) -> dict:
        return {**ran(placed, PASSING), "loaded_outside": {"mod": "site-packages/mod.py"}, "loaded_complete": True}

    Runs(monkeypatch, now=now)
    world.edit()
    step = world.advance()
    assert step.action == loop.CONTINUE
    assert "no longer run on your code" in step.text


def test_a_file_that_is_no_test_file_or_declares_a_plugin_never_runs(world, monkeypatch):
    runs = Runs(monkeypatch)
    files = {"test_p.py": PROBE, "conftest.py": "x = 1\n", "test_q.py": "pytest_plugins = ['evil']\n", "helper.py": "y = 2\n"}
    world.edit()
    world.advance(Scouts(files))
    assert {Path(call[1]).name for call in runs.calls} == {"test_p.py"}


def test_each_probe_file_runs_confined_by_gs_own_runner_in_turn(world, monkeypatch):
    runs = Runs(monkeypatch)
    world.edit()
    world.advance()
    assert all(call[3] == loop.RUN_S for call in runs.calls)
    assert all(re.search(r"g6_check_[0-9a-f]{12}/test_p\.py$", call[1]) for call in runs.calls)


def defect(said_rule: str = "the rule") -> FakeLLM:
    def reply(system: str, user: str) -> str:
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        return (f"READ: {proof}\nRULE: {said_rule}\nVERDICT: defect\nWHY: because\n```python\n"
                "def test_critic():\n    assert False\n```\n")

    return FakeLLM(reply)


def test_the_critic_reads_the_finished_patch_once_and_its_failing_test_goes_back(world, monkeypatch):
    def now(placed: str) -> dict:
        return ran(placed, {"test_critic": "failed"} if "test_critic.py" in placed else PASSING)

    Runs(monkeypatch, now=now)
    critic = defect()
    world.edit()
    step = world.advance(critic=critic)
    assert step.action == loop.CONTINUE
    assert "A reviewer from another team read your patch" in step.text and "the rule" in step.text
    world.edit(FIXED + "# again\n")
    world.advance(critic=critic)
    assert len(critic.calls) == 1
    system, user = critic.calls[0]
    assert system == loop.harness().critic
    assert TASK in user and "x * 2" in user and "parent/mod.py" in user and "x + 2" in user


def test_a_critics_test_that_will_not_run_says_nothing_of_the_patch(world, monkeypatch):
    def now(placed: str) -> dict:
        return {"verdict": "collection_error", "said": "no"} if "test_critic.py" in placed else ran(placed, PASSING)

    Runs(monkeypatch, now=now)
    world.edit()
    assert world.advance(critic=defect()).action == loop.STOP


def test_a_critic_that_fails_is_recorded_and_the_loop_goes_on(world, monkeypatch):
    def broken(system: str, user: str) -> str:
        raise LLMError("no such program")

    Runs(monkeypatch)
    world.edit()
    step = world.advance(critic=FakeLLM(broken))
    assert step.action == loop.STOP
    assert "no such program" in step.text and "THE REVIEW" in step.text


def test_with_no_critic_the_report_says_none_read_the_patch(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    assert "another model family" in world.advance().text


def test_a_scout_that_fails_is_recorded_and_the_others_go_on(world, monkeypatch):
    Runs(monkeypatch)
    calls = []

    def host(prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        calls.append(where)
        if len(calls) == 1:
            raise LLMError("G6_E_LLM_TIMEOUT: no answer")
        return Scouts()(prompt, cwd, where, seconds)

    world.edit()
    step = world.advance(host)
    assert step.action == loop.STOP
    assert "G6_E_LLM_TIMEOUT" in step.text
    assert f"{2 * len(CASES)} probes" in step.text


def test_a_later_scout_is_skipped_when_the_hooks_time_is_spent(world, monkeypatch):
    Runs(monkeypatch)
    monkeypatch.setattr(loop, "HOOK_S", 0)
    scouts = Scouts()
    world.edit()
    step = world.advance(scouts)
    assert not scouts.calls
    assert step.action == loop.STOP and "time" in step.text


def test_a_scout_session_has_no_shell_no_hooks_no_servers_and_keeps_nothing(monkeypatch, tmp_path):
    seen = {}

    def run(argv, stdin, cwd, files, env, timeout, name):
        seen.update(argv=argv, stdin=stdin, cwd=cwd, env=env, timeout=timeout,
                    settings=json.loads((files / "g6_settings.json").read_text(encoding="utf-8")))
        return 0, json.dumps({"type": "result", "result": "finished"}), ""

    program = tmp_path / "claude.exe"
    program.write_text("", encoding="utf-8")
    monkeypatch.setenv("G6_CLAUDE", str(program))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    monkeypatch.setattr(host_models, "_run", run)
    where = tmp_path / "work"
    where.mkdir()
    assert loop.ToolSession("a-model").run("the prompt", tmp_path, where, 30.0) == "finished"
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Write"
    assert argv[argv.index("--add-dir") + 1] == str(where)
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.00"
    assert argv[argv.index("--model") + 1] == "a-model"
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert "the prompt" not in " ".join(argv) and seen["stdin"] == "the prompt"
    assert seen["settings"]["disableAllHooks"] is True
    assert "Read(~/.g6/**)" in seen["settings"]["permissions"]["deny"]
    assert seen["env"][loop.CHILD] == "1"
    assert seen["cwd"] == tmp_path and seen["timeout"] == 30.0
    assert "G6_ALLOW_UNCONFINED" not in seen["env"]


CROSS = """import itertools
import random

import pytest

from calc import clamp


@pytest.mark.parametrize("side,kind", itertools.product(["low", "mid", "high"], ["int", "float"]))
def test_cross(side, kind):
    if side == "mid" and kind == "float":
        pytest.skip("cannot occur")
    x = {"low": -5, "mid": 5, "high": 50}[side]
    assert clamp(float(x) if kind == "float" else x, 0, 10) == {"low": 0, "mid": 5, "high": 10}[side]


@pytest.mark.parametrize("seed", range(300))
def test_random(seed):
    x = random.Random(seed).randint(-100, 100)
    assert clamp(x, 0, 10) == min(10, max(0, x))
"""


def test_end_to_end_with_real_confined_runs_and_the_real_review(bwrap_backend, tmp_path, monkeypatch):
    """Everything real but the scouts' sessions: a file of passes and in-test skips is a run, a failure goes back, and a
    repair that holds ends in the review, with no claim for the patch."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return max(x, lo)\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base, session = git(root, "rev-parse", "HEAD"), tmp_path / "g6" / "completion_loop" / "s1"
    tree = json.dumps({"goal": "g", "dimensions": {"side": ["low", "mid", "high"], "kind": ["int", "float"]}, "branches": []})
    scouts = Scouts({"test_probe.py": CROSS}, tree)
    task = "clamp must keep x between lo and hi"

    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return min(max(x, lo), hi) if x < 40 else x\n", encoding="utf-8")
    first = loop.advance(root, task, base, session, scout=scouts)
    assert first.action == loop.CONTINUE and "test_cross[high-int]" in first.text
    assert not list(root.rglob("g6_check_*"))      # the runs were in scratch copies

    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return min(max(x, lo), hi)\n", encoding="utf-8")
    second = loop.advance(root, task, base, session, scout=scouts)
    assert second.action == loop.STOP
    assert f"no counterexample found in {3 * 305} probes" in second.text     # five crossings and 300 inputs in each set
    assert "insufficient_evidence" in second.text and "Not established" in second.text


def test_an_unfinished_hook_is_picked_up_where_it_stopped(world, monkeypatch):
    Runs(monkeypatch)
    calls = []

    def host(prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        calls.append(where)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return Scouts()(prompt, cwd, where, seconds)

    world.edit()
    with pytest.raises(KeyboardInterrupt):
        world.advance(host)
    world.advance(host)
    assert len(calls) == 4     # the first scout's work was kept
