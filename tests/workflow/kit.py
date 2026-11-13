# SPDX-License-Identifier: Apache-2.0
"""Synthetic workflow records for the section 17 tests: fixed ids, fixed clocks, no real task or repository text."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from g6.harness.workflow import record as rec

T0 = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
BASE = hashlib.sha256(b"baseline").hexdigest()
ART = hashlib.sha256(b"artefact").hexdigest()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def at(seconds: float = 0, *, days: float = 0) -> str:
    return (T0 + timedelta(seconds=seconds, days=days)).strftime(rec.TIME_FORMAT)


PROJECT, OTHER_PROJECT = sha("project one"), sha("project two")
TASK, OTHER_TASK = sha("task one"), sha("task two")


def event(run: str, kind: str, native: str = "", *, project: str = PROJECT, task: str = TASK, seconds: float = 0,
          days: float = 0, provenance: str = "host_observed", data: dict | None = None, refs=(), missing=(),
          observed=()) -> rec.Event:
    return rec.Event(project=project, task=task, run=run, event_id=rec.event_id(run, kind, native), kind=kind,
                     at=at(seconds, days=days), provenance=provenance, sources=(rec.workflow_source(project),),
                     baseline_sha256=BASE, artifact_sha256=ART, refs=tuple(refs), observed=tuple(observed),
                     missing=tuple(missing), data=data or {})


def started(record: rec.ProjectRecord, origin: str = "session-a", *, task: str = TASK, kind: str = "loop_session",
            seconds: float = 0, days: float = 0) -> str:
    return record.start_run(task, kind, origin, baseline_sha256=BASE, at=at(seconds, days=days))


def make_repo(folder):
    """A git repository with one commit, for the adapters (they take the task's identity from its top folder)."""
    import subprocess

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)
    return folder


class Step:
    """A loop step as the adapters read it."""

    def __init__(self, action: str, ending: str = "", code: str = "") -> None:
        self.action, self.ending, self.code = action, ending, code


def loop_state(reviews: int, *, failing=(("tests/test_a.py::test_one",),), done: bool = False, ending: str = "") -> dict:
    """A completion loop's saved state with ``reviews`` review rounds; ``failing`` gives each repair round's names."""
    # each review names the reviewer's test file it wrote; here the first review's test stays failing throughout
    state = {"reviews": [{"n": n, "verdict": "defect", "file": f"g6_review_{n}/test_a.py", "rule": "r", "why": "w",
                          "results": {"g6_review_1/test_a.py": "failed"}, "patch": sha(f"patch {n}")}
                         for n in range(1, reviews + 1)],
             "rounds": [{"failing": list(names), "text": "", "tree": None} for names in failing[:reviews]]}
    if done:
        state.update(done=True, ending=ending, code="")
    return state


def call(server, tool: str, arguments: dict, *, client: str = "codex", handler=None) -> dict:
    """One tool call through a real in-memory MCP session, as the named client; the structured reply."""
    import asyncio

    from fastmcp import Client
    from mcp.types import Implementation

    async def run():
        async with Client(server, client_info=Implementation(name=client, version="test"),
                          **({"elicitation_handler": handler} if handler else {})) as session:
            reply = await session.call_tool(tool, arguments, raise_on_error=False)
            return reply.structured_content if reply.structured_content is not None else {"text": str(reply.content)}
    return asyncio.run(run())