# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_nanoclaw: the mailbox, durable messages between agents (agent_openclaw merged in). Nothing is lost
or mixed up (D1, lost_agent_message): a reader pages through every message with a cursor, a message it received and
did not acknowledge comes back after a crash, a retried send is stored once, and a message addressed to one member
never reaches another (AGENT_NANOCLAW-02, AGENT_OPENCLAW-02). A group may type its messages; a message that does not
fit is refused. Store ops answer facts with ids, never ``verified`` (AGENT_NANOCLAW-01, AGENT_OPENCLAW-01)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import g6.agents.agent_nanoclaw as mailbox_pkg
from g6.agents.agent_nanoclaw import Mailbox
from g6.core.registry import get_registry
from g6.mcp import invoke_reply


@pytest.fixture(autouse=True)
def own_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "g6-home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    return home


#: The server's owner lists the mailbox in G6_MCP_SENSITIVE, so its sensitive op (delete_group) answers over MCP too.
OWNER_ALLOWS = frozenset({"agent_nanoclaw"})


def invoke(op: str, params: dict) -> dict:
    reply = invoke_reply("agent_nanoclaw", op, json.dumps(params), allow_sensitive=OWNER_ALLOWS)
    assert reply["ok"], reply
    return reply["result"]


def team(box: Mailbox | None = None, **kw) -> Mailbox:
    box = box or Mailbox()
    box.create_group("build", ["planner", "coder", "tester"], **kw)
    return box


# --- D1: nothing lost, nothing duplicated, nothing mixed up ---------------------------------------------------------

def test_d1_send_120_read_50_crash_and_reread_loses_and_duplicates_nothing() -> None:
    box = team()
    sent = [box.send("build", "planner", {"n": i}, to="coder")["id"] for i in range(120)]
    first = box.receive("build", "coder", limit=50)
    assert [m["id"] for m in first["messages"]] == sent[:50] and first["more"] is True
    del box                                                   # the reader crashes before it acknowledges anything
    again = Mailbox()                                         # a new process: a new connection to the same store
    redelivered = again.receive("build", "coder", limit=50)
    assert [m["id"] for m in redelivered["messages"]] == sent[:50]
    assert all(m["deliveries"] == 2 for m in redelivered["messages"])     # said, so the reader can drop repeats
    seen, cursor = [], None
    while True:
        page = again.receive("build", "coder", since_cursor=cursor, limit=50)
        seen += [m["id"] for m in page["messages"]]
        cursor = page["next_cursor"]
        if not page["more"]:
            break
    assert seen == sent                                       # all 120, in order, each once
    assert again.ack("build", "coder", upto=sent[-1])["acked_upto"] == sent[-1]
    assert again.receive("build", "coder")["messages"] == []


def test_d1_a_message_for_one_member_never_reaches_another() -> None:
    box = team()
    secret = box.send("build", "planner", "only for the tester", to="tester")["id"]
    everyone = box.send("build", "planner", "for everyone")["id"]
    assert [m["id"] for m in box.receive("build", "coder")["messages"]] == [everyone]
    assert [m["id"] for m in box.receive("build", "tester")["messages"]] == [secret, everyone]
    assert box.receive("build", "planner")["messages"] == []    # a sender is not sent its own messages


def test_d1_a_retried_send_is_stored_once() -> None:
    box = team()
    first = box.send("build", "planner", "deploy at 5", to="coder", dedupe_key="plan-7")
    retry = box.send("build", "planner", "deploy at 5", to="coder", dedupe_key="plan-7")
    assert retry["id"] == first["id"] and retry["code"] == "G6_E_MAILBOX_ALREADY_STORED"
    assert len(box.receive("build", "coder")["messages"]) == 1


