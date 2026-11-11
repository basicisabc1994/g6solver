# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The confirmed answer's two records and every comparison the module makes, listed from the code (G6 0.3, slice 2,
repair 3; the same-mistake rule: three classes met in this module before, removed at the root).

- A question is a record G6 made and an answer names only what the person chose; each is held as its canonical JSON
  bytes, and the inventory lists every field of each with its type. The answer holds none of the question's fields:
  it names the question by its digest alone.
- Every comparison in the module is listed from its AST with what it compares; none is Python equality on a record.
- Planted in turn: an answer whose question digest is another question's; each question field carried in an answer;
  unreadable or malformed bytes; a stop and restart between each two records written.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import pickle
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core.ledger import canonical, digest
from g6.harness import confirmed_expectation as ce
from g6.harness.confirmed_expectation import (
    ANSWER_FIELDS,
    QUESTION_FIELDS,
    Answer,
    ConfirmedExpectation,
    ExpectationStandard,
    Question,
    Unconfirmed,
    confirm,
)
from g6.harness.verification_templates import MAX_DEPTH

BASE = "a" * 64
AT = "2026-11-07T12:00:00Z"
MODULE = Path(ce.__file__)


def question(**over) -> Question:
    base = {"requirement_id": "R1", "function": "shop.total", "args": [1],
            "outcomes": {"A": {"returns": 1}, "B": {"returns": 2}}, "scope": "the displayed input", "cited": "task",
            "baseline_sha256": BASE}
    return Question(**{**base, **over})


def answer(shown: Question, option: str = "A", **over) -> Answer:
    base = {"question_sha256": shown.sha256, "option": option, "answered_by": "person:owner", "answered_at": AT}
    return Answer(**{**base, **over})


def written(value) -> bytes:
    return canonical(value).encode("ascii")


# --- the inventory: every field of the two records, with its type -------------------------------------------------

#: The design's question: the scenario, the options shown, the citation and the artefact's digest.
QUESTION = {"requirement_id": str, "function": str, "args": list, "kwargs": dict, "outcomes": dict, "options": list,
            "scope": str, "cited": str, "baseline_sha256": str, "intent": str}
#: The design's answer: the question's digest, the option chosen, any free text, the answerer and the time.
ANSWER = {"question_sha256": str, "option": str, "text": str, "answered_by": str, "answered_at": str}


def test_every_field_of_the_question_and_answer_records_with_its_type():
    assert dict(QUESTION_FIELDS) == QUESTION and dict(ANSWER_FIELDS) == ANSWER
    shown = question(kwargs={"rate": 1})
    chosen = answer(shown, text="as the task says")
    for record, fields in ((shown, QUESTION), (chosen, ANSWER)):
        held = json.loads(record.record)
        assert {name: type(value) for name, value in held.items()} == fields
        assert record.record == written(held) and record.sha256 == hashlib.sha256(record.record).hexdigest()
        assert record.sha256 == digest(held) and set(vars(record)) == {"_record"}   # its bytes, and nothing else
    # the fields a caller gives: the question's own (its options are G6's), the answer's, and the pair's two records
    assert [f.name for f in dataclasses.fields(Question)] == ["requirement_id", "function", "args", "outcomes", "scope",
                                                              "cited", "baseline_sha256", "kwargs", "intent"]
    assert {f.name for f in dataclasses.fields(Question)} == set(QUESTION) - {"options"}
    assert {f.name for f in dataclasses.fields(Answer)} == set(ANSWER)
    assert [f.name for f in dataclasses.fields(ConfirmedExpectation)] == ["question", "answer"]
    # the answer repeats nothing the question holds, and names it by its digest alone
    assert set(ANSWER).isdisjoint(QUESTION) and chosen.question_sha256 == shown.sha256
    got = ConfirmedExpectation(shown, chosen)
    assert set(vars(got)) == {"_question", "_answer"}
    assert (got._question, got._answer) == (shown.record, chosen.record)
    standard = ExpectationStandard([got])
    assert set(vars(standard)) == {"expectations", "revision", "previous", "replaced"}
    assert type(standard.expectations) is tuple and type(standard.replaced) is tuple


