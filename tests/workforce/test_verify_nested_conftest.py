# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010's test-integrity gap: ``verify.test_integrity`` compared pytest's config files at the
repository root only, so a ``tests/sub/conftest.py`` that deselects every test was not seen and the verdict passed.

``_archive_test_integrity`` is that check (the root-level loop, nothing else); ``test_null_integrity_gap`` puts it back in
memory (monkeypatching only) and shows the regression test fails on it. The survival controls show an honest change
still passes and the root-level findings keep their words.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from g6.workforce import verify

CALC = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
DESELECT = "def pytest_collection_modifyitems(config, items):\n    items[:] = []\n"
BASE = {"calc.py": CALC, "tests/sub/test_calc.py": TEST}


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _archive_test_integrity(original, workdir):
    """The check before this batch: the test files' rules, then pytest config files at the root only."""
    original, workdir = Path(original), Path(workdir)
    findings = verify.weakened_tests(verify._tests_in(original), verify._tests_in(workdir))
    for name in verify._PYTEST_CONFIG:
        a, b = original / name, workdir / name
        if b.exists() and (not a.exists() or a.read_bytes() != b.read_bytes()):
            findings.append(f"{name}: pytest config added or changed")
    return verify.Verdict(not findings, "VERIFY_TEST_INTEGRITY", "; ".join(findings)[:300], {"findings": findings})


@pytest.mark.parametrize(("name", "text", "said"), [
    ("tests/sub/conftest.py", DESELECT, "pytest_collection_modifyitems"),
    ("tests/sub/pytest.ini", "[pytest]\naddopts = --deselect tests/sub/test_calc.py::test_add\n",
     "pytest configuration added"),
])
def test_integrity_gap_a_nested_conftest_or_config_that_deselects_tests_is_flagged(tmp_path, name, text, said):
    before = _tree(tmp_path / "before", BASE)
    after = _tree(tmp_path / "after", {**BASE, name: text})
    verdict = verify.test_integrity(before, after)
    assert verdict.ok is False and verdict.kind == "VERIFY_TEST_INTEGRITY"
    nested = [f for f in verdict.detail["findings"] if f.startswith(f"{name}: ")]
    assert nested and any(said in f for f in nested), verdict.detail["findings"]


def test_null_integrity_gap(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "test_integrity", _archive_test_integrity)
    with pytest.raises(AssertionError):
        test_integrity_gap_a_nested_conftest_or_config_that_deselects_tests_is_flagged(
            tmp_path, "tests/sub/conftest.py", DESELECT, "pytest_collection_modifyitems")


def test_an_honest_change_is_not_flagged(tmp_path):
    """Survival control: new code, a new nested test and an unchanged nested conftest.py give no finding."""
    fixture = "import pytest\n\n\n@pytest.fixture\ndef two():\n    return 2\n"
    before = _tree(tmp_path / "before", {**BASE, "tests/sub/conftest.py": fixture})
    after = _tree(tmp_path / "after", {
        **BASE, "tests/sub/conftest.py": fixture, "calc.py": CALC + "\n\ndef mul(a, b):\n    return a * b\n",
        "tests/sub/test_mul.py": "from calc import mul\n\n\ndef test_mul(two):\n    assert mul(two, 3) == 6\n"})
    verdict = verify.test_integrity(before, after)
    assert verdict.ok is True and verdict.detail["findings"] == []


def test_root_config_findings_keep_their_words(tmp_path):
    """Survival control: a root conftest.py change reads exactly as before, once (no second finding for the same file)."""
    before = _tree(tmp_path / "before", BASE)
    after = _tree(tmp_path / "after", {**BASE, "conftest.py": DESELECT})
    assert verify.test_integrity(before, after).detail["findings"] == ["conftest.py: pytest config added or changed"]


def test_a_conftest_under_out_is_not_read(tmp_path):
    """``_tests_in``'s rule kept: nothing under ``out/`` (a run's own output) is a test or its configuration."""
    before = _tree(tmp_path / "before", BASE)
    after = _tree(tmp_path / "after", {**BASE, "out/tests/conftest.py": DESELECT})
    assert verify.test_integrity(before, after).ok is True
