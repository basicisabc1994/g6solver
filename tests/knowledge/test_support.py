# SPDX-License-Identifier: Apache-2.0
"""A cited passage beside a behaviour-question candidate: relevance, byte match and authority are three records;
retrieval alone never settles a question; a normative grant comes only from the person's confirmed rule answer in the
question store, and covers one repository, one source version and one scenario."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import g6.knowledge
from g6.harness import behaviour_question as bq
from g6.harness import question_store as qs
from g6.harness.confirmed_expectation import Answer
from g6.knowledge import _engine as engine
from g6.knowledge import _support
from g6.knowledge._registry import ProjectStore, Refused
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS = SUBJECTS[0]
TASK = f"Follow the {DOCS} convention when parse_limit gets an empty value."


def candidate(*, function="pkg.limits.parse_limit", args=("",), rule=""):
    return bq.caller_candidates(TASK, [{
        "requirement_id": "R1", "function": function, "args": list(args),
        "outcomes": {"A": {"returns": 0}, "B": {"raises": "ValueError"}},
        "cited": f"Follow the {DOCS} convention", "evidence": ["limits.py: parse_limit has no branch for an empty value"],
        **({"rule": rule} if rule else {})}])[0]


def support(rig, record=None, **kw):
    return _support.support_for(rig.local(), record or candidate().to_dict(), clock=lambda: NOW, **kw)


def confirmed_rule(rig, one, *, option="A", identity="a" * 64, binds="rule"):
    """The person confirms ``option`` for the rule the form offered; what the question store then holds."""
    offered = bq.with_support(candidate(), [s.model_dump() for s in [one]], rule=_support.designation_rule(one))
    case, rule = offered.questions("0" * 64)
    store = qs.TaskQuestions(identity)
    store.ask(case, rule, offered)
    shown = rule if binds == "rule" else case
    answer = Answer(question_sha256=shown.sha256, option=option, answered_by="person:Ada", answered_at=qs.now())
    return store, store.answer(answer), answer


def test_relevance_byte_match_and_authority_are_separate_records(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    found = support(rig)
    assert found and found[0].path == f"docs/{DOCS}.md" and found[0].registration_id == source
    one = found[0]
    assert one.relevance["backend"] == "sqlite" and one.relevance["passage_id"] == one.passage_id
    assert one.byte_match.outcome == "intact" and len(one.byte_match.reread_sha256) == 64
    assert one.authority == "none" and one.grant_id == "" and one.outcome == ""
    assert set(one.model_dump()) >= {"relevance", "byte_match", "authority"}
    assert DOCS in one.view.text, "a view, never the raw record"
    assert len(support(rig, top_k=1)) == 1


def test_passage_without_normative_grant_never_suppresses(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    found = support(rig)
    allowed, why = _support.may_suppress(found)
    assert allowed is False and "retrieval alone settles nothing" in why
    before = candidate()
    after = bq.with_support(before, [s.model_dump() for s in found])
    assert after.settled == before.settled == [] and after.rule == before.rule == ""
    assert after.to_dict()["outcomes"] == before.to_dict()["outcomes"]
    lines = after.to_dict()["evidence"]
    assert lines[:1] == before.to_dict()["evidence"]
    assert lines[1] == f"knowledge:{found[0].passage_id} docs/{DOCS}.md"
    assert bq.ranked([after, before])[0] is after, "an unsettled candidate keeps its place: it is still asked"
    assert _support.may_suppress([]) == (False, why)


def test_grant_for_another_source_version_does_not_suppress(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    one = support(rig)[0]
    store, state, answer = confirmed_rule(rig, one)
    grant = _support.designate(rig.local("fetch"), state, answer)
    assert grant.outcome == "A" and grant.source_version == one.byte_match.reread_sha256
    assert grant.repository == rig.binding.project and grant.passage_id == one.passage_id
    settled = support(rig)
    assert settled[0].authority == "normative" and settled[0].grant_id == grant.id
    assert _support.may_suppress(settled) == (True, grant.id)
    done = bq.with_support(candidate(), [s.model_dump() for s in settled], settles="A", by=f"normative grant {grant.id}")
    assert done.settled == [{"outcome": "A", "by": f"the normative source says so (normative grant {grant.id})"}]
    assert bq.ranked([done, candidate()])[0].settled == [], "a settled candidate is asked after an unsettled one"
    # another scenario: the grant does not reach it
    other = candidate(args=("  ",)).to_dict()
    assert all(s.authority == "none" for s in support(rig, other))
    # another version of the source: the file changes, a refresh publishes it, the grant no longer applies
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + f"\nThe {DOCS} convention was revised.\n", encoding="utf-8")
    engine.refresh(rig.local("refresh"), clock=lambda: NOW)
    later = support(rig)
    assert later and all(s.authority == "none" for s in later)
    assert _support.may_suppress(later)[0] is False
    # and another repository never sees it
    elsewhere = make(tmp_path, monkeypatch, name="elsewhere")
    elsewhere.add()
    assert all(s.authority == "none" for s in support(elsewhere))


def test_conflicting_passages_block_suppression_and_are_both_cited(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch, build=False)
    folder = rig.project / "rules"
    folder.mkdir()
    (folder / "old.md").write_text("The parse_limit convention: an empty value must return 0 for every caller in the "
                                   "billing path.\n", encoding="utf-8")
    (folder / "new.md").write_text("The parse_limit convention: an empty value must not return 0 for every caller in "
                                   "the billing path.\n", encoding="utf-8")
    rig.add(str(folder))
    record = bq.caller_candidates("Follow the parse_limit convention for an empty value.", [{
        "requirement_id": "R1", "function": "pkg.limits.parse_limit", "args": [""],
        "outcomes": {"A": {"returns": 0}, "B": {"raises": "ValueError"}},
        "cited": "Follow the parse_limit convention", "evidence": ["limits.py: no branch for an empty value"]}])[0]
    found = _support.support_for(rig.local(), record.to_dict(), clock=lambda: NOW)
    assert {s.path for s in found} == {"old.md", "new.md"}
    assert all(len(s.conflicts) == 1 for s in found) and found[0].conflicts[0] == found[1].passage_id
    one = next(s for s in found if s.path == "old.md")
    offered = bq.with_support(record, [s.model_dump() for s in found], rule=_support.designation_rule(one))
    case, rule = offered.questions("0" * 64)
    store = qs.TaskQuestions("b" * 64)
    store.ask(case, rule, offered)
    answer = Answer(question_sha256=rule.sha256, option="A", answered_by="person:Ada", answered_at=qs.now())
    grant = _support.designate(rig.local("fetch"), store.answer(answer), answer)
    again = _support.support_for(rig.local(), record.to_dict(), clock=lambda: NOW)
    normative = next(s for s in again if s.grant_id == grant.id)
    assert normative.conflicts, "the grant does not remove the disagreement"
    allowed, why = _support.may_suppress(again)
    assert allowed is False and "conflict" in why
    cited = bq.with_support(record, [s.model_dump() for s in again]).to_dict()["evidence"]
    assert sum(line.startswith("knowledge:") for line in cited) == 2


def test_support_never_becomes_an_issuer_receipt():
    tree = ast.parse((Path(g6.knowledge.__file__).parent / "_support.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
        n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(name.startswith(("g6.evidence", "g6.core.ledger", "g6.core.evidence", "g6.action_logic"))
                   for name in imported), sorted(imported)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"issue", "certificate", "Certificate", "receipt", "support_receipt"}


def test_designation_spends_the_one_question_budget(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    one = support(rig)[0]
    store, state, answer = confirmed_rule(rig, one)
    assert state.left == 0 and len(state.asked) == 1, "the designation was the task's one question"
    assert _support.designate(rig.local("fetch"), state, answer).id.startswith("ng-")
    # a second designation on the same task needs a second question, which the budget does not have
    another = candidate(args=("x",))
    offered = bq.with_support(another, [one.model_dump()], rule=_support.designation_rule(one))
    case, rule = offered.questions("0" * 64)
    with pytest.raises(qs.BudgetSpent):
        store.ask(case, rule, offered)
    # no argument makes a grant: an answer the store does not hold, a case-only answer, a skipped rule
    forged = Answer(question_sha256=rule.sha256, option="A", answered_by="person:Ada", answered_at=qs.now())
    with pytest.raises(Refused) as refused:
        _support.designate(rig.local("fetch"), state, forged)
    assert refused.value.code == _support.NOT_DESIGNATED
    _, case_state, case_answer = confirmed_rule(rig, one, identity="c" * 64, binds="case")
    with pytest.raises(Refused):
        _support.designate(rig.local("fetch"), case_state, case_answer)
    _, skipped_state, skipped = confirmed_rule(rig, one, identity="d" * 64, option="neither")
    with pytest.raises(Refused):
        _support.designate(rig.local("fetch"), skipped_state, skipped)
    assert len(ProjectStore(rig.binding).normative_grants()) == 1
    # a rule the person wrote themselves is not a designation
    plain = candidate(rule="An empty value returns 0.")
    case, rule = plain.questions("0" * 64)
    own = qs.TaskQuestions("e" * 64)
    own.ask(case, rule, plain)
    said = Answer(question_sha256=rule.sha256, option="A", answered_by="person:Ada", answered_at=qs.now())
    with pytest.raises(Refused):
        _support.designate(rig.local("fetch"), own.answer(said), said)


def test_support_respects_scope_revocation_and_disclosure(tmp_path, monkeypatch):
    from g6.knowledge._scope import ScopeDenied, access

    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    record = candidate().to_dict()
    one = support(rig)[0]
    store, state, answer = confirmed_rule(rig, one)
    grant = _support.designate(rig.local("fetch"), state, answer)
    # disclosure: a provider the source is not shared with gets no support, so no evidence line and no settlement
    cloud = access(rig.binding, "search", "anthropic:claude_code")
    assert _support.support_for(cloud, record, clock=lambda: NOW) == []
    assert _support.support_for(access(rig.binding, "search", "cloud:unknown"), record, clock=lambda: NOW) == []
    # narrowing never widens
    assert _support.support_for(rig.local(sources=["src-0000000000000000"]), record, clock=lambda: NOW) == []
    # another project
    other = make(tmp_path, monkeypatch, name="other", build=False)
    assert _support.support_for(other.local(), record, clock=lambda: NOW) == []
    with pytest.raises(ScopeDenied):
        _support.support_for(access(None, "search", "local"), record)
    # revocation: the passage is gone and the grant with it
    assert _support.may_suppress(support(rig))[0] is True
    engine.revoke(rig.local("revoke"), source)
    assert support(rig) == []
    assert ProjectStore(rig.binding).invalidated("normative_grant", grant.id)[0].why == "revoked"
    again = rig.add()
    fresh = support(rig)
    assert fresh and fresh[0].registration_id == again["source"] and all(s.authority == "none" for s in fresh)
