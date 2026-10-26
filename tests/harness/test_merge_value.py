# SPDX-License-Identifier: Apache-2.0
"""Weak links use executed failures and the pipeline's two-level criticality vocabulary."""
from g6.harness import Check, Memory, Standard, run, value_report
from g6.reliability_pipeline.task_contract import SuccessCriterion


def test_value_completion_01_d1_reads_failed_checks_in_criticality_order():
    # Removal makes this fail: `failed.sort(key=lambda item: not item["critical"])` in value_report.
    memory = Memory(":memory:")
    try:
        checks = (Check("has a title", lambda o: "title" in o), Check("has correct total", lambda o: "total=42" in o),
                  Check("has a source", lambda o: "source" in o))
        standard = Standard("Summarise the refund policy", checks, criteria=(
            SuccessCriterion("has a title"), SuccessCriterion("has correct total", critical=True),
            SuccessCriterion("has a source", critical=True)))
        reply = run(standard, lambda c: "Declared complete, all keys present", memory=memory)
        report = value_report(reply["run_id"], memory=memory)
        assert report["highest"]["check"] == "has-correct-total"
        assert report["next"]["check"] == "has-a-source"
        assert report["highest"]["critical"] is True
        assert report["completion_state"] == "qualified-draft"
        # Mutating a returned claim cannot edit the stored receipt.
        reply["results"]["has-correct-total"] = "pass"
        assert value_report(reply["run_id"], memory=memory)["highest"]["check"] == "has-correct-total"
    finally:
        memory.close()


def test_unknown_check_is_a_gap_not_a_pass_or_an_invented_failure():
    # Removal makes this fail: `unmeasured=[k for k, v in results.items() if v == "unknown"]` in value_report.
    memory = Memory(":memory:")
    try:
        reply = run(Standard("Summarise the refund policy", (Check("has a source", lambda o: "unknown"),)),
                    lambda c: "policy", memory=memory)
        report = value_report(reply["run_id"], memory=memory)
        assert report["highest"] is None and report["next"] is None
        assert report["unmeasured"] == ["has-a-source"]
    finally:
        memory.close()


def test_criticality_must_map_to_the_standard_checks():
    # Removal makes this fail: `if standard.criteria and ...` in _run.
    memory = Memory(":memory:")
    try:
        standard = Standard("Summarise the refund policy", (Check("has a title", lambda o: False),),
                            criteria=(SuccessCriterion("unrelated declaration", critical=True),))
        reply = run(standard, lambda c: "title", memory=memory)
        assert reply["warning_card"]["code"] == "G6_E_HARNESS_CRITERIA"
    finally:
        memory.close()
