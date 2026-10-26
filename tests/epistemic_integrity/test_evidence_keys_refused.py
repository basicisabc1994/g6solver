# SPDX-License-Identifier: Apache-2.0
"""An evidence record's keys and stance are checked, never dropped (review B1-1).

``_to_records`` kept the keys it knew and dropped the rest, so two opposing primary records sent as
``{"stance": "opposes"}`` lost their stance, took the default ``supports`` and graded E3. A stance outside the
vocabulary (``"refutes"``, ``"Opposes"``) counted as neither support nor opposition, so REFUTED and CONTESTED were
silently missed. An unknown key, or a stance outside ``supports``, ``opposes``, ``mixed``, ``context_only``, is now
refused with a stable code, over MCP and in Python. A record that gives no stance still supports the claim it is
declared for (the documented default).
"""
from __future__ import annotations

import json

import pytest

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicate
from g6.mcp import invoke_reply

_CODE = "G6_E_EPISTEMIC_INTEGRITY_EVIDENCE_INVALID"
_CLAIM = "The drug cures the infection in adults."


def _records(**stance) -> list[dict]:
    return [{"source_uri_or_artifact_id": f"trial-{g}", "source_type": "primary", "independence_group": g,
             "authority": "high", **stance} for g in ("a", "b")]


def _adjudicate(evidence: list[dict]) -> dict:
    return invoke_reply("epistemic_integrity", "adjudicate", json.dumps(
        {"message": _CLAIM, "action_criticality": "health", "evidence": evidence,
         "has_human_approval": True, "has_rollback": True}))


def test_a_misspelt_stance_key_is_refused_not_graded_as_support() -> None:
    reply = _adjudicate(_records(stance="opposes"))
    assert reply["ok"] is False and reply["error"].startswith(f"[{_CODE}]"), reply
    assert "stance" in reply["error"] and reply["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("stance", ["refutes", "Opposes", "against", ""])
def test_a_stance_outside_the_vocabulary_is_refused(stance: str) -> None:
    reply = _adjudicate(_records(supports_or_opposes=stance))
    assert reply["ok"] is False and reply["error"].startswith(f"[{_CODE}]"), reply


def test_the_same_records_spelt_right_are_refuted() -> None:
    """Survival control: the vocabulary's own words still work, and opposition still refutes."""
    reply = _adjudicate(_records(supports_or_opposes="opposes"))
    assert reply["ok"] is True, reply
    assert (reply["result"]["overall_status"], reply["result"]["overall_disposition"]) == ("REFUTED", "BLOCK")


def test_a_record_with_no_stance_supports_the_claim_it_is_declared_for() -> None:
    reply = _adjudicate(_records())
    assert reply["ok"] is True and reply["result"]["claims"][0]["evidence_grade"] == "E3", reply


def test_the_source_aliases_the_block_reads_are_not_unknown_keys() -> None:
    reply = invoke_reply("epistemic_integrity", "adjudicate", json.dumps(
        {"message": "The office opens at nine.", "evidence": [{"source": "notice-board", "source_type": "primary"}]}))
    assert reply["ok"] is True, reply


@pytest.mark.parametrize("stance", ["refutes", "Opposes", ""])
def test_python_refuses_a_stance_outside_the_vocabulary(stance: str) -> None:
    with pytest.raises(ValueError, match="supports_or_opposes"):
        EvidenceRecord("trial-a", source_type="primary", supports_or_opposes=stance)


def test_python_keeps_the_vocabulary_and_the_default() -> None:
    for stance in ("supports", "opposes", "mixed", "context_only"):
        assert EvidenceRecord("x", supports_or_opposes=stance).supports_or_opposes == stance
    assert EvidenceRecord("x").supports_or_opposes == "supports"
    opposed = (EvidenceRecord("a", source_type="primary", independence_group="a", supports_or_opposes="opposes"),)
    assert adjudicate(_CLAIM, action_criticality="info", evidence_records=opposed).overall_status == "REFUTED"
