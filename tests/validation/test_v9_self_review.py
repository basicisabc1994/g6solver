# SPDX-License-Identifier: Apache-2.0
"""V9's arm 2, the agent's own review (PROTOCOL.md, "Arms"): the session inputs and caps, and the disposition read
from a session record. Nothing here starts a model or an agent session.

Development fixtures only: the contract and the answers are made up for these tests."""
from __future__ import annotations

import hashlib

import pytest

from patch_review_utility import contract, self_review

CONTRACT = contract.split("Parsing a date with a trailing space fails.", "- A trailing space is ignored.")
PATCH = "--- a/dates.py\n+++ b/dates.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


@pytest.fixture
def prepared():
    return self_review.prepare("dev-1", CONTRACT, patch=PATCH, time_limit_s=1800, token_cap=200_000)


def session(answer, **more):
    return {"task_id": "dev-1", "patch_sha256": hashlib.sha256(PATCH.encode("utf-8")).hexdigest(), "answer": answer,
            "ended": "completed", "elapsed_s": 300.0, "tokens": 50_000, "model_calls": 12, **more}


def test_the_session_inputs_carry_the_contract_the_caps_and_no_label_or_g6_material(prepared):
    prompt = prepared["prompt"]
    assert contract.render(CONTRACT) in prompt and prompt.rstrip().endswith("VERDICT: REJECT")
    assert "G6" not in prompt and "hidden" not in prompt.lower() and "label" not in prompt.lower()
    assert prepared["caps"] == {"time_limit_s": 1800, "token_cap": 200_000, "network": "none",
                                "tools": "the same tools the session had while writing the patch",
                                "edits": "discarded"}
    assert prepared["patch_sha256"] == hashlib.sha256(PATCH.encode("utf-8")).hexdigest()
    assert prepared["prompt_sha256"] == hashlib.sha256(self_review.REVIEW_PROMPT.encode("utf-8")).hexdigest()
    with pytest.raises(ValueError):
        self_review.prepare("dev-1", CONTRACT, patch=PATCH, time_limit_s=0, token_cap=1)


@pytest.mark.parametrize("answer, category", [
    ("The patch handles the space.\nVERDICT: ACCEPT\n\n", "accept"),
    ("It misses the second case.\nVERDICT: REJECT", "defect_found"),
    ("Looks fine.\nVERDICT: ACCEPT (with reservations)", "insufficient_evidence"),
    ("Looks fine.\n**VERDICT: ACCEPT**", "insufficient_evidence"),
    ("VERDICT: REJECT\nOn reflection:\nVERDICT: ACCEPT", "insufficient_evidence"),
    ("VERDICT: ACCEPT\nThat is my answer.", "insufficient_evidence"),
    ("No verdict here.", "insufficient_evidence"),
    ("", "insufficient_evidence"),
])
def test_only_a_final_single_exact_accept_line_is_acceptance(prepared, answer, category):
    found = self_review.disposition(prepared, session(answer))
    assert found["category"] == category and found["accepted"] is (category == "accept")


@pytest.mark.parametrize("ended, kind", [("timed_out", "killed_at_time_limit"), ("token_cap", "stopped_at_token_cap")])
def test_a_session_stopped_at_its_limit_without_a_reply_is_a_tool_error_with_one_an_abstention(prepared, ended, kind):
    silent = self_review.disposition(prepared, session("", ended=ended))
    assert (silent["category"], silent["error_kind"], silent["time_or_cap_reached"]) == (
        "tool_error_or_refusal", kind, True)
    said = self_review.disposition(prepared, session("VERDICT: ACCEPT", ended=ended))
    assert (said["category"], said["time_or_cap_reached"]) == ("insufficient_evidence", True)


def test_a_late_or_over_cap_reply_an_error_a_refusal_or_no_record_is_never_acceptance(prepared):
    accept = "VERDICT: ACCEPT"
    late = self_review.disposition(prepared, session(accept, elapsed_s=1801.0))
    assert late["category"] == "insufficient_evidence" and "time limit" in late["detail"]
    over = self_review.disposition(prepared, session(accept, tokens=200_001))
    assert over["category"] == "insufficient_evidence" and "token cap" in over["detail"]
    assert late["time_or_cap_reached"] is True and over["time_or_cap_reached"] is True
    plain = self_review.disposition(prepared, session("No verdict here."))
    assert plain["category"] == "insufficient_evidence" and plain["time_or_cap_reached"] is False
    assert self_review.disposition(prepared, session(accept, ended="error"))["error_kind"] == "tool_error"
    assert self_review.disposition(prepared, session(accept, ended="refused"))["error_kind"] == "refusal"
    assert self_review.disposition(prepared, None)["error_kind"] == "tool_error"
    other = self_review.disposition(prepared, session(accept, patch_sha256="0" * 64))
    assert other["error_kind"] == "tool_error" and "captured patch" in other["detail"]


def test_the_disposition_records_its_cost(prepared):
    found = self_review.disposition(prepared, session("VERDICT: ACCEPT"))
    assert found["cost"] == {"seconds": 300.0, "model_calls": 12, "tokens": 50_000}
