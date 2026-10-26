# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.shared_workspace: several agents in one workspace folder, where a write says which version it read and
is refused with both hashes when someone has replaced it since, locks are taken with a token only the taker holds, and
every refused write and every takeover is logged (D1, the P1 findings, G3, G4, G5), and the member through its doors
(D2, D4, D6)."""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

import g6.workspace.shared_workspace as shared_workspace
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply
from g6.workspace import _envelope as E
from g6.workspace.shared_workspace import Workspace, ops

OPS = ["read", "write", "lock", "unlock", "locks", "conflicts"]
ALLOW = frozenset({"shared_workspace.write"})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _invoke(op: str, allow: frozenset[str] = frozenset(), **params: object) -> dict:
    return invoke_reply("shared_workspace", op, json.dumps(params), allow_sensitive=allow)


@pytest.fixture
def plan(home) -> str:  # noqa: ANN001
    """Task t1's workspace with plan.md written once; its SHA-256, the version both agents read."""
    done = ops.write("t1", "plan.md", "v1\n", expect_sha256=None, holder="setup", dry_run=False)
    assert done.written, done.reason
    return done.sha256


# --- D1: agents overwriting each other's work -------------------------------------------------------------------------

def test_d1_of_two_agents_that_read_the_same_version_only_the_first_write_lands(plan: str) -> None:
    base = ops.read("t1", "plan.md").sha256
    first = ops.write("t1", "plan.md", "v1\nalice's line\n", expect_sha256=base, holder="alice", dry_run=False)
    second = ops.write("t1", "plan.md", "v1\nbob's line\n", expect_sha256=base, holder="bob", dry_run=False)
    assert first.completion_state == "qualified-draft" and first.written
    assert second.completion_state == "blocked-escalated" and second.code == E.STALE_WRITE
    assert (second.expected_sha256, second.current_sha256) == (base, _sha("v1\nalice's line\n"))
    assert ops.read("t1", "plan.md").text == "v1\nalice's line\n"            # alice's work is still there
    logged = ops.conflicts("t1").conflicts
    assert [(c["kind"], c["claimed_holder"], c["expected_sha256"], c["current_sha256"]) for c in logged] == [
        ("stale_write", "bob", base, first.sha256)]


def test_d1_a_write_that_says_nothing_of_what_it_read_never_replaces_a_file(plan: str) -> None:
    # a case the check was not written around: the archive's writers overwrote whatever was there
    blind = ops.write("t1", "plan.md", "overwritten", dry_run=False)
    assert blind.code == E.STALE_WRITE and blind.current_sha256 == plan and blind.expected_sha256 is None
    assert ops.read("t1", "plan.md").text == "v1\n"


