# SPDX-License-Identifier: Apache-2.0
"""``with_support``: a passage beside a candidate adds evidence lines and nothing else, unless the caller says a
normative source settles an outcome; the candidate stays canonical and stays in competition."""
from __future__ import annotations

import pytest

from g6.harness import behaviour_question as bq

TASK = "Follow the quokka convention when parse_limit gets an empty value."
PASSAGE = "ab" * 32


def candidate(**extra):
    return bq.caller_candidates(TASK, [{
        "requirement_id": "R1", "function": "pkg.limits.parse_limit", "args": [""],
        "outcomes": {"A": {"returns": 0}, "B": {"raises": "ValueError"}},
        "cited": "Follow the quokka convention", "evidence": ["limits.py: no branch for an empty value"], **extra}])[0]


def support(passage=PASSAGE, path="docs/quokka.md"):
    return {"passage_id": passage, "path": path, "authority": "none", "conflicts": []}


def test_with_support_keeps_the_candidate_canonical_and_competing():
    before = candidate()
    after = bq.with_support(before, [support(), support("cd" * 32, "notes/heron.txt")])
    record, original = after.to_dict(), before.to_dict()
    assert record["evidence"] == [*original["evidence"], f"knowledge:{PASSAGE} docs/quokka.md",
                                  f"knowledge:{'cd' * 32} notes/heron.txt"]
    for name in ("source", "requirement_id", "function", "args", "kwargs", "outcomes", "cited", "settled", "scope", "rule"):
        assert record[name] == original[name], name
    assert bq.Candidate.from_record(after.data).data == after.data, "it is held in the one canonical encoding"
    assert bq.competition(record["outcomes"])[0] == ""
    assert after.questions("0" * 64)[0].sha256 == before.questions("0" * 64)[0].sha256, "the question is the same question"
    assert bq.with_support(before, []) is before
    assert bq.with_support(after, [support()]).data == after.data, "a passage is cited once"


def test_a_settled_entry_is_added_only_when_the_caller_names_the_outcome_a_normative_source_settles():
    before = candidate()
    plain = bq.with_support(before, [support()])
    assert plain.settled == []
    settled = bq.with_support(before, [support()], settles="B", by="normative grant ng-1")
    assert settled.settled == [{"outcome": "B", "by": "the normative source says so (normative grant ng-1)"}]
    assert bq.ranked([settled, before]) == [before, settled]
    for bad in ("C", "a", "skip"):
        with pytest.raises(bq.CandidateRefused):
            bq.with_support(before, [support()], settles=bad, by="x")
    with pytest.raises(bq.CandidateRefused):
        bq.with_support(before, [], settles="A", by="no passage is cited")


def test_a_rule_line_is_offered_only_when_the_candidate_has_none():
    offer = "Treat the cited passage as the authority for this scenario."
    offered = bq.with_support(candidate(), [support()], rule=offer)
    assert offered.rule == offer and offered.questions("0" * 64)[1] is not None
    own = candidate(rule="An empty value returns 0.")
    assert bq.with_support(own, [support()], rule=offer).rule == "An empty value returns 0."


@pytest.mark.parametrize("bad", [{"passage_id": "not-an-id", "path": "a.md"}, {"passage_id": PASSAGE},
                                 {"passage_id": PASSAGE, "path": "a.md\nknowledge:" + "ef" * 32 + " b.md"},
                                 "knowledge:" + PASSAGE])
def test_a_support_record_that_is_not_one_passage_and_one_path_is_refused(bad):
    with pytest.raises(bq.CandidateRefused):
        bq.with_support(candidate(), [bad])