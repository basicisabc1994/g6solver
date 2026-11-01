# SPDX-License-Identifier: Apache-2.0
"""The completion loop: its confined runs are replaced by recorded results, which say nothing about confinement, and its
sessions and its critic by fakes. No model is called and nothing a model wrote runs."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from g6.core import host_models, keys
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_hook as hook
from g6.harness import completion_loop as loop
from g6.harness import patch_report
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.metaharness.domain import FailureRecord
from g6.workforce import repo_tests
from g6.workspace import _coordination

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
        code = cwd / "mod.py"
        self.calls.append({"prompt": prompt, "cwd": cwd, "where": where, "mod": code.read_text() if code.is_file() else "",
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

        def advance(self, scouts=None, critic=None, task=TASK, said=""):
            return loop.advance(root, task, self.base, self.session, scout=scouts or Scouts(), critic=critic, said=said)

        def prepare(self, scouts=None):
            monkeypatch.setattr(keys, "read", lambda: {loop.WHEN_LINE: "before_fix"})
            return loop.prepare(root, TASK, self.base, self.session, scout=scouts or Scouts())

        def state(self) -> dict:
            return json.loads((self.session / "state.json").read_text(encoding="utf-8"))

        def state_exists(self) -> bool:
            return (self.session / "state.json").is_file()

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
    assert world.session.as_posix() not in step.text and str(world.session) not in step.text
    told = Path(world.state()["visible"])
    assert told.as_posix() in step.text and (told / "probes" / "test_p.py").is_file()   # its scout's work, as a copy
    assert not told.is_relative_to(world.session)      # so nothing beside it leads to the rest


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


def scouts_chosen(monkeypatch) -> None:
    """The line in the person's key file that keeps the scouts' loop where a critic is approved."""
    monkeypatch.setattr(keys, "read", lambda: {loop.MODE_LINE: "scouts"})


def test_the_critic_reads_the_finished_patch_once_and_its_failing_test_goes_back(world, monkeypatch):
    def now(placed: str) -> dict:
        return ran(placed, {"test_critic": "failed"} if "test_critic.py" in placed else PASSING)

    scouts_chosen(monkeypatch)
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

    scouts_chosen(monkeypatch)
    Runs(monkeypatch, now=now)
    world.edit()
    assert world.advance(critic=defect()).action == loop.STOP


def test_a_critic_that_fails_is_recorded_and_the_loop_goes_on(world, monkeypatch):
    def broken(system: str, user: str) -> str:
        raise LLMError("no such program")

    scouts_chosen(monkeypatch)
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
    monkeypatch.setattr(loop, "SCOUTS_S", 0)
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


def test_the_scouts_run_before_the_fix_and_the_agent_starts_with_the_fixer_and_the_tree(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    context = world.prepare(scouts)
    assert len(scouts.calls) == 3 and {call["mod"] for call in scouts.calls} == {BASE}
    assert {call[0] for call in runs.calls} == {"base"}
    assert loop.harness().fixer[:40] in context and '"dimensions"' in context
    assert "/opt/we" not in context and "run them from the repository" not in context
    assert world.session.as_posix() not in context and str(world.session) not in context


def test_the_stop_hook_uses_the_sets_already_made_and_tells_the_fixer_nothing_twice(world, monkeypatch):
    runs, scouts = Runs(monkeypatch, now=dict(CASES)), Scouts()
    context = world.prepare(scouts)
    world.edit()
    step = world.advance(scouts)
    assert len(scouts.calls) == 3 and sum(call[0] == "base" for call in runs.calls) == 3
    assert step.action == loop.CONTINUE and context[:40] not in step.text
    assert "assert test_cross[0]" in step.text


def test_what_is_told_to_the_agent_stays_within_what_a_hook_may_add(world, monkeypatch):
    Runs(monkeypatch)
    big = json.dumps({"goal": "g" * 50_000, "dimensions": {"a": ["1", "2"], "b": ["1", "2"]}})
    assert len(world.prepare(Scouts(tree=big))) <= loop.MAX_CONTEXT_CHARS


def test_an_edit_to_the_agents_copy_changes_nothing_that_runs(world, monkeypatch):
    runs = Runs(monkeypatch, now=dict(CASES))
    world.prepare()
    told = Path(world.state()["visible"]) / "probes" / "test_p.py"
    told.write_text("def test_nothing():\n    pass\n", encoding="utf-8")
    world.edit()
    world.advance()
    assert {call[2] for call in runs.calls} == {PROBE}


def test_a_first_task_with_no_scout_set_is_told_nothing(world, monkeypatch):
    def broken(prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        raise LLMError("no session")

    Runs(monkeypatch)
    assert world.prepare(broken) == ""


@pytest.mark.parametrize("line", [{}, {loop.WHEN_LINE: "at_stop"}, {loop.WHEN_LINE: "anything else"}])
def test_by_default_nothing_is_spent_at_the_first_prompt(world, monkeypatch, line):
    runs, scouts = Runs(monkeypatch), Scouts()
    monkeypatch.setattr(keys, "read", lambda: dict(line))
    monkeypatch.setenv(loop.WHEN_LINE, "before_fix")      # an agent's environment is not the person's choice
    assert loop.prepare(world.repo, TASK, world.base, world.session, scout=scouts) == ""
    assert not scouts.calls and not runs.calls
    assert not (world.session / "state.json").exists()


def test_by_default_the_scouts_run_only_when_the_session_changed_python(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    (world.repo / "notes.md").write_text("a note", encoding="utf-8")
    assert world.advance(scouts).text == "" and not scouts.calls and not runs.calls
    world.edit()
    world.advance(scouts)
    assert len(scouts.calls) == 3


def test_the_default_loop_says_it_is_not_the_one_measured_and_how_to_run_that_one(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    text = world.advance().text
    assert "Not the loop the worked examples measured" in text and f"{loop.WHEN_LINE}=before_fix" in text


@pytest.mark.parametrize("stopped", ["boundary", "approval", "no python"])
def test_when_the_scouts_cannot_run_before_the_fix_the_turn_goes_ahead(world, monkeypatch, stopped):
    runs, scouts = Runs(monkeypatch), Scouts()
    if stopped == "boundary":
        monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    if stopped == "no python":
        (world.repo / "mod.py").unlink()
        (world.repo / "notes.txt").write_text("n", encoding="utf-8")
        git(world.repo, "add", "-A")
        git(world.repo, "commit", "-qm", "no python")
    if stopped == "approval":
        monkeypatch.setattr(keys, "read", lambda: {loop.WHEN_LINE: "before_fix"})
        assert loop.prepare(world.repo, TASK, world.base, world.session) == ""
    else:
        assert world.prepare(scouts) == ""
    assert not scouts.calls and not runs.calls


def test_with_no_boundary_before_the_fix_the_stop_hook_says_what_did_not_happen(world, monkeypatch):
    Runs(monkeypatch)
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    world.prepare()
    world.edit()
    step = world.advance()
    assert step.action == loop.STOP and "did not run" in step.text and "no backend here" in step.text


def test_a_limit_reached_before_the_fix_leaves_the_rest_for_the_stop_hook_and_the_report_says_so(world, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(loop, "_clock", lambda: clock[0])
    Runs(monkeypatch)
    scouts = Scouts()

    def slow(prompt: str, cwd: Path, where: Path, seconds: float) -> str:
        clock[0] += loop.SCOUTS_S - loop.RUN_S      # all a session is given: its run at the base commit still fits
        return scouts(prompt, cwd, where, seconds)

    assert loop.harness().fixer[:40] in world.prepare(slow)     # the agent's own scout comes first, so it had its set
    assert list(world.state()["sets"]) == ["scout"]
    world.edit()
    step = world.advance(scouts)
    assert len(scouts.calls) == 3
    assert "held0" in step.text and "after the agent stopped" in step.text and "worked examples measured" in step.text
    assert "time" in step.text


def test_a_loop_whose_scouts_all_ran_before_the_fix_says_nothing_of_the_kind(world, monkeypatch):
    Runs(monkeypatch)
    world.prepare()
    world.edit()
    assert "after the agent stopped" not in world.advance().text


def test_scouts_made_at_the_stop_say_so_when_no_prompt_was_seen(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    text = world.advance().text
    assert "after the agent stopped" in text and "worked examples measured" in text


def test_a_task_nobody_can_read_is_reported_and_nothing_is_spent(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    world.edit()
    step = world.advance(scouts, task="")
    assert step.action == loop.STOP and "did not run" in step.text
    assert not scouts.calls and not runs.calls


def test_the_report_names_the_boundary_the_probes_ran_in(world, monkeypatch):
    def now(placed: str) -> dict:
        return {**ran(placed, PASSING), "isolation": {"backend": "a-boundary"}}

    Runs(monkeypatch, now=now)
    world.edit()
    assert "ran inside `a-boundary`" in world.advance().text


def test_a_file_with_no_time_left_is_not_started_and_says_nothing_of_the_code(world, monkeypatch):
    runs = Runs(monkeypatch)
    probes = world.session / "p"
    probes.mkdir(parents=True)
    (probes / "test_p.py").write_text(PROBE, encoding="utf-8")
    world.edit()
    change = repo_tests.change(world.repo, world.base)
    got = loop.run_set(change, probes, at_base=False, deadline=loop._clock() + 1)
    assert not runs.calls and not got.usable and "time" in got.tail


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
    """Everything real but the scouts' sessions, by the default path: the sets are made before the fix, a file of passes and
    in-test skips is a run, a failure goes back, and a repair that holds ends in the review, with no claim for the patch."""
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

    monkeypatch.setattr(keys, "read", lambda: {loop.WHEN_LINE: "before_fix"})
    context = loop.prepare(root, task, base, session, scout=scouts)
    assert "Work as a counterexample loop" in context and '"dimensions"' in context and "def test_cross" in context
    assert len(scouts.calls) == 3
    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return min(max(x, lo), hi) if x < 40 else x\n", encoding="utf-8")
    first = loop.advance(root, task, base, session, scout=scouts)
    assert len(scouts.calls) == 3      # the sets made before the fix
    assert first.action == loop.CONTINUE and "test_cross[high-int]" in first.text
    assert "Work as a counterexample loop" not in first.text
    assert not list(root.rglob("g6_check_*"))      # the runs were in scratch copies

    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return min(max(x, lo), hi)\n", encoding="utf-8")
    second = loop.advance(root, task, base, session, scout=scouts)
    assert second.action == loop.STOP
    assert f"no counterexample found in {3 * 305} probes" in second.text     # five crossings and 300 inputs in each set
    assert "insufficient_evidence" in second.text and "Not established" in second.text
    assert "after the agent stopped" not in second.text
    assert "ran inside `bubblewrap`" in second.text     # the review ran no test of its own, so its line says none


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


# --- the review-until-clean loop: no scout, no probe, and a fresh review of each repair ------------------------------------

def reviewer(*replies) -> FakeLLM:
    """The reviews in turn: a word is a defect's rule, "none" finds nothing, and an exception is a review that fails."""
    left = list(replies)

    def reply(system: str, user: str) -> str:
        said = left.pop(0)
        if isinstance(said, Exception):
            raise said
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        if said == "none":
            return f"READ: {proof}\nRULE: no rule\nVERDICT: none\nWHY: nothing\n"
        return (f"READ: {proof}\nRULE: rule {said}\nVERDICT: defect\nWHY: why {said}\n```python\n"
                f"def test_critic():\n    assert '{said}' == ''\n```\n")

    return FakeLLM(reply)


