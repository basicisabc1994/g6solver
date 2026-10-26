# SPDX-License-Identifier: Apache-2.0
"""Experience recall across runs. Optional: nothing calls it unless you do (audit 009, RP-007).

``distill_run`` stores what a finished run did (its task signature, trace, outcome and reward); ``reconsume`` hands
back the stored experiences for the same signature, each cited by the run it came from. Both work over an
``ExperienceStore`` the caller passes in: there is no default or shared store, and this module imports no other
component.

The rules it keeps:

* A citation is a run the store knows. ``distill_run`` stores nothing for a run the store's ``has_run`` does not
  know, and ``reconsume`` drops every stored experience whose ``run_id`` it does not know, so a forged record cites
  nothing. A citation is only as good as ``has_run``: a store that answers yes to any id gives that up.
* No confidence is reported, because nothing here measures one: ``confidence`` is ``None``.
* Recall does not change a run. Its result says ``effect="disclosure_only"``: past outcomes for the caller to show
  or use. Whether a later run behaves differently is the caller's code, and only a measurement of that code can
  say it does.
* No model is called (``llm_used`` is ``False``), and a store that fails or raises gives an empty result with the
  reason; neither function raises because of the store.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_LOG = logging.getLogger(__name__)
EFFECT = "disclosure_only"


@dataclass(frozen=True)
class Experience:
    run_id: str
    signature: str
    outcome: str
    reward: float
    trace: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExperienceStore(Protocol):
    def has_run(self, run_id: str) -> bool:
        """True only for a run the store's owner registered. This is the one provenance check."""
        ...

    def add(self, experience: Experience) -> bool:
        """Store one experience; True when it was stored."""
        ...

    def similar(self, signature: str, features: Mapping[str, Any], k: int) -> Sequence[Experience]:
        """Up to ``k`` stored experiences, most similar first; how similarity is judged is the store's choice."""
        ...


def _run_exists(store: ExperienceStore, run_id: Any) -> bool:
    """audit 009, RP-007: provenance is a run the store knows, not whatever ``run_id`` a record carries (the archive
    cited any non-empty ``metadata.run_id``). A store that cannot answer cites nothing."""
    if not isinstance(run_id, str) or not run_id:
        return False
    try:
        return store.has_run(run_id) is True
    except Exception:  # degrade, never raise because of the store
        return False


def _same_signature(experience: Any, signature: str) -> bool:
    """audit 009, RP-007: only experiences stored for this signature; the archive let an empty ``task_type`` match
    every signature."""
    return isinstance(experience, Experience) and experience.signature == signature


def distill_run(store: ExperienceStore, *, run_id: str, signature: str, outcome: str, reward: float,
                trace: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Store a finished run's experience, only if ``store`` knows the run.

    Returns ``{run_id, signature, recorded, provenance, confidence, llm_used, reason}``; ``confidence`` is always
    ``None`` (audit 009, RP-007: the archive returned a constant 1.0 for anything it stored)."""
    base: dict[str, Any] = {"run_id": run_id, "signature": signature, "recorded": False, "provenance": [],
                            "confidence": None, "llm_used": False}
    if not run_id or not signature:
        return {**base, "reason": "missing_run_id_or_signature"}
    try:
        reward = float(reward)
    except (TypeError, ValueError):
        return {**base, "reason": "reward_not_a_number"}
    if not math.isfinite(reward):
        return {**base, "reason": "reward_not_a_number"}
    if not _run_exists(store, run_id):
        return {**base, "reason": "unknown_run"}
    try:
        recorded = bool(store.add(Experience(run_id, signature, str(outcome), reward, dict(trace or {}))))
    except Exception as exc:  # degrade, never raise because of the store
        _LOG.warning("distill_run: the store did not take the experience: %s", type(exc).__name__)
        return {**base, "reason": f"store_unavailable: {type(exc).__name__}"}
    return {**base, "recorded": recorded, "provenance": [run_id] if recorded else [],
            "reason": "recorded" if recorded else "not_recorded"}


def reconsume(store: ExperienceStore, *, signature: str, state_features: Mapping[str, Any] | None = None,
              top_k: int = 3) -> dict[str, Any]:
    """Recall stored experiences for ``signature``, each cited by a run ``store`` knows.

    Returns ``{signature, recalled, experiences, cited_runs, uncited_dropped, effect, confidence, llm_used,
    detail}``. ``effect`` is always ``"disclosure_only"``: this returns history and changes no run (audit 009,
    RP-007: the archive said ``primed=True`` while the run it primed went on unchanged)."""
    k = max(1, int(top_k))
    base: dict[str, Any] = {"signature": signature, "recalled": False, "experiences": [], "cited_runs": [],
                            "uncited_dropped": 0, "effect": EFFECT, "confidence": None, "llm_used": False}
    if not signature:
        return {**base, "detail": "missing_signature"}
    try:
        found = list(store.similar(signature, dict(state_features or {}), k) or [])
    except Exception as exc:  # degrade, never raise because of the store
        _LOG.warning("reconsume: the store could not be read: %s", type(exc).__name__)
        return {**base, "detail": f"store_unavailable: {type(exc).__name__}"}
    matched = [e for e in found if _same_signature(e, signature)]
    cited = [e for e in matched if _run_exists(store, e.run_id)]
    shown = cited[:k]
    return {**base, "recalled": bool(shown),
            "experiences": [{"run_id": e.run_id, "outcome": e.outcome, "reward": e.reward} for e in shown],
            "cited_runs": list(dict.fromkeys(e.run_id for e in shown)),
            "uncited_dropped": len(matched) - len(cited),
            "detail": "recalled" if shown else "no_cited_experience"}
