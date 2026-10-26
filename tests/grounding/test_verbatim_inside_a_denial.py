# SPDX-License-Identifier: Apache-2.0
"""GROUNDING-01 (audit 2026-10-23): when a claim's words appeared in order in a passage, ``check_claim`` returned the claim
itself as the source sentence, so the negation, number and judge checks compared the claim with itself, and "It is not
true that X" made X read as quoted. The verbatim branch now returns the source sentence that contains the claim, and
every check runs against it."""
from __future__ import annotations

from g6.grounding import Passage, check_claim
from g6.mcp import invoke_reply

DENIAL = "It is not true that the bridge opened in 1932."


def test_a_claim_quoted_inside_a_sentence_that_denies_it_is_not_supported() -> None:
    # fails if claims._best_sentence returns the claim instead of its containing source sentence
    r = check_claim("The bridge opened in 1932.", [Passage(DENIAL, "history")])
    assert r.status == "conflict" and r.completion_state == "blocked-escalated", r
    assert r.sentence == DENIAL and "negation" in r.conflicts[0]


def test_the_number_and_judge_checks_read_the_source_sentence_too() -> None:
    # fails if claims.check_claim passes the claim, not the containing sentence, to the number check or the judge
    source = "The bridge opened in 1932 and closed in 1990; it never reopened."
    seen = []
    r = check_claim("bridge opened in 1932", [Passage(source, "history")],
                    judge=lambda claim, sentence: seen.append(sentence) or "entails")
    assert seen == ["The bridge opened in 1932 and closed in 1990;"] and r.sentence == seen[0]
    # a different case the branch was not written around: a quote spanning a wrapped line, inside a denial
    wrapped = check_claim("refunds are issued within 14 days",
                          [Passage("Policy: it is not the case that refunds are\nissued within 14 days.", "policy")])
    assert wrapped.status == "conflict" and "\n" in wrapped.sentence


def test_a_plain_verbatim_quote_is_still_quoted() -> None:
    # survival: the containing sentence agrees, so the quote stands, and so it does over MCP
    r = check_claim("The bridge opened in 1932", [Passage("The bridge opened in 1932. It was repainted in 2001.", "h")])
    assert r.status == "quoted" and r.completion_state == "qualified-draft" and r.sentence == "The bridge opened in 1932."
    reply = invoke_reply("grounding", "check_claim", '{"claim": "The bridge opened in 1932", "sources": {"h": "%s"}}'
                         % DENIAL)
    assert reply["result"]["check"]["status"] == "conflict", reply
