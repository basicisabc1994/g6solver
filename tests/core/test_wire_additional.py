# SPDX-License-Identifier: Apache-2.0
"""Additional identity copies found by the principal census follow-through."""
import sqlite3
from types import SimpleNamespace

import pytest


def test_operator_uses_shared_principals_and_declared_kind():
    from g6.operator.queue import QueueError, _person

    for reviewer in ("alice ", "ＡLICE"):
        with pytest.raises(QueueError):
            _person(reviewer, "Alice", actor_kind="person")
    with pytest.raises(QueueError):
        _person("Bob", "Alice", actor_kind="automated")
    with pytest.raises(QueueError):   # a declared kind never lifts G6's own auto: marker
        _person("auto:Pat", "Alice", actor_kind="person")
    assert _person("Bob", "Alice") == "Bob"


@pytest.mark.parametrize("reviewer,kind,allowed", [
    ("alice ", "person", False), ("Bob", "automated", False), ("auto:Pat", "person", False),
    ("Bob", "unknown", True), ("Bob", "person", True),
])
def test_operator_sql_uses_the_same_identity_rule(reviewer, kind, allowed):
    from g6.operator.queue import _SCHEMA

    with sqlite3.connect(":memory:") as conn:   # pure SQL: no Python function is registered
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO actions (action_id, venture_id, task_id, action_type, body, meta_json, drafted_by, "
                     "state, review_json, status, decoy, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("a", "v", "t", "email", "draft", "{}", "Alice", "qualified-draft", "{}", "proposed", 0, "now"))
        conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, reviewer_kind, decoy, "
                     "text_before, text_after, note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("a", "v", "approved", reviewer, kind, 0, "draft", "draft", "", "now"))
        if allowed:
            conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
            assert conn.execute("SELECT status FROM actions").fetchone()[0] == "approved"
        else:
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
            assert conn.execute("SELECT status FROM actions").fetchone()[0] == "proposed"


def test_harness_recorded_author_is_normalized_by_shared_reader():
    from g6.harness import Check, _caller_author

    assert _caller_author(Check("synthetic", lambda value: True, author="ｇ６")) == "caller"


def test_retriever_accepts_declared_source_kinds():
    from g6.grounding.lineage import _lineage
    from g6.grounding.retrieval import TextRetriever

    texts = {"https://one.example/a": "the threshold is 42", "https://two.example/b": "the threshold is 42"}
    passages = TextRetriever.from_texts(texts, source_kinds={key: "url" for key in texts}).search("threshold")
    assert {_lineage(passage) for passage in passages} == {"host:one.example", "host:two.example"}


def test_exact_failed_job_state_still_lowers_usage():
    from g6.workforce.contracts import _usage_declared

    assert _usage_declared({"state": "failed"}) == {"error"}
    assert _usage_declared({"state": "FAILED"}) == {"error"}   # a downward reading: the base's, any case


def test_held_out_by_the_builder_under_another_spelling_is_not_independent():
    from g6.metaharness.builder import Builder

    builder = Builder("synthetic", "task", "Alice")
    assert builder.set_held_out((SimpleNamespace(case_id="synthetic"),), source="person", author="ＡLICE ") == "caller"


@pytest.mark.parametrize("declared", [False, True])
def test_review_observer_records_typed_refusal_code(monkeypatch, declared):
    from g6.harness.workflow import adapters

    monkeypatch.setenv("G6_WORKFLOW_RECORD", "on")
    events = {}
    def write(kind, *args, **kwargs):
        events[kind] = kwargs["data"]
    monkeypatch.setattr(adapters, "_Run", lambda *args, **kwargs: SimpleNamespace(write=write))
    reply = {"error": "[G6_E_PLANTED]"}
    if declared:
        reply["code"] = "G6_E_INPUT_VALIDATION"
    assert adapters.observe_review("synthetic", "task", "job", reply, started_at="now", finished_at="now") == ""
    assert events["review_finished"]["refusal_code"] == ("G6_E_INPUT_VALIDATION" if declared else "G6_E_PLANTED")


@pytest.mark.parametrize("declared", [False, True])
def test_model_attempt_does_not_guess_withheld_from_message(declared):
    from g6.core import cloud_consent as cloud
    from g6.core.llm import LLMError
    from g6.harness import verification_proposer, verification_search

    error = LLMError("ordinary human text" if declared else cloud.REQUIRED)
    if declared:
        error.code = cloud.REQUIRED
    def failed(*args, **kwargs):
        raise error
    limited = verification_proposer._Limits(SimpleNamespace(check=lambda: None, complete=failed),
        SimpleNamespace(last_usage=None), dict(verification_search.PROPOSER_SETTINGS), ["synthetic"])
    with pytest.raises(LLMError):
        limited.complete("system", "user")
    assert limited.calls[-1]["sent"] is False   # a consent refusal before any request: declared or leading its words
