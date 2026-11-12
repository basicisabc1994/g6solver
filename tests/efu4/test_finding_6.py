"""A final pass cannot also claim that execution did not complete."""
from tests.harness.efu2_cases import FAILED, PASSED, assess_runs, report


def test_explicit_noncompletion_cannot_authorize_checked(monkeypatch):
    patch = report(PASSED, completed=False)
    got = assess_runs(monkeypatch, report(FAILED), patch)
    assert got["requirements"][0]["checked"] is False
