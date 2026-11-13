"""A replay's original-code baseline needs positive loading authority."""
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import patch_review as review
from g6.workforce import repo_tests as runner
from tests.harness.efu2_cases import FAILED, NODE, PASSED, report


@pytest.mark.parametrize("loading", [
    {"loaded_complete": False, "loaded_outside": {}},
    {"loaded_complete": True, "loaded_outside": {"calc": "elsewhere/calc.py"}},
])
def test_unestablished_baseline_cannot_establish_a_regression(monkeypatch, loading):
    change = SimpleNamespace(repo=Path.cwd(), before={"test_calc.py": "def test_add(): pass\n"},
                             renamed={}, base_commit="0" * 40, sources=("calc.py",))
    baseline = report(PASSED, degraded=False, not_passed=0, deselected=0,
                      python_sources=["calc.py"], **loading)
    patch = report(FAILED, loaded_complete=True, loaded_outside={}, python_sources=["calc.py"])
    assert runner.read_report(baseline).valid
    assert runner.loaded_from(change.repo, baseline) != {}
    reports = iter([baseline, patch])
    # Fake only execution and copy boundaries; run_before and the review's
    # regression classifier are the production implementations.
    monkeypatch.setattr(runner, "repository_copy", lambda *a, **kw: nullcontext(Path.cwd()))
    monkeypatch.setattr(runner, "_base_revision", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(runner, "interpreter", lambda *a: "recorded-python")
    monkeypatch.setattr(runner, "_run", lambda *a, **kw: next(reports))
    replays = runner.run_before(change)
    monkeypatch.setattr(review, "_execute", lambda *a, **kw: replays)
    reasons = []
    review._on_base(Path.cwd(), change, ["test_calc.py"], patch, time.monotonic() + 30,
                    [], [], lambda stage, code, detail, **kw: reasons.append((stage, code)),
                    stated=[NODE], observed=[])
    assert ("tier_2", "derived_test_regressed") not in reasons