class Reviewed:
    """Stands for the runs of the reviewers' tests: the files named in ``failing`` fail, and the others pass."""

    def __init__(self, monkeypatch, *failing):
        self.failing, self.runs = set(failing), Runs(monkeypatch, now=self)

    def __call__(self, placed: str) -> dict:
        return ran(placed, {"test_critic": "failed" if Path(placed).name in self.failing else "passed"})

    def ran_in_turn(self) -> list[str]:
        return [Path(call[1]).name for call in self.runs.calls]


def rows(world) -> list[dict]:
    path = world.session.parent / "failures.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def test_a_review_that_reproduces_nothing_ends_the_loop_with_no_scout_and_no_probe(world, monkeypatch):
    runs, scouts, critic = Runs(monkeypatch), Scouts(), reviewer("none")
    world.edit()
    step = world.advance(scouts, critic)
    assert step.action == loop.STOP and "a review reproduced nothing" in step.text and "THE REVIEW" in step.text
    assert len(critic.calls) == 1 and not scouts.calls and not runs.calls
    assert not (world.session / "sets" / "scout").exists()


def test_a_critic_in_the_key_file_is_all_the_loop_needs(world, monkeypatch):
    runs, critic = Runs(monkeypatch), reviewer("none")
    monkeypatch.setattr(keys, "read", lambda: {loop.CRITIC_LINE: "codex"})    # and no G6_LLM_PROVIDER line
    monkeypatch.setattr(host_models, "CodexCaller", lambda timeout: critic)
    world.edit()
    step = loop.advance(world.repo, TASK, world.base, world.session)
    assert step.action == loop.STOP and "a review reproduced nothing" in step.text
    assert len(critic.calls) == 1 and not runs.calls


def test_with_no_boundary_no_reviewer_is_asked(world, monkeypatch):
    critic = reviewer("none")
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    world.edit()
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and "no backend here" in step.text and step.code == ISOLATION_UNAVAILABLE
    assert not critic.calls


def test_a_run_refused_for_want_of_a_boundary_stops_the_review_loop_and_says_so(world, monkeypatch):
    def refused(*args, **kwargs):
        raise repo_tests.RepoRefused(ISOLATION_UNAVAILABLE, "no boundary now")

    monkeypatch.setattr(vs, "_run_check", refused)
    world.edit()
    step = world.advance(critic=reviewer("one"))
    assert step.action == loop.STOP and "no boundary now" in step.text and step.code == ISOLATION_UNAVAILABLE


def test_the_scouts_line_keeps_the_scouts_loop_and_its_one_review(world, monkeypatch):
    scouts_chosen(monkeypatch)
    Runs(monkeypatch)
    scouts, critic = Scouts(), reviewer("none")
    world.edit()
    step = world.advance(scouts, critic)
    assert len(scouts.calls) == 3 and len(critic.calls) == 1
    assert "What the completion loop tested" in step.text and "read the patch once" in step.text


def test_with_no_critic_approved_the_scouts_loop_runs_as_it_did(world, monkeypatch):
    Runs(monkeypatch)
    scouts = Scouts()
    world.edit()
    assert "What the completion loop tested" in world.advance(scouts).text
    assert len(scouts.calls) == 3


def test_an_agents_environment_chooses_no_loop(world, monkeypatch):
    runs, critic = Runs(monkeypatch), reviewer("none")
    monkeypatch.setattr(keys, "read", lambda: {})
    monkeypatch.setenv(loop.MODE_LINE, "scouts")
    world.edit()
    scouts = Scouts()
    world.advance(scouts, critic)
    assert not scouts.calls and not runs.calls      # the critic this call passed, and no line of the person's

    monkeypatch.setenv(loop.CRITIC_LINE, "codex")
    assert loop.critic_host() is None


def test_with_nothing_approved_the_report_says_plainly_how_to_turn_the_loop_on(world, monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {})
    runs = Runs(monkeypatch)
    world.edit()
    step = loop.advance(world.repo, TASK, world.base, world.session)
    assert step.action == loop.STOP and "did not run" in step.text and "Nothing was tested" in step.text
    assert f"{loop.CRITIC_LINE}=codex" in step.text and step.code == keys.KEY_MISSING
    assert not runs.calls


def test_the_scouts_line_with_no_scouts_approved_says_what_it_needs(world, monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {loop.MODE_LINE: "scouts", loop.CRITIC_LINE: "codex"})
    runs = Runs(monkeypatch)
    world.edit()
    step = loop.advance(world.repo, TASK, world.base, world.session)
    assert step.action == loop.STOP and "G6_LLM_PROVIDER=claude_code" in step.text
    assert not runs.calls


def test_a_critic_approved_means_no_scout_runs_before_the_fix(world, monkeypatch):
    runs, scouts = Runs(monkeypatch), Scouts()
    monkeypatch.setattr(keys, "read", lambda: {loop.WHEN_LINE: "before_fix", loop.CRITIC_LINE: "codex"})
    assert loop.prepare(world.repo, TASK, world.base, world.session, scout=scouts) == ""
    assert not scouts.calls and not runs.calls


def test_the_scouts_line_with_a_critic_still_runs_them_before_the_fix(world, monkeypatch):
    Runs(monkeypatch)
    scouts = Scouts()
    monkeypatch.setattr(keys, "read", lambda: {loop.WHEN_LINE: "before_fix", loop.CRITIC_LINE: "codex",
                                               loop.MODE_LINE: "scouts"})
    assert loop.harness().fixer[:40] in loop.prepare(world.repo, TASK, world.base, world.session, scout=scouts)
    assert len(scouts.calls) == 3


def test_a_finding_goes_back_in_the_reviewers_words_and_a_fresh_review_reads_each_repair(world, monkeypatch):
    tests, scouts, critic = Reviewed(monkeypatch, "test_critic.py"), Scouts(), reviewer("one", "two", "none")
    shipped = loop.harness()
    world.edit()
    first = world.advance(scouts, critic)
    assert first.action == loop.CONTINUE
    assert first.text == loop.review_note({"rule": "rule one", "why": "why one", "test": "def test_critic():\n    assert 'one' == ''"},
                                          shipped)
    assert first.text.endswith(shipped.review_ask)
    world.edit(FIXED + "# after one\n")
    tests.failing = {"test_critic_2.py"}
    second = world.advance(scouts, critic)
    assert second.action == loop.CONTINUE and "rule two" in second.text and "rule one" not in second.text
    world.edit(FIXED + "# after two\n")
    tests.failing = set()
    third = world.advance(scouts, critic)
    assert third.action == loop.STOP and "a review reproduced nothing" in third.text
    assert [("# after one" in call[1], "# after two" in call[1]) for call in critic.calls] == [(False, False), (True, False),
                                                                                                (False, True)]
    assert not scouts.calls


