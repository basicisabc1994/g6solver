# SPDX-License-Identifier: Apache-2.0
"""A Claude Code error result's status fields survive a long message (0.3.0 issuance window: review l4b finding 3).

``g6.core.host_models`` kept the last 300 characters of what a failed program printed. On an error result those are
the end of its message, so a long one pushed out the two fields that say a limit of the account was met, and a reader
of the failure took the limit for a failed request. Pinned: the fields are said whole, first, read from the parsed
result and never from a message's text. Offline: only the reply reader runs, no program is started.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.self_maintenance.usage import limit_ends

NOW = datetime(2026, 11, 1, tzinfo=UTC)
LONG = "You've hit your usage limit. " + "Please wait for the reset. " * 20


@pytest.fixture(autouse=True)
def no_key_file(monkeypatch):
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})


def _failure(result: dict, code: int = 1) -> str:
    host = host_models.ClaudeCodeCaller()
    with pytest.raises(LLMError) as failed:
        host_models.ClaudeCodeCaller._reply(host, code, json.dumps(result), "", Path("."), ())
    return str(failed.value)


@pytest.mark.parametrize("fields", [{"api_error_status": 429}, {"api_error": "usage_limit_reached"},
                                    {"api_error_status": 429, "api_error": "usage_limit_reached"}])
def test_a_long_message_does_not_cut_a_limit_out_of_what_the_failure_says(fields):
    said = _failure({"type": "result", "is_error": True, **fields, "result": LONG})
    assert said.startswith(f"{host_models.HOST_FAILED}: claude exited 1 and gave an error result: " + json.dumps(fields))
    assert limit_ends(said, NOW) is not None, "the reader of a failed request no longer finds the limit"


def test_a_short_message_reads_as_it_did():
    said = _failure({"type": "result", "is_error": True, "api_error_status": 429, "result": "limit"})
    assert limit_ends(said, NOW) is not None and said.endswith('"result": "limit"}')


def test_an_error_result_with_no_status_fields_says_none():
    result = {"type": "result", "is_error": True, "result": LONG}
    assert _failure(result) == (f"{host_models.HOST_FAILED}: claude exited 1 and gave an error result: "
                                + " ".join(json.dumps(result).split())[-300:])


def test_words_in_a_message_cannot_stand_in_for_the_fields():
    written = 'the model writes "api_error_status": 429 and "api_error": "usage_limit_reached" in its reply. '
    said = _failure({"type": "result", "is_error": True, "result": LONG + written})
    assert limit_ends(said, NOW) is None


@pytest.mark.parametrize("fields", [{"api_error_status": True}, {"api_error_status": "429"}, {"api_error_status": 429.0},
                                    {"api_error": "usage limit; reached"}, {"api_error": ["usage_limit_reached"]},
                                    {"api_error": "x" * 200}])
def test_a_field_of_another_shape_is_not_said_whole(fields):
    said = _failure({"type": "result", "is_error": True, **fields, "result": LONG})
    assert said.startswith(f"{host_models.HOST_FAILED}: claude exited 1 and gave an error result: ")
    assert "api_error" not in said and limit_ends(said, NOW) is None


def test_a_result_that_is_no_error_result_gains_nothing():
    said = _failure({"type": "result", "is_error": False, "api_error_status": 429, "result": 7, "note": LONG})
    assert "gave a result with no text" in said and limit_ends(said, NOW) is None
