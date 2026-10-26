# SPDX-License-Identifier: Apache-2.0
"""K8 (audit fix pass 2026-10-23, after ACTION_GATING-01): the action gate allows an overwrite only when it names a
checkpoint or backup the gate can read, so a live state write through the default gate was held
(``G6_E_HAT_GATE_HELD``). The store now keeps the state each write replaces as a backup beside it, and the block names
that copy as the write's checkpoint. The first write replaces no file: its backup holds the empty state, which is what
the store reads when there is no file. The tests here use the default gate, never an injected one."""
from __future__ import annotations

import json

import pytest

import g6.action_gating as action_gating
from g6.core.storage import state_path
from g6.integrations.hat_orchestrator import HATOrchestratorBlock, HATOrchestratorInput
from g6.integrations.hat_orchestrator.state import Snapshot


def call(block, op, **values):
    return block.infer(HATOrchestratorInput(op=op, **values)).unwrap()


def backup():
    return state_path("hat_orchestrator", "state.previous.json")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    return tmp_path


def test_a_live_write_through_the_default_gate_names_its_backup_and_is_allowed(home, monkeypatch):
    # fails if block.infer stops naming the store's backup as the write's checkpoint: the default gate then holds it
    seen, real = [], action_gating.evaluate_action
    monkeypatch.setattr(action_gating, "evaluate_action", lambda action, *a, **k: seen.append(action) or real(action, *a, **k))
    block = HATOrchestratorBlock()   # gate=None: g6.action_gating.evaluate_action, as a person's G6 runs it
    for agent, capabilities in (("editor", ["write"]), ("reviewer", ["review"])):
        result = call(block, "register_agent", agent_id=agent, capabilities=capabilities, available=True, dry_run=False)
        assert result["code"] == "G6_E_HAT_RECORDED" and result["gate"]["decision"] == "allow", result
    assert [action.get("checkpoint") for action in seen] == [str(backup())] * 2
    assert set(block.store.read().agents) == {"editor", "reviewer"}


def test_the_backup_holds_the_state_each_write_replaced(home):
    # fails if Store.transaction stops copying the state it is about to replace into the backup first
    block = HATOrchestratorBlock()
    call(block, "register_agent", agent_id="editor", capabilities=["write"], available=True, dry_run=False)
    assert json.loads(backup().read_text(encoding="utf-8")) == Snapshot().model_dump()   # the first write: no file before
    before = block.store.path.read_bytes()
    call(block, "register_agent", agent_id="reviewer", capabilities=["review"], available=True, dry_run=False)
    assert backup().read_bytes() == before
    assert set(Snapshot.model_validate_json(backup().read_bytes()).agents) == {"editor"}
    assert set(block.store.read().agents) == {"editor", "reviewer"}


def test_a_dry_run_keeps_no_backup_and_a_held_write_changes_nothing(home, monkeypatch):
    # survival: only a live write is backed up, and a gate that holds the write still leaves the state as it was
    block = HATOrchestratorBlock()
    call(block, "register_agent", agent_id="editor", capabilities=["write"], available=True)
    assert not backup().exists() and not block.store.path.exists()
    call(block, "register_agent", agent_id="editor", capabilities=["write"], available=True, dry_run=False)
    before = block.store.path.read_bytes()
    monkeypatch.setattr(action_gating, "evaluate_action", lambda action, *a, **k: (_ for _ in ()).throw(OSError("down")))
    held = call(block, "register_agent", agent_id="intruder", capabilities=["write"], available=True, dry_run=False)
    assert held["code"] == "G6_E_HAT_GATE_HELD" and block.store.path.read_bytes() == before
