# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the fresh-user MCP journey (``test_fresh_journey.py``): a tiny git repository with an uncommitted
patch, the review's test runs recorded, a scripted person who answers and logs each dialog, and an agent that builds
each call only from the templates in G6's replies.

The agent's rule: a call is a template G6 returned (``g6_select``'s ``next`` or a candidate, a refused reply's
``repair``), its placeholders filled from the situation (the task's text, the repository's folder) or from what an
earlier reply or the agent's own refused call said. It writes no other argument. A reply withheld for consent is
repaired as its ``repair`` block says: one ``g6_cloud_grant`` per withheld source, the person answering each dialog,
then the same call again.

The review's test runs are recorded (``RecordedRuns``), as ``tests/harness/test_patch_review.py`` records them,
because a review runs code only inside an operating-system boundary and not every machine the suite runs on has one
ready (Windows needs a one-time access grant, macOS has none); the classification of those runs is G6's own code."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastmcp.client.elicitation import ElicitResult

TASK = "Change add(a, b) in calc.py so it adds two integers and returns their sum as an int."
BASE = "def add(a, b):\n    return a + b\n"
PATCH = "def add(a, b):\n    return int(a) - int(b)\n"     # the defect: subtracts
TEST_FILE = "tests/test_calc.py"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
NODE = TEST_FILE + "::test_add"
CONSENT = "G6_E_CLOUD_CONSENT_REQUIRED"
_WITHHELD = re.compile(r"Withheld source identifiers: (.+?)\. Provider: (\S+?)\.")


