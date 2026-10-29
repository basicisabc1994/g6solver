# SPDX-License-Identifier: Apache-2.0
"""A project cannot ship a meta-harness workflow already "confirmed" (the security review of W1-W5, F2).

Workflows are found in the folder G6 starts in (``.g6/workflows``), and a workflow's confirmation lived only in its
own store there, a hash chain with no secret: a project could ship one confirmed under any name, and
``metaharness_run`` ran its code with no person. Now a confirmation counts only when G6 recorded it under its state
folder (``G6_HOME``) when the person confirmed, so one the project's files carry leaves the workflow unconfirmed; and
the tools that run a workflow's code (``metaharness_run``, ``metaharness_evaluate_start``, ``metaharness_evaluate``)
are sensitive operations, refused unless the person lists them in ``G6_MCP_SENSITIVE``. The baseline code here only
echoes its input.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from g6.metaharness.api import NOT_CONFIRMED
from g6.metaharness.surface import Surface

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from g6.mcp import build_server  # noqa: E402

SPEC = {"name": "echo", "task": "Echo {input}", "cases": [{"input": "a", "expected": "a"}, {"input": "b", "expected": "b"}],
        "baseline_code": {"main.py": "def solve(task):\n    return task.split(' ', 1)[1]\n"}}


def _confirmed_elsewhere(tmp_path: Path, monkeypatch) -> Path:
    """A workflow a person confirmed in another project, under another G6_HOME: the store a project could ship."""
    from g6.metaharness.declarative import create

    elsewhere = tmp_path / "elsewhere" / ".g6" / "workflows"
    monkeypatch.setenv("G6_HOME", str(tmp_path / "their-home"))
    create(SPEC, elsewhere)
    assert Surface(workflow_root=elsewhere).harness("echo").confirm("Forged Name")["code"] == "G6_E_META_CONFIRMED"
    shipped = tmp_path / "project" / ".g6" / "workflows"
    shutil.copytree(elsewhere / "echo", shipped / "echo")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "my-home"))
    return shipped


def _run_over_mcp(surface: Surface, tool: str = "metaharness_run") -> dict:
    server = build_server(surface)

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow():
        async with Client(server) as client:
            job = data(await client.call_tool(tool, {"workflow": "echo", "task_input": "hello"}))
            if "job_id" not in job:
                return job
            for _ in range(200):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return got["result"]
                await asyncio.sleep(0.05)
        raise AssertionError("the job did not finish")

    return asyncio.run(flow())


@pytest.fixture
def unconfined(monkeypatch):
    monkeypatch.setenv("G6_ALLOW_UNCONFINED", "1")   # the echo baseline runs where no boundary is, as phase 6's does


def test_a_shipped_confirmation_never_lets_the_workflow_run(tmp_path, monkeypatch, unconfined):
    shipped = _confirmed_elsewhere(tmp_path, monkeypatch)
    surface = Surface(workflow_root=shipped)
    got = surface.run("echo", "hello")
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == NOT_CONFIRMED, got
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness")
    got = _run_over_mcp(Surface(workflow_root=shipped))
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == NOT_CONFIRMED, got


def test_running_a_workflows_code_over_mcp_is_refused_unless_the_person_allows_it(tmp_path, monkeypatch, unconfined):
    from g6.metaharness.declarative import create

    root = tmp_path / "project" / ".g6" / "workflows"
    create(SPEC, root)
    surface = Surface(workflow_root=root)
    assert surface.harness("echo").confirm("Ada")["code"] == "G6_E_META_CONFIRMED"
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)
    for tool in ("metaharness_run",):
        got = _run_over_mcp(surface, tool)
        assert got["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", got
    tools = asyncio.run(build_server(surface).get_tools())
    for name in ("metaharness_run", "metaharness_evaluate_start", "metaharness_evaluate"):
        assert "G6_MCP_SENSITIVE" in tools[name].description, name


def test_the_persons_own_confirmed_workflow_runs_when_allowed(tmp_path, monkeypatch, unconfined):
    from g6.metaharness.declarative import create

    root = tmp_path / "project" / ".g6" / "workflows"
    create(SPEC, root)
    surface = Surface(workflow_root=root)
    assert surface.harness("echo").confirm("Ada")["code"] == "G6_E_META_CONFIRMED"
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.run")
    got = _run_over_mcp(surface)
    assert got.get("output") == "hello", got


def test_confirming_a_shipped_workflow_records_the_persons_own_confirmation(tmp_path, monkeypatch, unconfined):
    shipped = _confirmed_elsewhere(tmp_path, monkeypatch)
    surface = Surface(workflow_root=shipped)
    assert surface.harness("echo").confirm("Ada")["code"] == "G6_E_META_CONFIRMED"
    assert surface.harness("echo").store.confirmed()["accepted_by"] == "Ada"
    got = surface.run("echo", "hello")
    assert got.get("output") == "hello", got
