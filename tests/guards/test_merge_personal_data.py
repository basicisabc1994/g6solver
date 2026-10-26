# SPDX-License-Identifier: Apache-2.0
"""Advisory personal-data tripwires, using synthetic reserved-domain fixtures."""
import dataclasses
import hashlib

import pytest

from g6.guards import PatternGuard, scan_personal_data


def test_d1_unseen_address_is_redacted_in_all_findings():
    # Removal oracle: scan_personal_data's email expression and redacted-only finding construction.
    address = "x.o'unit+tag@sub.example.invalid"
    scan = scan_personal_data("Send a copy to " + address)
    assert scan.flagged and scan.decision == "BLOCK" and scan.advisory
    finding = next(f for f in scan.findings if f.kind == "email")
    assert finding.sha256 == hashlib.sha256(address.encode()).hexdigest()
    assert finding.preview == "[REDACTED:email]"
    assert address not in str(dataclasses.asdict(scan))
    assert "sub.example.invalid" not in finding.preview


def test_csf_cognitive_02_clean_scan_is_never_clearance():
    # Removal oracle: PersonalDataScan.decision returns NO_MATCH for an empty findings tuple.
    result = scan_personal_data("An unnamed patient has a diagnosis.")
    assert result.decision == "NO_MATCH" and not result.flagged
    assert result.completion_state == "qualified-draft" and result.advisory
    assert "health" in result.blind_spots


@pytest.mark.parametrize(("kind", "value"), [
    ("ssn", "000-00-0000"), ("email", "fixture@example.invalid"),
    ("phone", "000-555-0100"), ("card", "0000 0000 0000 0000"),
])
def test_supported_patterns_return_only_redacted_preview_and_hash(kind, value):
    # Removal oracle: scan_personal_data's per-pattern finditer loop.
    result = scan_personal_data(value)
    finding = next(f for f in result.findings if f.kind == kind)
    assert finding.sha256 == hashlib.sha256(value.encode()).hexdigest()
    assert value not in repr(result) and finding.preview.startswith("[REDACTED:")


def test_scan_refuses_malformed_input():
    # Removal oracle: scan_personal_data's isinstance(text, str) guard.
    with pytest.raises(TypeError):
        scan_personal_data({"approved": True})


def test_blocked_patterns_already_use_pattern_guard():
    # Removal oracle: PatternGuard.scan_text's existing _scan call.
    result = PatternGuard(["publish the records"]).scan_text("Please PUBLISH  THE records.")
    assert result.decision == "BLOCK" and result.advisory
