# SPDX-License-Identifier: Apache-2.0
"""Audit 011, VEN-002: a repository's tests never run in a plain ``python -m pytest`` subprocess.

The archive's LocalExecutor ran model-written tests with no network guard. The operator port has no runner, but three
plain runners survived in ``g6.workforce`` (``verify.pytest_check``, ``verify.baseline_failures`` and
``truth.run_test``, the default of ``Truth.check``). Every run now goes through ``truth.run_isolated`` / ``run_suite``
or ``repo_tests``, which install the network guard before any of the repository's code is imported; that guard's own
survival test is ``test_truth_network_guard.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import g6
from g6.workforce import truth, verify

_PLAIN_PYTEST = re.compile(r"""["']-m["']\s*,\s*["']pytest["']""")


def test_ven002_no_plain_pytest_subprocess_anywhere_in_the_library() -> None:
    src = Path(g6.__file__).parent
    found = [f"{p.relative_to(src)}:{n}" for p in sorted(src.rglob("*.py"))
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if _PLAIN_PYTEST.search(line)]
    assert found == []


def test_ven002_the_unguarded_runners_are_gone() -> None:
    assert not hasattr(verify, "pytest_check") and not hasattr(verify, "baseline_failures")
    assert not hasattr(truth, "run_test")


def test_ven002_truth_check_defaults_to_the_isolated_runner(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(truth, "run_isolated", lambda nodeid, repo: calls.append((nodeid, repo)) or True)
    claims = {"fast": {"phrases": ["fast"], "test": "tests/test_speed.py::test_fast"}}
    assert truth.Truth("seed", tmp_path, claims).check("fast") is True
    assert calls == [("tests/test_speed.py::test_fast", tmp_path)]