def test_what_an_answer_means_is_derived_from_the_question_s_bytes_every_time():
    shown = question(kwargs={"rate": 1})
    got = confirm(shown, "B", answered_by="person:owner", answered_at=AT)
    first, again = got.instance, got.instance
    assert first is not again and first.bindings is not again.bindings
    assert written(first.bindings) == written({"function": "shop.total",
                                               "cases": [{"args": [1], "kwargs": {"rate": 1}, "returns": 2}]})
    assert (got.artefact_sha256, got.requirement_id, got.scope) == (BASE, "R1", "the displayed input")
    assert got.provenance == ce.PERSON_CONFIRMED == first.provenance
    assert got.expectation_id == first.id and got.check_sha256 == ce.instance_digest(first)


# --- the inventory: every comparison in the module, with what it compares ------------------------------------------

#: Every comparison the module makes, by the function it is in and its source, with what it compares. ``type``: an
#: exact type, never a value; ``bytes``: canonical JSON bytes; ``digest``: a SHA-256 of bytes, or an id made from
#: digests; ``label``: an option's label, or other text, among the module's own; ``keys``: a record's field names;
#: ``count`` and ``time`` (the answer's time as written) and ``none`` (the absent default) are what they say.
COMPARISONS = {
    ("normative", "provenance in NORMATIVE"): "label",
    ("_parsed", "type(data) is not bytes"): "type",
    ("_parsed", "again != data"): "bytes",
    ("_parsed", "type(record) is not dict"): "type",
    ("_parsed", "set(record) != set(fields)"): "keys",
    ("_typed", "type(value) is not kind"): "type",
    ("_shown", "len(outcomes) < 2"): "count",
    ("_shown", "label in NOT_CONFIRMING"): "label",
    ("_shown", "type(outcome) is not dict"): "type",
    ("_shown", "'raises' in outcome"): "keys",
    ("_Field.__get__", "obj is None"): "none",
    ("_Record.__eq__", "type(other) is not type(self)"): "type",
    ("_Record.__eq__", "self.record == other.record"): "bytes",
    ("Question.__init__", "kwargs is None"): "none",
    ("Question.from_record", "made.record != data"): "bytes",
    ("Answer.__init__", "type(answered_by) is not str"): "type",
    ("Answer.__init__", "kept != answered_at"): "time",
    ("ConfirmedExpectation.__init__", "type(question) is not Question"): "type",
    ("ConfirmedExpectation.__init__", "type(answer) is not Answer"): "type",
    ("ConfirmedExpectation.__init__", "chosen.question_sha256 != shown.sha256"): "digest",
    ("ConfirmedExpectation.__init__", "chosen.option in NOT_CONFIRMING"): "label",
    ("ConfirmedExpectation.__init__", "chosen.option not in shown.outcomes"): "label",
    ("ConfirmedExpectation.for_question", "type(question) is not Question"): "type",
    ("ConfirmedExpectation.for_question", "question.record != self._question"): "bytes",
    ("confirm", "type(question) is not Question"): "type",
    ("confirm", "answered_at is None"): "none",
    ("_copied", "type(one) is not ConfirmedExpectation"): "type",
    ("ExpectationStandard.__post_init__", "type(self.revision) is not int"): "type",
    ("ExpectationStandard.__post_init__", "self.revision < 1"): "count",
    ("ExpectationStandard.__post_init__", "type(self.previous) is not str"): "type",
    ("ExpectationStandard.__post_init__", "type(one) is not str"): "type",
    ("ExpectationStandard.__post_init__", "scenario in seen"): "bytes",
    ("ExpectationStandard.__post_init__", "before != meaning"): "bytes",
    ("ExpectationStandard.__eq__", "type(other) is not ExpectationStandard"): "type",
    ("ExpectationStandard.__eq__", "self.sha256 == other.sha256"): "digest",
    ("ExpectationStandard.contradicted_by", "type(new) is not ConfirmedExpectation"): "type",
    ("ExpectationStandard.contradicted_by", "_scenario(one) == scenario"): "bytes",
    ("ExpectationStandard.contradicted_by", "_meaning(one) != meaning"): "bytes",
    ("latest_confirmed", "answer_scenarios is None"): "none",
    ("ExpectationStandard.with_expectation", "len(held) == len(self.expectations)"): "count",
    ("ExpectationStandard.with_expectation", "one.answer_sha256 == old.answer_sha256"): "digest",
    ("ExpectationStandard.superseding", "one.expectation_id not in contradicted"): "digest",
}
#: What a side of a comparison may never be: a record, or a Python value read from one (an argument list, an outcome,
#: a compiled check's bindings), named as the module names them; a record made in place; or a value written inline.
RECORD_NAMES = frozenset({"question", "answer", "shown", "chosen", "one", "new", "other", "self", "made", "held",
                          "instance", "bindings", "expectations", "outcomes", "outcome", "args", "kwargs", "case",
                          "cases", "standard", "superseding"})
