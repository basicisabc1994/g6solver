"""Review 2 findings, brought in unchanged as regression tests."""
from g6.harness import patch_review as review
from tests.harness.efu2_cases import CHECK, FAILED, NODE, PASSED, assess_runs, report


def test_false_valued_nontext_root_is_unknown(monkeypatch):
    assert assess_runs(monkeypatch, report(FAILED), report(PASSED))["requirements"][0]["checked"]
    got = assess_runs(monkeypatch, report(FAILED, rootdir=False), report(PASSED))
    assert got["requirements"][0]["checked"] is False, "a boolean rootdir killed a negative control"



def test_a_collected_stated_check_without_phases_is_kept_unknown(monkeypatch):
    interrupted = report({}, verdict="crashed", test_outcomes={}, collected_ids=[NODE])
    observed = review._observed([CHECK], interrupted)
    got = assess_runs(monkeypatch, report(FAILED), report(PASSED),
                      observed=[{"run": ["derived"], "verdicts": observed}])
    assert got["requirements"][0]["checked"] is False, "the collected-only observation vanished before issuance"



def test_reordered_phase_report_cannot_kill_a_control(monkeypatch):
    assert assess_runs(monkeypatch, report(FAILED), report(PASSED))["requirements"][0]["checked"]
    reversed_phases = dict(reversed(list(FAILED.items())))
    got = assess_runs(monkeypatch, report(reversed_phases), report(PASSED))
    assert got["requirements"][0]["checked"] is False, "a teardown-before-call-before-setup report killed a control"



def test_a_failure_also_listed_as_omitted_is_unknown(monkeypatch):
    base = report(FAILED, omitted_ids=[NODE])
    got = assess_runs(monkeypatch, base, report(PASSED))
    assert got["requirements"][0]["checked"] is False, "a test listed as omitted still killed its control"



def test_a_body_failure_absent_from_the_collected_listing_is_unknown(monkeypatch):
    base = report(FAILED, collected_ids=[])
    got = assess_runs(monkeypatch, base, report(PASSED))
    assert got["requirements"][0]["checked"] is False, "an uncollected test still killed its control"

