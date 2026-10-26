# SPDX-License-Identifier: Apache-2.0
import pytest
from g6.intent_engine import IntentContract


def test_typed_checks_report_matching_spans_and_word_range():
    # Removing checks.py's _evaluate dispatch makes this fail.
    from g6.intent_engine import Check, check_output
    contract = IntentContract(goal="Write a report", success_criteria=["title", "brief"],
                              success_checks={"title": Check(kind="contains", value="Report"),
                                              "brief": Check(kind="word_count", min_words=2, max_words=4)},
                              failure_criteria=["leak"], failure_checks={"leak": Check(kind="contains", value="secret")})
    rows = check_output(contract, "Report ready")
    assert [r["status"] for r in rows] == ["pass", "pass", "pass"]
    assert rows[0]["span"] == (0, 6)
    assert check_output(contract, "Report secret")[2]["status"] == "fail"


def test_plain_language_prohibition_is_unchecked_not_literal_absence():
    # Removing check_output's unmapped-criterion branch makes this fail (ALIGN_SPECS-01, D1).
    from g6.intent_engine import check_output
    contract = IntentContract(goal="Write a report", success_criteria=["Do not expose credentials"])
    assert check_output(contract, "password: example")[0]["status"] == "unchecked"


def test_checks_depend_on_output_and_accept_no_solver_assignment():
    # Removing checks.py's literal search makes this fail (ALIGN_SPECS-02).
    from g6.intent_engine import Check, check_output
    contract = IntentContract(goal="Write a report", success_criteria=["contains ref"],
                              success_checks={"contains ref": Check(kind="regex", value=r"ref-[0-9]{3}")})
    assert check_output(contract, "ref-123")[0]["status"] == "pass"
    assert check_output(contract, "missing")[0]["status"] == "fail"
    with pytest.raises(ValueError):
        Check(kind="z3", value="true")


@pytest.mark.parametrize("pattern", ["(a+)+$", "a*a*a*$", r"(a)\1", "a|aa", "a{1,60}"])
def test_regex_outside_bounded_subset_is_unchecked(pattern):
    # Removing checks.py's bounded-pattern validation makes this fail.
    from g6.intent_engine import Check, check_output
    contract = IntentContract(goal="Write a report", success_criteria=["pattern"],
                              success_checks={"pattern": Check(kind="regex", value=pattern)})
    assert check_output(contract, "a" * 30 + "!")[0]["status"] == "unchecked"


def test_failed_check_dependency_is_unchecked(monkeypatch):
    # Removing check_output's exception-to-unchecked branch makes this fail.
    from g6.intent_engine import Check, check_output
    import g6.intent_engine.checks as checks
    contract = IntentContract(goal="Write a report", success_criteria=["title"],
                              success_checks={"title": Check(kind="contains", value="Report")})
    monkeypatch.setattr(checks, "_evaluate", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    assert check_output(contract, "Report")[0]["status"] == "unchecked"


def test_a_failure_criterion_is_recorded_as_what_the_output_avoids():
    # Removing harness_checks's "avoids: " prefix makes this fail (a passed "leak" check read "leak: pass").
    from g6.intent_engine import harness_checks
    contract = IntentContract(goal="Write a report", success_criteria=["title"], failure_criteria=["leak"])
    checks = harness_checks(contract)
    assert [c.predicate for c in checks] == ["title", "avoids: leak"]
    assert [c.check_id for c in checks] == ["success-0", "failure-0"]


def test_checks_feed_value_report_without_a_second_ledger():
    # Removing checks.py's unchecked-to-unknown conversion makes this fail.
    from g6.intent_engine import harness_checks
    from g6.harness import Memory, Standard, run, value_report
    contract = IntentContract(goal="Summarise the refund policy", success_criteria=["protect private details"])
    memory = Memory(":memory:")
    try:
        result = run(Standard(contract.goal, harness_checks(contract)), lambda c: "policy", memory=memory)
        report = value_report(result["run_id"], memory=memory)
        assert report["unmeasured"] == ["success-0"]
        assert report["highest"] is None
    finally:
        memory.close()
