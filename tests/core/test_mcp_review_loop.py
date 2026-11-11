# SPDX-License-Identifier: Apache-2.0
"""``g6_review_until_clean``: the completion loop for a client the plugin's Stop hook cannot reach. Its confined runs are
recorded results and its critic a fake, so no model is called and nothing a model wrote runs."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from g6.core import host_models, keys
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_hook as hook
from g6.harness import completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.invariants import check_envelope
from g6.workforce import repo_tests
from g6.workspace import _coordination

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from g6.mcp import build_server, review_loop  # noqa: E402

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")

TASK = "double must return twice its argument"
ENVELOPE = ("completion_state", "warning_card", "evidence", "request_id", "run_id")


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


def reviewer(*replies) -> FakeLLM:
    left = list(replies)

    def reply(system: str, user: str) -> str:
        said = left.pop(0)
        if isinstance(said, Exception):
            raise said
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        if said == "none":
            return f"READ: {proof}\nRULE: no rule\nVERDICT: none\nWHY: nothing\n"
        return (f"READ: {proof}\nRULE: rule {said}\nVERDICT: defect\nWHY: why {said}\n```python\n"
                "def test_critic():\n    assert False\n```\n")

    return FakeLLM(reply)


class Rig:
    """A repository with the bug at its base commit, a fresh G6 home, and a server; the boundary, the final review and the
    runs of the reviewers' tests are stood in for."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
        monkeypatch.setattr(proposer, "boundary", lambda root: "")
        monkeypatch.setattr(loop, "review_patch", lambda repo, **asked: {"assessment": {"report": "THE REVIEW"}})
        monkeypatch.setattr(vs, "_run_check", self._run_check)
        self.failing: set[str] = {"test_critic"}
        self.repo = tmp_path / "project"
        self.repo.mkdir()
        (self.repo / "mod.py").write_text("def double(x):\n    return x + 2\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.server = build_server()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def _run_check(self, change, placed, source, node, *, at_base, timeout_s, roots=()):
        from tests.conftest import recorded_synthetic_check
        got = "failed" if self.failing else "passed"
        return recorded_synthetic_check({"verdict": got, "said": "", "rootdir": "", "loaded_outside": {}, "loaded_complete": True, "os_errors": [],
                "test_outcomes": {f"{placed}::test_critic": {"setup": "passed", "call": got, "teardown": "passed"}},
                "failure_messages": {f"{placed}::test_critic": "assert False"} if self.failing else {}})

    def approve(self, critic) -> None:
        self.monkeypatch.setattr(keys, "read", lambda: {loop.CRITIC_LINE: "codex"})
        self.monkeypatch.setattr(host_models, "CodexCaller", lambda timeout: critic)

    def edit(self, text: str = "def double(x):\n    return x * 2\n") -> None:
        (self.repo / "mod.py").write_text(text, encoding="utf-8")

    def call(self, tool: str, **arguments) -> dict:
        async def flow():
            async with Client(self.server) as client:
                return data(await client.call_tool(tool, arguments))

        return asyncio.run(flow())

    def step(self, **more) -> dict:
        """One step, polled as an agent polls it; a refusal before any job is returned as it is."""
        async def flow():
            async with Client(self.server) as client:
                job = data(await client.call_tool("g6_review_until_clean", {
                    "repo": str(self.repo), "task": TASK, "session": "one", "base": self.base, **more}))
                if "job_id" not in job:
                    return job
                for _ in range(3000):
                    got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                    if got.get("status") == "completed":
                        return got["result"]
                    if got.get("status") == "failed":
                        return got
                    await asyncio.sleep(0.02)
                raise AssertionError("the job did not finish")

        return asyncio.run(flow())


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def enveloped(reply: dict) -> None:
    assert check_envelope(reply) == [] and all(field in reply for field in ENVELOPE)
    assert reply["request_id"] and reply["run_id"]
    assert reply["completion_state"] != "verified"
    json.dumps(reply)      # JSON all the way down


def test_the_tool_is_on_the_surface_and_says_it_is_a_job_a_client_polls(rig):
    tools = asyncio.run(rig.server.get_tools())
    said = " ".join(tools["g6_review_until_clean"].description.split())
    assert "metaharness_job" in said and "patch_review.review" in said and "G6_LOOP_CRITIC=codex" in said
    assert "seven training bugs" in said
    assert set(tools["g6_review_until_clean"].parameters["required"]) == {"repo", "task", "session"}


@pytest.mark.parametrize("allowed", ["", "workforce.mission"])
def test_it_needs_the_permission_the_review_needs(rig, monkeypatch, allowed):
    monkeypatch.setenv("G6_MCP_SENSITIVE", allowed)
    critic = reviewer("none")
    rig.approve(critic)
    rig.edit()
    got = rig.step()
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert got["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and "patch_review.review" in got["error"]
    assert not critic.calls


def test_a_step_that_finds_a_failure_says_continue_with_the_text_for_the_agent(rig):
    rig.approve(reviewer("one"))
    rig.edit()
    got = rig.step()
    assert got["ok"] is True and got["action"] == "continue"
    assert "rule one" in got["text"] and got["text"].endswith(loop.harness().review_ask)
    assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_REVIEW_UNTIL_CLEAN"
    assert got["evidence"][0]["summary"].startswith("review 1: defect")
    enveloped(got)


def test_a_clean_review_says_stop_with_the_report_and_no_more_than_a_draft(rig):
    rig.approve(reviewer("none"))
    rig.failing = set()
    rig.edit()
    got = rig.step()
    assert got["ok"] is True and got["action"] == "stop" and "THE REVIEW" in got["report"]
    assert "a review reproduced nothing" in got["report"] and got["completion_state"] == "qualified-draft"
    assert "not evidence the work is right" in got["warning_card"]["message"]
    enveloped(got)


def test_the_steps_of_a_session_run_the_whole_loop_across_calls(rig):
    critic = reviewer("one", "none")
    rig.approve(critic)
    rig.edit()
    assert rig.step()["action"] == "continue"
    rig.edit("def double(x):\n    return 2 * x\n")
    rig.failing = set()
    got = rig.step()
    assert got["action"] == "stop" and len(critic.calls) == 2 and "return 2 * x" in critic.calls[1][1]
    assert len(got["evidence"]) == 2


def test_the_agents_reason_goes_with_its_call(rig):
    rig.approve(reviewer("one"))
    rig.edit()
    rig.step()
    got = rig.step(reason="The task asks for this.")
    assert "the agent left the code as it was" in got["report"] and "The task asks for this." in got["report"]
    assert got["completion_state"] == "qualified-draft"
    enveloped(got)


def test_a_reason_that_is_a_question_rejects_nothing_and_says_who_must_answer(rig):
    critic = reviewer("one", "none")
    rig.approve(critic)
    rig.edit()
    rig.step()
    got = rig.step(reason="Shall I fix this?")
    assert "report" not in got and got["action"] == loop.WAIT and got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == loop.WAITING_ON_PERSON and "waiting for your answer" in got["warning_card"]["message"]
    enveloped(got)
    assert len(critic.calls) == 1
    rig.edit("def double(x):\n    return 2 * x\n")      # the person said yes
    rig.failing = set()
    assert rig.step()["action"] == "stop" and len(critic.calls) == 2


def test_a_review_that_fails_is_blocked_and_says_so_and_carries_the_report(rig):
    rig.approve(reviewer(LLMError("no such program")))
    rig.edit()
    got = rig.step()
    assert got["ok"] is False and got["action"] == "stop" and got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_NO_CLEAN_REVIEW" and "no such program" in got["report"]
    enveloped(got)


def test_a_defect_that_could_not_be_reproduced_is_blocked_and_says_so_and_carries_the_report(rig, monkeypatch):
    from tests.conftest import recorded_synthetic_check
    critic = reviewer("one", "one")
    rig.approve(critic)
    monkeypatch.setattr(vs, "_run_check", lambda *args, **more: recorded_synthetic_check(
        {"verdict": "collection_error", "said": "no such import"}))
    rig.edit()
    got = rig.step()
    assert len(critic.calls) == 2 and got["ok"] is False and got["action"] == "stop"
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == loop.NOT_REPRODUCED
    assert "a defect was claimed and could not be reproduced" in got["warning_card"]["message"]
    assert "**No clean review:**" in got["report"]
    enveloped(got)


def test_a_stop_names_its_ending_beside_the_three_states(rig, monkeypatch):
    rig.approve(reviewer("none"))
    rig.failing = set()
    rig.edit()
    got = rig.step()
    assert "**Ending:** `clean`" in got["report"] and got["ending"] == "clean" and got["completion_state"] == "qualified-draft"
    enveloped(got)


def test_a_rejected_finding_ends_contested_and_stays_a_draft(rig):
    rig.approve(reviewer("one"))
    rig.edit()
    rig.step()
    got = rig.step(reason="They misread the task.")
    assert got["ending"] == "contested" and got["completion_state"] == "qualified-draft"
    assert rig.step()["ending"] == "contested"      # a repeat says the same
    enveloped(got)


def test_a_stop_that_tested_nothing_ends_untested_and_stays_blocked(rig):
    rig.approve(reviewer(LLMError("no such program")))
    rig.edit()
    got = rig.step()
    assert got["ending"] == "untested" and got["completion_state"] == "blocked-escalated"
    enveloped(got)


def test_a_step_that_goes_on_has_no_ending(rig):
    rig.approve(reviewer("one"))
    rig.edit()
    assert "ending" not in rig.step()


def test_nothing_approved_is_blocked_with_how_to_turn_it_on(rig, monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {})
    rig.edit()
    got = rig.step()
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == keys.KEY_MISSING
    assert "G6_LOOP_CRITIC=codex" in got["report"]
    enveloped(got)


def test_no_boundary_is_blocked_and_no_reviewer_is_asked(rig, monkeypatch):
    critic = reviewer("none")
    rig.approve(critic)
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    rig.edit()
    got = rig.step()
    assert got["completion_state"] == "blocked-escalated" and "no backend here" in got["report"]
    assert not critic.calls
    enveloped(got)


def test_with_no_python_changed_nothing_is_tested_and_nothing_is_spent(rig):
    critic = reviewer("none")
    rig.approve(critic)
    got = rig.step()
    assert got["action"] == "stop" and got["report"] == "" and "nothing was tested" in got["warning_card"]["message"]
    assert not critic.calls
    enveloped(got)


def test_a_stop_that_ran_nothing_is_blocked_with_its_stable_code_and_ends_nothing(rig):
    critic = reviewer("one")
    rig.approve(critic)
    got = rig.step()
    assert got["completion_state"] == "blocked-escalated" and got["ok"] is False and got["action"] == "stop"
    assert got["warning_card"]["code"] == "G6_E_NO_PYTHON_CHANGED" and "nothing was tested" in got["warning_card"]["message"]
    assert got["report"] == "" and got["evidence"] == [] and not critic.calls
    enveloped(got)
    rig.edit()
    assert rig.step()["action"] == "continue"      # no loop had started, so the session is free to start one


def test_a_call_after_the_loop_ended_gives_the_same_report_and_spends_nothing(rig):
    critic = reviewer("none", "never")
    rig.approve(critic)
    rig.failing = set()
    rig.edit()
    first = rig.step()
    again = rig.step()
    assert again["report"] == first["report"] and len(critic.calls) == 1


def test_repeating_a_failed_review_keeps_its_state_and_its_stable_code(rig):
    rig.approve(reviewer(LLMError("no such program"), "never"))
    rig.edit()
    first = rig.step()
    again = rig.step()
    assert again["report"] == first["report"]
    assert (again["ok"], again["completion_state"], again["warning_card"]["code"]) == (
        False, "blocked-escalated", "G6_E_NO_CLEAN_REVIEW")
    enveloped(again)


def test_repeating_a_refused_step_keeps_its_state_and_its_stable_code(rig, monkeypatch):
    monkeypatch.setattr(keys, "read", lambda: {})
    rig.edit()
    rig.step()
    again = rig.step()
    assert (again["ok"], again["completion_state"], again["warning_card"]["code"]) == (
        False, "blocked-escalated", keys.KEY_MISSING)
    assert "G6_LOOP_CRITIC=codex" in again["report"]


def test_repeating_a_stop_with_a_finding_left_open_stays_a_draft(rig):
    rig.approve(reviewer("one"))
    rig.edit()
    rig.step()
    first = rig.step(reason="They misread the task.")
    again = rig.step()
    assert again["report"] == first["report"]
    assert (again["ok"], again["completion_state"], again["warning_card"]["code"]) == (
        True, "qualified-draft", "G6_E_REVIEW_UNTIL_CLEAN")


def race_for_a_session(rig, monkeypatch, repo: Path, task: str) -> list[dict]:
    """Two first calls for one session, the second while the first is still working out what it asks."""
    reached, release, real = threading.Event(), threading.Event(), repo_tests.change

    def paused(where, *args, **kwargs):
        if Path(where) == rig.repo.resolve() and not reached.is_set():
            reached.set()
            assert release.wait(30)
        return real(where, *args, **kwargs)

    monkeypatch.setattr(repo_tests, "change", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(review_loop.step, str(rig.repo), TASK, "racing", rig.base)
        assert reached.wait(30)
        second = pool.submit(review_loop.step, str(repo), task, "racing")
        try:
            second.result(timeout=0.2)
        except TimeoutError:
            pass      # a call that waits its turn is held here
        release.set()
        return [first.result(timeout=30), second.result(timeout=30)]


def bound(session: str) -> dict:
    return json.loads((hook.folder(f"mcp:{session}") / "task.json").read_text(encoding="utf-8"))


def test_racing_first_calls_bind_a_session_to_one_repository(rig, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "mod.py").write_text("x = 1\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "b")):
        subprocess.run(["git", "-C", str(other), *args], check=True, capture_output=True)    # a repository a session could bind
    rig.approve(reviewer("none"))
    rig.failing = set()
    rig.edit()
    answers = race_for_a_session(rig, monkeypatch, other, "another task")
    assert [answer["ok"] for answer in answers] == [True, False]
    assert bound("racing") == {"repo": str(rig.repo.resolve()), "task": TASK, "base": rig.base}


def test_racing_first_calls_bind_a_session_to_one_task(rig, monkeypatch):
    rig.approve(reviewer("none"))
    rig.failing = set()
    rig.edit()
    answers = race_for_a_session(rig, monkeypatch, rig.repo, "another task")
    assert [answer["ok"] for answer in answers] == [True, False]
    assert bound("racing")["task"] == TASK


def test_a_session_cannot_move_to_another_task(rig):
    critic = reviewer("one", "never")
    rig.approve(critic)
    rig.edit()
    rig.step()
    got = rig.step(task="another task")
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "another task" in got["error"] and len(critic.calls) == 1
    assert bound("one")["task"] == TASK


def test_a_binding_is_written_whole_or_not_at_all(rig, monkeypatch):
    rig.approve(reviewer("one"))
    rig.edit()
    real = os.replace

    def killed(*args):
        raise OSError("the call was cut short")

    monkeypatch.setattr(os, "replace", killed)
    with pytest.raises(OSError):
        review_loop.step(str(rig.repo), TASK, "one", rig.base)
    monkeypatch.setattr(os, "replace", real)
    assert not (hook.folder("mcp:one") / "task.json").exists()
    assert rig.step()["action"] == "continue"      # the session is free, and its task can still be bound


def test_a_retry_with_the_code_unchanged_and_no_reason_gets_the_same_finding_and_spends_nothing(rig):
    critic = reviewer("one", "never")
    rig.approve(critic)
    rig.edit()
    first = rig.step()
    again = rig.step()
    assert (again["ok"], again["action"]) == (True, "continue") and again["text"] == first["text"]
    assert len(critic.calls) == 1
    enveloped(again)
    assert rig.step(reason="   ")["action"] == "continue"      # blank says nothing
    stopped = rig.step(reason="They misread the task.")
    assert stopped["action"] == "stop" and "the agent left the code as it was" in stopped["report"]


def test_two_calls_queued_on_one_server_are_one_step_and_one_finding(rig):
    """A server's jobs run one at a time, so a client's retry never overlaps: it runs just after the call it repeats."""
    critic = reviewer("one", "never")
    rig.approve(critic)
    rig.edit()
    arguments = {"repo": str(rig.repo), "task": TASK, "session": "one", "base": rig.base}

    async def flow():
        async with Client(rig.server) as client:
            jobs = [data(await client.call_tool("g6_review_until_clean", arguments)) for _ in range(2)]
            found = []
            for job in jobs:
                for _ in range(3000):
                    got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                    if got.get("status") in ("completed", "failed"):
                        found.append(got.get("result") or got)
                        break
                    await asyncio.sleep(0.02)
            return found

    first, second = asyncio.run(flow())
    assert (first["action"], second["action"]) == ("continue", "continue") and first["text"] == second["text"]
    assert len(critic.calls) == 1


def test_a_call_that_finds_another_step_running_is_refused_and_starts_nothing(rig, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    critic = reviewer("one")
    rig.approve(critic)
    rig.edit()
    folder = hook.folder("mcp:one")
    folder.mkdir(parents=True)
    def read(session):
        raise AssertionError("the running step owns the state: on Windows a reader can make its replace of the file fail")

    real = loop._load
    monkeypatch.setattr(loop, "_load", read)
    with loop._held(folder):
        got = rig.step()
    monkeypatch.setattr(loop, "_load", real)
    assert got["ok"] is False and got["action"] == loop.WAIT and got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == loop.BUSY and "did nothing" in got["warning_card"]["message"]
    assert not critic.calls
    enveloped(got)
    assert rig.step()["action"] == "continue"      # the session was left as it was


def test_the_first_call_fixes_the_base_so_a_commit_cannot_move_it(rig):
    critic = reviewer("one", "none")
    rig.approve(critic)
    rig.edit()
    rig.step(base="HEAD")
    rig.git("add", ".")
    rig.git("commit", "-qm", "the agent's commit")
    rig.failing = set()
    got = rig.step(base="HEAD", reason="left as it was")
    assert got["action"] == "stop" and json.loads((hook.folder("mcp:one") / "task.json").read_text(encoding="utf-8"))["base"] == rig.base


def test_a_session_cannot_move_to_another_repository(rig, tmp_path):
    rig.approve(reviewer("one"))
    rig.edit()
    rig.step()
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    got = rig.step(repo=str(other))
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


@pytest.mark.parametrize("session", ["../../outside", "a/b", "C:\\x"])
def test_a_sessions_name_never_names_a_path(rig, session):
    rig.approve(reviewer("one"))
    rig.edit()
    rig.step(session=session)
    assert hook.folder(f"mcp:{session}").parent == hook.folder("other").parent
    assert (hook.folder(f"mcp:{session}") / "task.json").is_file()


@pytest.mark.parametrize("more", [{"task": "   "}, {"session": "  "}, {"base": "-x"}, {"repo": "not/absolute"}])
def test_a_bad_call_is_refused_and_starts_nothing(rig, more):
    critic = reviewer("one")
    rig.approve(critic)
    rig.edit()
    got = rig.step(**more)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert not critic.calls


def test_a_hook_session_and_a_tool_session_of_the_same_name_share_nothing():
    assert hook.folder("one") != hook.folder("mcp:one")