def make_repo(root: Path) -> Path:
    """A git repository whose base commit adds correctly, with an uncommitted patch that subtracts."""
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(BASE, encoding="utf-8")
    (root / TEST_FILE).write_text(TEST, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                                  "-c", "commit.gpgsign=false", "commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "calc.py").write_text(PATCH, encoding="utf-8")
    return root


def _result(passed=(), failed=()) -> dict:
    """One recorded ``repo_tests.run`` result, in the shape ``tests/harness/test_patch_review.py`` records."""
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    return {"verdict": "failed" if failed else "passed", "said": "", "rootdir": "", "passed": len(passed),
            "failed": len(failed), "not_passed": 0, "total": len(passed) + len(failed), "deselected": 0,
            "test_outcomes": outcomes, "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [],
            "output": "", "seconds": 0.25, "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "recorded-e2e-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": "", "failure_messages": {}, "collection_stopped": None}


class RecordedRuns:
    """Stands in for every test run of a review (``patch_review._execute`` and ``_execute_at_base``), as the fixture's
    code would run: on the patched tree the repository's test fails; replayed in a copy it passes at the base commit
    and fails on the patch (``repo_tests.run_before``'s report). Records what was asked."""

    def __init__(self, monkeypatch) -> None:
        from g6.harness import patch_review

        self.calls: list[tuple[str, tuple[str, ...]]] = []

        def execute(root, ids, *, timeout_s, change=None, roots=()):
            self.calls.append(("base" if change is not None else "patch", tuple(ids)))
            if change is not None:
                return {rel: {**_result(failed=[NODE]), "baseline": "passed", "patched": "failed"} for rel in ids}
            return _result(failed=[NODE])

        def execute_at_base(change, ids, *, timeout_s, roots=()):
            self.calls.append(("at_base", tuple(ids)))
            return {"verdict": "not_run", "said": "no run was recorded", "output": "", "rootdir": "",
                    "test_outcomes": None, "failed_ids": [], "setup_errors": {}, "collected_ids": [],
                    "omitted_ids": []}

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)


class Person:
    """The scripted person: accepts each sharing dialog with a name, answers a behaviour question with option A,
    declines anything else, and logs every dialog (its kind and text)."""

    NAME = "Fixture person"

    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []

    async def __call__(self, message, response_type, params, context):
        fields = set((getattr(params, "requestedSchema", None) or {}).get("properties", {}))
        if "cannot be recalled" in message:
            self.log.append({"kind": "share", "message": message})
            return response_type(value=self.NAME)
        if "choice" in fields:
            self.log.append({"kind": "behaviour", "message": message})
            return {"choice": "A", "name": self.NAME}
        self.log.append({"kind": "other", "message": message})
        return ElicitResult(action="decline")

    def kinds(self, start: int = 0) -> list[str]:
        return [entry["kind"] for entry in self.log[start:]]


def data(reply) -> Any:
    """A tool's reply as JSON values (a list reply is read from its structured content, not FastMCP's objects)."""
    if isinstance(reply.data, dict):
        return reply.data
    content = reply.structured_content
    return content.get("result", content) if isinstance(content, dict) and set(content) == {"result"} else content


def withheld(reply: Any) -> dict | None:
    """The withheld reply itself (or the one a finished job wraps), or None."""
    if not isinstance(reply, dict):
        return None
    for value in (reply, reply.get("result")):
        if isinstance(value, dict) and value.get("cloud_consent_code") == CONSENT:
            return value
    return None


def placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("<") and value.endswith(">")


def fill(call: dict, known: dict[str, Any]) -> tuple[str, dict]:
    """A template's tool and its arguments, each placeholder filled from ``known`` (the situation and what earlier
    replies said); a placeholder with nothing to fill it fails the step that needed it."""
    assert isinstance(call, dict) and isinstance(call.get("arguments"), dict), f"no call template: {call!r}"
    arguments = {}
    for key, value in call["arguments"].items():
        if placeholder(value):
            assert key in known, (f"the template for {call['tool']} leaves {key} as {value} and no earlier reply or "
                                  f"the task gives it")
            arguments[key] = known[key]
        else:
            arguments[key] = value
    return call["tool"], arguments


class Agent:
    """The scripted agent over one in-process MCP client."""

    def __init__(self, client, person: Person, situation: dict[str, Any]) -> None:
        self.client, self.person, self.situation = client, person, situation
        self.calls: list[tuple[str, dict]] = []
        self.grants: list[dict] = []

    async def raw(self, tool: str, arguments: dict) -> Any:
        self.calls.append((tool, arguments))
        return data(await self.client.call_tool(tool, arguments))

    async def share(self, refused: dict) -> None:
        """Repair a withheld reply as its repair block says: the person answers one g6_cloud_grant dialog per source.
        The source and provider are read from G6's fixed withheld line, since the template leaves them to fill."""
        repair = refused.get("repair") or {}
        assert repair.get("by") == "person" and (repair.get("call") or {}).get("tool") == "g6_cloud_grant", repair
        said = _WITHHELD.search(refused.get("error", ""))
        assert said, refused.get("error")
        for source in said.group(1).split(", "):
            tool, arguments = fill(repair["call"], {"source": source, "provider": said.group(2)})
            got = await self.raw(tool, arguments)
            assert got.get("ok") is True, got
            self.grants.append(arguments)

    async def call(self, tool: str, arguments: dict, *, rounds: int = 3) -> Any:
        """``tool`` with ``arguments``; a reply withheld for consent is shared, as the person decides, and the same
        call made again."""
        for _ in range(rounds):
            got = await self.raw(tool, arguments)
            refused = withheld(got)
            if refused is None:
                return got
            await self.share(refused)
        return got

    async def finished(self, reply: Any, *, polls: int = 600) -> Any:
        """A job's finished reply, polled through the metaharness_job template its reply names."""
        if not isinstance(reply, dict) or "job_id" not in reply:
            return reply
        for _ in range(polls):
            got = await self.call("metaharness_job", {"job_id": reply["job_id"]})
            if got.get("status") in ("completed", "failed") or "job_id" not in got:
                return got
            await asyncio.sleep(0.02)
        raise AssertionError(f"job {reply['job_id']} did not finish")

    async def select(self, task: str, phase: str = "", last_outcome: Any = "") -> dict:
        arguments = {"task": task, "phase": phase}
        if last_outcome:
            arguments["last_outcome"] = last_outcome if isinstance(last_outcome, str) else json.dumps(last_outcome)
        got = await self.call("g6_select", arguments)
        assert got.get("ok") is True and got.get("completion_state") == "qualified-draft", got
        return got


__all__ = ["Agent", "BASE", "CONSENT", "NODE", "PATCH", "Person", "RecordedRuns", "TASK", "TEST_FILE", "data",
           "fill", "make_repo", "placeholder", "withheld"]