def test_d1_an_ack_past_what_a_reader_received_is_refused_so_nothing_is_skipped_unseen() -> None:
    box = team()
    ids = [box.send("build", "planner", i, to="coder")["id"] for i in range(5)]
    box.receive("build", "coder", limit=2)
    refused = box.ack("build", "coder", upto=ids[-1])
    assert refused["completion_state"] == "blocked-escalated" and refused["code"] == "G6_E_MAILBOX_ACK_UNSEEN"
    assert box.ack("build", "coder", upto=ids[1])["acked_upto"] == ids[1]
    assert [m["id"] for m in box.receive("build", "coder")["messages"]] == ids[2:]


def test_d1_an_ack_that_spans_messages_never_delivered_is_refused_and_they_stay_receivable() -> None:
    box = team()
    ids = [box.send("build", "planner", i, to="coder")["id"] for i in range(3)]
    after_second = box.receive("build", "coder", since_cursor=ids[1])      # delivers only the third
    assert [m["id"] for m in after_second["messages"]] == [ids[2]]
    refused = box.ack("build", "coder", upto=ids[2])
    assert refused["completion_state"] == "blocked-escalated" and refused["code"] == "G6_E_MAILBOX_ACK_UNSEEN"
    assert "2" in refused["message"]                                        # it says how many it would skip
    assert [m["id"] for m in box.receive("build", "coder")["messages"]] == ids   # the first two are still there
    assert box.ack("build", "coder", upto=ids[2])["acked_upto"] == ids[2]    # once each was delivered, the ack holds


def test_a_group_and_the_store_hold_at_most_their_stated_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.agents.agent_nanoclaw import mailbox

    assert mailbox.MAX_MESSAGES_PER_GROUP == 10_000 and mailbox.MAX_STORE_BYTES == 256 * 1024 * 1024
    monkeypatch.setattr(mailbox, "MAX_MESSAGES_PER_GROUP", 2)
    box = team()
    assert [box.send("build", "planner", i)["code"] for i in range(2)] == ["G6_E_MAILBOX_STORED"] * 2
    full = box.send("build", "planner", "one too many")
    assert full["completion_state"] == "blocked-escalated" and full["code"] == "G6_E_MAILBOX_GROUP_FULL"
    monkeypatch.setattr(mailbox, "MAX_STORE_BYTES", 10)
    box.create_group("other", ["a", "b"])
    assert box.send("other", "a", "more than ten bytes in all")["code"] == "G6_E_MAILBOX_STORE_FULL"
    assert len(box.history("build")["messages"]) == 2 and box.history("other")["messages"] == []


# --- typed groups (agent_openclaw) -----------------------------------------------------------------------------------

def test_a_typed_group_refuses_a_message_that_does_not_fit_its_schema() -> None:
    box = team(schema={"task": "text", "priority": "integer"})
    assert box.send("build", "planner", {"task": "write tests", "priority": 2}, to="coder")["code"] == \
        "G6_E_MAILBOX_STORED"
    for body in ({"task": "x"}, {"task": "x", "priority": "high"}, {"task": "x", "priority": 1, "approved": True},
                 "free text"):
        refused = box.send("build", "planner", body, to="coder")
        assert refused["completion_state"] == "blocked-escalated" and refused["code"] == "G6_E_MAILBOX_BAD_MESSAGE"
    assert len(box.receive("build", "coder")["messages"]) == 1


# --- membership, the sender as declared, refusals -------------------------------------------------------------------

def test_only_members_send_and_receive_and_the_sender_is_kept_as_declared() -> None:
    box = team()
    assert box.send("build", "stranger", "hi")["code"] == "G6_E_MAILBOX_NOT_A_MEMBER"
    assert box.send("build", "planner", "hi", to="stranger")["code"] == "G6_E_MAILBOX_NOT_A_MEMBER"
    assert box.receive("build", "stranger")["code"] == "G6_E_MAILBOX_NOT_A_MEMBER"
    box.send("build", "planner", "hi", to="coder")
    (message,) = box.receive("build", "coder")["messages"]
    assert message["sender"] == "planner" and message["sender_is"] == "declared, not authenticated"