def test_d1_of_many_writers_at_once_exactly_one_lands_and_every_other_is_logged(plan: str) -> None:
    results: list[E.WorkspaceResult] = []
    start = threading.Barrier(8)

    def agent(n: int) -> None:
        start.wait()
        results.append(ops.write("t1", "plan.md", f"v1\nagent {n}\n", expect_sha256=plan, holder=f"agent-{n}",
                                 dry_run=False))

    threads = [threading.Thread(target=agent, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    landed = [r for r in results if r.completion_state == "qualified-draft"]
    assert len(landed) == 1 and landed[0].written is True
    assert sorted(r.code for r in results if r not in landed) == [E.STALE_WRITE] * 7, [r.reason for r in results]
    assert ops.read("t1", "plan.md").sha256 == landed[0].sha256
    assert len(ops.conflicts("t1").conflicts) == 7


# --- the P1 findings --------------------------------------------------------------------------------------------------

def test_p1_conflicts_are_the_refused_writes_that_happened_and_locks_are_the_components_own(plan: str) -> None:
    # SHARED_WORKSPACE-01: the archive's detect_conflicts keyed on fields its own locks never wrote, so it found
    # conflicts only in hand-written test files
    assert ops.conflicts("t1").conflicts == []
    taken = ops.lock("t1", "plan.md", "alice", ttl_seconds=60)
    assert taken.completion_state == "qualified-draft" and taken.token and taken.claimed_holder == "alice"
    assert [lock["path"] for lock in ops.locks("t1").locks] == ["plan.md"]
    refused = ops.lock("t1", "plan.md", "bob", ttl_seconds=60)
    assert refused.code == E.LOCKED
    write = ops.write("t1", "plan.md", "bob's", expect_sha256=plan, holder="bob", dry_run=False)
    assert write.code == E.LOCKED and write.locked_by == "alice"
    assert [(c["kind"], c["claimed_holder"], c["locked_by"]) for c in ops.conflicts("t1").conflicts] == [
        ("locked_write", "bob", "alice")]


def test_p1_the_stated_rule_settles_it_the_first_write_wins_and_the_other_re_reads(plan: str) -> None:
    # SHARED_WORKSPACE-02: the archive's floor named no winner and a winner existed only as a model's recommendation
    ops.write("t1", "plan.md", "v2\n", expect_sha256=plan, holder="alice", dry_run=False)
    lost = ops.write("t1", "plan.md", "v1 and bob\n", expect_sha256=plan, holder="bob", dry_run=False)
    assert lost.code == E.STALE_WRITE and "Read it again" in lost.warning_card["suggested_action"]
    again = ops.read("t1", "plan.md")
    merged = ops.write("t1", "plan.md", again.text + "and bob\n", expect_sha256=again.sha256, holder="bob",
                       dry_run=False)
    assert merged.written and ops.read("t1", "plan.md").text == "v2\nand bob\n"
    assert not hasattr(shared_workspace, "arbitrate_conflict") and "llm" not in json.dumps(describe_reply(
        "shared_workspace")["ops"])


def test_p2_a_holders_name_is_recorded_never_used_as_a_path(home, outside) -> None:  # noqa: ANN001
    # SHARED_WORKSPACE-03: write_note(agent_id) wrote root / "agent-notes" / f"{agent_id}.md"
    taken = ops.lock("t1", "notes.md", "../../../outside/secret", ttl_seconds=60)
    assert taken.claimed_holder == "../../../outside/secret"
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


# --- G3: a holder's name is a claim; the token is what opens a lock ----------------------------------------------------

def test_g3_claiming_the_holders_name_does_not_open_their_lock(plan: str) -> None:
    taken = ops.lock("t1", "plan.md", "alice", ttl_seconds=60)
    impostor = ops.write("t1", "plan.md", "mine", expect_sha256=plan, holder="alice", dry_run=False)
    assert impostor.code == E.LOCKED
    assert ops.unlock("t1", "plan.md", token="guess").code == E.LOCKED
    assert ops.lock("t1", "plan.md", "alice", ttl_seconds=60).code == E.LOCKED   # no renewal without the token
    through = ops.write("t1", "plan.md", "alice's", expect_sha256=plan, token=taken.token, holder="alice",
                        dry_run=False)
    assert through.written
    assert ops.unlock("t1", "plan.md", token=taken.token).released is True and ops.locks("t1").locks == []


@pytest.mark.parametrize("claim", [{"approved": True}, {"force": True}, {"override": True}, {"winner": "bob"},
                                   {"reviewer": "the owner"}, {"root": "C:/"}])
def test_g3_a_callers_approval_or_override_opens_nothing(plan: str, claim: dict) -> None:
    reply = _invoke("write", ALLOW, task_id="t1", path="plan.md", content="x", dry_run=False, **claim)
    assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"]
    assert ops.read("t1", "plan.md").text == "v1\n"


# --- locks expire, a takeover is logged, and an unreadable record fails closed -----------------------------------------

def test_a_lock_past_its_time_to_live_is_taken_over_and_the_takeover_is_logged(home) -> None:  # noqa: ANN001
    now = [1_000_000.0]
    ws = Workspace.for_task("t1", clock=lambda: now[0])
    old = ws.lock("plan.md", "alice", ttl=30)
    with pytest.raises(E.WorkspaceRefused) as held:
        ws.lock("plan.md", "bob", ttl=30)
    assert held.value.code == E.LOCKED
    renewed = ws.lock("plan.md", "alice", ttl=30, token=old["token"])
    assert renewed["renewed"] is True and renewed["token"] == old["token"]
    now[0] += 31
    taken = ws.lock("plan.md", "bob", ttl=30)
    assert taken["took_over"]["claimed_holder"] == "alice" and taken["claimed_holder"] == "bob"
    takeovers = ws.conflicts()["takeovers"]
    assert [(t["claimed_holder"], t["previous_holder"]) for t in takeovers] == [("bob", "alice")]
    with pytest.raises(E.WorkspaceRefused):                       # alice's old token opens nothing now
        ws.write("plan.md", "alice's", expect_sha256=None, token=old["token"], dry_run=False)


def test_g4_an_unreadable_lock_refuses_writes_and_an_unreadable_log_line_is_counted(plan: str) -> None:
    ws = Workspace.for_task("t1")
    ws.lock("plan.md", "alice", ttl=60)
    (record,) = (ws.bookkeeping / "locks").glob("*.json")
    record.write_text("{not json", encoding="utf-8")
    assert ops.write("t1", "plan.md", "x", expect_sha256=plan, holder="bob", dry_run=False).code == E.LOCKED
    with open(ws.bookkeeping / "coordination.jsonl", "a", encoding="utf-8") as log:
        log.write("{torn line\n")
    found = ops.conflicts("t1")
    assert found.unreadable_lines == 1 and len(found.conflicts) == 1


def test_g4_a_plain_write_a_restore_and_a_clean_up_respect_a_lock(plan: str) -> None:
    from g6.workspace.workspace_manager import ops as manager

    ops.lock("t1", "plan.md", "alice", ttl_seconds=60)
    saved = manager.backup("t1", dry_run=False).backup
    assert manager.write("t1", "plan.md", "plain", dry_run=False).code == E.LOCKED
    assert manager.restore("t1", saved, dry_run=False).code == E.LOCKED
    assert manager.cleanup("t1", dry_run=False).code == E.LOCKED
    assert ops.read("t1", "plan.md").text == "v1\n"


# --- G5: the write is sensitive and a dry run unless asked ------------------------------------------------------------

def test_g5_the_write_is_refused_over_mcp_and_on_an_untrusted_surface(plan: str) -> None:
    reply = _invoke("write", task_id="t1", path="plan.md", content="x", expect_sha256=plan, dry_run=False)
    assert not reply["ok"] and "G6_E_SENSITIVE_COMPONENT_DENIED" in reply["error"]
    blocked = get_registry().invoke("shared_workspace", "write", {"task_id": "t1"}, block_sensitive=True)
    assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(blocked.failure())
    dry = _invoke("write", ALLOW, task_id="t1", path="plan.md", content="x", expect_sha256=plan)
    assert dry["ok"] and dry["result"]["written"] is False and dry["warning_card"]["code"] == E.DRY_RUN
    stale = _invoke("write", ALLOW, task_id="t1", path="plan.md", content="x", expect_sha256="0" * 64)
    assert stale["result"]["code"] == E.STALE_WRITE                  # a dry run says it would be refused
    assert ops.read("t1", "plan.md").text == "v1\n" and ops.conflicts("t1").conflicts == []


# --- the member through its doors -------------------------------------------------------------------------------------

def test_it_is_a_member_of_the_workspace_family_registered_under_its_own_name() -> None:
    ref = get_registry().refs["shared_workspace"]
    assert (ref.module, ref.family) == ("g6.workspace.shared_workspace", "workspace")


def test_its_declarations_are_registered_failure_modes() -> None:
    assert shared_workspace.__g6_sensitive_ops__ == ("write",)
    assert shared_workspace.__g6_mitigates__ == ("hidden_state_or_workspace_conflict",)
    codes = shared_workspace.__g6_mitigates__ + shared_workspace.__g6_introduces__
    assert all(FailureModeRegistry.is_registered(c) for c in codes)
    assert shared_workspace.__g6_outside__ == () and shared_workspace.__g6_extras__ == {}
    assert "g6.workspace.shared_workspace" not in FailureModeRegistry.planned()


def test_an_agent_finds_it_by_the_failure_it_guards_against() -> None:
    found = [r["component"] for r in discovery.find("agents overwriting each other's work")]
    assert found[0] == "shared_workspace", found


def test_describe_lists_its_ops_and_its_skill() -> None:
    info = describe_reply("shared_workspace")
    assert info["ops"] == OPS and info["sensitive_ops"] == ["write"]
    assert info["skill"].startswith("---\nname: g6-shared-workspace")


def test_every_non_sensitive_op_round_trips_over_mcp(plan: str) -> None:
    taken = _invoke("lock", task_id="t1", path="plan.md", holder="alice", ttl_seconds=30)
    replies = {"read": _invoke("read", task_id="t1", path="plan.md"), "lock": taken,
               "locks": _invoke("locks", task_id="t1"), "conflicts": _invoke("conflicts", task_id="t1"),
               "unlock": _invoke("unlock", task_id="t1", path="plan.md", token=taken["result"]["token"])}
    for op, reply in replies.items():
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", (op, reply)
        assert reply["result"]["op"] == op and reply["warning_card"]["code"].startswith("G6_E_WORKSPACE_")
    assert replies["unlock"]["result"]["released"] is True
    assert "token_sha256" not in json.dumps(replies["locks"])


def test_the_typed_input_refuses_unknown_ops_and_fields_and_says_what_is_missing() -> None:
    assert "G6_E_INPUT_VALIDATION" in _invoke("arbitrate")["error"]
    missing = _invoke("lock", task_id="t1", path="plan.md")
    assert missing["ok"] and missing["result"]["code"] == E.INPUT_INVALID          # no holder
    listed = get_registry().invoke("shared_workspace", "ops").unwrap()
    assert listed["ops"] == OPS and listed["sensitive"] == ["write"]
    assert len(get_registry().invoke("shared_workspace", "help").unwrap()["help"]) == len(OPS)
