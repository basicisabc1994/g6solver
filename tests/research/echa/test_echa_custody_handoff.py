# SPDX-License-Identifier: Apache-2.0
"""Public controls for external-check handoff, never models or Docker."""
import importlib
import json
from dataclasses import replace

import pytest

from g6.research.echa import (
    Ledger,
    LedgerError,
    ResearchDisabled,
    StandardRef,
)


@pytest.fixture
def custody(monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    return importlib.import_module("g6.research.echa.custody")


def binding(custody):
    standard = StandardRef.from_text("public", "Return the expected integer", "public development control", ("value",))
    return standard, custody.CheckBinding(
        request_id="r-1", task_id="public-1", predicate_id="value", artifact_sha256="a" * 64,
        standard_sha256=standard.standard_sha256, fixture_sha256="b" * 64,
        evaluator_sha256="c" * 64,
    )


def reply(expected, **changes):
    return json.dumps({**expected.to_dict(), "result": "pass", **changes}).encode()


def test_decodes_bounded_artifact_without_adopting_authority(custody):
    value = custody.decode_artifact(b'{"value":7,"claimed_verified":true}')
    assert value == {"value": 7, "claimed_verified": True}
    assert type(value) is dict  # data only, not an EvidenceRecord


@pytest.mark.parametrize("raw", [b'[]', b'{"value":NaN}', b'{"value":Infinity}',
                                  b'{"value":1,"value":7}', b'{"nested":{"x":1,"x":2}}', b'\xff'])
def test_ambiguous_or_invalid_json_refused(custody, raw):
    with pytest.raises(ValueError):
        custody.decode_artifact(raw)


def test_oversize_refused_before_parsing(custody):
    with pytest.raises(ValueError, match="size"):
        custody.decode_artifact(b" " * (custody.MAX_ARTIFACT_BYTES + 1))


def test_separate_check_creates_only_scoped_nonindependent_evidence(custody, tmp_path):
    standard, expected = binding(custody)
    evidence = custody.checked_evidence(reply(expected), expected, predicate_id="value")
    assert evidence.result == "pass"
    assert evidence.independent is False and evidence.oracle_category == "PARTIAL_FALSIFIER"
    assert evidence.fixture_version == "unsealed"
    assert evidence.standard_sha256 == standard.standard_sha256
    assert expected.artifact_sha256 in evidence.source_ref
    ledger = Ledger(tmp_path / "evidence.jsonl")
    ledger.add_standard(standard)
    ledger.add_evidence(evidence)
    assert ledger.verify() == ()
    with pytest.raises(LedgerError, match="already"):
        ledger.add_evidence(evidence)
    assert len(ledger.entries()) == 2


@pytest.mark.parametrize("field", ["request_id", "task_id", "artifact_sha256", "standard_sha256",
                                    "fixture_sha256", "evaluator_sha256"])
def test_mismatched_receipt_never_becomes_evidence(custody, field):
    _, expected = binding(custody)
    with pytest.raises(ValueError, match="binding"):
        custody.checked_evidence(reply(expected, **{field: "other"}), expected, predicate_id="value")


@pytest.mark.parametrize("changes", [{"independent": True}, {"verified": True}, {"result": True},
                                    {"result": "verified"}])
def test_forged_authority_fields_or_invalid_result_refused(custody, changes):
    _, expected = binding(custody)
    with pytest.raises(ValueError):
        custody.checked_evidence(reply(expected, **changes), expected, predicate_id="value")


def test_missing_interrupted_receipt_cannot_pass(custody):
    _, expected = binding(custody)
    for raw in (b"", b'{"result":"pass"}', b'{"result":'):
        with pytest.raises(ValueError):
            custody.checked_evidence(raw, expected, predicate_id="value")


def test_default_off_is_checked_at_use(custody, monkeypatch):
    _, expected = binding(custody)
    monkeypatch.delenv("G6_ECHA_RESEARCH")
    with pytest.raises(ResearchDisabled):
        custody.decode_artifact(b'{"value":7}')
    with pytest.raises(ResearchDisabled):
        custody.checked_evidence(reply(expected), expected, predicate_id="value")


def test_receipt_cannot_be_reassigned_to_another_predicate(custody):
    _, expected = binding(custody)
    with pytest.raises(ValueError, match="predicate"):
        custody.checked_evidence(reply(expected), expected, predicate_id="unexecuted-check")


def test_receipt_replay_against_another_bound_predicate_is_refused(custody):
    _, expected = binding(custody)
    other = replace(expected, predicate_id="other")
    with pytest.raises(ValueError, match="binding"):
        custody.checked_evidence(reply(expected), other, predicate_id="other")
