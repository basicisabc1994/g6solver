# SPDX-License-Identifier: Apache-2.0
"""ACTION_GATING-01 (audit 2026-10-23) through the agent runtime: a delete reached ``g6.action_gating`` as a
``write_file``, and the action gate's ``write_file`` profile called every overwrite reversible. Now a delete is a
``delete_file`` (irreversible by its profile), and a write is reversible only with a checkpoint the gate can read: the
runtime names the workspace's version control as one, and a workspace without it has none."""
from __future__ import annotations

from pathlib import Path

import pytest

import g6.action_gating as action_gating
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.classifiers import classify_reversibility
from g6.agents._runtime import codes
from g6.agents._runtime.gate import ToolGate
from g6.agents._runtime.model import AgentTask, Permissions, ToolCall, ToolSpec

MEMORY = ActionGateAuditLogger()   # in memory: these tests never write the person's audit trail


def _task(workspace: Path, spec: ToolSpec) -> AgentTask:
    return AgentTask(goal="tidy the notes", tools=(spec,),
                     permissions=Permissions(workspace=str(workspace), read_files=True, write_files=True))


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every (action, evaluation) the runtime sends to the real action gate."""
    found, real = [], action_gating.evaluate_action

    def spy(action, *args, **kwargs):
        evaluated = real(action, *args, **kwargs)
        found.append((action, evaluated))
        return evaluated

    monkeypatch.setattr(action_gating, "evaluate_action", spy)
    return found


def test_a_delete_reaches_the_gate_as_delete_file_and_is_irreversible(tmp_path: Path, seen: list) -> None:
    # fails if gate._ACTION_TYPE maps "delete" to "write_file" again, or the delete_file profile is not irreversible
    (tmp_path / ".git").mkdir()       # a checkpoint the gate can read changes nothing for a delete
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")
    decided = ToolGate(_task(tmp_path, ToolSpec(name="remove", effect="delete")), dry_run=False,
                       audit_logger=MEMORY).decide(ToolCall(tool="remove", paths=("notes.txt",)))
    ((action, evaluated),) = seen
    assert action["action_type"] == "delete_file" and decided.gate["action_type"] == "delete_file"
    assert classify_reversibility(evaluated.action_capsule) == "irreversible"
    assert evaluated.gate_decision.risk.aggregate_risk == "high"
    assert decided.verdict == "refused" and decided.code == codes.GATE_BLOCKED and not decided.runs


def test_a_write_in_a_versioned_workspace_names_its_checkpoint_and_runs_as_before(tmp_path: Path, seen: list) -> None:
    # fails if gate._evaluate stops naming the workspace's version control as the write's checkpoint
    (tmp_path / ".git").mkdir()
    decided = ToolGate(_task(tmp_path, ToolSpec(name="edit", effect="write")), dry_run=False,
                       audit_logger=MEMORY).decide(ToolCall(tool="edit", paths=("notes.txt",)))
    ((action, _),) = seen
    assert action["action_params"]["checkpoint"] == str((tmp_path / ".git").resolve())
    assert decided.gate["decision"] == "allow" and decided.verdict == "run"


def test_a_write_in_a_workspace_with_no_checkpoint_is_refused(tmp_path: Path, seen: list) -> None:
    # fails if the action gate's write_file profile calls an overwrite reversible with nothing to undo it from
    decided = ToolGate(_task(tmp_path, ToolSpec(name="edit", effect="write")), dry_run=False,
                       audit_logger=MEMORY).decide(ToolCall(tool="edit", paths=("notes.txt",)))
    ((action, _),) = seen
    assert "checkpoint" not in action["action_params"]
    assert decided.verdict == "refused" and decided.code == codes.GATE_BLOCKED
    assert decided.gate["decision"] == "require_additional_information"
