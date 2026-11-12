"""A source-change disposition must not coexist with positive evidence."""
from tests.harness.efu2_cases import FAILED, PASSED, assess_runs, report


def test_source_change_annotation_cannot_authorize_checked(monkeypatch):
    patch = report(PASSED, changed=["calc.py"])
    got = assess_runs(monkeypatch, report(FAILED), patch)
    assert got["requirements"][0]["checked"] is False