RECORD_MAKERS = frozenset({"Question", "Answer", "ConfirmedExpectation", "ExpectationStandard", "from_record",
                           "_compiled", "model_dump", "to_dict", "loads"})


def comparisons(tree: ast.AST) -> list[tuple[str, str, ast.Compare]]:
    """(the function it is in, one operator and its two sides as source, the node) for every comparison in a module;
    a chained comparison gives one entry per operator."""
    found = []

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*where, child.name])
                continue
            if isinstance(child, ast.Compare):
                sides = [child.left, *child.comparators]
                for index, op in enumerate(child.ops):
                    single = ast.Compare(left=sides[index], ops=[op], comparators=[sides[index + 1]])
                    found.append((".".join(where) or "<module>", ast.unparse(single), single))
            visit(child, where)

    visit(tree, [])
    return found


def record_shaped(node: ast.AST) -> bool:
    """Whether one side of a comparison is a record or a Python value of one, by its shape in the code."""
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Name):
        return node.id in RECORD_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in RECORD_NAMES
    if isinstance(node, ast.Subscript):
        return record_shaped(node.value)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name in RECORD_MAKERS
    return False


def equality_on_records(node: ast.Compare) -> bool:
    """Whether a comparison is Python equality on a record: ``==`` or ``!=`` with a record on either side, or ``in``
    looking for a record (which compares it by ``==``)."""
    op = node.ops[0]
    if isinstance(op, (ast.Eq, ast.NotEq)):
        return record_shaped(node.left) or record_shaped(node.comparators[0])
    if isinstance(op, (ast.In, ast.NotIn)):
        return record_shaped(node.left)
    return False


def test_every_comparison_in_the_module_is_listed_and_none_is_equality_on_records():
    found = comparisons(ast.parse(MODULE.read_text(encoding="utf-8")))
    listed = {(where, text) for where, text, _ in found}
    assert sorted(listed - set(COMPARISONS)) == [], "a comparison the inventory does not list: say what it compares"
    assert sorted(set(COMPARISONS) - listed) == [], "a listed comparison the module no longer makes: remove it"
    assert set(COMPARISONS.values()) <= {"type", "bytes", "digest", "label", "keys", "count", "time", "none"}
    assert [(where, text) for where, text, node in found if equality_on_records(node)] == []
    # the records' own equality is by bytes, never the dataclass's field-by-field equality
    for kind in (Question, Answer, ConfirmedExpectation, ExpectationStandard):
        assert kind.__dataclass_params__.eq is False
    assert Question.__eq__ is Answer.__eq__ is ConfirmedExpectation.__eq__ is ce._Record.__eq__


