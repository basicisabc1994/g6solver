# SPDX-License-Identifier: Apache-2.0
"""The repair block over MCP (``g6.mcp.repair``): every refused G6 reply says who repairs it and how, from the refusal
declarations; retries are bounded and never unchanged; the repair log keeps codes and ids only. Tests that need a
grant use the conftest's ``declared_mcp_grants``; the others run on a fresh client that granted nothing."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402

SECRET = "zebra-quartz-17 the person's own words"


def _session(calls: list[tuple[str, dict]], client: str = "codex") -> list[dict]:
    """Each call in one MCP session, in order: its reply."""
    async def run() -> list[dict]:
        out = []
        async with Client(build_server(), client_info=Implementation(name=client, version="test")) as session:
            for name, arguments in calls:
                result = await session.call_tool(name, arguments, raise_on_error=False)
                out.append(result.structured_content or json.loads(result.content[0].text))
        return out
    return asyncio.run(run())


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    return tmp_path / "fresh-home"


def test_a_typed_input_error_is_the_agents_to_fix_with_a_call_that_then_succeeds(home: Path) -> None:
    refused, = _session([("g6_select", {"task": SECRET, "phase": "sometime"})])
    repair = refused["repair"]
    assert refused["completion_state"] == "blocked-escalated" and repair["code"] == "G6_E_INPUT_VALIDATION"
    assert repair["fix_by"] == "input" and repair["by"] == "agent" and repair["validated"] is True
    assert repair["call"]["tool"] == "g6_select" and "phase" in repair["fill"], repair
    assert repair["retries_left"] >= 1
    arguments = {**repair["call"]["arguments"], "task": "review my patch before I say it is done", "phase": "verify"}
    answered, = _session([(repair["call"]["tool"], arguments)])
    assert answered["ok"] is True and "repair" not in answered


def test_a_fresh_clients_refused_review_is_the_persons(home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    refused, = _session([("g6_review_patch", {"repo": str(repo), "task": "check my change"})])
    repair = refused["repair"]
    assert repair["by"] == "person" and repair["fix_by"] == "person", repair
    if repair["code"] == "G6_E_CLOUD_CONSENT_REQUIRED":
        assert repair["call"]["tool"] == "g6_cloud_grant"       # the person answers in the dialog it opens
    else:
        assert repair["call"] is None


def test_a_withheld_reply_carries_the_persons_repair(home: Path) -> None:
    refused, = _session([("g6_describe", {"component": "grounding"})])
    assert refused["cloud_consent_code"] == "G6_E_CLOUD_CONSENT_REQUIRED"
    assert refused["repair"]["by"] == "person" and refused["repair"]["call"]["tool"] == "g6_cloud_grant"


def test_a_tool_class_refusal_names_g6s_own_next_call(home: Path, declared_mcp_grants: None) -> None:
    refused, = _session([("g6_invoke", {"component": "no_such_component", "op": "run", "params_json": "{}"})])
    repair = refused["repair"]
    assert repair["code"] == "G6_E_UNKNOWN_COMPONENT" and repair["fix_by"] == "tool:g6_find", repair
    assert repair["by"] == "agent" and repair["call"]["tool"] == "g6_find" and repair["validated"] is True


def test_a_sensitive_refusal_is_the_persons_with_the_setup_step(home: Path, declared_mcp_grants: None) -> None:
    refused, = _session([("g6_invoke", {"component": "lean", "op": "check_proof", "params_json": "{}"})])
    repair = refused["repair"]
    assert repair["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and repair["by"] == "person", repair
    assert repair["call"] is None and "g6 setup --allow lean.check_proof" in repair["step"]


def test_retries_are_bounded_and_never_unchanged(home: Path) -> None:
    replies = _session([("g6_select", {"task": "a", "phase": "one"}), ("g6_select", {"task": "a", "phase": "two"}),
                        ("g6_select", {"task": "a", "phase": "three"})])
    left = [r["repair"]["retries_left"] for r in replies]
    assert left == [2, 1, 0] and [r["repair"]["by"] for r in replies] == ["agent", "agent", "person"], left
    same = _session([("g6_select", {"task": "a", "phase": "one"}), ("g6_select", {"task": "a", "phase": "one"})])
    assert same[1]["repair"]["retries_left"] == 0 and same[1]["repair"]["by"] == "person"
    other = _session([("g6_select", {"task": "a", "phase": "one"})])           # another session counts afresh
    assert other[0]["repair"]["retries_left"] == 2


def test_the_repair_log_keeps_codes_and_ids_only_and_says_how_each_repair_ended(home: Path) -> None:
    from g6.mcp.repair import repair_stats

    _session([("g6_select", {"task": SECRET, "phase": "later"}),
              ("g6_select", {"task": SECRET, "phase": "verify"})])
    log = home / "repairs" / "repairs.jsonl"
    text = log.read_text(encoding="utf-8")
    assert "zebra" not in text and "later" not in text and "person's own" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert {k for row in rows for k in row} <= {"event", "id", "session", "tool", "code", "fix_by", "by", "outcome"}
    assert [row["event"] for row in rows] == ["refused", "next"] and rows[1]["outcome"] == "resolved"
    assert repair_stats(log)["G6_E_INPUT_VALIDATION"] == {"refused": 1, "resolved": 1, "not": 0, "person": 0,
                                                          "rate": 1.0}


def test_no_person_class_is_ever_the_agents_and_only_consent_opens_the_grant_dialog() -> None:
    from g6.core import refusals
    from g6.mcp.repair import block

    rows = [r for codes in refusals.component_declarations().values() for r in codes.values()]
    rows += [refusals.layer(code) for code in refusals.LAYERS]
    seen = set()
    for row in rows:
        if row.code in seen:
            continue
        seen.add(row.code)
        found = block("g6_invoke", {"ok": False, "completion_state": "blocked-escalated",
                                    "error": f"[{row.code}] refused", "warning_card": {"code": row.code}})
        if row.fix_by in ("person", "none"):
            assert found["by"] == "person", (row.code, found)
        call = found["call"] or {}
        assert call.get("tool") != "g6_cloud_grant" or row.code == "G6_E_CLOUD_CONSENT_REQUIRED", row.code
    assert len(seen) > 1000


@pytest.mark.parametrize("code,fix_by,by", [
    ("G6_E_WORKSPACE_BUSY", "retry", "agent"), ("G6_E_INTERNAL", "none", "person"),
    ("G6_E_DESIGN_BAD_PATH", "input", "agent"), ("G6_E_GIT_REPO_DIRTY", "person", "person")])
def test_each_class_has_its_repair(code: str, fix_by: str, by: str) -> None:
    from g6.mcp.repair import block

    found = block("g6_invoke", {"ok": False, "completion_state": "blocked-escalated", "error": f"[{code}] refused"})
    assert found["fix_by"] == fix_by and found["by"] == by, found
    assert (found["call"] is not None) == (by == "agent"), found


def test_a_reply_that_was_not_refused_has_no_repair(home: Path) -> None:
    answered, = _session([("g6_select", {"task": "review my patch", "phase": "verify",
                                         "last_outcome": "G6_E_CLOUD_CONSENT_REQUIRED"})])
    assert "repair" not in answered and answered["ok"] is True
