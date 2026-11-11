"""A supplied loading-completeness failure survives a partial report."""
from tests.harness.efu2_cases import FAILED, PASSED, assess_runs, report


def test_missing_module_listing_does_not_erase_explicit_incomplete_loading(monkeypatch):
    incomplete = report(PASSED, loaded_outside={}, loaded_complete=False)
    assert assess_runs(monkeypatch, report(FAILED), incomplete)["requirements"][0]["checked"] is False
    del incomplete["loaded_outside"]
    got = assess_runs(monkeypatch, report(FAILED), incomplete)
    assert got["requirements"][0]["checked"] is False, (
        "missing loaded_outside erased loaded_complete=False and authorized checked"
    )
