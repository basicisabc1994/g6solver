# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A labelled corpus for ``venture.weakened_tests`` (Gate W D-W7, founder 2026-10-10).

Each case is a test file as it was and as it is, with a label written from what a reviewer would say, never from what the
detector prints:

* ``weakened``: the change makes the tests prove less (a reviewer would object);
* ``benign``: the change does not (a reviewer would accept it).

``expect_alarm`` is the detector's measured behaviour on the case, pinned so that any change in it is a visible diff. A
weakened case with ``expect_alarm=False`` is a **blind spot** and is listed as one, never hidden. A benign case with
``expect_alarm=True`` is a **false alarm**. The detector stays partial evidence: it parses and compares assertion
syntax; it runs nothing, so a clean result is never a statement that the tests are as strong as before.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    label: str            # "weakened" | "benign"
    before: str
    after: str
    expect_alarm: bool
    note: str = ""


def _t(body: str, head: str = "import pytest\n\nfrom mod import f\n\n\n") -> str:
    return head + body


BASE = _t("def test_double():\n    assert f(2) == 4\n    assert f(0) == 0\n")

CASES: tuple[Case, ...] = (
    # -- weakened, and the detector says so --------------------------------------------------------------------------------
    Case("w-assert-removed", "ordinary assertion", "weakened", BASE,
         _t("def test_double():\n    assert f(2) == 4\n"), True),
    Case("w-assert-changed", "ordinary assertion", "weakened", BASE,
         _t("def test_double():\n    assert f(2) == 4\n    assert f(0) >= 0\n"), True),
    Case("w-assert-true", "ordinary assertion", "weakened", BASE,
         _t("def test_double():\n    assert True\n"), True),
    Case("w-expected-value-edited", "ordinary assertion", "weakened", BASE,
         _t("def test_double():\n    assert f(2) == 5\n    assert f(0) == 0\n"), True,
         "an edited expectation may be a correction: the detector cannot tell, it flags every change"),
    Case("w-test-deleted", "test deletion", "weakened", BASE + "\n\ndef test_other():\n    assert f(1) == 2\n", BASE, True),
    Case("w-test-renamed-out-of-collection", "test deletion", "weakened", BASE,
         _t("def double_check():\n    assert f(2) == 4\n    assert f(0) == 0\n"), True),
    Case("w-assert-in-catch-all", "swallowed failure", "weakened", BASE,
         _t("def test_double():\n    try:\n        assert f(2) == 4\n    except Exception:\n        pass\n    assert f(0) == 0\n"), True),
    Case("w-assert-in-nested-catch-all", "swallowed failure", "weakened",
         _t("def test_double():\n    try:\n        assert f(2) == 4\n    except ValueError:\n        pass\n"),
         _t("def test_double():\n    try:\n        try:\n            assert f(2) == 4\n        except BaseException:\n            pass\n    except ValueError:\n        pass\n"),
         True, "the nested-try shape fixed under D-W7"),
    Case("w-early-return", "swallowed failure", "weakened", BASE,
         _t("def test_double():\n    return\n    assert f(2) == 4\n    assert f(0) == 0\n"), True),
    Case("w-skip-added", "skip or expected failure", "weakened", BASE,
         _t("@pytest.mark.skip(reason='later')\ndef test_double():\n    assert f(2) == 4\n    assert f(0) == 0\n"), True),
    Case("w-xfail-added", "skip or expected failure", "weakened", BASE,
         _t("@pytest.mark.xfail\ndef test_double():\n    assert f(2) == 4\n    assert f(0) == 0\n"), True),
    Case("w-importorskip-added", "skip or expected failure", "weakened", BASE,
         BASE.replace("import pytest\n", "import pytest\n\npytest.importorskip('nothing_installed')\n"), True),
    Case("w-module-pytestmark-skip", "skip or expected failure", "weakened", BASE,
         BASE.replace("from mod import f\n", "from mod import f\n\npytestmark = pytest.mark.skip\n"), True),
    Case("w-mock-added-to-old-test", "mock", "weakened", BASE,
         _t("from unittest.mock import patch\n\n\ndef test_double():\n    with patch('mod.f', return_value=4):\n        assert f(2) == 4\n        assert f(0) == 4\n", head="import pytest\n\nfrom mod import f\n\n\n"),
         True, "the asserts changed as well; a mock added with the asserts untouched is the next case"),
    Case("w-mock-added-asserts-untouched", "mock", "weakened", BASE,
         _t("def test_double(monkeypatch):\n    monkeypatch.setattr('mod.f', lambda x: x * 2)\n    assert f(2) == 4\n    assert f(0) == 0\n"),
         True, "flagged as a new monkeypatch in a test that existed"),
    Case("w-class-method-assert-removed", "class-based test", "weakened",
         _t("class TestDouble:\n    def test_it(self):\n        assert f(2) == 4\n        assert f(0) == 0\n"),
         _t("class TestDouble:\n    def test_it(self):\n        assert f(2) == 4\n"), True),
    Case("w-file-deleted", "test deletion", "weakened", BASE, "", True,
         "the corpus compares sources per path; a deleted file is the path missing from the after map"),
    # -- weakened, and the detector cannot see it (blind spots, stated) ------------------------------------------------------
    Case("b-unittest-assertEqual-removed", "unittest style", "weakened",
         "import unittest\n\nfrom mod import f\n\n\nclass TestDouble(unittest.TestCase):\n    def test_it(self):\n        self.assertEqual(f(2), 4)\n        self.assertEqual(f(0), 0)\n",
         "import unittest\n\nfrom mod import f\n\n\nclass TestDouble(unittest.TestCase):\n    def test_it(self):\n        self.assertEqual(f(2), 4)\n",
         False, "BLIND SPOT: only the assert statement is counted, not unittest's assert methods"),
    Case("b-pytest-raises-removed", "expected exception", "weakened",
         _t("def test_bad():\n    with pytest.raises(ValueError):\n        f(-1)\n    assert f(1) == 2\n"),
         _t("def test_bad():\n    f(-1)\n    assert f(1) == 2\n"),
         False, "BLIND SPOT: `with pytest.raises` is not an assert; its removal is not seen"),
    Case("b-parametrize-cases-dropped", "parametrisation", "weakened",
         _t("@pytest.mark.parametrize('x,y', [(1, 2), (2, 4), (3, 6)])\ndef test_double(x, y):\n    assert f(x) == y\n"),
         _t("@pytest.mark.parametrize('x,y', [(1, 2)])\ndef test_double(x, y):\n    assert f(x) == y\n"),
         False, "BLIND SPOT: the assert is unchanged; the data it runs over shrank"),
    Case("b-fixture-made-trivial", "fixture", "weakened",
         _t("@pytest.fixture\ndef data():\n    return [1, 2, 3]\n\n\ndef test_all(data):\n    assert all(f(x) == 2 * x for x in data)\n"),
         _t("@pytest.fixture\ndef data():\n    return []\n\n\ndef test_all(data):\n    assert all(f(x) == 2 * x for x in data)\n"),
         False, "BLIND SPOT: the assert is unchanged and vacuously true over an empty fixture"),
    Case("b-assert-loosened-in-helper", "helper", "weakened",
         _t("def check(v):\n    assert v == 4\n\n\ndef test_double():\n    check(f(2))\n"),
         _t("def check(v):\n    assert v is not None\n\n\ndef test_double():\n    check(f(2))\n"),
         False, "BLIND SPOT: only functions named test* are compared, so a helper's asserts are not"),
    Case("b-conftest-autouse-patch", "fixture", "weakened", BASE, BASE, False,
         "BLIND SPOT, not in this API: conftest.py is not part of the source map `weakened_tests` receives; "
         "`test_integrity` (repository mode) flags a changed pytest config file separately"),
    # -- benign, and the detector stays quiet ------------------------------------------------------------------------------
    Case("n-new-test-added", "addition", "benign", BASE, BASE + "\n\ndef test_triple():\n    assert f(3) == 6\n", False),
    Case("n-new-assert-added", "addition", "benign", BASE,
         _t("def test_double():\n    assert f(2) == 4\n    assert f(0) == 0\n    assert f(-1) == -2\n"), False),
    Case("n-comment-and-blank-lines", "formatting", "benign", BASE, BASE.replace("def test_double():\n", "# doubles\n\n\ndef test_double():\n"), False),
    Case("n-docstring-added", "formatting", "benign", BASE,
         _t('def test_double():\n    """f doubles."""\n    assert f(2) == 4\n    assert f(0) == 0\n'), False),
    Case("n-unrelated-code-changed", "formatting", "benign", BASE, BASE.replace("from mod import f\n", "from mod import f\n\nTAG = 'x'\n"), False),
    Case("n-new-test-with-monkeypatch", "addition", "benign", BASE,
         BASE + "\n\ndef test_env(monkeypatch):\n    monkeypatch.setenv('A', '1')\n    assert f(1) == 2\n", False,
         "a test that did not exist cannot weaken one that did"),
    Case("n-narrow-handler-around-assert", "formatting", "benign",
         _t("def test_double():\n    assert f(2) == 4\n"),
         _t("def test_double():\n    try:\n        assert f(2) == 4\n    except KeyError:\n        raise\n"), False,
         "a narrow handler does not swallow an AssertionError, so the assert still counts"),
    Case("n-tests-reordered", "formatting", "benign", BASE + "\n\ndef test_b():\n    assert f(1) == 2\n",
         "import pytest\n\nfrom mod import f\n\n\ndef test_b():\n    assert f(1) == 2\n\n\ndef test_double():\n    assert f(2) == 4\n    assert f(0) == 0\n", False),
    # -- benign, and the detector raises an alarm anyway (false alarms, stated) -------------------------------------------
    Case("f-assert-operands-swapped", "equivalent rewrite", "benign", BASE,
         _t("def test_double():\n    assert 4 == f(2)\n    assert f(0) == 0\n"), True,
         "FALSE ALARM: the assert says the same thing, written the other way round"),
    Case("f-asserts-moved-into-helper", "refactor", "benign", BASE,
         _t("def check(a, b):\n    assert f(a) == b\n\n\ndef test_double():\n    check(2, 4)\n    check(0, 0)\n"), True,
         "FALSE ALARM: the same checks, moved; the detector sees the test lose its asserts"),
    Case("f-test-renamed-in-place", "refactor", "benign", BASE,
         _t("def test_doubling():\n    assert f(2) == 4\n    assert f(0) == 0\n"), True,
         "FALSE ALARM: a rename is reported as a removed test"),
    Case("n-message-added-to-assert", "equivalent rewrite", "benign", BASE,
         _t("def test_double():\n    assert f(2) == 4, 'two doubles to four'\n    assert f(0) == 0\n"), False,
         "an assert message is not part of the compared expression, so it is not flagged"),
)

