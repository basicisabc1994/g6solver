# SPDX-License-Identifier: Apache-2.0
"""A network path is refused on its words, before any file system call or process reaches it (must-close under the
owner's 11-09 criteria: data leaves without a grant). On Windows, asking about ``\\\\host\\share`` (``is_dir``, ``stat``,
``resolve``, or a git run there) opens an SMB connection to that host, which sends the person's credentials over the
network. Each entry point that takes a repository or a folder is called with each spelling of a share, and every file
system call and process start that names the share is recorded and answered as absent, never made: the test fails on any.

Only Windows reads a backslash as a separator: elsewhere ``\\\\host\\share`` is a plain relative name, so only
``//host/share`` is a network spelling there (``storage._elsewhere``)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from g6.core import component_process
from g6.workforce import repo_tests

HOST = "g6-unc-host"
SHARES = ((rf"\\{HOST}\g6share\repo", f"//{HOST}/g6share/repo", rf"\\?\UNC\{HOST}\g6share\repo") if os.name == "nt"
          else (f"//{HOST}/g6share/repo",))


def _names_share(value) -> bool:
    if isinstance(value, (list, tuple)):
        return any(_names_share(one) for one in value)
    if isinstance(value, (str, bytes, os.PathLike)):
        try:
            return HOST in os.fsdecode(value).lower()
        except TypeError:
            return False
    return False


def _absent(*args, **kwargs):
    raise FileNotFoundError("the share is never asked about in this test")


@pytest.fixture
def share_calls(monkeypatch, tmp_path):
    """Every file system call and process start naming the share: recorded, and answered as absent without being made."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    calls: list[tuple[str, str]] = []

    def guard(owner, name, answer):
        real = getattr(owner, name)

        def recorded(*args, **kwargs):
            if _names_share(args) or _names_share(kwargs.get("cwd")):
                calls.append((name, repr(args[:2])))
                return answer(*args, **kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, recorded)

    for name in ("stat", "lstat", "scandir", "listdir", "open"):
        guard(os, name, _absent)
    for name in ("exists", "lexists", "isdir", "isfile"):
        guard(os.path, name, lambda *a, **k: False)
    guard(os.path, "realpath", _absent)
    for name in ("stat", "lstat", "iterdir", "open", "resolve"):
        guard(Path, name, _absent)
    for name in ("exists", "is_dir", "is_file"):
        guard(Path, name, lambda *a, **k: False)
    guard(component_process, "run_process", lambda *a, **k: 128)
    guard(subprocess.Popen, "__init__", _absent)
    return calls


@pytest.mark.parametrize("share", SHARES)
def test_checked_repo_refuses_a_share_on_its_words(share_calls, share):
    with pytest.raises(repo_tests.RepoRefused) as refused:
        repo_tests.checked_repo(share)
    assert refused.value.code == "G6_E_INPUT_VALIDATION"
    assert share_calls == []


@pytest.mark.parametrize("share", SHARES)
def test_the_completion_hook_never_asks_git_about_a_share(share_calls, share):
    from g6.harness import completion_hook

    assert completion_hook._here(share) is None
    assert share_calls == []


@pytest.mark.parametrize("share", SHARES)
def test_the_workflow_tools_refuse_a_share_before_asking_about_it(share_calls, share):
    from g6.mcp import workflow

    assert workflow._project(share) is None
    reply = workflow.export(share)
    assert reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert share_calls == []


@pytest.mark.parametrize("share", SHARES)
@pytest.mark.parametrize("ask", ["status", "start", "enrol", "stop"])
def test_the_self_improvement_tools_refuse_a_share_before_asking_about_it(share_calls, share, ask):
    """A stop too: no runner can be started on a share (start refuses one), so there is nothing there to stop, and a
    stop would open the share and write its STOP file there."""
    from g6.mcp import improve

    reply = {"status": lambda: improve.status(share), "start": lambda: improve.asked(share),
             "enrol": lambda: improve.asked_act("enrol", share), "stop": lambda: improve.stop(share, "done")}[ask]()
    assert isinstance(reply, dict) and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == improve.NOT_A_FOLDER
    assert share_calls == []


@pytest.mark.parametrize("share", SHARES)
def test_a_review_loop_step_refuses_a_share_with_its_code(share_calls, share):
    from g6.mcp import review_loop

    reply = review_loop.step(share, "keep the parser total", "session-1")
    assert reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert share_calls == []


@pytest.mark.parametrize("share", SHARES)
def test_g6_review_patch_over_mcp_never_asks_about_a_share(share_calls, monkeypatch, share):
    fastmcp = pytest.importorskip("fastmcp")
    from mcp.types import Implementation

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow() -> dict:
        async with fastmcp.Client(server, client_info=Implementation(name="claude-code", version="test")) as client:
            job = data(await client.call_tool("g6_review_patch", {"repo": share, "task": "keep the parser total",
                                                                  "requirements": []}))
            if "job_id" not in job:
                return job
            for _ in range(400):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return got.get("result") or got
                await asyncio.sleep(0.02)
        raise AssertionError("the review job did not finish")

    reply = asyncio.run(flow())
    assert reply.get("completion_state") == "blocked-escalated", reply
    assert str((reply.get("warning_card") or {}).get("code", "")).startswith("G6_E_"), reply
    assert share_calls == []
