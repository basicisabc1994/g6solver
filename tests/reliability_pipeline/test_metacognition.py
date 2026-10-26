# SPDX-License-Identifier: Apache-2.0
"""Experimental metacognition over an injected ``ExperienceStore`` (audit 009, RP-007), with survival controls.

The archive cannot run here; each regression test says in its docstring what the archive did. Each ``test_null_*``
puts that archive behaviour back in memory (monkeypatching only) and shows the regression test fails.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from g6.reliability_pipeline import metacognition as mc


class _Store:
    """A run registry plus experience records. ``records`` can be written directly, the way any producer of an
    ``experience_loop`` record could write one in the archive."""

    def __init__(self, runs=()):
        self.runs = set(runs)
        self.records: list = []

    def has_run(self, run_id):
        return run_id in self.runs

    def add(self, experience):
        self.records.append(experience)
        return True

    def similar(self, signature, features, k):
        return list(self.records)


class _Broken:
    def has_run(self, run_id):
        raise RuntimeError("down")

    def add(self, experience):
        raise RuntimeError("down")

    def similar(self, signature, features, k):
        raise RuntimeError("down")


def _fails(test, *args) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


def _distill(store, run_id, outcome="success", reward=1.0, signature="sig"):
    return mc.distill_run(store, run_id=run_id, signature=signature, outcome=outcome, reward=reward)


# --- archive behaviours, for the null-instrument checks ---------------------------------------------------------------

def _archive_run_exists(store, run_id) -> bool:
    """The archive's ``_provenance_of`` (metacognition.py:98-110): any non-empty ``metadata.run_id`` is genuine."""
    return isinstance(run_id, str) and bool(run_id)


def _archive_same_signature(experience, signature) -> bool:
    """The archive's match (metacognition.py:233): ``task_type == signature or not task_type``."""
    return experience.signature == signature or not experience.signature


def _archive_distill_run(store, *, run_id, signature, outcome, reward, trace=None):
    """The archive's ``distill_run`` (metacognition.py:149-183): records anything, confidence 1.0 when cached."""
    cached = bool(store.add(mc.Experience(run_id, signature, outcome, float(reward), dict(trace or {}))))
    return {"ok": True, "signature": signature, "run_id": run_id, "cached": cached,
            "provenance": [run_id] if cached else [], "confidence": 1.0 if cached else 0.0, "llm_used": False,
            "reason": "recorded" if cached else "not_recorded"}


def _archive_reconsume(store, *, signature, state_features=None, top_k=3):
    """The archive's ``reconsume`` (metacognition.py:186-272): a hit says it primed the run."""
    found = [e for e in store.similar(signature, dict(state_features or {}), top_k) if e.signature == signature]
    return {"ok": True, "signature": signature, "primed": bool(found),
            "recommendations": [{"outcome": e.outcome, "reward": e.reward, "run_id": e.run_id} for e in found],
            "cited_runs": [e.run_id for e in found], "llm_used": False,
            "detail": "primed_from_experience" if found else "no_prior_experience"}


# --- survival controls ------------------------------------------------------------------------------------------------

def test_a_record_whose_run_exists_is_cited() -> None:
    store = _Store(runs={"run-1", "run-2"})
    assert _distill(store, "run-1")["provenance"] == ["run-1"]
    assert _distill(store, "run-2", outcome="failure", reward=0.0)["recorded"] is True
    store.records.append(mc.Experience("run-1", "other-sig", "success", 1.0))
    out = mc.reconsume(store, signature="sig", top_k=5)
    assert out["recalled"] is True and out["cited_runs"] == ["run-1", "run-2"]
    assert out["experiences"] == [{"run_id": "run-1", "outcome": "success", "reward": 1.0},
                                  {"run_id": "run-2", "outcome": "failure", "reward": 0.0}]
    assert out["uncited_dropped"] == 0 and out["llm_used"] is False
    assert mc.reconsume(store, signature="sig", top_k=1)["cited_runs"] == ["run-1"]
    assert isinstance(store, mc.ExperienceStore)


def test_a_failing_store_degrades_and_never_raises() -> None:
    assert _distill(_Broken(), "run-1")["reason"] == "unknown_run"         # a store that cannot answer cites nothing

    class _AddFails(_Store):
        def add(self, experience):
            raise RuntimeError("full")

    out = _distill(_AddFails(runs={"run-1"}), "run-1")
    assert (out["recorded"], out["provenance"], out["reason"]) == (False, [], "store_unavailable: RuntimeError")
    out = mc.reconsume(_Broken(), signature="sig")
    assert (out["recalled"], out["cited_runs"], out["detail"]) == (False, [], "store_unavailable: RuntimeError")
    assert _distill(_Store(runs={"r"}), "r", reward=float("nan"))["reason"] == "reward_not_a_number"
    assert _distill(_Store(runs={"r"}), "r", signature="")["reason"] == "missing_run_id_or_signature"
    assert mc.reconsume(_Store(), signature="")["detail"] == "missing_signature"


