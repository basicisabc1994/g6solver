"""Captured test output cannot override the producer's complete body failure."""
from g6.harness import patch_review as review
from tests.harness.efu2_cases import FAILED, NODE, PASSED, assess_runs, report


def test_printed_collection_error_does_not_erase_a_real_base_body_failure(monkeypatch):
    assert assess_runs(monkeypatch, report(FAILED), report(PASSED))["requirements"][0]["checked"] is True
    base = report(FAILED, output=(
        "FAILED test_calc.py::test_add\n"
        "---------------- Captured stdout call ----------------\n"
        "ERROR collecting test_calc.py\n"
    ))
    assert review.observation(review._verdicts([NODE], base)[NODE]) == "failed"
    got = assess_runs(monkeypatch, base, report(PASSED))
    assert got["requirements"][0]["checked"] is True, (
        "captured stdout was reparsed as a collection error and erased a body failure"
    )