#: Cases whose "file deleted" is expressed by a missing path (the API compares ``path -> text``).
FILE_DELETED_ID = "w-file-deleted"


def as_sources(case: Case) -> tuple[dict[str, str], dict[str, str]]:
    after = {} if case.id == FILE_DELETED_ID else {"test_x.py": case.after}
    return {"test_x.py": case.before}, after


def measure(detect) -> dict:
    """Run ``detect(before, after) -> list[str]`` over the corpus: counts, rates, the blind spots and the false alarms."""
    caught = missed = alarms = quiet = 0
    blind, false_alarm, mismatches = [], [], []
    for case in CASES:
        before, after = as_sources(case)
        alarmed = bool(detect(before, after))
        if alarmed != case.expect_alarm:
            mismatches.append(case.id)
        if case.label == "weakened":
            caught, missed = caught + alarmed, missed + (not alarmed)
            if not alarmed:
                blind.append(case.id)
        else:
            alarms, quiet = alarms + alarmed, quiet + (not alarmed)
            if alarmed:
                false_alarm.append(case.id)
    weakened, benign = caught + missed, alarms + quiet
    return {"weakened": weakened, "caught": caught, "missed": missed, "recall": round(caught / weakened, 3),
            "benign": benign, "false_alarms": alarms, "false_alarm_rate": round(alarms / benign, 3),
            "blind_spots": blind, "false_alarm_cases": false_alarm, "unexpected": mismatches}