# --- RP-007 ------------------------------------------------------------------------------------------------------------

def test_rp007_a_forged_record_is_not_cited() -> None:
    """Archive: an experience_loop record with any non-empty ``metadata.run_id`` was a "genuine" citation
    (metacognition.py:98-110), so a record forged with ``run_id="nonexistent"`` was cited by reconsume."""
    store = _Store(runs={"run-1"})
    assert _distill(store, "run-1")["provenance"] == ["run-1"]
    store.records.append(mc.Experience("nonexistent", "sig", "success", 1.0))   # written past distill_run
    out = mc.reconsume(store, signature="sig")
    assert "nonexistent" not in out["cited_runs"]
    assert out["cited_runs"] == ["run-1"] and [e["run_id"] for e in out["experiences"]] == ["run-1"]
    assert out["uncited_dropped"] == 1


def test_rp007_distill_run_stores_nothing_for_an_unknown_run() -> None:
    """Archive: ``distill_run`` stored any ``run_id`` without checking that the run exists (metacognition.py:149)."""
    store = _Store(runs={"run-1"})
    out = _distill(store, "nonexistent")
    assert out["provenance"] == [] and out.get("reason") == "unknown_run" and store.records == []


def test_rp007_no_constant_confidence() -> None:
    """Archive: ``distill_run`` returned ``confidence: 1.0`` for anything it stored (metacognition.py:180)."""
    store = _Store(runs={"run-1"})
    stored = _distill(store, "run-1", reward=0.2)
    assert stored["provenance"] == ["run-1"] and stored.get("confidence", "missing") is None
    assert mc.reconsume(store, signature="sig").get("confidence", "missing") is None


def test_rp007_recall_does_not_claim_to_change_the_run() -> None:
    """Archive: ``reconsume`` returned ``primed=True`` / ``"primed_from_experience"``, while the GUI loop stored the
    prime and ran the case unchanged (workflow_store.py:1714-1721)."""
    store = _Store(runs={"run-1"})
    _distill(store, "run-1")
    out = mc.reconsume(store, signature="sig")
    assert out["cited_runs"] == ["run-1"] and out.get("effect") == "disclosure_only"
    assert not any("prim" in key for key in out) and "prim" not in out["detail"]


def test_rp007_an_unsigned_experience_does_not_match_every_signature() -> None:
    """Archive: an experience with an empty ``task_type`` primed every signature (metacognition.py:233)."""
    store = _Store(runs={"run-1"})
    store.records.append(mc.Experience("run-1", "", "success", 1.0))
    out = mc.reconsume(store, signature="sig")
    assert out["cited_runs"] == [] and out["experiences"] == [] and out["recalled"] is False


def test_rp007_the_store_is_injected_and_nothing_else_is_imported() -> None:
    """No default or global store, and no component import (``g6.research`` included, founder decision A1)."""
    for fn in (mc.distill_run, mc.reconsume):
        assert inspect.signature(fn).parameters["store"].default is inspect.Parameter.empty
    assert not any(isinstance(v, mc.ExperienceStore) for v in vars(mc).values() if not isinstance(v, type))
    probe = ("import sys; import g6.reliability_pipeline.metacognition; "
             "print(sorted(m for m in sys.modules if m.startswith('g6')))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True).stdout
    loaded = eval(out)  # noqa: S307 - our own repr of a list of module names
    assert loaded == ["g6", "g6.reliability_pipeline", "g6.reliability_pipeline.metacognition"], loaded
    assert "g6.research" not in Path(mc.__file__).read_text(encoding="utf-8")
    from g6.reliability_pipeline import ExperienceStore, distill_run, reconsume
    assert (ExperienceStore, distill_run, reconsume) == (mc.ExperienceStore, mc.distill_run, mc.reconsume)


def test_null_rp007_regressions_fail_on_the_archive_behaviour(monkeypatch) -> None:
    monkeypatch.setattr(mc, "_run_exists", _archive_run_exists)
    _fails(test_rp007_a_forged_record_is_not_cited)
    _fails(test_rp007_distill_run_stores_nothing_for_an_unknown_run)
    monkeypatch.undo()
    monkeypatch.setattr(mc, "_same_signature", _archive_same_signature)
    _fails(test_rp007_an_unsigned_experience_does_not_match_every_signature)
    monkeypatch.undo()
    monkeypatch.setattr(mc, "distill_run", _archive_distill_run)
    _fails(test_rp007_no_constant_confidence)
    monkeypatch.undo()
    monkeypatch.setattr(mc, "reconsume", _archive_reconsume)
    _fails(test_rp007_recall_does_not_claim_to_change_the_run)
