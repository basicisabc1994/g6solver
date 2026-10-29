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


def _unapproved_version(mh, text: str) -> int:
    """An "activate" row no person approved here, written with G6's own store class as any process could: its code
    returns ``text`` (benign)."""
    from g6.metaharness.schema import Profile
    from g6.metaharness.store import ProfileStore

    _, head = mh._head()
    sha = mh.artifacts.put_blob(f"def solve(task):\n    return {text!r}\n")
    profile = Profile.model_validate(head.model_dump(mode="json") | {"code": dict(head.code) | {"main.py": sha}})
    return ProfileStore(mh.workspace / "store.jsonl").append_version(
        "activate", profile, at="2026-10-29T00:00:00Z", accepted_by="Someone Else", decision_digest="0" * 64,
        proposer="x")


def test_a_shipped_activation_never_runs_after_the_person_confirms(tmp_path, monkeypatch, unconfined):
    """The fresh review of the F1-F8 fix pass, finding 2: the confirmation was the person's, but the version run was
    still the store's latest row, one the project's files carried."""
    from g6.metaharness.declarative import create

    elsewhere = tmp_path / "elsewhere" / ".g6" / "workflows"
    monkeypatch.setenv("G6_HOME", str(tmp_path / "their-home"))
    create(SPEC, elsewhere)
    theirs = Surface(workflow_root=elsewhere).harness("echo")
    assert theirs.confirm("Forged Name")["code"] == "G6_E_META_CONFIRMED"
    shipped_version = _unapproved_version(theirs, "SHIPPED-VERSION")
    shipped = tmp_path / "project" / ".g6" / "workflows"
    shutil.copytree(elsewhere / "echo", shipped / "echo")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "my-home"))
    surface = Surface(workflow_root=shipped)
    confirmed = surface.harness("echo").confirm("Ada")
    # version 0 is exactly the initial version the person's own confirmation makes, so it counts; the activation does not
    assert confirmed["code"] == "G6_E_META_CONFIRMED" and confirmed["head_version"] == 0, confirmed
    assert confirmed["versions_not_counted"] == [shipped_version], confirmed
    assert surface.run("echo", "hello").get("output") == "hello"
    rolled = surface.harness("echo").rollback(shipped_version, "Ada")
    assert rolled["warning_card"]["code"] == "G6_E_META_APPROVAL_REQUIRED", rolled
    refused, _, _ = surface.rollback_check("echo", shipped_version)
    assert refused is not None and refused["warning_card"]["code"] == "G6_E_META_APPROVAL_REQUIRED", refused


def test_a_shipped_initial_version_with_other_code_is_never_confirmed(tmp_path, monkeypatch, unconfined):
    from g6.metaharness.declarative import create
    from g6.metaharness.schema import Profile

    elsewhere = tmp_path / "elsewhere" / ".g6" / "workflows"
    monkeypatch.setenv("G6_HOME", str(tmp_path / "their-home"))
    create(SPEC, elsewhere)
    theirs = Surface(workflow_root=elsewhere).harness("echo")
    sha = theirs.artifacts.put_blob("def solve(task):\n    return 'SHIPPED-INITIAL'\n")
    initial = Profile(routing={s: next(iter(theirs.workflow.models)) for s in theirs.workflow.steps},
                      pipeline=tuple(n for n, s in theirs.workflow.pipeline_steps.items() if s.mandatory),
                      code={"main.py": sha})
    assert theirs.confirm("Forged Name", initial=initial)["code"] == "G6_E_META_CONFIRMED"
    shipped = tmp_path / "project" / ".g6" / "workflows"
    shutil.copytree(elsewhere / "echo", shipped / "echo")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "my-home"))
    surface = Surface(workflow_root=shipped)
    got = surface.harness("echo").confirm("Ada")
    assert got["warning_card"]["code"] == "G6_E_META_APPROVAL_REQUIRED", got
    got = surface.run("echo", "hello")
    assert got["completion_state"] == "blocked-escalated" and got.get("output") != "SHIPPED-INITIAL", got


def test_the_person_can_confirm_their_workflow_again_from_another_state_folder(tmp_path, monkeypatch, unconfined):
    """The fresh review of the F1-F8 fix pass (its probe ``probe_two_homes``): with F2 a confirmation counts only where
    G6 recorded it, so a person whose clients use two G6 state folders confirms in each, and may come back to the
    first; every one of those confirmations is recorded and counts."""
    from g6.metaharness.declarative import create

    root = tmp_path / "project" / ".g6" / "workflows"
    create(SPEC, root)
    for home in ("home-a", "home-b", "home-a", "home-c"):
        monkeypatch.setenv("G6_HOME", str(tmp_path / home))
        got = Surface(workflow_root=root).harness("echo").confirm("Ada")
        assert got["code"] == "G6_E_META_CONFIRMED", (home, got)
        assert Surface(workflow_root=root).run("echo", "hello").get("output") == "hello", home


def test_running_or_evaluating_a_workflows_code_over_mcp_is_refused_unless_allowed(tmp_path, monkeypatch, unconfined):
    """F2's gate on the evaluation tools (the fresh review's mutation run: removing it from them failed no test)."""
    from g6.metaharness.declarative import create

    root = tmp_path / "project" / ".g6" / "workflows"
    create(SPEC, root)
    surface = Surface(workflow_root=root)
    assert surface.harness("echo").confirm("Ada")["code"] == "G6_E_META_CONFIRMED"
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)

    async def flow(tool: str) -> dict:
        async with Client(build_server(surface)) as client:
            reply = await client.call_tool(tool, {"workflow": "echo", "proposal_digest": "0" * 64})
            return reply.data if isinstance(reply.data, dict) else reply.structured_content

    for tool in ("metaharness_evaluate_start", "metaharness_evaluate"):
        got = asyncio.run(flow(tool))
        assert got["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", (tool, got)


def test_a_version_written_into_the_store_after_confirmation_never_runs(tmp_path, monkeypatch, unconfined):
    from g6.metaharness.declarative import create

    root = tmp_path / "project" / ".g6" / "workflows"
    monkeypatch.setenv("G6_HOME", str(tmp_path / "my-home"))
    create(SPEC, root)
    surface = Surface(workflow_root=root)
    mh = surface.harness("echo")
    head = mh.confirm("Ada")["head_version"]
    _unapproved_version(mh, "UNAPPROVED-VERSION")
    again = Surface(workflow_root=root)
    assert again.run("echo", "hello").get("output") == "hello"
    assert again.harness("echo")._head()[0] == head
    assert again.harness("echo")._previous_approved() is None
