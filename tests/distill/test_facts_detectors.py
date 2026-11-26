# SPDX-License-Identifier: Apache-2.0
from dataclasses import asdict

import pytest

from g6.distill.detectors import SEED_TEMPLATES, detect, run_template
from g6.distill.facts import extract_facts
from g6.safety_engine import weakening
from tests.safety_engine import test_weakening_se005 as fixtures
from tests.workforce.weakened_tests_corpus import CASES, as_sources


def test_facts_exact_fixture_and_diff():
    before = {"tests/test_a.py": "def test_a():\n    assert calc(2) == 4\n"}
    after = {"tests/test_a.py": "@pytest.mark.skip\ndef test_a():\n    assert calc(2) is not None\n"}
    facts = extract_facts(before, after, split="training", changed_functions=["calc"])
    old = facts["files"]["tests/test_a.py"]["before"]["test_a"]
    new = facts["files"]["tests/test_a.py"]["after"]["test_a"]
    assert old == dict(assertions={"assert": 1, "Eq": 1}, compared_literals=[2, 4], markers=[], mocks=[],
                       try_blocks=0, parametrize_counts=[], calls=["calc"], calls_into_changed=["calc"])
    assert new["assertions"] == {"assert": 1, "IsNot": 1}
    assert new["markers"] == ["pytest.mark.skip"]
    assert "-    assert calc(2) == 4" in facts["diff_hunks"]["tests/test_a.py"]
    assert facts["probe_outcomes"] == {}


def test_facts_syntax_errors_scopes_and_nested_helpers():
    source = ("class TestX:\n    @pytest.mark.parametrize('x', [1, 2])\n    def test_x(self, x):\n"
              "        def helper():\n            assert False\n"
              "        with patch('calc'):\n            try:\n                assert x == 1\n"
              "            except Exception:\n                pass\n")
    facts = extract_facts({}, {"tests/test_x.py": source, "tests/test_bad.py": "def broken("}, split="dev")
    test = facts["files"]["tests/test_x.py"]["after"]["TestX::test_x"]
    assert test["assertions"] == {"assert": 1, "Eq": 1}
    assert test["try_blocks"] == 1
    assert test["mocks"] == ["patch"]
    assert test["parametrize_counts"] == [2]
    assert "tests/test_bad.py:after" in facts["parse_errors"]


def test_held_out_refused_before_content_access():
    class Unreadable(dict):
        def items(self):
            raise AssertionError("content accessed")
        def __iter__(self):
            raise AssertionError("content accessed")
    for operation in (extract_facts, detect):
        with pytest.raises(ValueError, match="split"):
            operation(Unreadable(), Unreadable(), split="held_out")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_seed_matches_existing_labelled_corpus(case):
    before, after = as_sources(case)
    assert detect(before, after, split="dev") == weakening.detect_test_weakening(before, after)


@pytest.mark.parametrize("after", [a for _, a, _ in fixtures._SKIPS] + [a for _, a, _ in fixtures._WEAKER])
def test_seed_matches_own_weakening_fixtures(after):
    before, after = {fixtures.T: fixtures.BASE}, {fixtures.T: after}
    report = weakening.detect_test_weakening(before, after)
    assert asdict(detect(before, after, split="training")) == asdict(report)
    result = run_template("weakening.v1", before, after, split="training")
    assert result.verdict == ("concern" if report.weakened else "no_concern")
    assert result.report == report


def test_seeds_delegate_and_unimplemented_templates_abstain(monkeypatch):
    calls = []
    report = weakening.TestWeakeningReport(True, ("sentinel",), ("test_x.py",))
    def replacement(before, after):
        calls.append((before, after))
        return report
    monkeypatch.setattr(weakening, "detect_test_weakening", replacement)
    assert detect({}, {}, split="dev") is report
    assert len(calls) == 1
    assert SEED_TEMPLATES["weakening.v1"].authority == "A0"
    for name in ("discriminates_at_base.v1", "kill_set.v1"):
        assert run_template(name, {}, {}, split="dev").verdict == "abstain"


@pytest.mark.parametrize(("template", "after"), [
    ("assert_strength.v1", "def test_a():\n    assert True\n"),
    ("skip_gate.v1", "@pytest.mark.skip\ndef test_a():\n    assert calc() == 4\n"),
    ("swallow.v1", "def test_a():\n    try:\n        assert calc() == 4\n    except Exception:\n        pass\n"),
    ("self_reference.v1", "def test_a():\n    assert calc() == calc()\n"),
    ("mock_target.v1", "@patch('calc')\ndef test_a():\n    assert calc() == 4\n"),
])
def test_specialized_seed_instances_reuse_existing_detector(template, after):
    before = {"tests/test_a.py": "def test_a():\n    assert calc() == 4\n"}
    result = run_template(template, before, {"tests/test_a.py": after}, split="training")
    assert result.verdict == "concern"
    assert result.report == weakening.detect_test_weakening(before, {"tests/test_a.py": after})