def test_a_review_sees_the_task_and_the_patch_and_none_of_the_agents_reasoning(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    world.edit()
    world.advance(critic=critic, said="my reasoning, which no reviewer reads")
    system, user = critic.calls[0]
    assert system == loop.harness().critic
    assert TASK in user and "x * 2" in user and "parent/mod.py" in user and "my reasoning" not in user


def test_each_review_has_its_own_test_and_the_earlier_ones_keep_running(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "two", "none")
    world.edit()
    world.advance(critic=critic)
    assert tests.ran_in_turn() == ["test_critic.py"]
    world.edit(FIXED + "# 1\n")
    world.advance(critic=critic)
    assert tests.ran_in_turn()[1:] == ["test_critic.py", "test_critic_2.py"]
    world.edit(FIXED + "# 2\n")
    tests.failing = set()
    world.advance(critic=critic)
    assert tests.ran_in_turn()[3:] == ["test_critic.py", "test_critic_2.py"]     # the third review wrote none
    where = world.session / "sets" / "critic"
    assert sorted(path.name for path in where.iterdir()) == ["test_critic.py", "test_critic_2.py"]
    assert "'one'" in (where / "test_critic.py").read_text(encoding="utf-8")


def test_a_repair_that_undoes_an_earlier_fix_goes_back_in_that_reviewers_words(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "two", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = {"test_critic_2.py"}
    world.advance(critic=critic)
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic.py"}
    step = world.advance(critic=critic)      # this review finds nothing, and the first reviewer's test fails again
    assert step.action == loop.CONTINUE and "rule one" in step.text and "rule two" not in step.text


def test_each_round_keeps_its_patch_its_verdict_its_rule_and_its_reasons(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    world.advance(critic=critic)
    first, second = world.state()["reviews"]
    assert (first["verdict"], first["rule"], first["why"]) == ("defect", "rule one", "why one")
    assert (second["verdict"], second["rule"]) == ("none", "no rule")
    assert first["patch"] != second["patch"] and all(re.fullmatch(r"[0-9a-f]{64}", one["patch"]) for one in (first, second))


def test_at_most_four_reviews_and_three_repairs_and_what_is_left_is_open(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py", "test_critic_2.py", "test_critic_3.py", "test_critic_4.py")
    critic = reviewer("one", "two", "three", "four", "never")
    for repair in range(loop.MAX_REPAIRS):
        world.edit(FIXED + f"# {repair}\n")
        assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# the last\n")
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and len(critic.calls) == loop.MAX_REPAIRS + 1
    assert "the repairs ran out with a defect open" in step.text and "**Open:**" in step.text
    assert "after 4 reviews and 3 of at most 3 repairs" in step.text and not step.code


def test_an_agent_that_leaves_the_code_has_rejected_the_finding_and_its_reason_is_kept(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    assert world.advance(critic=critic).action == loop.CONTINUE
    step = world.advance(critic=critic, said="The task asks\n\nfor the old behaviour.")
    assert step.action == loop.STOP and "the agent left the code as it was" in step.text
    assert "The task asks for the old behaviour." in step.text and "**Open:**" in step.text
    assert world.state()["left"] == "The task asks for the old behaviour."
    assert len(critic.calls) == 1      # the code is the code that was reviewed, so nothing new is asked


def test_an_agent_that_gives_no_reason_is_recorded_as_giving_none(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    step = world.advance(critic=critic)
    assert world.state()["left"] == loop.NO_REASON and loop.NO_REASON in step.text


@pytest.mark.parametrize("critic", [
    reviewer(LLMError("no such program")),
    FakeLLM(lambda system, user: "no form at all"),                                   # it never quoted the pack, so it never read it
    FakeLLM(lambda system, user: f"READ: {re.search(r'review [0-9a-f]{32}', user)[0]}\nVERDICT: maybe\n"),
])
def test_a_review_that_fails_or_cannot_be_read_is_no_clean_review(world, monkeypatch, critic):
    Reviewed(monkeypatch)
    world.edit()
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW
    assert "a review failed" in step.text and "No clean review" in step.text
    assert "a review reproduced nothing" not in step.text


def test_a_review_that_fails_after_a_repair_ends_the_loop_and_counts_as_no_clean_review(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", LLMError("gone away"))
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW
    assert "gone away" in step.text and "**Open:**" in step.text      # the first test ran again, and still fails
    assert tests.ran_in_turn() == ["test_critic.py", "test_critic.py"]


def test_a_reviewers_test_that_will_not_run_is_no_clean_review_and_the_report_says_so(world, monkeypatch):
    Runs(monkeypatch, now=lambda placed: {"verdict": "collection_error", "said": "no such import"})
    world.edit()
    step = world.advance(critic=reviewer("one", "one"))
    assert step.action == loop.STOP and step.code == loop.NOT_REPRODUCED and loop.UNREPRODUCED in step.text
    assert "showed no failure" in step.text and "would not run" in step.text and "rule one" in step.text


def test_one_test_that_will_not_run_hides_no_other(world, monkeypatch):
    def now(placed: str) -> dict:
        return ran(placed, {"test_critic": "failed"}) if placed.endswith("/test_critic.py") else {
            "verdict": "collection_error", "said": "no"}

    Runs(monkeypatch, now=now)
    critic = reviewer("one", "two")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    step = world.advance(critic=critic)
    assert step.action == loop.CONTINUE and "rule one" in step.text


def test_a_test_that_passes_is_no_finding(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    step = world.advance(critic=reviewer("one"))
    assert step.action == loop.STOP and "a review reproduced nothing" in step.text and "showed no failure" in step.text
    assert patch_report.findings(world.state()["reviews"]) == ([], []) and len(rows(world)) == 1


def test_a_test_the_runner_refuses_never_runs_and_is_named(world, monkeypatch):
    runs = Runs(monkeypatch)

    def plugin(system: str, user: str) -> str:
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        return (f"READ: {proof}\nRULE: r\nVERDICT: defect\nWHY: w\n```python\npytest_plugins = ['evil']\n"
                "def test_critic():\n    assert False\n```\n")

    world.edit()
    step = world.advance(critic=FakeLLM(plugin))
    assert step.action == loop.STOP and not runs.calls and "pytest plugin" in step.text


def test_the_reviewers_tests_never_reach_the_review_as_checks(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    world.advance(critic=critic)
    [asked] = world.reviewed
    assert asked["requirements"] == [{"id": "task", "text": TASK, "checks": []}]
    assert not asked.get("proposed_checks") and not asked.get("template_instances")


def test_the_loops_time_is_held_across_hooks_and_running_out_is_no_clean_review(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    world.advance(critic=critic)
    state = world.state()
    state["started"] = time.time() - loop.LOOP_S - 1
    (world.session / "state.json").write_text(json.dumps(state), encoding="utf-8")
    world.edit(FIXED + "# late\n")
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and "time ran out" in step.text and step.code == loop.NO_CLEAN_REVIEW
    assert len(critic.calls) == 1


def test_an_agent_that_takes_its_change_back_ends_the_loop_and_says_so(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    world.advance(critic=critic)
    world.edit(BASE)
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and "took its change back" in step.text and "**Open:**" in step.text
    assert len(critic.calls) == 1 and rows(world)


def test_once_it_has_stopped_the_review_loop_is_done_for_the_session(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "never")
    world.edit()
    assert "THE REVIEW" in world.advance(critic=critic).text
    world.edit(FIXED + "# later\n")
    step = world.advance(critic=critic)
    assert (step.action, step.text) == (loop.STOP, "") and len(critic.calls) == 1


def test_the_report_leads_with_what_was_found_fixed_and_left_open(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "two")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = {"test_critic_2.py"}
    world.advance(critic=critic)
    text = world.advance(critic=critic, said="they are wrong").text
    assert text.index("**Found:**") < text.index("**Fixed:**") < text.index("**Open:**") < text.index("THE REVIEW")
    fixed, still = text.split("**Fixed:**")[1].split("**Open:**")
    assert "rule one" in fixed and "rule two" in still and "rule one" not in still


def test_the_report_says_how_many_reviews_read_the_patch(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    text = world.advance(critic=critic).text
    assert "read the patch twice: `defect`, `none`" in text and "after 2 reviews and 1 of at most 3 repairs" in text
    assert "read the patch once" not in text


def test_the_report_states_its_limits_and_never_says_the_patch_is_right(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    text = world.advance(critic=critic).text
    flat = " ".join(text.split())
    assert "seven training bugs only" in flat
    assert "not evidence the work is right, only that this reviewer found no failure it could show" in flat
    assert "did not always mean a wrong patch" in flat
    for word in ("verified", "proved", "proven", "experimental", "correct"):
        assert word not in text.lower()


def test_a_run_with_a_finding_leaves_one_row_a_failure_record_can_be_built_from(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "two")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = {"test_critic_2.py"}
    world.advance(critic=critic)
    world.advance(critic=critic, said="they are wrong")
    [row] = rows(world)
    assert row["task"] == digest(TASK) and row["base"] == world.base and row["rounds"] == 2
    assert row["stop"] == "the agent left the code as it was" and row["left"] == "they are wrong"
    assert [one["verdict"] for one in row["reviews"]] == ["defect", "defect"]
    assert [one["rule"] for one in row["reviews"]] == ["rule one", "rule two"]
    assert row["reviews"][1]["why"] == "why two" and "'two'" in row["reviews"][1]["test"]
    assert row["tree_changed"] is True
    record = FailureRecord(**{field.name: row[field.name] for field in dataclasses.fields(FailureRecord)})
    assert record.cause == "review_finding_open" and record.changeable and record.independently_observed
    assert record.case_id == f"{digest(TASK)[:16]}:{world.base[:12]}" and record.note == "rule two"


def test_a_finding_that_was_fixed_is_a_row_too_and_says_so(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    world.advance(critic=critic)
    [row] = rows(world)
    assert row["cause"] == "review_finding_fixed" and row["stop"] == "a review reproduced nothing" and row["tree_changed"] is True


def test_a_finding_the_agent_left_alone_says_the_tree_never_changed(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    world.advance(critic=critic)
    [row] = rows(world)
    assert row["tree_changed"] is False and row["left"] == loop.NO_REASON


@pytest.mark.parametrize("reply", ["none", "one"])
def test_a_run_with_no_finding_leaves_a_row_too(world, monkeypatch, reply):
    Reviewed(monkeypatch)
    world.edit()
    world.advance(critic=reviewer(reply))
    assert len(rows(world)) == 1 and world.state()["done"]


def test_two_runs_append_and_the_row_stays_in_gs_own_folder(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    for name in ("s1", "s2"):
        session = world.session.parent / name
        critic = reviewer("one")
        world.edit(FIXED + f"# {name}\n")
        loop.advance(world.repo, TASK, world.base, session, critic=critic)
        loop.advance(world.repo, TASK, world.base, session, critic=critic)
    assert len(rows(world)) == 2
    assert not list(world.repo.rglob("failures.jsonl"))


def test_a_row_that_cannot_be_written_loses_no_report(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    (world.session.parent / "failures.jsonl").mkdir(parents=True)     # a folder where the file goes
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and "THE REVIEW" in step.text and world.state()["row"]


def test_a_review_repeated_after_a_cut_hook_never_takes_a_stale_test_for_its_own(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    where = world.session / "sets" / "critic"
    where.mkdir(parents=True)
    (where / "test_critic.py").write_text("def test_critic():\n    assert False\n", encoding="utf-8")   # no state knows of it
    world.edit()
    step = world.advance(critic=reviewer("none"))
    assert step.action == loop.STOP and "a review reproduced nothing" in step.text
    assert not tests.runs.calls and not (where / "test_critic.py").exists()


def test_a_fix_that_stops_an_earlier_test_running_shows_nothing_fixed(world, monkeypatch):
    def now(placed: str) -> dict:
        return {"verdict": "collection_error", "said": "gone"} if state["broken"] else ran(placed, {"test_critic": "failed"})

    state = {"broken": False}
    Runs(monkeypatch, now=now)
    critic = reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# renamed the thing its test imports\n")
    state["broken"] = True
    text = world.advance(critic=critic).text
    assert "**Open:** review 1" in text and "did not run again" in text and "**Fixed:**" not in text


def test_the_report_names_the_boundary_the_reviewers_tests_ran_in(world, monkeypatch):
    Runs(monkeypatch, now=lambda placed: {**ran(placed, {"test_critic": "passed"}), "isolation": {"backend": "a-boundary"}})
    world.edit()
    assert "ran inside `a-boundary`" in world.advance(critic=reviewer("one")).text


def test_with_no_test_run_the_report_names_no_boundary(world, monkeypatch):
    Runs(monkeypatch)
    world.edit()
    assert "ran inside" not in world.advance(critic=reviewer("none")).text


def test_a_row_that_cannot_be_built_loses_no_report(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)

    def gone(change):
        raise repo_tests.RepoRefused("G6_E_GIT_REPO_INVALID", "the repository went away")

    monkeypatch.setattr(repo_tests, "diff_text", gone)
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and "THE REVIEW" in step.text
    assert "the repository went away" in world.state()["row"] and not rows(world)


def test_end_to_end_the_review_loop_with_real_confined_runs_and_the_real_review(bwrap_backend, tmp_path, monkeypatch):
    """Everything real but the reviewer: its tests run confined, a failure goes back, the earlier test keeps running, and a
    review that reproduces nothing ends in the real review, with no claim for the patch."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return max(x, lo)\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base, session = git(root, "rev-parse", "HEAD"), tmp_path / "g6" / "completion_loop" / "s1"
    tests = iter(["from calc import clamp\n\n\ndef test_high():\n    assert clamp(50, 0, 10) == 10\n",
                  "from calc import clamp\n\n\ndef test_low():\n    assert clamp(-5, 0, 10) == 0\n"])

    def reply(system: str, user: str) -> str:
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        test = next(tests, "")
        return (f"READ: {proof}\nRULE: the rule\nVERDICT: {'defect' if test else 'none'}\nWHY: because\n"
                + (f"```python\n{test}```\n" if test else ""))

    critic = FakeLLM(reply)
    task = "clamp must keep x between lo and hi"
    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return max(x, lo) if x < 40 else x\n", encoding="utf-8")
    first = loop.advance(root, task, base, session, critic=critic)
    assert first.action == loop.CONTINUE and "def test_high" in first.text
    assert not list(root.rglob("g6_check_*"))      # the runs were in scratch copies
    (root / "calc.py").write_text("def clamp(x, lo, hi):\n    return min(max(x, lo), hi)\n", encoding="utf-8")
    second = loop.advance(root, task, base, session, critic=critic)     # the second review's test passes
    assert second.action == loop.STOP and "a review reproduced nothing" in second.text
    assert "**Fixed:** review 1" in second.text and "insufficient_evidence" in second.text
    assert "ran inside `bubblewrap`" in second.text


def test_the_scouts_loop_writes_no_row(world, monkeypatch):
    Runs(monkeypatch, now=dict(CASES))
    world.edit()
    world.advance()
    world.advance()
    assert not rows(world)


# --- what a review of this loop reproduced: each case failed on the code it was written against ------------------------------

def test_a_call_after_the_end_keeps_the_code_of_the_stop_it_repeats(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer(LLMError("gone away"), "never")
    world.edit()
    assert world.advance(critic=critic).code == loop.NO_CLEAN_REVIEW
    again = world.advance(critic=critic)
    assert (again.action, again.text, again.code) == (loop.STOP, "", loop.NO_CLEAN_REVIEW)      # a hook repeats nothing
    assert len(critic.calls) == 1


def test_a_call_after_a_refusal_keeps_the_refusals_code(world, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    world.edit()
    assert world.advance(critic=reviewer("never")).code == ISOLATION_UNAVAILABLE
    assert world.advance(critic=reviewer("never")).code == ISOLATION_UNAVAILABLE


def test_a_call_after_a_stop_with_no_code_has_none(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "never")
    world.edit()
    assert not world.advance(critic=critic).code and not world.advance(critic=critic).code


def test_a_task_too_long_to_send_with_the_patch_earns_no_clean_review_and_asks_no_reviewer(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    world.edit()
    step = world.advance(critic=critic, task="A" * (loop.MAX_PACK_CHARS + 1))
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW
    assert loop.CLEAN not in step.text and "No clean review" in step.text and "no reviewer was asked" in step.text
    assert not critic.calls


def test_a_patch_too_long_to_send_earns_no_clean_review(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    world.edit(FIXED + "# " + "x" * loop.MAX_PACK_CHARS + "\n")
    step = world.advance(critic=critic)
    assert step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text and not critic.calls


def test_a_new_file_the_diff_itself_cuts_earns_no_clean_review(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    (world.repo / "big.py").write_text("x = 1\n" * (repo_tests.DIFF_FILE_CHARS // 6 + 100), encoding="utf-8")
    step = world.advance(critic=critic)
    assert step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text and not critic.calls


def test_a_review_refused_for_its_length_after_a_repair_keeps_the_finding_open(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# " + "x" * loop.MAX_PACK_CHARS + "\n")
    step = world.advance(critic=critic)
    assert step.code == loop.NO_CLEAN_REVIEW and "**Open:** review 1" in step.text and len(critic.calls) == 1


def test_the_parent_files_may_be_cut_but_the_task_and_the_patch_never(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    (world.repo / "mod.py").write_text(BASE + "# padding\n" * (loop.MAX_PACK_CHARS // 9), encoding="utf-8")
    git(world.repo, "add", "mod.py")
    git(world.repo, "commit", "-qm", "a large file")
    base = git(world.repo, "rev-parse", "HEAD")
    (world.repo / "mod.py").write_text(FIXED + "# padding\n" * (loop.MAX_PACK_CHARS // 9), encoding="utf-8")
    step = loop.advance(world.repo, TASK, base, world.session, critic=critic)
    pack = critic.calls[0][1]
    assert step.action == loop.STOP and not step.code and loop.CLEAN in step.text
    assert TASK in pack and repo_tests.diff_text(repo_tests.change(world.repo, base)) in pack
    assert len(pack) == loop.MAX_PACK_CHARS      # the parent file was cut, and that is all that was


def the_patch(world) -> str:
    return repo_tests.diff_text(repo_tests.change(world.repo, world.base))


def test_a_patch_quoted_in_a_long_task_cannot_hide_that_the_task_was_cut(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    world.edit()
    task = "Repair this candidate:\n" + the_patch(world) + "\n" + "context " * loop.MAX_PACK_CHARS + "THE LAST REQUIREMENT"
    step = world.advance(critic=critic, task=task)
    assert step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text and not critic.calls


def test_a_patch_quoted_in_a_task_that_fits_cannot_stand_for_the_patch_itself(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none")
    world.edit()
    patch = the_patch(world)
    task = patch + "a" * (loop.MAX_PACK_CHARS - len(patch) - 50)      # the whole task fits, and the patch's own section does not
    step = world.advance(critic=critic, task=task)
    assert step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text and not critic.calls


def test_the_head_fits_to_the_character_and_the_pack_is_the_same_text_it_was(world, monkeypatch):
    world.edit()
    change = repo_tests.change(world.repo, world.base)
    proof = f"review {'0' * 32}"
    room = loop.MAX_PACK_CHARS - len(loop._head(change, "", proof))
    assert loop._read_whole(change, "t" * room) and not loop._read_whole(change, "t" * (room + 1))
    assert loop._pack(change, "t" * room, proof).endswith(f"candidate.patch:\n{the_patch(world)}")      # to its last character
    pack = loop._pack(change, TASK, proof)
    assert pack == (f"issue.txt:\n{proof}\n\n{TASK}\n\ncandidate.patch:\n{the_patch(world)}\n\nparent/mod.py:\n{BASE}")


def test_whatever_the_diff_itself_cuts_is_past_what_a_review_can_read():
    assert loop.MAX_PACK_CHARS < repo_tests.DIFF_FILE_CHARS


def test_each_reviewed_patch_stays_recoverable_byte_for_byte_after_the_repairs(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none")
    world.edit()
    first = the_patch(world)
    world.advance(critic=critic)
    world.edit(FIXED + "# after one\n")
    second = the_patch(world)
    tests.failing = set()
    world.advance(critic=critic)
    kept = [(world.session / "rounds" / f"round-{n}.patch").read_bytes().decode("utf-8") for n in (1, 2)]
    assert kept == [first, second]
    assert [digest(one) for one in kept] == [one["patch"] for one in world.state()["reviews"]]


def test_a_patch_is_kept_before_its_review_so_a_review_that_fails_still_leaves_it(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    patch = the_patch(world)
    world.advance(critic=reviewer(LLMError("gone away")))
    assert (world.session / "rounds" / "round-1.patch").read_bytes().decode("utf-8") == patch


def test_a_row_names_the_session_that_holds_its_patches(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    patch = the_patch(world)
    world.advance(critic=critic)
    world.advance(critic=critic)
    [row] = rows(world)
    assert (world.session.parent / row["session"] / "rounds" / "round-1.patch").read_bytes().decode("utf-8") == patch
    assert digest(patch) == row["reviews"][0]["patch"]


def killed(*args, **kwargs):
    raise KeyboardInterrupt("the hook was killed")


def test_a_hook_killed_in_the_final_review_adds_one_row_and_asks_for_no_second_review(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none", "never")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    real = loop._final_review
    monkeypatch.setattr(loop, "_final_review", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic)
    monkeypatch.setattr(loop, "_final_review", real)
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and loop.CLEAN in step.text
    assert len(rows(world)) == 1 and len(critic.calls) == 2      # the review that was paid for stands


def test_a_hook_killed_in_the_final_review_of_a_rejection_keeps_the_agents_reason(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    real = loop._final_review
    monkeypatch.setattr(loop, "_final_review", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic, said="They misread the task.")
    monkeypatch.setattr(loop, "_final_review", real)
    step = world.advance(critic=critic)       # this stop gives no words
    [row] = rows(world)
    assert row["left"] == "They misread the task." and "They misread the task." in step.text


def test_a_stop_with_no_python_changed_carries_its_code_and_no_report(world, monkeypatch):
    runs = Runs(monkeypatch)
    step = world.advance(critic=reviewer("never"))
    assert (step.action, step.text, step.code) == (loop.STOP, "", "G6_E_NO_PYTHON_CHANGED")
    assert not runs.calls and not world.state_exists()


def killed_in_the_final_review(world, monkeypatch, critic) -> None:
    """A clean review of the patch as it stands, whose hook dies before it can report it."""
    real = loop._final_review
    monkeypatch.setattr(loop, "_final_review", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic)
    monkeypatch.setattr(loop, "_final_review", real)


def test_a_clean_review_a_killed_hook_never_reported_speaks_only_for_the_patch_it_read(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none")
    world.edit()
    killed_in_the_final_review(world, monkeypatch, critic)
    world.edit(FIXED + "# changed after the review\n")
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and loop.CLEAN in step.text
    assert len(critic.calls) == 2 and "# changed after the review" in critic.calls[1][1]
    assert [one["patch"] for one in world.state()["reviews"]] == [digest(the_patch(world))]
    assert "read the patch once" in step.text


def test_a_change_after_a_clean_review_a_hook_never_reported_can_still_be_found_wrong(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("none", "one")
    world.edit()
    killed_in_the_final_review(world, monkeypatch, critic)
    world.edit(FIXED + "# changed after the review\n")
    step = world.advance(critic=critic)
    assert step.action == loop.CONTINUE and "rule one" in step.text and "stop" not in world.state()


def test_a_commit_that_leaves_the_patch_as_it_was_buys_no_second_review(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "never")
    world.edit()
    patch = the_patch(world)
    killed_in_the_final_review(world, monkeypatch, critic)
    git(world.repo, "add", "mod.py")
    git(world.repo, "commit", "-qm", "the agent's commit")
    assert the_patch(world) == patch
    assert loop.CLEAN in world.advance(critic=critic).text and len(critic.calls) == 1


def test_a_rejection_a_killed_hook_never_finished_is_gone_once_the_agent_repairs_after_all(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    real = loop._final_review
    monkeypatch.setattr(loop, "_final_review", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic, said="They misread the task.")
    monkeypatch.setattr(loop, "_final_review", real)
    world.edit(FIXED + "# repaired after all\n")
    tests.failing = set()
    step = world.advance(critic=critic)
    assert loop.CLEAN in step.text and "left the code" not in step.text and "misread" not in step.text
    assert not world.state()["left"]


def test_a_hook_killed_between_the_row_and_the_end_adds_no_second_row(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    real = loop._finish
    monkeypatch.setattr(loop, "_finish", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic)
    assert len(rows(world)) == 1
    monkeypatch.setattr(loop, "_finish", real)
    assert world.advance(critic=critic).action == loop.STOP
    assert len(rows(world)) == 1


def test_no_session_is_ever_marked_ended_with_no_report(world, monkeypatch):
    Reviewed(monkeypatch)
    real, seen = loop._save, []

    def save(session: Path, state: dict) -> None:
        seen.append(not state.get("done") or (session / "report.md").is_file())
        real(session, state)

    monkeypatch.setattr(loop, "_save", save)
    world.edit()
    world.advance(critic=reviewer("none"))
    assert seen and all(seen)


class HookKilled(BaseException):
    """A killed process: past every handler in the loop."""


def tears_the_row(context) -> None:
    """A writer of the log that dies partway through a row, past its session field."""
    real = os.fdopen

    def opened(fd, *args, **kwargs):
        sink = real(fd, *args, **kwargs)
        if args[:1] != ("a",):
            return sink

        class Torn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                sink.close()

            def write(self, text):
                sink.write(text[:text.index('"reviews":')])
                sink.flush()
                raise HookKilled

        return Torn()

    context.setattr(os, "fdopen", opened)


def whole_rows(world) -> list[dict]:
    """The rows a reader can use: a line that is not a whole row says nothing."""
    found = []
    for line in (world.session.parent / "failures.jsonl").read_text(encoding="utf-8").split("\n"):
        try:
            found.append(json.loads(line))
        except ValueError:
            pass
    return found


def test_a_row_a_killed_hook_tore_is_written_whole_before_the_run_ends(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    with monkeypatch.context() as context:
        tears_the_row(context)
        with pytest.raises(HookKilled):
            world.advance(critic=critic, said="I reject it.")
    assert not world.state().get("done")
    assert world.advance(critic=critic).action == loop.STOP
    assert world.state()["done"] is True
    assert [row["session"] for row in whole_rows(world)] == [world.session.name]


def test_a_row_after_a_torn_line_stands_on_a_line_of_its_own(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    (world.session.parent / "failures.jsonl").write_text('{"session": "a run that died", "note": "half a ro', encoding="utf-8")
    world.advance(critic=critic)
    assert [row["session"] for row in whole_rows(world)] == [world.session.name]


def test_a_whole_row_with_no_line_end_takes_nothing_with_it(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    (world.session.parent / "failures.jsonl").write_text('{"session": "another"}', encoding="utf-8")
    world.advance(critic=critic)
    assert [row["session"] for row in whole_rows(world)] == ["another", world.session.name]


def test_a_rows_text_that_names_a_session_is_not_that_sessions_row(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    (world.session.parent / "failures.jsonl").write_text(
        json.dumps({"session": "another", "session of": world.session.name, "note": f'"session": "{world.session.name}"'}) + "\n",
        encoding="utf-8")
    world.advance(critic=critic)
    assert [row["session"] for row in whole_rows(world)] == ["another", world.session.name]


def test_a_row_stays_one_line_whatever_its_text_holds(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one two\x85three")
    world.edit()
    world.advance(critic=critic)
    world.advance(critic=critic)
    [row] = rows(world)      # rows reads as any reader would, with splitlines
    assert row["note"] == "rule one two\x85three"


def test_a_log_another_run_holds_is_never_written_over_and_costs_no_report(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    with _coordination.Coordination(world.session.parent / ".failures").mutex():
        step = world.advance(critic=critic)
    assert step.action == loop.STOP and "THE REVIEW" in step.text
    assert "another writer" in world.state()["row"] and not rows(world)


@pytest.mark.parametrize("after", [
    {"test_bug": "skipped", "test_smoke": "passed"},     # a skip closes nothing
    {"test_smoke": "passed"},                            # uncollected or deselected: it is simply gone
    {"test_bug_2": "passed", "test_smoke": "passed"},    # renamed: another test's pass is not this one's
])
def test_a_finding_closes_only_when_the_case_that_failed_has_passed(world, monkeypatch, after):
    outcomes = {"test_bug": "failed", "test_smoke": "passed"}
    Runs(monkeypatch, now=lambda placed: ran(placed, dict(outcomes)))
    critic = reviewer("one", "none")
    world.edit()
    assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# 1\n")
    outcomes = after
    step = world.advance(critic=critic)
    assert step.action == loop.STOP and loop.CLEAN in step.text      # the stop is K's: nothing fails on this run
    fixed, still = patch_report.findings(world.state()["reviews"])
    assert not fixed and [one["n"] for one in still] == [1]
    assert "**Fixed:**" not in step.text and "**Open:** review 1" in step.text
    [row] = rows(world)
    assert row["cause"] == loop.OPEN


def test_a_row_a_held_log_kept_out_is_written_by_the_sessions_next_call(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    with _coordination.Coordination(world.session.parent / ".failures").mutex():
        ended = world.advance(critic=critic, said="They misread the task.")
    assert ended.action == loop.STOP and "THE REVIEW" in ended.text and not rows(world)
    again = world.advance(critic=critic)
    assert (again.action, again.text) == (loop.STOP, "") and len(critic.calls) == 1      # the run ended: this adds only its row
    [row] = rows(world)
    assert (row["session"], row["left"]) == (world.session.name, "They misread the task.")
    world.advance(critic=critic)
    assert len(rows(world)) == 1 and "row" not in world.state()


def test_a_log_that_stays_unwritable_keeps_the_row_and_why_and_writes_it_when_it_can(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    blocked = world.session.parent / "failures.jsonl"
    blocked.mkdir(parents=True)      # a folder where the file goes
    critic = reviewer("one")
    world.edit()
    world.advance(critic=critic)
    assert "THE REVIEW" in world.advance(critic=critic).text and world.state()["row"]
    assert world.advance(critic=critic).action == loop.STOP and world.state()["row"]      # still no log: still no harm
    blocked.rmdir()
    world.advance(critic=critic)
    [row] = rows(world)
    assert row["session"] == world.session.name and "row" not in world.state()


def test_a_finding_closes_when_the_case_that_failed_passes(world, monkeypatch):
    outcomes = {"test_bug": "failed", "test_smoke": "passed"}
    Runs(monkeypatch, now=lambda placed: ran(placed, dict(outcomes)))
    critic = reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    outcomes = {"test_bug": "passed", "test_smoke": "passed"}
    step = world.advance(critic=critic)
    assert "**Fixed:** review 1" in step.text and "**Open:**" not in step.text
    [row] = rows(world)
    assert row["cause"] == loop.FIXED


def test_a_case_a_later_run_skips_stays_open_though_it_passed_in_between(world, monkeypatch):
    outcomes = {"test_bug": "failed", "test_smoke": "failed"}
    Runs(monkeypatch, now=lambda placed: ran(placed, dict(outcomes)))
    critic = reviewer("one", "none", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    outcomes = {"test_bug": "passed", "test_smoke": "failed"}
    assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# 2\n")
    outcomes = {"test_bug": "skipped", "test_smoke": "passed"}
    assert "**Open:** review 1" in world.advance(critic=critic).text


def test_a_case_that_first_failed_in_a_later_round_is_owed_too(world, monkeypatch):
    outcomes = {"test_bug": "failed", "test_other": "passed"}
    Runs(monkeypatch, now=lambda placed: ran(placed, dict(outcomes)))
    critic = reviewer("one", "none", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    outcomes = {"test_bug": "passed", "test_other": "failed"}
    assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# 2\n")
    outcomes = {"test_bug": "passed", "test_other": "skipped"}
    assert "**Open:** review 1" in world.advance(critic=critic).text


def test_a_retained_test_a_later_repair_breaks_is_an_open_finding(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "two", "none")
    world.edit()
    assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# 1\n")
    assert world.advance(critic=critic).action == loop.CONTINUE      # review 2's test passes, and review 1's still fails
    assert world.state()["reviews"][1]["results"]["test_critic_2.py"] == "passed"
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic_2.py"}     # the repair fixes review 1 and breaks review 2's test
    step = world.advance(critic=critic)
    assert step.action == loop.CONTINUE and "rule two" in step.text
    ended = world.advance(critic=critic, said="I reject the second finding.")
    [row] = rows(world)
    assert (row["cause"], row["note"]) == (loop.OPEN, "rule two")
    assert "**Found:** 2 reproduced" in ended.text and "**Fixed:** review 1" in ended.text
    assert "**Open:** review 2" in ended.text and "showed no failure" not in ended.text


def test_a_retained_test_that_failed_once_and_passes_at_the_end_was_found_and_fixed(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "two", "none", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    world.advance(critic=critic)
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic_2.py"}
    world.advance(critic=critic)
    world.edit(FIXED + "# 3\n")
    tests.failing = set()
    ended = world.advance(critic=critic)
    assert ended.action == loop.STOP and "**Found:** 2 reproduced" in ended.text and "**Open:**" not in ended.text
    assert "**Fixed:** review 2" in ended.text and "showed no failure" not in ended.text
    [row] = rows(world)
    assert row["cause"] == loop.FIXED


def test_overlapping_calls_cannot_delete_a_pending_test_or_earn_a_clean_stop(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    written, release = threading.Event(), threading.Event()
    first_critic, second_critic, original = reviewer("one"), reviewer("none"), loop.run_critic

    def pause_after_writing(change, task, caller, system, where, n=1):
        found = original(change, task, caller, system, where, n)
        if caller is first_critic:
            written.set()
            assert release.wait(30), "the second call never gave way"
        return found

    monkeypatch.setattr(loop, "run_critic", pause_after_writing)
    world.edit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(world.advance, None, first_critic)
        assert written.wait(30), "the first review never wrote its test"
        second = pool.submit(world.advance, None, second_critic)
        try:
            second.result(timeout=1)
        except TimeoutError:
            pass      # a call that waits its turn is held here, and goes on once the first has ended
        release.set()
        steps = [first.result(timeout=30), second.result(timeout=30)]
    assert tests.ran_in_turn() == ["test_critic.py"]      # the first reviewer's test ran, and it ran once
    assert not any(loop.CLEAN in step.text for step in steps)


def test_a_call_while_a_step_runs_says_wait_and_never_reads_that_step_as_a_rejection(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    reviewing, release, contending = threading.Event(), threading.Event(), threading.Event()
    critic, original, real_sleep = reviewer("one"), loop.run_critic, _coordination.time.sleep

    def paused(*args, **kwargs):
        reviewing.set()
        assert release.wait(30)
        return original(*args, **kwargs)

    def sleep(seconds):
        if seconds == 0.01:      # the lock sleeps only once the operating system has refused it
            contending.set()
        return real_sleep(seconds)

    monkeypatch.setattr(loop, "run_critic", paused)
    monkeypatch.setattr(_coordination.time, "sleep", sleep)
    world.edit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(world.advance, None, critic)
        assert reviewing.wait(30)
        second = pool.submit(world.advance, None, critic)
        second.add_done_callback(lambda _: contending.set())
        try:
            assert contending.wait(30), "the second call never met the lock"
        finally:
            release.set()
        steps = [first.result(timeout=30), second.result(timeout=30)]
    assert steps[0].action == loop.CONTINUE and len(critic.calls) == 1
    assert (steps[1].action, steps[1].code) == (loop.WAIT, loop.BUSY)
    assert "stop" not in world.state() and not rows(world)


def test_a_call_that_may_not_be_the_agents_reply_sends_the_finding_back_and_decides_nothing(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    first = world.advance(critic=critic)
    before = world.state()
    again = loop.advance(world.repo, TASK, world.base, world.session, critic=critic, answered=False)
    assert (again.action, again.text, again.code) == (loop.CONTINUE, first.text, "")
    assert world.state() == before and len(critic.calls) == 1 and not rows(world)
    step = world.advance(critic=critic)      # a Stop hook fires only once the agent has taken its turn
    assert step.action == loop.STOP and "the agent left the code as it was" in step.text


def test_a_changed_tree_is_a_reply_whatever_the_call_says(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    step = loop.advance(world.repo, TASK, world.base, world.session, critic=critic, answered=False)
    assert step.action == loop.STOP and loop.CLEAN in step.text and len(critic.calls) == 2


def test_a_finding_goes_back_the_same_way_on_the_scouts_loop(world, monkeypatch):
    scouts_chosen(monkeypatch)
    Runs(monkeypatch, now=dict(CASES))
    world.edit()
    first = world.advance()
    again = loop.advance(world.repo, TASK, world.base, world.session, scout=Scouts(), answered=False)
    assert again.action == loop.CONTINUE and "assert test_cross[0]" in again.text and "assert test_cross[0]" in first.text


def test_a_call_that_finds_another_step_running_does_nothing_and_says_so(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    world.edit()
    world.session.mkdir(parents=True)
    with loop._held(world.session):
        step = world.advance(critic=critic)
    assert (step.action, step.code) == (loop.WAIT, loop.BUSY) and "did nothing" in step.text
    assert not critic.calls and not (world.session / "state.json").exists() and not rows(world)
    assert world.advance(critic=critic).action == loop.CONTINUE      # it left nothing behind


def test_a_process_killed_holding_a_session_leaves_it_free(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch)
    world.edit()
    world.session.mkdir(parents=True)
    holds = ("import sys, time\nfrom pathlib import Path\nfrom g6.harness.completion_loop import _held\n"
             "with _held(Path(sys.argv[1])):\n    print('held', flush=True)\n    time.sleep(120)\n")
    child = subprocess.Popen([sys.executable, "-c", holds, str(world.session)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        assert world.advance(critic=reviewer("never")).action == loop.WAIT      # the lock is real, so its release means something
    finally:
        child.kill()
        child.wait(timeout=30)
        child.stdout.close()
    step = world.advance(critic=reviewer("none"))
    assert step.action == loop.STOP and loop.CLEAN in step.text


def test_a_step_that_raises_leaves_the_session_free(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    monkeypatch.setattr(loop, "_final_review", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=reviewer("none"))
    with loop._held(world.session):
        pass


def test_two_sessions_never_wait_for_each_other(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch)
    world.edit()
    other = world.session.parent / "s2"
    other.mkdir(parents=True)
    with loop._held(other):
        assert world.advance(critic=reviewer("none")).action == loop.STOP


# --- the second review of this loop: each case failed on the code it was written against, for the reason its name gives -------

def refused_access(placed: str, cases: dict[str, str]) -> dict:
    """A run in which the operating system refused the access ``test_bug`` needs: pytest's own count calls it a failure."""
    return {**ran(placed, cases), "os_errors": [f"{placed}::test_bug"],
            "failure_messages": {f"{placed}::test_bug": "PermissionError: [Errno 13] the boundary refused this"}}


def test_a_test_with_an_unknown_case_is_not_reported_as_passing(world, monkeypatch):
    Runs(monkeypatch, now=lambda placed: refused_access(placed, {"test_bug": "failed", "test_smoke": "passed"}))
    world.edit()
    step = world.advance(critic=reviewer("one", "one"))
    assert step.action == loop.STOP
    assert "its test passes on the patch" not in step.text and "PermissionError" in step.text
    assert world.state()["reviews"][0]["results"]["test_critic.py"] == patch_report.NOT_JUDGED


def test_a_test_whose_every_case_is_unknown_is_not_reported_as_passing(world, monkeypatch):
    Runs(monkeypatch, now=lambda placed: refused_access(placed, {"test_bug": "failed"}))
    world.edit()
    step = world.advance(critic=reviewer("one", "one"))
    assert "its test passes on the patch" not in step.text and "PermissionError" in step.text


def test_a_finding_whose_case_passed_is_not_fixed_while_another_case_is_unknown(world, monkeypatch):
    outcomes = {"test_bug": "failed", "test_smoke": "passed"}
    Runs(monkeypatch, now=lambda placed: ran(placed, dict(outcomes)))
    critic = reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    outcomes = {"test_bug": "passed", "test_smoke": "failed"}
    Runs(monkeypatch, now=lambda placed: {**ran(placed, dict(outcomes)), "os_errors": [f"{placed}::test_smoke"]})
    step = world.advance(critic=critic)
    assert "**Fixed:**" not in step.text and "**Open:** review 1" in step.text
    assert "a case could not be judged" in step.text


def test_a_clean_review_cannot_finish_a_different_patch(world, monkeypatch):
    """The reviewer's own case: another thread edits the file while the review of the first patch is in flight."""
    Reviewed(monkeypatch)
    reply, read, resume = reviewer("none", "none"), threading.Event(), threading.Event()

    def paused(system: str, user: str) -> str:
        answer = reply.complete(system, user)
        if not read.is_set():
            read.set()
            assert resume.wait(30), "the test never released the paused review"
        return answer

    world.edit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(world.advance, None, FakeLLM(paused))
        try:
            assert read.wait(30), "the review never received its patch"
            world.edit(FIXED + "# changed while the review read the patch\n")
        finally:
            resume.set()
        ended = running.result(timeout=30)
    state = world.state()
    assert not (ended.action == loop.STOP and state.get("stop") == loop.CLEAN
                and state["reviews"][-1]["patch"] != digest(the_patch(world)))


def reads_then(change_the_tree) -> FakeLLM:
    """A review that finds nothing, with the tree changed under it."""
    quiet = reviewer("none")

    def reply(system: str, user: str) -> str:
        change_the_tree()
        return quiet.complete(system, user)

    return FakeLLM(reply)


def test_a_review_that_read_another_patch_leaves_no_clean_review(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    step = world.advance(critic=reads_then(lambda: world.edit(FIXED + "# changed under the review\n")))
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW
    assert loop.CLEAN not in step.text and loop.MOVED in step.text and "**No clean review:**" in step.text
    assert world.state()["stop"] == loop.MOVED


def test_a_new_file_added_under_a_review_changes_the_patch_it_read(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    added = world.repo / "added.py"
    step = world.advance(critic=reads_then(lambda: added.write_text("y = 1\n", encoding="utf-8")))
    assert step.code == loop.NO_CLEAN_REVIEW and loop.MOVED in step.text


def test_a_patch_that_changes_during_the_final_review_earns_no_clean_end(world, monkeypatch):
    Reviewed(monkeypatch)
    real = loop._final_review

    def edits(root, task, base) -> str:
        world.edit(FIXED + "# changed during the final review\n")
        return real(root, task, base)

    monkeypatch.setattr(loop, "_final_review", edits)
    world.edit()
    step = world.advance(critic=reviewer("none"))
    assert step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text and "THE REVIEW" in step.text


def test_a_repository_gone_at_the_end_earns_no_clean_end(world, monkeypatch):
    Reviewed(monkeypatch)
    real, calls = repo_tests.change, []

    def goes_away(*args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            raise repo_tests.RepoRefused("G6_E_GIT_REPO_INVALID", "the repository went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo_tests, "change", goes_away)
    world.edit()
    step = world.advance(critic=reviewer("none"))
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text


def test_a_patch_unchanged_at_the_end_keeps_its_clean_stop(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    step = world.advance(critic=reviewer("none"))
    assert step.action == loop.STOP and not step.code and loop.CLEAN in step.text


def killed_at_the_end(world, monkeypatch, critic) -> None:
    """A step that writes its row and dies before it can mark the run as ended."""
    real = loop._finish
    monkeypatch.setattr(loop, "_finish", killed)
    with pytest.raises(KeyboardInterrupt):
        world.advance(critic=critic)
    monkeypatch.setattr(loop, "_finish", real)


def test_a_row_describes_the_runs_final_outcome_after_a_killed_finish(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none", "after the restart")
    world.edit()
    assert world.advance(critic=critic).action == loop.CONTINUE
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    killed_at_the_end(world, monkeypatch, critic)
    assert rows(world)[0]["cause"] == loop.FIXED and not world.state().get("done")
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic.py"}
    assert world.advance(critic=critic).action == loop.CONTINUE
    ended = world.advance(critic=critic, said="I reject the finding")
    assert ended.action == loop.STOP and "**Open:**" in ended.text and world.state()["done"]
    [row] = rows(world)
    assert row["cause"] == loop.OPEN, "the run ended with a finding open, and its row still says fixed"


def test_a_row_replaced_after_a_kill_leaves_every_other_line_as_it_was(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none", "after the restart")
    log = world.session.parent / "failures.jsonl"
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    log.write_text('{"session": "before"}\n', encoding="utf-8")
    killed_at_the_end(world, monkeypatch, critic)
    with log.open("a", encoding="utf-8", newline="\n") as out:
        out.write('{"session": "after"}\n{"session": "a run that died", "no')
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic.py"}
    world.advance(critic=critic)
    world.advance(critic=critic, said="I reject the finding")
    assert [row["session"] for row in whole_rows(world)] == ["before", "after", world.session.name]
    assert '{"session": "a run that died", "no\n' in log.read_bytes().decode("utf-8")      # a torn line is left as it was
    assert not (log.parent / "failures.jsonl.new").exists()


def test_a_row_that_cannot_replace_the_stale_one_is_kept_and_written_by_the_next_call(world, monkeypatch):
    tests, critic = Reviewed(monkeypatch, "test_critic.py"), reviewer("one", "none", "after the restart")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    killed_at_the_end(world, monkeypatch, critic)
    world.edit(FIXED + "# 2\n")
    tests.failing = {"test_critic.py"}
    world.advance(critic=critic)
    real = os.replace

    def held(source, target):      # on Windows a reader of the log makes this fail
        if Path(target).name == "failures.jsonl":
            raise PermissionError("another process has the log open")
        real(source, target)

    with monkeypatch.context() as context:
        context.setattr(os, "replace", held)
        ended = world.advance(critic=critic, said="I reject the finding")
    assert ended.action == loop.STOP and "another process" in world.state()["row"]
    assert rows(world)[0]["cause"] == loop.FIXED      # it stays what it was until it can be replaced whole
    world.advance(critic=critic)
    [row] = rows(world)
    assert row["cause"] == loop.OPEN and "row" not in world.state()

def test_a_failed_review_leaves_a_row_for_the_next_cycle(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    step = world.advance(critic=reviewer(LLMError("the reviewer exited unsuccessfully")))
    assert step.action == loop.STOP and step.code == loop.NO_CLEAN_REVIEW and world.state()["done"]
    assert len(rows(world)) == 1, "a run that ended on a failed review left no row"


def test_a_review_that_cannot_be_read_leaves_a_row(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    world.advance(critic=FakeLLM(lambda system, user: "no form at all"))
    assert len(rows(world)) == 1


def test_a_task_too_long_to_send_leaves_a_row(world, monkeypatch):
    Reviewed(monkeypatch)
    world.edit()
    world.advance(critic=reviewer("never"), task="A" * (loop.MAX_PACK_CHARS + 1))
    assert len(rows(world)) == 1


def test_a_refused_run_leaves_a_row(world, monkeypatch):
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    world.edit()
    assert world.advance(critic=reviewer("never")).code == ISOLATION_UNAVAILABLE
    [row] = rows(world)
    assert row["rounds"] == 0 and row["reviews"] == [] and row["session"] == world.session.name


def test_a_call_with_no_python_changed_ended_no_run_and_leaves_no_row(world, monkeypatch):
    Runs(monkeypatch)
    assert world.advance(critic=reviewer("never")).code == loop.NO_PYTHON
    assert not rows(world)

def will_not_run(placed: str = "") -> dict:
    """A run of a reviewer's test that never reached a case: pytest's own error is in its failure messages."""
    return {"verdict": "collection_error", "said": "the tests could not be collected",
            "failure_messages": {"test_critic.py": "ImportError: cannot import name 'nope' from 'mod'"}}


def test_a_defect_whose_test_cannot_run_goes_back_to_the_reviewer_once_with_pytests_error(world, monkeypatch):
    Runs(monkeypatch, now=will_not_run)
    critic = reviewer("one", "one", "never")
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2, "the reviewer was never told its test would not run"
    assert "ImportError: cannot import name 'nope'" in critic.calls[1][1]
    assert "ImportError" not in critic.calls[0][1]
    assert re.search(r"review [0-9a-f]{32}", critic.calls[0][1])[0] != re.search(r"review [0-9a-f]{32}", critic.calls[1][1])[0]
    assert step.action == loop.STOP


def test_a_defect_still_unrunnable_ends_blocked_with_a_stable_code_and_says_so(world, monkeypatch):
    Runs(monkeypatch, now=will_not_run)
    world.edit()
    step = world.advance(critic=reviewer("one", "one"))
    assert loop.CLEAN not in step.text and step.code, "a defect that could not be reproduced ended clean"
    assert step.code == loop.NOT_REPRODUCED and world.state()["stop"] == loop.UNREPRODUCED
    assert "a defect was claimed and could not be reproduced" in step.text and "**No clean review:**" in step.text
    assert "ImportError" in step.text and "THE REVIEW" in step.text


def test_a_rewritten_test_that_fails_goes_to_the_agent_and_spends_no_repair(world, monkeypatch):
    runs = [will_not_run()]
    Runs(monkeypatch, now=lambda placed: runs.pop() if runs else ran(placed, {"test_critic": "failed"}))
    critic = reviewer("one", "two")
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2 and step.action == loop.CONTINUE and "rule two" in step.text
    assert len(world.state()["reviews"]) == 1 and world.state()["reviews"][0]["told"]


def test_a_rewritten_test_that_passes_is_no_finding_and_the_first_claim_stays_on_the_record(world, monkeypatch):
    runs = [will_not_run()]
    Runs(monkeypatch, now=lambda placed: runs.pop() if runs else ran(placed, {"test_critic": "passed"}))
    critic = reviewer("one", "two")
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2 and step.action == loop.STOP and not step.code
    assert "`rule one`" in step.text and "ImportError" in step.text and "told once" in step.text
    assert "its test passes on the patch" in step.text


def test_a_reviewer_told_of_the_error_may_withdraw_its_claim_and_the_report_says_it_did(world, monkeypatch):
    Runs(monkeypatch, now=will_not_run)
    critic = reviewer("one", "none")
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2 and step.action == loop.STOP and not step.code and loop.CLEAN in step.text
    assert "`rule one`" in step.text and "told once" in step.text


def test_a_review_that_fails_when_told_of_the_error_is_no_clean_review(world, monkeypatch):
    Runs(monkeypatch, now=will_not_run)
    critic = reviewer("one", LLMError("gone away"))
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2 and step.code == loop.NO_CLEAN_REVIEW and loop.CLEAN not in step.text


def test_an_unknown_case_goes_back_as_an_error_too(world, monkeypatch):
    Runs(monkeypatch, now=lambda placed: refused_access(placed, {"test_bug": "failed", "test_smoke": "passed"}))
    critic = reviewer("one", "one")
    world.edit()
    step = world.advance(critic=critic)
    assert len(critic.calls) == 2 and "PermissionError" in critic.calls[1][1]
    assert step.code == loop.NOT_REPRODUCED and loop.CLEAN not in step.text


def test_a_test_that_will_not_run_beside_a_failing_one_is_not_retried_and_later_blocks_the_clean_end(world, monkeypatch):
    def now(placed: str) -> dict:
        if placed.endswith("/test_critic.py"):
            return ran(placed, {"test_critic": outcome[0]})
        return will_not_run()

    outcome = ["failed"]
    Runs(monkeypatch, now=now)
    critic = reviewer("one", "two", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    assert world.advance(critic=critic).action == loop.CONTINUE
    assert len(critic.calls) == 2      # what failed went back, so nothing here could end clean, and no reviewer was asked again
    world.edit(FIXED + "# 2\n")
    outcome[0] = "passed"
    step = world.advance(critic=critic)
    assert len(critic.calls) == 3 and loop.CLEAN not in step.text and step.code == loop.NOT_REPRODUCED


# --- a session's loops, through the hooks and the real loop: the defect was in the join between them ---------------------------

def approved(monkeypatch, critic) -> None:
    """The person's key file approves a reviewer, which is how a Stop hook finds ``critic``."""
    monkeypatch.setattr(keys, "read", lambda: {loop.CRITIC_LINE: "codex"})
    monkeypatch.setattr(host_models, "CodexCaller", lambda timeout: critic)


def prompted(world, text: str) -> dict:
    return hook.on_prompt({"session_id": "s1", "cwd": str(world.repo), "prompt": text})


def stopped(world, said: str = "") -> dict:
    return hook.on_stop({"session_id": "s1", "cwd": str(world.repo), "last_assistant_message": said})


def test_a_new_prompt_after_a_loop_ended_arms_a_new_one(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    assert loop.CLEAN in stopped(world)["systemMessage"]
    prompted(world, "now handle strings too")
    world.edit(FIXED + "# strings\n")
    said = stopped(world)
    assert len(critic.calls) == 2, "the next task in the session went unreviewed"
    assert "now handle strings too" in critic.calls[1][1] and "# strings" in critic.calls[1][1]
    assert loop.CLEAN in said["systemMessage"]


def test_each_loop_of_a_session_keeps_its_own_row_and_patches(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    prompted(world, "and again")
    world.edit(FIXED + "# again\n")
    stopped(world)
    first, second = rows(world)
    assert first["session"] != second["session"]
    for row in (first, second):
        kept = (world.session.parent / row["session"] / "rounds" / "round-1.patch").read_bytes().decode("utf-8")
        assert digest(kept) == row["reviews"][0]["patch"]


def test_two_loops_on_one_base_are_two_runs_and_a_third_is_armed_too(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none", "none")
    approved(monkeypatch, critic)
    prompted(world, "one")
    for n in range(3):
        world.edit(FIXED + f"# {n}\n")
        stopped(world)
        prompted(world, f"then {n}")
    assert len(critic.calls) == 3 and len({row["session"] for row in rows(world)}) == 3


def test_a_commit_between_two_tasks_moves_the_base_and_the_task_is_the_new_prompt(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    git(world.repo, "add", "mod.py")
    git(world.repo, "commit", "-qm", "the first task")
    head = git(world.repo, "rev-parse", "HEAD")
    prompted(world, "handle strings")
    assert json.loads((hook.folder("s1") / "task.json").read_text(encoding="utf-8"))["base"] == head
    world.edit(FIXED + "# strings\n")
    stopped(world)
    assert "handle strings" in critic.calls[1][1] and "fix double" not in critic.calls[1][1]
    assert f"parent/mod.py:\n{FIXED}" in critic.calls[1][1]


def test_on_one_base_the_earlier_task_stays_as_the_patch_still_holds_that_work(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    prompted(world, "handle strings")
    world.edit(FIXED + "# strings\n")
    stopped(world)
    assert "fix double" in critic.calls[1][1] and "handle strings" in critic.calls[1][1]


def test_a_prompt_that_changes_nothing_after_a_loop_ended_spends_no_review_and_says_why(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "never")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    prompted(world, "thanks")
    said = stopped(world)
    assert len(critic.calls) == 1
    assert "did not run" in said.get("systemMessage", ""), "the person was told nothing"
    assert "has not changed" in said["systemMessage"] and "\n" not in said["systemMessage"]
    world.edit(FIXED + "# a real change after all\n")
    assert loop.CLEAN in stopped(world, "done")["systemMessage"] and len(critic.calls) == 2


def test_a_stop_with_no_python_changed_tells_the_person_in_one_line(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("never")
    approved(monkeypatch, critic)
    prompted(world, "what does double do?")
    said = stopped(world)
    assert "did not run" in said.get("systemMessage", "") and "no Python changed" in said["systemMessage"]
    assert "\n" not in said["systemMessage"] and not critic.calls


def test_a_stop_after_a_loop_ended_with_no_new_prompt_says_so(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer("none", "never")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    world.edit(FIXED + "# later\n")
    said = stopped(world)
    assert "did not run" in said.get("systemMessage", "") and "\n" not in said["systemMessage"]
    assert len(critic.calls) == 1


def test_a_refused_run_is_armed_again_by_the_next_prompt(world, monkeypatch):
    Reviewed(monkeypatch)
    monkeypatch.setattr(keys, "read", lambda: {})
    prompted(world, "fix double")
    world.edit()
    assert "did not run" in stopped(world)["systemMessage"]
    critic = reviewer("none")
    approved(monkeypatch, critic)      # the person added the line the report named
    prompted(world, "I added the line")
    stopped(world)
    assert len(critic.calls) == 1


def test_a_row_the_log_kept_out_is_written_before_a_new_prompt_leaves_its_run_behind(world, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    with _coordination.Coordination(world.session.parent / ".failures").mutex():
        stopped(world, "They misread the task.")
    assert not rows(world)
    prompted(world, "thanks")
    assert len(rows(world)) == 1


def test_a_prompt_while_a_loop_runs_arms_nothing_and_moves_nothing(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    before = (hook.folder("s1") / "task.json").read_text(encoding="utf-8")
    prompted(world, "and one more thing")
    assert (hook.folder("s1") / "task.json").read_text(encoding="utf-8") == before

def test_a_review_that_failed_read_nothing_so_the_next_prompt_may_review_the_same_patch(world, monkeypatch):
    Reviewed(monkeypatch)
    critic = reviewer(LLMError("gone away"), "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    assert "gone away" in stopped(world)["systemMessage"]
    prompted(world, "try that again")
    said = stopped(world)
    assert len(critic.calls) == 2, "a review that failed was counted as having read the patch"
    assert loop.CLEAN in said["systemMessage"]

# --- a question is not a refusal: what a real agent writes when it has edited nothing, each way --------------------------------

ASKS = ["Shall I fix this?",
        "The reviewer is right that negative numbers fail here. Would you like me to fix it?",
        "Do you want me to change `double`, or leave it as it is?\n\nLet me know.",
        "**Should I go ahead with this repair?**",
        "Should I repair this? I'll wait for your answer.",
        "The finding looks valid to me.\n\nWant me to apply the fix?",
        "Should I change it? Please confirm."]
DECIDES = ["The task says x is never negative, so the reviewer's test asks for something else. I left the code as it was.",
           "Why would x be negative here? The task rules it out, so I am leaving the code alone.",
           "Not a defect: the reviewer misread the task.",
           "I am leaving the code as it is. Let me know if you want it changed.",
           "No change: `double('a')` is outside the task (is it even reachable? no)."]


@pytest.mark.parametrize("said", ASKS)
def test_a_reply_that_asks_a_question_and_edits_nothing_rejects_nothing(world, monkeypatch, said):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    world.advance(critic=critic)
    step = world.advance(critic=critic, said=said)
    assert world.state().get("stop") != loop.LEFT and not world.state().get("done"), "a question was recorded as a refusal"
    assert (step.action, step.code) == (loop.WAIT, loop.WAITING_ON_PERSON) and step.text == loop.ASKED
    assert not rows(world) and len(critic.calls) == 1


@pytest.mark.parametrize("said", DECIDES)
def test_a_reply_that_decides_is_still_a_refusal_whatever_it_mentions(world, monkeypatch, said):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    world.edit()
    world.advance(critic=critic)
    step = world.advance(critic=critic, said=said)
    assert step.action == loop.STOP and world.state()["stop"] == loop.LEFT
    assert world.state()["left"] == " ".join(said.split())


def test_the_persons_yes_to_the_agents_question_is_reviewed(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "none")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    assert stopped(world)["decision"] == "block"
    said = stopped(world, "The reviewer is right. Shall I fix this?")
    assert "decision" not in said      # a block would keep the question from the person, and go round for ever
    assert said == {"systemMessage": loop.ASKED} and "\n" not in said["systemMessage"]
    prompted(world, "yes")
    world.edit(FIXED + "# repaired\n")
    tests.failing = set()
    ended = stopped(world, "Fixed it.")["systemMessage"]
    assert len(critic.calls) == 2 and "**Fixed:** review 1" in ended and "left the code" not in ended


def test_the_persons_no_to_the_agents_question_then_counts_as_the_refusal_it_is(world, monkeypatch):
    Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "never")
    approved(monkeypatch, critic)
    prompted(world, "fix double")
    world.edit()
    stopped(world)
    stopped(world, "Shall I fix this?")
    prompted(world, "no, leave it")
    ended = stopped(world, "Leaving the code as it is: the task asks for this.")["systemMessage"]
    assert "the agent left the code as it was" in ended and "the task asks for this" in ended


def test_a_changed_tree_is_a_reply_though_the_words_ask_a_question(world, monkeypatch):
    tests = Reviewed(monkeypatch, "test_critic.py")
    critic = reviewer("one", "none")
    world.edit()
    world.advance(critic=critic)
    world.edit(FIXED + "# 1\n")
    tests.failing = set()
    step = world.advance(critic=critic, said="I changed it. Does that look right to you?")
    assert step.action == loop.STOP and loop.CLEAN in step.text and len(critic.calls) == 2


def test_a_question_on_the_scouts_loop_is_no_refusal_either(world, monkeypatch):
    scouts_chosen(monkeypatch)
    Runs(monkeypatch, now=dict(CASES))
    world.edit()
    world.advance()
    step = world.advance(said="Shall I repair these cases?")
    assert (step.action, step.code) == (loop.WAIT, loop.WAITING_ON_PERSON) and not world.state().get("done")