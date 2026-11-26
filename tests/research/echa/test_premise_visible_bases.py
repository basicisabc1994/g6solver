# SPDX-License-Identifier: Apache-2.0
"""A premise needs a visible base, even when invisible characters carry marks."""
from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from g6.research.echa import EvidenceRecord, Ledger, StandardRef, trace_claim
from g6.research.echa.records import premise_text

T = runpy.run_path(str(Path(__file__).with_name("test_premise_text.py")))
H = T["H"]
S = runpy.run_path(str(Path(__file__).with_name("test_premise_subjects.py")))

# Unicode 15.1 DerivedCoreProperties.txt, Default_Ignorable_Code_Point.
# This independent input fixture includes reserved code points, not just assigned
# format characters. Expected absence is literal, never derived with production code.
IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
    (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
BLANK_RANGES = ((0x2800, 0x2800), (0x3000, 0x3000))
DECORATIONS = (
    pytest.param("", "", id="alone"),
    pytest.param("\u0301", "\u20dd", id="combining-marks"),
    pytest.param("\ufe0f", "\U000e0100", id="variation-selectors"),
    pytest.param("\u0301\ufe0f", "\u20dd\U000e0100", id="marks-and-selectors"),
)
INVISIBLE = (
    "\u0301", "\u20dd", "\u093e", "\u034f", "\ufe0f", "\U000e0100",
    "\u115f\u1160", "\u3164", "\uffa0", "\u2800", "\u2065", "\ufff0", "\U000e0fff",
)
VISIBLE = (
    pytest.param("caf\u00e9", id="Latin"),
    pytest.param("\u4e2d\u6587", id="CJK"),
    pytest.param("\u0627\u0644\u0639\u0631\u0628\u064a\u0629", id="Arabic"),
    pytest.param("\u0939\u093f\u0928\u094d\u0926\u0940", id="Devanagari"),
    pytest.param("\u2764\ufe0f", id="emoji-with-selector"),
    pytest.param("42", id="number"),
    pytest.param("?!", id="punctuation"),
)
ROLE_IDS = {"check": "e1", "check_control": "ctl", "calibration": "cal",
            "calibration_control": "cal-ctl", "scope_mapping": "scope", "scope_control": "scope-ctl"}


class TestPremiseVisibility:
    @pytest.mark.parametrize("start,end", IGNORABLE_RANGES + BLANK_RANGES,
                             ids=lambda value: f"U+{value:04X}")
    @pytest.mark.parametrize("prefix,suffix", DECORATIONS)
    def test_every_default_ignorable_and_blank_lacks_a_visible_base(self, start, end, prefix, suffix):
        for codepoint in range(start, end + 1):
            value = prefix + chr(codepoint) + suffix
            assert premise_text(value) == "", f"U+{codepoint:04X}: {value!r}"

    @pytest.mark.parametrize("text", INVISIBLE + ("\ud800", "\ue000", "\u0378"))
    def test_marks_surrogates_private_use_and_unassigned_text_supply_no_base(self, text):
        assert premise_text(text) == ""

    @pytest.mark.parametrize("text", VISIBLE)
    def test_visible_bases_survive_the_comparison_form(self, text):
        assert premise_text(text) == text
        assert premise_text(premise_text(text)) == text


class TestPremiseAdmission:
    @pytest.mark.parametrize("text", INVISIBLE)
    @pytest.mark.parametrize("location", ["check", "mapping", "both"])
    def test_invisible_statements_cannot_establish_general_breadth(self, text, location):
        got = T["general"](text if location != "mapping" else "lemma L",
                           text if location != "check" else "lemma L")
        assert got["result"] == "inconclusive"
        assert got["effective_breadth"] == "covered_cases"

    @pytest.mark.parametrize("text", INVISIBLE)
    @pytest.mark.parametrize("field", ["scope", "population", "sample_unit"])
    def test_invisible_claim_premises_remain_absent_after_disk_reopen(self, tmp_path, text, field):
        path = tmp_path / "ledger.jsonl"
        led = Ledger(path)
        led.add_standard(H["STD"])
        led.add_vault("sealed", H["FX"], files=1, roots=1)
        led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a")))
        led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a")))
        led.add_claim(H["claim"](subject="sha256:" + "b" * 64, **{field: text}))
        traced = trace_claim(Ledger(path, expect_head=led.head()), "c1")
        assert traced["ledger_ok"] is True
        assert traced["verdict"]["result"] == "inconclusive"
        assert "claim_text_premise_missing" in traced["verdict"]["reasons"]

    @pytest.mark.parametrize("text", INVISIBLE)
    def test_invisible_estimands_are_refused(self, text):
        with pytest.raises(ValueError, match="estimand"):
            H["claim"](estimand=text)

    @pytest.mark.parametrize("text", INVISIBLE)
    @pytest.mark.parametrize("field", ["population", "sample_unit"])
    def test_matching_invisible_calibration_premises_cannot_identify(self, text, field):
        led = Ledger()
        hashes = {}
        for entry in S["receipts"]().entries():
            body = entry["body"]
            if entry["kind"] == "standard":
                led.add_standard(StandardRef.from_dict(body))
            elif entry["kind"] == "vault":
                led.add_vault(body["vault_id"], body["digest"], files=body["files"], roots=body["roots"])
            else:
                original = EvidenceRecord.from_dict(body)
                rec = replace(original, **{field: text})
                rec = replace(rec, applies_to_sha256=hashes.get(original.applies_to_sha256,
                                                               original.applies_to_sha256))
                hashes[original.sha256] = rec.sha256
                led.add_evidence(rec)
        got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                           breadth="general", scope="all inputs", **{field: text})
        assert got["result"] == "inconclusive"
        assert got["identification"] == "not_identified"
        assert got["effective_breadth"] == "covered_cases"

    @pytest.mark.parametrize("text", INVISIBLE)
    @pytest.mark.parametrize("role", ROLE_IDS)
    def test_no_receipt_role_can_use_invisible_scope(self, text, role):
        led = Ledger()
        hashes = {}
        for entry in S["receipts"]().entries():
            body = entry["body"]
            if entry["kind"] == "standard":
                led.add_standard(StandardRef.from_dict(body))
            elif entry["kind"] == "vault":
                led.add_vault(body["vault_id"], body["digest"], files=body["files"], roots=body["roots"])
            else:
                original = EvidenceRecord.from_dict(body)
                rec = replace(original, scope=text) if original.evidence_id == ROLE_IDS[role] else original
                rec = replace(rec, applies_to_sha256=hashes.get(original.applies_to_sha256,
                                                               original.applies_to_sha256))
                hashes[original.sha256] = rec.sha256
                led.add_evidence(rec)
        got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                           breadth="general", scope="all inputs")
        assert got["result"] == "inconclusive"

    @pytest.mark.parametrize("text", VISIBLE)
    def test_visible_scripts_keep_general_support(self, text):
        got = T["general"](text, text, mapping_scope=text, claim_scope=text)
        assert got["result"] == "supported"
        assert got["effective_breadth"] == "general"

    @pytest.mark.parametrize("text", VISIBLE)
    def test_visible_scripts_keep_scoped_support_for_every_claim_premise(self, text):
        led = Ledger()
        led.add_standard(H["STD"])
        led.add_vault("sealed", H["FX"], files=1, roots=1)
        led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a"), scope=text))
        led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a"), scope=text,
                                 population=text, sample_unit=text, checked_statement=text))
        got = H["verdict"](led, subject="sha256:" + "b" * 64, scope=text, population=text, sample_unit=text)
        assert got["result"] == "supported"
        assert got["effective_breadth"] == "covered_cases"
