# SPDX-License-Identifier: Apache-2.0
"""The repair block over MCP (``g6.mcp.repair``): every refused G6 reply says who repairs it and how, from the refusal
declarations; retries are bounded and never unchanged; the repair log keeps codes and ids only. Tests that need a
grant use the conftest's ``declared_mcp_grants``; the others run on a fresh client that granted nothing."""
from __future__ import annotations

import asyncio
import json
import re
import threading
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


# -- a reply withheld for consent was never delivered: it is not an attempt the agent saw ------------------------------
class _Session:
    """A session object the repair state can hold weakly."""


BAD_PATH = {"ok": False, "completion_state": "blocked-escalated", "error": "[G6_E_DESIGN_BAD_PATH] refused"}
DONE = {"ok": True, "completion_state": "qualified-draft"}


def _log(home: Path) -> list[dict]:
    from g6.core.repair_log import flush

    assert flush(10)
    path = home / "repairs" / "repairs.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []


def test_a_refusal_counts_only_once_it_is_delivered(home: Path) -> None:
    from g6.mcp import repair

    session, arguments = _Session(), {"component": "x", "op": "y", "params_json": "{}"}
    first, _withheld = repair.observed("g6_invoke", arguments, BAD_PATH, session)       # never delivered
    assert first["repair"]["by"] == "agent" and first["repair"]["retries_left"] == repair.MAX_RETRIES
    assert _log(home) == []                                         # nothing logged for a reply never delivered
    again, delivered = repair.observed("g6_invoke", arguments, BAD_PATH, session)
    assert again["repair"]["by"] == "agent" and again["repair"]["retries_left"] == repair.MAX_RETRIES, again
    delivered()
    assert [row["event"] for row in _log(home)] == ["refused"]
    third = repair.observe("g6_invoke", arguments, BAD_PATH, session)   # the same call, after one was delivered
    assert third["repair"]["retries_left"] == 0 and third["repair"]["by"] == "person", third


def test_a_withheld_reply_leaves_the_last_repairs_ending_to_the_next_delivered_reply(home: Path) -> None:
    from g6.mcp import repair

    session = _Session()
    repair.observe("g6_invoke", {"a": 1}, BAD_PATH, session)
    repair.observed("g6_invoke", {"a": 2}, DONE, session)           # withheld: the agent saw no answer
    assert [row["event"] for row in _log(home)] == ["refused"]
    repair.observe("g6_invoke", {"a": 2}, DONE, session)
    assert [(row["event"], row.get("outcome")) for row in _log(home)] == [("refused", None), ("next", "resolved")]


WITHHELD = re.compile(r"Withheld source identifiers: (.+?)\. Provider: (\S+?)\.")


def test_the_same_call_after_a_consent_grant_is_not_a_refusal_sent_unchanged(home: Path) -> None:
    """A refusal withheld for consent, the person's grant, then the same call as the withheld repair says: the
    refusal's own repair is the agent's, with every retry left (the e2e journey's step 7b). Sent unchanged once more
    after it was delivered, it is the person's."""
    from tests.conftest import accepting_cloud_elicitation

    call = ("g6_invoke", {"component": "no_such_component", "op": "run", "params_json": "{}"})

    async def run() -> list[dict]:
        async with Client(build_server(), client_info=Implementation(name="codex", version="test"),
                          elicitation_handler=accepting_cloud_elicitation) as session:
            async def reply(name: str, arguments: dict) -> dict:
                result = await session.call_tool(name, arguments, raise_on_error=False)
                return result.structured_content or json.loads(result.content[0].text)
            withheld = await reply(*call)
            assert withheld.get("cloud_consent_code") == "G6_E_CLOUD_CONSENT_REQUIRED", withheld
            sources, provider = WITHHELD.search(withheld["error"]).groups()
            for source in sources.split(", "):
                assert (await reply("g6_cloud_grant", {"source": source, "provider": provider}))["ok"] is True
            return [await reply(*call), await reply(*call)]
    delivered, unchanged = asyncio.run(run())
    assert "cloud_consent_code" not in delivered and delivered["repair"]["code"] == "G6_E_UNKNOWN_COMPONENT", delivered
    assert delivered["repair"]["by"] == "agent" and delivered["repair"]["retries_left"] == 2, delivered["repair"]
    assert unchanged["repair"]["by"] == "person" and unchanged["repair"]["retries_left"] == 0, unchanged["repair"]


def test_whether_a_reply_was_written_is_looked_for_only_once_the_ledger_is_free(home: Path, monkeypatch) -> None:
    """Looked for while admission holds the sharing ledger, a reply's writing would make code on the same event loop
    that reads the ledger itself (as the e2e journey does) wait on a send that waits on the loop. So each look finds
    the ledger free: a read of it from another thread finishes at once."""
    from g6.core import cloud_consent
    from g6.mcp import consent_transport

    real, held = consent_transport._written, []

    def look(stream):
        reader = threading.Thread(target=cloud_consent.grants, daemon=True)
        reader.start()
        reader.join(5)
        held.append(reader.is_alive())
        return real(stream)
    monkeypatch.setattr(consent_transport, "_written", look)
    replies = _session([("g6_select", {"task": "a", "phase": "verify"}), ("g6_select", {"task": "a", "phase": "x"})])
    assert [r["ok"] for r in replies] == [True, False] and held and not any(held), held


def _grant_calls(value: object) -> list[dict]:
    """Every g6_cloud_grant call template anywhere in a reply."""
    if isinstance(value, list):
        return [c for v in value for c in _grant_calls(v)]
    if not isinstance(value, dict):
        return []
    call = value.get("call")
    own = [call] if isinstance(call, dict) and call.get("tool") == "g6_cloud_grant" else []
    return own + [c for v in value.values() for c in _grant_calls(v)]


def test_a_reply_withheld_from_a_client_g6_cannot_name_says_how_to_go_on(home: Path) -> None:
    """No grant covers a provider G6 cannot name (g6_cloud_grant refuses ``cloud:unknown``), so the withheld reply
    offers no dialog: it says the client must name itself as one G6 knows, and which grant then follows; g6_select,
    given that reply, agrees."""
    withheld, rerouted = _session([
        ("g6_invoke", {"component": "grounding", "op": "check_claim", "params_json": "{}"}),
        ("g6_select", {"task": "check a claim", "phase": "verify",
                       "last_outcome": json.dumps({"ok": False, "completion_state": "blocked-escalated",
                                                   "cloud_consent_code": "G6_E_CLOUD_CONSENT_REQUIRED",
                                                   "error": "[G6_E_CLOUD_CONSENT_REQUIRED] withheld"})})],
        client="some-unnamed-client")
    assert withheld["cloud_consent_code"] == "G6_E_CLOUD_CONSENT_REQUIRED" and "Provider: cloud:unknown" in withheld[
        "error"], withheld
    repair = withheld["repair"]
    assert repair["by"] == "person" and repair["call"] is None and repair["fill"] == [], repair
    for said in (repair["step"], withheld["error"]):
        assert "claude-code" in said and "codex" in said and "g6_cloud_grant" in said, said
    assert "cloud_consent_code" not in rerouted and rerouted["ok"] is True, rerouted
    assert not _grant_calls(rerouted), _grant_calls(rerouted)
    assert rerouted["next"]["tell_person"] is True and "claude-code" in rerouted["next"]["say"], rerouted["next"]
