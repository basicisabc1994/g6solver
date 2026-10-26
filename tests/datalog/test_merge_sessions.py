# SPDX-License-Identifier: Apache-2.0
"""Persistent assertions retain Datalog's own checked semantics."""
import json

import pytest

from g6.core.registry import get_registry
from g6.datalog import DatalogBlock, DatalogInput
from g6.datalog import sessions


def call(op, **kw):
    return DatalogBlock().infer(DatalogInput(op=op, engine="python", **kw)).unwrap()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))


def test_assertions_survive_new_blocks_and_reset_keeps_rules():
    # Removal: sessions.py `conn.execute(... INSERT ...)` loses cross-call facts (capability/D1).
    out = call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["cedar"]]}, dry_run=False)
    assert out["completion_state"] == "qualified-draft", out
    assert call("query", session="one", query="q(X)")["queries"][0]["answers"][0]["bindings"] == {"X": "cedar"}
    call("assert_facts", session="one", facts={"p": [["elm"]]}, dry_run=False)
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 2
    assert call("reset", session="one", dry_run=False)["completion_state"] == "qualified-draft"
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 0
    assert call("query", session="other", query="q(X)")["completion_state"] == "blocked-escalated"


def test_dry_run_creates_no_session(tmp_path):
    # Removal: sessions.py `if dry_run:` would persist an unrequested change.
    out = call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]})
    assert out["report"]["dry_run"] and not out["report"]["written"]
    assert list(tmp_path.iterdir()) == []


def test_session_answer_is_checked_and_never_self_verified(monkeypatch):
    # Removal: sessions.py `result = ... evaluate(...)` bypasses G6's check (ADAPT_EXPERTA-01).
    call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]}, dry_run=False)
    from g6.datalog import checks
    def broken(*a, **kw):
        raise RuntimeError("checker down")
    monkeypatch.setattr(checks, "check_model", broken)
    out = call("query", session="one", query="q(X)")
    assert out["completion_state"] == "blocked-escalated" and out["queries"] == []
    assert "checker down" in out["reason"]


def test_invalid_assertions_do_not_change_session():
    # Removal: sessions.py `if result.completion_state == "blocked-escalated":` would store invalid facts.
    call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]}, dry_run=False)
    out = call("assert_facts", session="one", facts={"p": [["a", "b"]]}, dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 1


class Counted:
    """A fact value that counts the equality comparisons the session merge makes."""
    comparisons = 0

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        Counted.comparisons += 1
        return isinstance(other, Counted) and other.value == self.value

    def __hash__(self):
        return hash(self.value)


def test_oversized_assertion_is_refused_before_dedupe():
    # Removal: sessions.py `if sum(len(r) ...) > MAX_FACTS:` runs the dedupe over every asserted row first.
    from g6.datalog.language import MAX_FACTS
    call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]}, dry_run=False)
    Counted.comparisons = 0
    out = sessions.session_op("assert_facts", "one", facts={"p": [[Counted(i)] for i in range(MAX_FACTS)]},
                              engine="python", dry_run=False)
    assert Counted.comparisons == 0
    assert out.completion_state == "blocked-escalated" and out.code == "G6_E_DATALOG_INPUT_TOO_LARGE"
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 1


def test_dedupe_is_linear_within_the_cap():
    # Removal: sessions.py `seen = {tuple(row) ...}` (back to `row not in current`) compares every pair of rows.
    from g6.datalog.language import MAX_FACTS
    Counted.comparisons = 0
    sessions.session_op("assert_facts", "one", program="q(X) :- p(X).",
                        facts={"p": [[Counted(i)] for i in range(MAX_FACTS)]}, engine="python")
    assert Counted.comparisons < 100


def test_repeated_rows_are_stored_once():
    # Removal: sessions.py `if key not in seen:` stores a repeated row twice.
    out = call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"], ["a"], ["b"]]},
               dry_run=False)
    assert out["completion_state"] == "qualified-draft"
    call("assert_facts", session="one", facts={"p": [["b"], ["c"]]}, dry_run=False)
    with sessions.open_db(sessions.state_path("datalog", "sessions.db")) as conn:
        stored = json.loads(conn.execute("SELECT document FROM sessions WHERE session='one'").fetchone()[0])
    assert stored["facts"] == {"p": [["a"], ["b"], ["c"]]}


def test_dependency_failure_blocks_write(monkeypatch):
    # Removal: sessions.py `except Exception as exc:` would let dependency failure escape the refusal envelope.
    def broken(*a, **kw):
        raise OSError("dependency down")
    monkeypatch.setattr(sessions, "open_db", broken)
    out = call("assert_facts", session="one", program="p(a).", dry_run=False)
    assert out["completion_state"] == "blocked-escalated"
    assert "dependency down" in out["reason"]


@pytest.mark.parametrize("gate", ["raises", "holds"])
def test_session_write_is_a_sensitive_op_never_action_gated(monkeypatch, gate):
    # Removal: sessions.py `conn.commit()` loses the write; it also fails while sessions.py calls evaluate_action.
    from types import SimpleNamespace
    import g6.action_gating
    def raises(*a, **kw):
        raise OSError("action gate consulted")
    def holds(*a, **kw):
        return SimpleNamespace(gate_decision=SimpleNamespace(decision="block"))
    monkeypatch.setattr(g6.action_gating, "evaluate_action", raises if gate == "raises" else holds)
    out = call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]}, dry_run=False)
    assert out["completion_state"] == "qualified-draft" and out["report"]["written"], out
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 1


@pytest.mark.parametrize("op", ["assert_facts", "reset"])
def test_session_writes_are_refused_on_untrusted_surface(op):
    # Removal: datalog.__g6_sensitive_ops__ = ("assert_facts", "reset") exposes persistent mutations.
    reply = get_registry().invoke("datalog", op, {"session": "one"}, block_sensitive=True)
    assert reply.__class__.__name__ == "Failure"


def test_corrupt_session_and_program_replacement_are_refused():
    # Removal: sessions.py `if program and program != saved.program:` permits mid-task rule replacement.
    call("assert_facts", session="one", program="p(a).", dry_run=False)
    assert call("assert_facts", session="one", program="p(b).", dry_run=False)["completion_state"] == "blocked-escalated"
    with sessions.open_db(sessions.state_path("datalog", "sessions.db")) as conn:
        conn.execute("UPDATE sessions SET document=? WHERE session=?", (json.dumps({"program": "p(a).", "facts": {}, "confidence": 1}), "one"))
    assert call("evaluate", session="one")["completion_state"] == "blocked-escalated"

@pytest.mark.parametrize("dependency", ["read", "readback"])
def test_session_read_failure_and_readback_mismatch_refuse(monkeypatch, dependency):
    # Removal: sessions.py `_load(conn, session) != document` would commit an unconfirmed write.
    call("assert_facts", session="one", program="q(X) :- p(X).", facts={"p": [["a"]]}, dry_run=False)
    original, calls = sessions._load, []
    def failed_load(conn, session):
        calls.append(session)
        if dependency == "read":
            raise OSError("session read unavailable")
        return original(conn, session) if len(calls) == 1 else None
    with monkeypatch.context() as patch:
        patch.setattr(sessions, "_load", failed_load)
        out = (call("query", session="one", query="q(X)") if dependency == "read" else
               call("assert_facts", session="one", facts={"p": [["b"]]}, dry_run=False))
        assert out["completion_state"] == "blocked-escalated"
    assert call("query", session="one", query="q(X)")["queries"][0]["count"] == 1
