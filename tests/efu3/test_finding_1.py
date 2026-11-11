"""Contradictory report summaries cannot authorize issuer evidence."""
import pytest

from tests.harness.efu2_cases import FAILED, PASSED, assess_runs, report


@pytest.mark.parametrize("where,verdict", [
    ("patch", "failed"),
    ("base", "no_tests_collected"),
])
def test_summary_contradicting_complete_phases_is_unknown(monkeypatch, where, verdict):
    assert assess_runs(monkeypatch, report(FAILED), report(PASSED))["requirements"][0]["checked"]
    base = report(FAILED, verdict=verdict) if where == "base" else report(FAILED)
    patch = report(PASSED, verdict=verdict) if where == "patch" else report(PASSED)
    got = assess_runs(monkeypatch, base, patch)
    assert got["requirements"][0]["checked"] is False, (
        f"{where} report with contradictory {verdict!r} summary authorized checked"
    )
