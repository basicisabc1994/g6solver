# SPDX-License-Identifier: Apache-2.0
"""Premises need visible text and share one Unicode comparison rule."""
from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from g6.research.echa import EvidenceRecord, Ledger, StandardRef
from g6.research.echa.records import premise_text

T = runpy.run_path(str(Path(__file__).with_name("test_premise_text.py")))
H = T["H"]
S = runpy.run_path(str(Path(__file__).with_name("test_premise_subjects.py")))
ROLE_IDS = {"check": "e1", "check_control": "ctl", "calibration": "cal",
            "calibration_control": "cal-ctl", "scope_mapping": "scope", "scope_control": "scope-ctl"}
INVISIBLE = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\x00", "\x1b", "\u200b\x00 \u2028\ufeff")


@pytest.mark.parametrize("blank", INVISIBLE)
def test_format_and_control_characters_supply_no_premise(blank):
    assert premise_text(blank) == ""
    got = T["general"](blank, blank)
    assert got["result"] == "inconclusive"
    assert got["effective_breadth"] == "covered_cases"


@pytest.mark.parametrize("field", ["population", "sample_unit"])
@pytest.mark.parametrize("blank", INVISIBLE)
def test_invisible_calibration_population_and_units_cannot_identify(field, blank):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    H["calibrated"](led, calibration_over={field: blank})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5)
    assert got["identification"] == "not_identified"
    assert got["result"] == "inconclusive"


def test_removed_formats_and_controls_compare_like_visible_text():
    got = T["general"]("lem\u200bma\x00 L", "lemma\ufeff L",
                       mapping_scope="all\u200c\tinputs", claim_scope="all inputs")
    assert got["result"] == "supported"
    assert got["effective_breadth"] == "general"


@pytest.mark.parametrize("field", ["scope", "population", "sample_unit"])
@pytest.mark.parametrize("blank", INVISIBLE)
def test_an_invisible_claim_premise_cannot_support_even_a_scoped_claim(field, blank):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    led.add_evidence(H["control"]())
    led.add_evidence(H["ev"]("e1"))
    got = H["verdict"](led, **{field: blank})
    assert got["result"] == "inconclusive"
    assert "claim_text_premise_missing" in got["reasons"]


@pytest.mark.parametrize("role", ROLE_IDS)
@pytest.mark.parametrize("blank", INVISIBLE)
def test_no_receipt_role_can_use_invisible_scope(role, blank):
    led = Ledger()
    hashes = {}
    for entry in S["receipts"]().entries():
        body = entry["body"]
        if entry["kind"] == "standard":
            led.add_standard(StandardRef.from_dict(body))
        elif entry["kind"] == "vault":
            led.add_vault(body["vault_id"], body["digest"], files=body["files"], roots=body["roots"])
        else:
            assert entry["kind"] == "evidence"
            original = EvidenceRecord.from_dict(body)
            rec = replace(original, scope=blank) if original.evidence_id == ROLE_IDS[role] else original
            rec = replace(rec, applies_to_sha256=hashes.get(original.applies_to_sha256, original.applies_to_sha256))
            hashes[original.sha256] = rec.sha256
            led.add_evidence(rec)
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                       breadth="general", scope="all inputs")
    assert got["result"] == "inconclusive"
    assert led.verify() == ()


def test_calibration_and_estimand_use_the_visible_comparison_form():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    H["calibrated"](led, calibration_over={"population": "pilot\u200b task t1", "sample_unit": "one\x00 task"})
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_\ufeffcorrectness", target=0.95)
    assert got["identification"] == "identified"
    assert got["result"] == "inconclusive"
    assert "corrected_lower_bound_does_not_exceed_target" in got["reasons"]


@pytest.mark.parametrize("left,right", [("Lemma L", "lemma L"), ("lemma \uff2c", "lemma L")])
def test_case_and_compatibility_characters_remain_distinct(left, right):
    got = T["general"](left, right)
    assert got["result"] == "inconclusive"
    assert got["effective_breadth"] == "covered_cases"


@pytest.mark.parametrize("spacing", ["\t", "\n", "\r", "\x85", "\u00a0", "\u2028", "\u2029"])
def test_whitespace_controls_separate_words_instead_of_joining_them(spacing):
    got = T["general"]("lemma" + spacing + "L", "lemma L")
    assert got["result"] == "supported"


def test_normalization_stays_NFC_after_removing_invisible_characters():
    assert premise_text("e\u200b\u0301") == "\u00e9"
    assert premise_text(premise_text("e\u200b\u0301")) == "\u00e9"
