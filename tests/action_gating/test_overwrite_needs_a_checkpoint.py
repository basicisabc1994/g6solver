# SPDX-License-Identifier: Apache-2.0
"""ACTION_GATING-01 (audit 2026-10-23): the ``write_file`` profile once fixed every overwrite as reversible and local, so
``decide_gate`` allowed any non-secret overwrite (a thesis, a config, ``~/.bashrc``) on "bounded local impact", unaudited.
Nothing observed that the write could be undone. Now a write is reversible only when the request names a readable
checkpoint or backup the gate itself can open; a delete has its own profile, irreversible."""
from __future__ import annotations

from pathlib import Path

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import decide_gate, evaluate_action
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.schema import ActionCapsule

_RUNS = {"allow", "allow_with_notice"}
_BOUNDED = "This action has bounded local impact."


def _gate(action: dict):
    return evaluate_action(action, audit_logger=ActionGateAuditLogger()).gate_decision


def _thesis(tmp_path: Path) -> Path:
    thesis = tmp_path / "thesis.md"
    thesis.write_text("three years of work\n", encoding="utf-8")
    return thesis


def test_an_overwrite_with_no_checkpoint_is_not_allowed_on_bounded_local_impact(tmp_path: Path) -> None:
    # fails if normalizer._ACTION_PROFILES["write_file"] goes back to a fixed "reversibility": "reversible"
    thesis = _thesis(tmp_path)
    for action in ({"action_type": "write_file", "target_resource": str(thesis)},
                   # the caller's word that it is reversible is a claim, not a checkpoint (a case not written around)
                   {"action_type": "write_file", "target_resource": str(thesis), "reversibility": "reversible"},
                   {"action_type": "write_file", "action_params": {"path": str(Path.home() / ".bashrc")}}):
        gate = _gate(action)
        assert gate.decision not in _RUNS, (action, gate.decision)
        assert gate.required_user_message != _BOUNDED and "G6_E_RISK_UNKNOWN" in gate.reason_codes


def test_a_checkpoint_the_gate_cannot_read_is_no_checkpoint(tmp_path: Path) -> None:
    # fails if normalizer._checkpoint stops requiring an absolute, existing, readable path other than the target
    thesis = _thesis(tmp_path)
    for named in (str(tmp_path / "missing.bak"), "thesis.md.bak", str(thesis), "", 7):
        gate = _gate({"action_type": "write_file", "target_resource": str(thesis), "checkpoint": named})
        assert gate.decision not in _RUNS, (named, gate.decision)


def test_an_overwrite_with_a_readable_checkpoint_is_allowed_as_before(tmp_path: Path) -> None:
    # fails if normalizer._floors stops reading a readable checkpoint as making the write reversible
    thesis = _thesis(tmp_path)
    backup = tmp_path / "thesis.md.bak"
    backup.write_text("three years of work\n", encoding="utf-8")
    for action in ({"action_type": "write_file", "target_resource": str(thesis), "checkpoint": str(backup)},
                   {"action_type": "write_file", "target_resource": str(thesis),
                    "action_params": {"backup": str(backup)}}):
        gate = _gate(action)
        assert gate.decision == "allow" and gate.reason_codes == ["G6_E_GATE_OK"], gate.reason_codes
        assert gate.required_user_message == _BOUNDED


def test_a_built_capsule_that_declares_itself_reversible_is_floored_too() -> None:
    # fails if normalizer._floor_capsule takes a built capsule's own reversibility for a write without a checkpoint
    capsule = ActionCapsule(action_id="ag-built", action_type="write_file", description="d", user_visible_summary="d",
                            target_resource="/srv/app/config.yaml", reversibility="reversible", blast_radius="local",
                            created_at="2026-10-23T00:00:00+00:00")
    floored = normalize_action_capsule(capsule)
    assert floored.reversibility == "unknown"
    assert decide_gate(floored).decision not in _RUNS


def test_a_delete_has_its_own_profile_and_is_irreversible(tmp_path: Path) -> None:
    # fails if normalizer._ACTION_PROFILES loses "delete_file" or marks it anything but irreversible
    thesis = _thesis(tmp_path)
    backup = tmp_path / "thesis.md.bak"
    backup.write_text("x", encoding="utf-8")
    capsule = normalize_action_capsule({"action_type": "delete_file", "target_resource": str(thesis),
                                        "reversibility": "reversible", "checkpoint": str(backup)})
    assert classify_reversibility(capsule) == "irreversible"
    assert classify_impact(capsule, "irreversible").aggregate_risk == "high"
    gate = _gate({"action_type": "delete_file", "target_resource": str(thesis)})
    assert gate.decision not in _RUNS and "G6_E_ACTION_UNPROFILED" not in gate.reason_codes
