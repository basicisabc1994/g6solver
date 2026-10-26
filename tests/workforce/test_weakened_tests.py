# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""weakened_tests: the test-integrity check over test SOURCES, before and after a change (E10a, founder specs FE §10 and
TS §18, §43, 2026-10-05: "agent modifies tests to accommodate bad implementation").

The venture executor's ``test_integrity`` compares two directories; ``verify_task`` has only the caller's test file, as
text, before and after. Both read the same rules from one function:
- a test may gain asserts, never lose or change one;
- a removed test, an assert under a catch-all handler, an early return, or a new skip, xfail or mock is weakening;
- a deleted or unparsable test file is weakening.
"""
from __future__ import annotations

from pathlib import Path

from g6.workforce import verify

BEFORE = (
    "from solution import add\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n"
    "def test_negative():\n    assert add(-1, -1) == -2\n"
)


def _findings(after: str) -> list[str]:
    return verify.weakened_tests({"test_solution.py": BEFORE}, {"test_solution.py": after})


def test_an_unchanged_or_strengthened_test_has_no_findings():
    assert _findings(BEFORE) == []
    assert _findings(BEFORE + "\ndef test_zero():\n    assert add(0, 0) == 0\n") == []
    assert _findings(BEFORE.replace("== 5\n", "== 5\n    assert add(3, 2) == 5\n")) == []


def test_a_removed_assert_is_weakening():
    found = _findings(BEFORE.replace("    assert add(-1, -1) == -2\n", "    add(-1, -1)\n"))
    assert found == ["test_solution.py: test_negative: an assert was removed or changed"]


def test_a_rewritten_assert_is_weakening():
    found = _findings(BEFORE.replace("assert add(2, 3) == 5", "assert add(2, 3) is not None"))
    assert found == ["test_solution.py: test_add: an assert was removed or changed"]


def test_a_removed_test_is_weakening():
    found = _findings(BEFORE.split("\ndef test_negative")[0] + "\n")
    assert found == ["test_solution.py: test removed test_negative"]


def test_a_new_skip_or_a_catch_all_guard_is_weakening():
    skipped = _findings("import pytest\n" + BEFORE.replace("def test_negative", "@pytest.mark.skip\ndef test_negative"))
    assert "test_solution.py: added skip" in skipped
    guarded = _findings(BEFORE.replace(
        "    assert add(-1, -1) == -2\n",
        "    try:\n        assert add(-1, -1) == -2\n    except Exception:\n        pass\n"))
    assert guarded == ["test_solution.py: test_negative: an assert was removed or changed"]


def test_a_new_test_that_uses_a_fixture_like_monkeypatch_is_not_weakening():
    """Dogfood episode 2 (2026-10-05): five new tests taking ``monkeypatch`` read as "added monkeypatch" and blocked.

    A test that did not exist before cannot weaken one that did; its markers are not counted.
    """
    new_test = "\ndef test_env(monkeypatch):\n    monkeypatch.setenv('X', '1')\n    assert add(1, 1) == 2\n"
    assert _findings(BEFORE + new_test) == []


def test_an_existing_test_that_gains_monkeypatch_is_still_weakening():
    """Control: the same name inside an OLD test (patching the code under test) is the weakening the check exists for."""
    found = _findings(BEFORE.replace(
        "def test_add():\n    assert",
        "def test_add(monkeypatch):\n    monkeypatch.setattr('solution.add', lambda a, b: 5)\n    assert"))
    assert "test_solution.py: added monkeypatch" in found


def test_a_new_module_level_mock_is_still_weakening():
    """Control: a mock outside any new test (an import, a fixture) can reach the old tests, so it still counts."""
    fixture = "\n@pytest.fixture(autouse=True)\ndef _fake(monkeypatch):\n    monkeypatch.setattr('solution.add', lambda a, b: 5)\n"
    found = _findings("import pytest\nfrom unittest import mock\n" + BEFORE + fixture)
    assert "test_solution.py: added mock" in found and "test_solution.py: added monkeypatch" in found


def test_an_early_return_is_weakening():
    found = _findings(BEFORE.replace("def test_add():\n", "def test_add():\n    return\n"))
    assert "test_solution.py: test_add: early return added" in found


def test_a_return_inside_a_fake_defined_in_the_test_is_not_an_early_return():
    """Dogfood episode 4 (2026-10-05): a test double rewritten to answer a reset (``return`` inside a nested method)
    read as "early return added" on 7 tests. Only a return in the test's own body skips its asserts."""
    found = _findings(BEFORE.replace(
        "def test_add():\n",
        "def test_add():\n    class Fake:\n        def infer(self, x):\n            return x\n\n"
        "    def helper():\n        return 1\n\n"))
    assert found == []


def test_a_return_in_the_tests_own_body_is_still_an_early_return():
    """Control: the early return the rule exists for, even when the test also defines a nested helper."""
    found = _findings(BEFORE.replace(
        "def test_add():\n",
        "def test_add():\n    def helper():\n        return 1\n\n    if helper():\n        return\n"))
    assert "test_solution.py: test_add: early return added" in found


def test_a_deleted_or_unparsable_file_is_weakening():
    assert verify.weakened_tests({"test_solution.py": BEFORE}, {}) == ["test_solution.py: test file deleted"]
    assert verify.weakened_tests({"test_solution.py": BEFORE}, {"test_solution.py": "def test_add(:\n"})[0].startswith(
        "test_solution.py: unparsable")


def test_the_directory_check_reads_the_same_rules(tmp_path: Path):
    """Control: test_integrity (the venture executor's check over two directories) still finds the same weakening."""
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "test_solution.py").write_text(BEFORE, encoding="utf-8")
    (after / "test_solution.py").write_text(BEFORE.replace("assert add(2, 3) == 5", "assert True"), encoding="utf-8")
    verdict = verify.test_integrity(before, after)
    assert verdict.ok is False and verdict.kind == "VERIFY_TEST_INTEGRITY"
    assert verdict.detail["findings"] == ["test_solution.py: test_add: an assert was removed or changed"]
