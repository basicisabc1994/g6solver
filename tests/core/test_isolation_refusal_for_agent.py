# SPDX-License-Identifier: Apache-2.0
"""A Windows isolation refusal reads as whole sentences to the agent. The refusal names the person's one-time access grant
(``confine._grant_command``: ``icacls "C:\\..." /grant "*S-1-15-2-1:(OI)(CI)(RX)"``), and the reply filter used to cut
each command out of its arguments at the drive's colon, leaving fragments such as ``\\Users\\...\\venv" a command named in
G6's documentation:(OI)(CI)(RX)"``. The sentence now goes whole, and the agent is told to ask the person to run
``g6 doctor``, which prints the commands to the person. The agent still reads no setup command or setting (05718f35)."""
from __future__ import annotations

import asyncio
import json

import pytest

from g6.core import confine
from g6.core.agent_guidance import ISOLATION_STEP, text_for_agent
from g6.core.guidance_builders import blocked_reply
from tests.guidance_expectations import no_setup

FOLDER = r"C:\Users\someone\venv"
REFUSAL = ("[G6_E_ISOLATION_UNAVAILABLE] no operating-system boundary: unavailable: the Windows AppContainer cannot read "
           f"{FOLDER}, so untrusted code would run with your permissions; to let G6 confine it, run once in a terminal (it "
           "grants read and execute to ALL APPLICATION PACKAGES on those folders and everything in them, and nothing "
           f"else): {confine._grant_command(FOLDER)}")


def clean(said: str) -> None:
    assert "G6_E_ISOLATION_UNAVAILABLE" in said and "g6 doctor" in said, said
    assert "a command named in G6's documentation:" not in said and "(OI)(CI)" not in said and "(RX)" not in said, said
    no_setup(said)


def test_the_filtered_refusal_is_whole_sentences_naming_the_folder_and_g6_doctor():
    said = text_for_agent(REFUSAL)
    clean(said)
    assert said == ("[G6_E_ISOLATION_UNAVAILABLE] no operating-system boundary: unavailable: the Windows AppContainer "
                    f"cannot read {FOLDER}, so untrusted code would run with your permissions. {ISOLATION_STEP}")
    assert "read access" in said
    assert text_for_agent(said) == said                          # filtering again changes nothing


def test_the_refusal_over_mcp_reads_clean(tmp_path, monkeypatch, accepted_cloud_dialog):
    fastmcp = pytest.importorskip("fastmcp")
    from mcp.types import Implementation

    from g6.core.cloud_consent import bind_output, input_source, repository_source
    from g6.harness import patch_review
    from g6.mcp import build_server

    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    # The grants tests/core/test_mcp_review_reply.py makes for a review job's reply, so the refusal is not withheld.
    accepted_cloud_dialog("jobs", "openai:codex", text="retained synthetic report")
    accepted_cloud_dialog("host_configuration", "openai:codex", text="synthetic host diagnostics")
    accepted_cloud_dialog(repository_source(tmp_path / "state" / "reviews"), "openai:codex", text="review storage")
    accepted_cloud_dialog(repository_source(tmp_path), "openai:codex", text="synthetic review repository")
    arguments = {"repo": str(tmp_path), "task": "t", "requirements": []}
    accepted_cloud_dialog(input_source(json.dumps(arguments, sort_keys=True)), "openai:codex", text=json.dumps(arguments))
    monkeypatch.setattr(patch_review, "review_patch", lambda *a, **k: bind_output(blocked_reply(REFUSAL), ()))
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow() -> tuple[dict, str]:
        async with fastmcp.Client(server, client_info=Implementation(name="codex", version="test")) as client:
            job = data(await client.call_tool("g6_review_patch", arguments))
            for _ in range(400):
                got = await client.call_tool("metaharness_job", {"job_id": job["job_id"]})
                if data(got).get("status") in ("completed", "failed"):
                    return data(got), "".join(getattr(block, "text", "") for block in got.content)
                await asyncio.sleep(0.02)
        raise AssertionError("the review job did not finish")

    got, text = asyncio.run(flow())
    result = got.get("result") or {}
    assert result.get("completion_state") == "blocked-escalated", got
    assert result["warning_card"]["code"] == "G6_E_ISOLATION_UNAVAILABLE", got
    clean(text)
    clean(json.dumps(result))
    assert FOLDER in result["error"]                              # the folder the person must open is named


def test_g6_doctor_lists_the_grant_for_the_repository_it_is_run_in(tmp_path, monkeypatch):
    """The person's side: run inside a repository the sandbox cannot read, ``g6 doctor`` says so and prints that
    repository's own one-time command, whole (dogfood 2: it printed grants for the interpreter and G6 only)."""
    from types import SimpleNamespace

    from g6.cli import doctor

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg").mkdir()
    readable = {"value": False}
    chosen = SimpleNamespace(readable=lambda path: readable["value"],
                             cannot_read=lambda paths: "unavailable: " + ", then ".join(map(confine._grant_command, paths)))
    monkeypatch.setattr(confine, "backend", lambda python, probe=True: ("AppContainer", ""))
    monkeypatch.setattr(confine, "started", lambda python: True)
    monkeypatch.setattr(confine, "_select", lambda: (chosen, ""))
    monkeypatch.chdir(repo / "pkg")
    check = doctor._isolation()
    assert check.status == "warn" and str(repo) in check.detail, check
    assert check.fix == confine._grant_command(str(repo))
    readable["value"] = True
    assert doctor._isolation().status == "ok"