def test_the_scan_finds_equality_on_records_in_each_form_met_before():
    # survival control: review 3's finding 2 (Python equality on a compiled check), and the other shapes
    planted = ast.parse(
        "class ConfirmedExpectation:\n"
        "    def for_question(self, question):\n"
        "        if self.instance.bindings != {'function': question.function, 'cases': []}:\n"
        "            pass\n"
        "        return question == self.question\n"
        "def contradicted(standard, new):\n"
        "    return [one for one in standard.expectations if one.instance == new.instance or new in standard.held]\n"
        "def same(a, b):\n"
        "    return Question.from_record(a) == b or a.args == [1]\n")
    flagged = {(where, text) for where, text, node in comparisons(planted) if equality_on_records(node)}
    assert flagged == {
        ("ConfirmedExpectation.for_question",
         "self.instance.bindings != {'function': question.function, 'cases': []}"),
        ("ConfirmedExpectation.for_question", "question == self.question"),
        ("contradicted", "one.instance == new.instance"), ("contradicted", "new in standard.held"),
        ("same", "Question.from_record(a) == b"), ("same", "a.args == [1]")}


def test_each_record_is_equal_by_its_bytes_alone():
    shown, flag = question(args=[1]), question(args=[True])
    assert shown.to_dict()["args"] == flag.to_dict()["args"]   # Python equality takes the two for one value
    assert shown != flag and hash(shown) != hash(flag) and Question.from_record(shown.record) == shown
    assert answer(shown) != answer(flag) and answer(shown) == Answer.from_record(answer(shown).record)
    assert ConfirmedExpectation(shown, answer(shown)) != ConfirmedExpectation(flag, answer(flag))
    one, other = (ExpectationStandard([ConfirmedExpectation(q, answer(q))]) for q in (shown, flag))
    assert one != other and one == ExpectationStandard(one.expectations) and one.sha256 != other.sha256


# --- planted: an answer whose question digest is another question's --------------------------------------------------


def test_an_answer_whose_question_digest_is_for_another_question_is_refused():
    shown, other = question(), question(cited="another line of the task")
    with pytest.raises(ValueError, match="another question"):
        ConfirmedExpectation(shown, answer(other))
    got = ConfirmedExpectation(shown, answer(shown))
    assert got.for_question(shown) is got   # the positive control
    with pytest.raises(ValueError, match="another question"):
        got.for_question(other)
    with pytest.raises(ValueError, match="another question"):
        replace(got, answer=answer(other))
    with pytest.raises(ValueError, match="another question"):   # the same values, another type: another question
        got.for_question(question(args=[True]))


# --- planted: each question field carried in an answer --------------------------------------------------------------

#: Each field of a question, and what G6 derives from the pair, planted in an answer.
CARRIED = {"requirement_id": "R9", "function": "shop.other", "args": [100], "kwargs": {"rate": 2},
           "outcomes": {"A": {"returns": 3}}, "options": ["skip"], "scope": "every input", "cited": "elsewhere",
           "baseline_sha256": "b" * 64, "intent": "preserve", "artefact_sha256": "b" * 64, "returns": 3,
           "raises": "ValueError", "effect": {"argument": 0, "equals": 1}, "outcome": {"returns": 3},
           "instance": {"template": "example.v1"}, "bindings": {"function": "shop.other"}, "check_sha256": "c" * 64,
           "expectation_id": "d" * 64, "provenance": "model_hypothesis", "options_sha256": "e" * 64}


def test_every_question_field_is_planted():
    assert set(QUESTION) <= set(CARRIED)


