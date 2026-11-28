# SPDX-License-Identifier: Apache-2.0
"""Absent text never grants breadth; equivalent Unicode and spacing compare alike."""
from pathlib import Path
import runpy

import pytest

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))
BLANKS = ("", " ", "\t\r\n", "\u00a0\u2003\u2028")


def general(check_text, mapping_text, *, mapping_scope="all inputs", claim_scope="all inputs"):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    check = H["calibrated"](led, check_over={"oracle_category": "FORMAL_CHECK",
                                             "checked_statement": check_text, "scope_mapping_ref": "scope"})
    led.add_evidence(H["ev"]("scope", purpose="scope_mapping", source_ref=H["_binding"]("c"),
                              independence_group="assessor", negative_control_ref="e1-cal-ctl",
                              applies_to_sha256=check.sha256, population=check.population,
                              sample_unit=check.sample_unit, scope=mapping_scope, checked_statement=mapping_text))
    return H["verdict"](led, subject="sha256:" + "b" * 64, breadth="general", scope=claim_scope)


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("location", ["check", "mapping", "both"])
def test_blank_checked_statement_never_supports_general_breadth(blank, location):
    got = general(blank if location != "mapping" else "lemma L", blank if location != "check" else "lemma L")
    assert got["result"] == "inconclusive"
    assert got["effective_breadth"] == "covered_cases"


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", ["scope", "population", "sample_unit", "estimand"])
def test_blank_claim_text_cannot_support_any_claim(blank, field):
    # These required declarations are rejected at construction, before any evidence can support them.
    with pytest.raises(ValueError, match=field):
        H["claim"](**{field: blank})


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("field", ["scope", "population", "sample_unit"])
@pytest.mark.parametrize("purpose", ["calibration", "scope_mapping"])
def test_blank_premise_text_is_absent(blank, field, purpose):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    if field == "scope":
        with pytest.raises(ValueError, match="scope"):
            H["ev"]("premise", purpose=purpose, scope=blank)
        return
    check = H["calibrated"](led, calibration_over={field: blank} if purpose == "calibration" else None,
                             check_over={"oracle_category": "FORMAL_CHECK", "checked_statement": "lemma L",
                                         "scope_mapping_ref": "scope"})
    if purpose == "scope_mapping":
        over = {"population": check.population, "sample_unit": check.sample_unit, field: blank}
        led.add_evidence(H["ev"]("scope", purpose=purpose, source_ref=H["_binding"]("c"),
                                  independence_group="assessor", negative_control_ref="e1-cal-ctl",
                                  applies_to_sha256=check.sha256, scope="all inputs", checked_statement="lemma L", **over))
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                       breadth="general", scope="all inputs")
    assert got["result"] == "inconclusive"
    if purpose == "calibration":
        assert got["identification"] == "not_identified"
    else:
        assert got["effective_breadth"] == "covered_cases"


def test_equivalent_checked_and_scope_statements_use_NFC_and_collapsed_whitespace():
    got = general("  lemm\u0065\u0301\tL\n", "lemm\u00e9 L", mapping_scope="all\tinputs", claim_scope="all inputs")
    assert got["result"] == "supported"
    assert got["effective_breadth"] == "general"


def test_population_and_sample_unit_compare_with_the_same_text_rule():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    H["calibrated"](led, calibration_over={"population": " pilot\ttask t1\n", "sample_unit": "one\u00a0task"})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5)
    assert got["identification"] == "identified"
    assert got["result"] == "supported"


def test_estimand_uses_the_same_text_rule_before_its_decision():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    H["calibrated"](led)
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand=" \ttrue_correctness\n", target=0.95)
    assert got["identification"] == "identified"
    assert got["result"] == "inconclusive"
    assert "corrected_lower_bound_does_not_exceed_target" in got["reasons"]
