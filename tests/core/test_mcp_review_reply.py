# SPDX-License-Identifier: Apache-2.0
"""The patch review's reply over MCP is compact by default (worked examples, W2).

Every review G6 finished in the worked examples replied with about 64,000 characters or more (64,108 in run 1, about
240,000 in starlette's runs). Claude Code saves a tool result over 50,000 characters (or 25,000 tokens) to a file and
warns over 10,000 tokens, and the agent then read the reply with jq and paraphrased the report. Over MCP the review's
job now replies with the outcome, ``assessment.report`` first, the reasons, the next actions, the unknowns, the merge
authorisation, the job id and where the full assessment is: a file under G6's state folder, and
``metaharness_job(job_id, detail="full")``. The Python API's reply is unchanged. The fixture is run 1's reply (black
#5634), with the container's home folder taken out of its paths. Offline: no model, no confined run.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from g6.invariants import check_envelope

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from g6.harness import patch_review  # noqa: E402
from g6.mcp import build_server  # noqa: E402

FIXTURE = Path(__file__).with_name("data") / "worked_example_review_reply.json"
#: Well under Claude Code's documented limits for a tool result (a warning over 10,000 tokens; a file over 50,000
#: characters or 25,000 tokens). Codex documents no default for MCP tool output.
COMPACT_CHARS = 20_000


def _real() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _text(reply) -> str:
    return "".join(getattr(block, "text", "") for block in reply.content)


def _data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


def _review_over_mcp(monkeypatch, tmp_path, reply: dict, *, detail: str | None = None):
    """Run g6_review_patch over a real MCP client with ``review_patch`` giving ``reply``; the finished job's reply,
    its text as the client receives it, and the full form asked for after it."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    monkeypatch.setattr(patch_review, "review_patch", lambda *a, **k: copy.deepcopy(reply))
    server = build_server()

    async def flow():
        async with Client(server) as client:
            job = _data(await client.call_tool("g6_review_patch", {"repo": str(tmp_path), "task": "t",
                                                                   "requirements": []}))
            arguments = {"job_id": job["job_id"], **({"detail": detail} if detail else {})}
            for _ in range(400):
                got = await client.call_tool("metaharness_job", arguments)
                if _data(got).get("status") in ("completed", "failed"):
                    full = await client.call_tool("metaharness_job", {"job_id": job["job_id"], "detail": "full"})
                    return _data(got), _text(got), _data(full)
                await asyncio.sleep(0.02)
        raise AssertionError("the review job did not finish")

    return asyncio.run(flow())


def test_the_finished_review_replies_compactly_with_the_report_first(tmp_path, monkeypatch):
    real = _real()
    assert len(json.dumps(real, separators=(",", ":"))) > 50_000   # the shape the worked examples saw
    got, text, _ = _review_over_mcp(monkeypatch, tmp_path, real)
    assert got["status"] == "completed" and len(text) < COMPACT_CHARS, len(text)
    result = got["result"]
    assessment = result["assessment"]
    assert list(result)[0] == "assessment" and list(assessment)[0] == "report"
    assert assessment["report"] == real["assessment"]["report"]                     # verbatim, never cut
    assert assessment["outcome"] == real["assessment"]["outcome"] == "test_integrity_compromised"
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [
        (r["stage"], r["code"]) for r in real["assessment"]["reasons"]]
    assert assessment["next_actions"] == real["assessment"]["next_actions"]
    assert len(assessment["unknowns"]) == len(real["assessment"]["unknowns"])
    assert result["merge"]["status"] == "requires_separate_approval" and result["merge"] == real["merge"]
    assert result["job_id"] == got["job_id"]
    for field in ("completion_state", "reliability_label", "request_id", "run_id", "ok", "artifact_digest"):
        assert result[field] == real[field], field
    assert result["warning_card"]["code"] == "G6_E_PATCH_REVIEW" and check_envelope(result) == []
    assert [item["summary"] for item in result["evidence"]] == [item["summary"] for item in real["evidence"]]
    for left_out in ("requirements", "completion", "scope"):
        assert left_out not in assessment, left_out
    assert "gate" not in result and "loaded_outside" not in text


def test_the_compact_reply_says_where_the_full_assessment_is_and_both_hold_everything(tmp_path, monkeypatch):
    real = _real()
    got, _, full = _review_over_mcp(monkeypatch, tmp_path, real)
    where = got["result"]["full_assessment"]
    saved = Path(where["file"])
    assert saved.is_file() and saved.parent == (tmp_path / "state" / "reviews").resolve()
    assert saved.name == f"{got['job_id']}.json"
    assert where["detail"] == f'metaharness_job(job_id="{got["job_id"]}", detail="full")'
    assert json.loads(saved.read_text(encoding="utf-8")) == real                    # everything the reply held before
    assert full["status"] == "completed" and full["result"] == real                 # the full form over MCP
    assert full["job_id"] == got["job_id"]


def test_asking_for_the_full_form_from_the_start_gives_what_the_reply_was_before(tmp_path, monkeypatch):
    real = _real()
    got, text, _ = _review_over_mcp(monkeypatch, tmp_path, real, detail="full")
    assert got["result"] == real and len(text) > 50_000


def test_a_huge_review_still_fits_and_keeps_its_report_whole(tmp_path, monkeypatch):
    real = _real()
    huge = copy.deepcopy(real)
    assessment = huge["assessment"]
    assessment["reasons"] = [{"stage": "integrity", "code": f"code_{i}", "detail": "x" * 5_000} for i in range(100)]
    assessment["next_actions"] = [f"action {i}: " + "y" * 5_000 for i in range(100)]
    assessment["unknowns"] = [f"unknown {i}: " + "z" * 5_000 for i in range(100)]
    huge["evidence"] = huge["evidence"] * 100
    huge["warning_card"] = {**huge["warning_card"], "message": "m" * 20_000, "why": "w" * 20_000,
                            "risks": ["r" * 20_000] * 10}
    got, text, full = _review_over_mcp(monkeypatch, tmp_path, huge)
    result = got["result"]
    assert len(text) < 25_000, len(text)
    assert result["assessment"]["report"] == real["assessment"]["report"]
    assert result["assessment"]["reasons"][0]["code"] == "code_0" and check_envelope(result) == []
    assert result["full_assessment"]["shortened"]                                    # it says it shortened lists
    assert full["result"] == huge


def test_a_refusal_is_returned_as_it_is(tmp_path, monkeypatch):
    from g6.core.door import blocked_reply

    refused = blocked_reply("[G6_E_INPUT_VALIDATION] template instance ex lacks bindings.relation")
    got, _, full = _review_over_mcp(monkeypatch, tmp_path, refused)
    assert got["result"] == refused == full["result"]
    assert not (tmp_path / "state" / "reviews").exists()


def test_an_unknown_detail_is_refused_with_a_code(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    server = build_server()

    async def flow():
        async with Client(server) as client:
            return _data(await client.call_tool("metaharness_job", {"job_id": "x", "detail": "everything"}))

    got = asyncio.run(flow())
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_the_tool_descriptions_say_the_reply_is_compact_and_how_to_get_the_full_form():
    tools = asyncio.run(build_server().get_tools())
    review = " ".join(tools["g6_review_patch"].description.split())
    job = " ".join(tools["metaharness_job"].description.split())
    assert 'detail="full"' in review and 'detail="full"' in job
    assert "detail" in tools["metaharness_job"].parameters["properties"]