@pytest.mark.parametrize("field", sorted(CARRIED))
def test_each_question_field_carried_in_an_answer_is_refused_by_name(field):
    shown = question()
    value = CARRIED[field]
    with pytest.raises(ValueError, match=field):
        answer(shown, **{field: value})
    with pytest.raises(ValueError, match=field):
        replace(answer(shown), **{field: value})
    got = ConfirmedExpectation(shown, answer(shown))
    with pytest.raises(ValueError, match=field):
        replace(got, **{field: value})
    with pytest.raises(ValueError, match=field):
        ConfirmedExpectation(shown, answer(shown), **{field: value})
    with pytest.raises(ValueError, match="holds exactly"):   # in an answer's stored bytes too
        Answer.from_record(written({**json.loads(answer(shown).record), field: value}))


# --- unreadable or malformed state ------------------------------------------------------------------------------------


def _question_bytes() -> bytes:
    return question().record


MALFORMED_QUESTIONS = {
    "text, not bytes": lambda: _question_bytes().decode("ascii"),
    "not JSON": lambda: b"{",
    "not UTF-8": lambda: b'{"args":"\xff"}',
    "spaces": lambda: json.dumps(json.loads(_question_bytes()), sort_keys=True).encode("ascii"),
    "keys out of order": lambda: json.dumps(dict(reversed(json.loads(_question_bytes()).items())),
                                            separators=(",", ":")).encode("ascii"),
    "a duplicate key": lambda: _question_bytes()[:-1] + b',"scope":"every input"}',
    "NaN": lambda: _question_bytes().replace(b'"args":[1]', b'"args":[NaN]'),
    "a list, not an object": lambda: b"[]",
    "a field missing": lambda: written({k: v for k, v in json.loads(_question_bytes()).items() if k != "cited"}),
    "a field added": lambda: written({**json.loads(_question_bytes()), "artefact_sha256": "b" * 64}),
    "args an object": lambda: written({**json.loads(_question_bytes()), "args": {"0": 1}}),
    "options not G6's": lambda: written({**json.loads(_question_bytes()), "options": ["skip"]}),
    "one outcome": lambda: written({**json.loads(_question_bytes()), "outcomes": {"A": {"returns": 1}}}),
    "no such function name": lambda: written({**json.loads(_question_bytes()), "function": "not a name"}),
    "another intent": lambda: written({**json.loads(_question_bytes()), "intent": "replace"}),
    "a short baseline": lambda: written({**json.loads(_question_bytes()), "baseline_sha256": "a" * 63}),
}


@pytest.mark.parametrize("damage", sorted(MALFORMED_QUESTIONS))
def test_an_unreadable_or_malformed_question_record_is_refused(damage):
    with pytest.raises(ValueError):
        Question.from_record(MALFORMED_QUESTIONS[damage]())
    assert Question.from_record(_question_bytes()) == question()   # the positive control


def _answer_bytes() -> bytes:
    return answer(question()).record


MALFORMED_ANSWERS = {
    "text, not bytes": lambda: _answer_bytes().decode("ascii"),
    "not JSON": lambda: b'{"option"',
    "spaces": lambda: json.dumps(json.loads(_answer_bytes()), sort_keys=True).encode("ascii"),
    "a field missing": lambda: written({k: v for k, v in json.loads(_answer_bytes()).items() if k != "answered_at"}),
    "the option a number": lambda: written({**json.loads(_answer_bytes()), "option": 0}),
    "a short question digest": lambda: written({**json.loads(_answer_bytes()), "question_sha256": "a" * 63}),
    "a local time": lambda: written({**json.loads(_answer_bytes()), "answered_at": "2026-11-07T12:00:00+01:00"}),
    "a time with no seconds": lambda: written({**json.loads(_answer_bytes()), "answered_at": "2026-11-07T12:00Z"}),
    "no such day": lambda: written({**json.loads(_answer_bytes()), "answered_at": "2026-02-30T12:00:00Z"}),
}


@pytest.mark.parametrize("damage", sorted(MALFORMED_ANSWERS))
def test_an_unreadable_or_malformed_answer_record_is_refused(damage):
    with pytest.raises(ValueError):
        Answer.from_record(MALFORMED_ANSWERS[damage]())
    assert Answer.from_record(_answer_bytes()) == answer(question())   # the positive control


