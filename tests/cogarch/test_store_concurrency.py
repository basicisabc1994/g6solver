# SPDX-License-Identifier: Apache-2.0
"""Contended named stores keep learning and use separate atomic-write files."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event

import pytest

from g6.cogarch.cog_arch_actr import CogArchActrBlock, CogArchActrInput, memory
from g6.cogarch.cog_arch_aixi import CogArchAixiBlock, CogArchAixiInput
from g6.cogarch.cog_arch_aixi import block as aixi
from g6.core.storage import state_path


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))


def _actr(**params):
    return CogArchActrBlock().infer(CogArchActrInput(**params)).unwrap()


def _aixi(**params):
    return CogArchAixiBlock().infer(CogArchAixiInput(**params)).unwrap()


def _rendezvous(barrier: Barrier) -> None:
    # With a transaction lock the first load must time out; without it both reach the barrier on stale snapshots.
    try:
        barrier.wait(timeout=0.5)
    except BrokenBarrierError:
        pass


def test_actr_concurrent_writers_preserve_both_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix: cog_arch_actr/block.py:315 (store_lock context); removing it loses a concurrent chunk update.
    barrier, first_loaded = Barrier(2), Event()
    original = memory.Memory.load

    def load(store):
        state = original(store)
        first_loaded.set()
        _rendezvous(barrier)
        return state

    monkeypatch.setattr(memory.Memory, "load", load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_actr, op="add_chunk", memory="shared", name="a", isa="fact", slots={"v": 1})
        assert first_loaded.wait(5)
        second = pool.submit(_actr, op="add_chunk", memory="shared", name="b", isa="fact", slots={"v": 2})
        replies = [first.result(timeout=10), second.result(timeout=10)]
    assert all(r["code"] == "G6_E_ACTR_KEPT" for r in replies)
    kept = original(memory.Memory("shared"))
    assert set(kept.chunks) == {"a", "b"}
    assert kept.chunks["a"].slots == {"v": 1} and kept.chunks["b"].slots == {"v": 2}


def test_aixi_concurrent_writers_preserve_action_and_learning(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix: cog_arch_aixi/block.py:339 (store_lock context); removing it rejects observe before act commits.
    _aixi(op="create", agent="shared", playouts=2, horizon=1)
    barrier, first_loaded = Barrier(2), Event()
    original = aixi._load

    def load(name):
        model = original(name)
        first_loaded.set()
        _rendezvous(barrier)
        return model

    monkeypatch.setattr(aixi, "_load", load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_aixi, op="act", agent="shared")
        assert first_loaded.wait(5)
        second = pool.submit(_aixi, op="observe", agent="shared", observation=1, reward=1)
        chose, learned = first.result(timeout=10), second.result(timeout=10)
    assert chose["code"] == "G6_E_AIXI_ACTED" and learned["code"] == "G6_E_AIXI_OBSERVED"
    kept = original("shared")
    assert kept.steps == 1 and kept.total_reward == 1 and kept.pending_action is None
    assert kept.model.history == [chose["action"], 1, 1]


@pytest.mark.parametrize("family", ["actr", "aixi"])
def test_store_writes_use_unique_same_directory_parts(monkeypatch: pytest.MonkeyPatch, family: str) -> None:
    # Fix: _store.py:80 (mkstemp); restoring a shared .json.part name fails the unique-path assertion.
    real_replace, parts = os.replace, []

    def replace(source, target):
        source, target = Path(source), Path(target)
        assert source.parent == target.parent and source.is_file()
        parts.append(source)
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    if family == "actr":
        _actr(op="add_chunk", name="a", isa="fact", slots={"v": 1})
        _actr(op="add_chunk", name="b", isa="fact", slots={"v": 2})
    else:
        _aixi(op="create", agent="a", playouts=2)
        _aixi(op="act", agent="a")
    assert len(parts) == 2 and parts[0] != parts[1]
    assert all(not p.exists() for p in parts)


@pytest.mark.parametrize("family", ["actr", "aixi"])
def test_store_lock_timeout_and_stale_recovery(monkeypatch: pytest.MonkeyPatch, family: str) -> None:
    # Fix: _store.py:37 (O_EXCL) and :42 (stale unlink); removing them loses refusal or recovery.
    from g6.cogarch import _store

    monkeypatch.setattr(_store, "LOCK_TIMEOUT_S", 0.02)
    component = "cog_arch_" + family
    path = state_path("cogarch", component, "busy.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".json.lock")
    lock.write_text("held", encoding="utf-8")
    call = (lambda: _actr(op="add_chunk", memory="busy", name="a", isa="fact")) if family == "actr" else (
        lambda: _aixi(op="create", agent="busy"))
    denied = call()
    assert denied["completion_state"] == "blocked-escalated"
    assert denied["code"] == f"G6_E_{family.upper()}_STORE_BUSY" and not path.exists()
    assert lock.read_text(encoding="utf-8") == "held"
    os.utime(lock, (1, 1))
    monkeypatch.setattr(_store, "LOCK_TIMEOUT_S", 1.0)
    recovered = call()
    assert recovered["code"] == ("G6_E_ACTR_KEPT" if family == "actr" else "G6_E_AIXI_CREATED")
    assert json.loads(path.read_text(encoding="utf-8")) and not lock.exists()


def test_expired_store_lease_refuses_late_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix: _store.py:85 (_check_lease); removing it lets an expired writer overwrite newer state.
    from g6.cogarch import _store

    clock = [0.0]
    monkeypatch.setattr(_store.time, "monotonic", lambda: clock[0])
    path = tmp_path / "leased.json"
    path.write_text("newer state", encoding="utf-8")
    with _store.store_lock(path):
        clock[0] = _store.STALE_LOCK_S + 1
        with pytest.raises(_store.StoreBusy):
            _store.atomic_write(path, "stale state")
    assert path.read_text(encoding="utf-8") == "newer state"
    assert not list(tmp_path.glob("*.part"))
