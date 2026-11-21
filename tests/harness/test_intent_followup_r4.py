# SPDX-License-Identifier: Apache-2.0
"""Repair 4 of claude/wire-intent-fu (Codex review 4): a critic's stated verdict is read by one escape-aware scan
that never depends on the decoder succeeding, so anything that might be a stated verdict G6 cannot read makes the
reply unreadable; one gate at check scheduling drops any check, whoever made it, asserting an outcome the person
rejected for that typed call. FakeLLM and synthetic inputs only."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.harness import candidate_standard as cs
from g6.harness import completion_loop as loop
from g6.harness import verification_search as vs
from g6.harness.confirmed_expectation import ExpectationStandard, Question, confirm

TOKEN = "review-intfu4-fixture"
DEEP = "[" * 6000 + "0" + "]" * 6000
PROTOCOL = {"verdict": "defect", "rule": "r", "why": "actual regression", "test": "", "error": "", "read": TOKEN}


@pytest.fixture(autouse=True)
def online_reader(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


def _report(said):
    with semantic.request(llm=None):
        return loop.critic_report(said, marker=TOKEN)


# --- A: anything that might be a stated verdict G6 cannot read ---------------------------------------------------------

@pytest.mark.parametrize("obj", [
    '{"x":1,"x":2,}',
    '{"bad":oops,"x":1,"x":2}',
    '{"x":1,"x":2,"bad":]}',
    '{"\\u0078":1,"x":2}',
    '{"verdict":"defect","why":"a"]}',
    '{"verdict":"defect","nested":' + DEEP + "}",
    '{"nested":' + DEEP + ',"\\u0076erdict":"defect"}',
    '{"verdict":"defect",}',
    '[{"verdict":"defect"',
    'the "verdict" is in prose',
    '{note "verdict" here}',
    '{"why": "x"',
], ids=["repeat_trailing_comma", "repeat_invalid_value", "repeat_stray_closer", "repeat_escaped", "verdict_mismatched",
        "verdict_deep", "verdict_escaped_deep", "verdict_trailing_comma", "verdict_unclosed_in_array",
        "verdict_in_prose", "verdict_in_prose_braces", "never_closed"])
def test_a_verdict_g6_cannot_read_makes_the_reply_unreadable(obj):
    got = _report(f"READ: {TOKEN}\n{obj}\nVERDICT: none\n")
    assert got["verdict"] == "unreadable", got


def test_disagreeing_stated_verdicts_are_unreadable():
    said = f"READ: {TOKEN}\n{json.dumps(PROTOCOL)}\nVERDICT: none\n"
    assert _report(said)["verdict"] == "unreadable"


def test_a_verdict_inside_a_code_block_never_counts():
    said = (f"READ: {TOKEN}\nVERDICT: defect\nWHY: actual regression\n"
            '```python\nexpected = {"verdict": "none", "x": 1, "x": 2\n```\n'
            '    {"verdict": "none"\n')
    assert _report(said)["verdict"] == "defect"


# --- controls -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prose", ["the set {1, 2}", "it returns {'a': 1}", '{"a": ' + DEEP + "}", "a dict {}",
                                   'a list [1, 2) and {x]', '{"why": "the \\"verdict\\" key"}'])
def test_control_prose_and_objects_naming_no_verdict_stay_prose(prose):
    assert _report(f"READ: {TOKEN}\nVERDICT: defect\nWHY: {prose}\n")["verdict"] == "defect"


@pytest.mark.parametrize("layout", ["alone", "mid_line", "json_fence_in_quote"])
def test_control_a_stated_protocol_object_is_final(layout):
    obj = json.dumps(PROTOCOL)
    said = {"alone": f"{obj}\n", "mid_line": f"Result: {obj} (end)\n",
            "json_fence_in_quote": f"> ```json\n> {obj}\n> ```\n"}[layout]
    got = _report(said)
    assert (got["verdict"], got["why"]) == ("defect", "actual regression"), got


# --- B: one gate at check scheduling --------------------------------------------------------------------------------------

def _rejected(option="A"):
    """The person chose ``option`` for add(2, 3): A returns 5, B returns -1; the other is rejected."""
    shown = Question(requirement_id="R1", function="calc.add", args=[2, 3],
                     outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this one case: this input, this "
                     "outcome", cited="Repair addition.", baseline_sha256="b" * 64)
    standard = ExpectationStandard().with_expectation(
        confirm(shown, option, answered_by="person:Fixture", answered_at="2026-11-19T00:00:00Z"))
    return {"rejected": cs.rejections(standard)}


def _instance(template, bindings):
    return SimpleNamespace(id="I1", requirement_id="R1", template=template, bindings=bindings)


def _check(source, test=""):
    return vs.ProposedCheck(id="P1", requirement_id="R1", intent="preserve", file="tests/test_calc.py",
                            source=source, test=test)


def _dropped(one, option="A"):
    kept, skipped = cs.gate(_rejected(option), [one], "proposed check", "caller")
    assert len(kept) + len(skipped) == 1 and all(s["reason"] == cs.REJECTED for s in skipped)
    return not kept


def test_rejections_list_every_unchosen_outcome():
    assert _rejected("A")["rejected"] == [{"function": "calc.add", "case": {"args": [2, 3], "kwargs": {},
                                                                              "returns": -1}}]


@pytest.mark.parametrize("cases, dropped", [
    ([{"args": [2, 3], "returns": -1}], True),
    ([{"args": [1, 1], "returns": 2}, {"args": [2, 3], "returns": -1.0}], True),   # == as the assertion compares
    ([{"args": [2, 3], "returns": 5}], False),
    ([{"args": [2.0, 3], "returns": -1}], False),   # another typed call
])
def test_example_instances_asserting_a_rejected_outcome_are_dropped(cases, dropped):
    assert _dropped(_instance("example.v1", {"function": "calc.add", "cases": cases})) is dropped


@pytest.mark.parametrize("calls, dropped", [([{"args": [2, 3]}], True), ([{"args": [4, 3]}], False)])
def test_differential_calls_of_a_rejected_typed_call_are_dropped(calls, dropped):
    bindings = {"function": "calc.add", "mode": "outputs", "calls": calls}
    assert _dropped(_instance("differential.v1", bindings)) is dropped


HEAD = "import pytest\nfrom calc import add\n"


@pytest.mark.parametrize("body, test, dropped", [
    ("def test_x():\n    assert add(2, 3) == -1\n", "test_x", True),
    ("def test_x():\n    with pytest.raises(ValueError):\n        add(2, 3)\n", "test_x", True),
    ("def test_x():\n    assert add(2, 3) == 5\n", "test_x", False),
    ("def test_x():\n    assert add(2, 3) == 5\n\ndef test_y():\n    assert add(2, 3) == -1\n", "test_x", False),
    ("def test_x():\n    assert add(2, 3) == 5\n\ndef test_y():\n    assert add(2, 3) == -1\n", "", True),
    ("assert add(2, 3) == -1\n\ndef test_x():\n    pass\n", "test_x", True),   # module code runs for every test
    ("class TestAdd:\n    def test_x(self):\n        assert add(2, 3) == -1\n", "TestAdd::test_x", True),
    ("def test_x():\n    import calc\n    assert calc.add(2, 3) == -1\n", "test_x", True),
    ("def test_x():\n    assert add(2, 4) == -1\n", "test_x", False),
], ids=["asserted", "called_unread", "chosen", "other_test", "whole_file", "module_code", "class", "local_import",
        "other_call"])
def test_proposed_checks_asserting_a_rejected_outcome_are_dropped(body, test, dropped):
    assert _dropped(_check(HEAD + body, test)) is dropped


def test_a_review_never_runs_a_caller_check_asserting_a_rejected_outcome(tmp_path, monkeypatch):
    from tests.harness.test_confirmed_enforcement import compiled, review, standard, stated

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    root = _git_repo(tmp_path / "repo")
    stated(monkeypatch)
    given = compiled(monkeypatch, "passed", "failed", "passed")
    caller = {"id": "C1", "template": "example.v1", "requirement_id": "R1", "intent": "change",
              "bindings": {"function": "calc.add", "cases": [{"args": [2, 3], "returns": -1}]}}
    got = review(root, confirmed=standard(), template_instances=[caller])
    about = [one for one in _records(got["assessment"]) if one.get("check") == "C1"]
    assert about and all(one["result"] == "skipped" and one["reason"] == cs.REJECTED for one in about), about
    assert len(given) == 3 and not any("C1" in one["node"] for one in given), given   # only the confirmed check ran


def _records(value):
    if isinstance(value, dict):
        yield value
        value = list(value.values())
    if isinstance(value, list):
        for one in value:
            yield from _records(one)


def _git_repo(root):
    import subprocess

    from tests.harness.test_confirmed_enforcement import BAD, GOOD, TEST

    root.mkdir()
    (root / "calc.py").write_text(BAD)
    (root / "test_calc.py").write_text(TEST)
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c",
                                                "commit.gpgsign=false", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "calc.py").write_text(GOOD)
    return root