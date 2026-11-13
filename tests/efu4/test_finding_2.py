"""A run explicitly marked degraded cannot establish positive evidence."""
from tests.harness.efu2_cases import FAILED, PASSED, assess_runs, report


def test_degraded_run_cannot_authorize_checked(monkeypatch):
    patch = report(PASSED, degraded=True)
    got = assess_runs(monkeypatch, report(FAILED), patch)
    assert got["requirements"][0]["checked"] is False