def test_groups_are_kept_under_g6s_state_folder_and_deleting_one_says_what_went(own_state: Path) -> None:
    box = team()
    box.send("build", "planner", "unread", to="coder")
    assert (own_state / "agents" / "mailbox.db").is_file()
    gone = box.delete_group("build")
    assert gone["messages_deleted"] == 1 and gone["unacknowledged"] == 1
    assert box.receive("build", "coder")["code"] == "G6_E_MAILBOX_NO_GROUP"
    assert box.create_group("build", ["planner", "coder"])["code"] == "G6_E_MAILBOX_GROUP_CREATED"


def test_a_group_name_is_created_once() -> None:
    box = team()
    again = box.create_group("build", ["a", "b"])
    assert again["completion_state"] == "blocked-escalated" and again["code"] == "G6_E_MAILBOX_GROUP_EXISTS"


def test_history_shows_every_message_with_its_deliveries_and_acks() -> None:
    box = team()
    one = box.send("build", "planner", "a", to="coder")["id"]
    box.receive("build", "coder")
    box.ack("build", "coder", upto=one)
    (row,) = box.history("build")["messages"]
    assert row["id"] == one and row["delivered_to"] == {"coder": 1} and row["acked_by"] == ["coder"]


# --- AGENT_NANOCLAW-01 / AGENT_OPENCLAW-01: facts, not labels; over MCP -----------------------------------------------

def test_agent_nanoclaw_01_every_store_op_answers_a_fact_never_verified() -> None:
    replies = [invoke("create_group", {"group": "ops", "members": ["a", "b"]}),
               invoke("send", {"group": "ops", "sender": "a", "to": "b", "body": "hello"}),
               invoke("receive", {"group": "ops", "member": "b"}),
               invoke("ack", {"group": "ops", "member": "b", "upto": 1}),
               invoke("history", {"group": "ops"}),
               invoke("delete_group", {"group": "ops"})]
    assert [r["completion_state"] for r in replies] == ["qualified-draft"] * 6
    assert all(r["code"].startswith("G6_E_MAILBOX_") and r["evidence"] and r["warning_card"] for r in replies)
    assert replies[1]["id"] == 1 and replies[2]["messages"][0]["body"] == "hello"


def test_g3_an_approval_or_an_owner_cannot_be_written_into_the_input() -> None:
    for extra in ({"approved": True}, {"authenticated": True}, {"owner": "root"}):
        reply = invoke_reply("agent_nanoclaw", "send", json.dumps({"group": "g", "sender": "a", "body": "x", **extra}))
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"], extra


def test_an_op_without_what_it_needs_is_refused_with_its_own_code() -> None:
    for op in ("create_group", "send", "receive", "ack", "history", "delete_group"):
        reply = invoke_reply("agent_nanoclaw", op, "{}", allow_sensitive=OWNER_ALLOWS)
        assert reply["ok"] and reply["result"]["completion_state"] == "blocked-escalated", op
        assert reply["result"]["code"].startswith("G6_E_MAILBOX_"), op


def test_the_mailbox_is_one_store_with_no_runtime_sandbox_or_skill_ops() -> None:
    assert invoke("ops", {})["ops"] == ["create_group", "send", "receive", "ack", "history", "delete_group"]
    assert mailbox_pkg.__g6_mitigates__ == ("lost_agent_message",)


def test_g5_delete_group_destroys_messages_so_it_is_sensitive_and_history_is_the_one_read_only_op() -> None:
    module = get_registry().get_module("agent_nanoclaw")
    assert module.__g6_sensitive_ops__ == ("delete_group",)
    assert set(module.__g6_read_only_ops__) == {"history", "ops", "help"}
    team().send("build", "planner", "keep me", to="coder")
    result = get_registry().invoke("agent_nanoclaw", "delete_group", {"group": "build"}, block_sensitive=True)
    assert str(result.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")
    assert not invoke_reply("agent_nanoclaw", "delete_group", json.dumps({"group": "build"}))["ok"]
    assert len(Mailbox().history("build")["messages"]) == 1


def test_the_store_is_sqlite_in_wal_mode(own_state: Path) -> None:
    team()
    with sqlite3.connect(own_state / "agents" / "mailbox.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"