@pytest.mark.parametrize("who", ["agent", "model:claude", "person:", "", "g6"])
def test_an_answer_record_from_anything_but_a_person_is_unconfirmed(who):
    with pytest.raises(Unconfirmed):
        Answer.from_record(written({**json.loads(_answer_bytes()), "answered_by": who}))
    with pytest.raises(Unconfirmed):
        answer(question(), answered_by=who)


@pytest.mark.parametrize("option", [*ce.NOT_CONFIRMING, "C", ""])
def test_an_answer_record_that_chooses_no_outcome_shown_confirms_nothing(option):
    shown = question()
    chosen = answer(shown, option)   # a record of what the person chose ...
    assert chosen.option == option
    with pytest.raises(Unconfirmed):   # ... which confirms nothing
        ConfirmedExpectation(shown, chosen)


# --- a change between the check and the write; a stop and restart between each two records written ---------------


def test_a_caller_s_value_changed_between_the_check_and_the_write_is_checked_as_written(monkeypatch):
    args, real = [1], ce.literal_problem
    too_deep = cursor = []
    for _ in range(MAX_DEPTH + 2):
        cursor.append([])
        cursor = cursor[0]

    def changes_after_the_check(value):
        problem = real(value)
        if value is args:   # the caller changes its list right after G6 checked it, before G6 writes it
            args.append(too_deep)
        return problem

    monkeypatch.setattr(ce, "literal_problem", changes_after_the_check)
    with pytest.raises(ValueError, match="nested at most"):
        question(args=args)
    monkeypatch.setattr(ce, "literal_problem", real)
    assert question(args=[1]).args == [1]   # the positive control


def test_a_restart_between_each_two_records_written_restores_the_same_records():
    shown = question()
    asked = shown.record                                     # written: the question
    restarted = Question.from_record(asked)
    chosen = confirm(restarted, "A", answered_by="person:owner", answered_at=AT).answer
    answered = chosen.record                                 # written: the answer
    got = ConfirmedExpectation(Question.from_record(asked), Answer.from_record(answered))
    assert got == ConfirmedExpectation(shown, chosen) and got.answer_sha256 == hashlib.sha256(answered).hexdigest()
    standard = ExpectationStandard().with_expectation(got)   # written: the standard
    for record in (shown, chosen, got, standard):
        for again in (copy.deepcopy(record), pickle.loads(pickle.dumps(record))):
            assert again == record and again is not record
    restored = pickle.loads(pickle.dumps(standard))
    assert (restored.sha256, restored.predicates, restored.revision) == (standard.sha256, standard.predicates, 2)
    assert ConfirmedExpectation.from_record(got.record) == got
    assert restored.expectations[0].check_sha256 == got.check_sha256


def test_a_record_changed_while_it_was_stored_is_refused_or_read_as_what_its_bytes_say():
    shown = question()
    got = ConfirmedExpectation(shown, answer(shown))
    stored = pickle.dumps(got)
    assert stored.count(b'"option":"A"') == 1 and stored.count(b'"cited":"task"') == 1
    with pytest.raises(Unconfirmed):   # the answer's option changed to one the question did not show
        pickle.loads(stored.replace(b'"option":"A"', b'"option":"C"'))
    with pytest.raises(ValueError, match="another question"):   # the question changed under its answer
        pickle.loads(stored.replace(b'"cited":"task"', b'"cited":"tusk"'))
    other = pickle.loads(stored.replace(b'"option":"A"', b'"option":"B"'))   # another answer, read as such
    assert other.option == "B" and other.expectation_id != got.expectation_id
    standard = ExpectationStandard([got])
    with pytest.raises(Unconfirmed):   # a standard restored from changed bytes is refused with them
        pickle.loads(pickle.dumps(standard).replace(b'"option":"A"', b'"option":"C"'))
