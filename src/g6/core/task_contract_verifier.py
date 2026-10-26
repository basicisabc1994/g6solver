# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The "available verifier" predicate for a task contract's success criteria.

Intake (``reliability_pipeline.intake``) uses it to turn a critical criterion that names no runnable verifier into an
escalation trigger. ``reliability_pipeline.task_contract`` re-exports these names, so callers importing them from
there are unchanged.

The task-contract floor that once read ``task_mode`` and ``task_success_criteria`` off an envelope is gone: nothing
fed it, and its staging switches were a bypass (plan A1, DECISIONS #40).
"""
from __future__ import annotations

from collections.abc import Iterable

#: Marker prefix for a named human-review oracle used as a criterion's verifier
#: (e.g. ``"human:expert_review"``). A criterion whose verifier names a registered
#: programmatic method OR a registered promotion-law oracle OR carries this prefix
#: is considered to have an available verifier; anything else does not.
HUMAN_VERIFIER_PREFIX = "human:"


def verifier_available(verifier: str, *, registry_names: Iterable[str] | None = None) -> bool:
    """Whether ``verifier`` names a verifier this system can actually run.

    Available iff any of:
      1. it is a non-empty name registered in ``g6.core.verification`` (a
         programmatic method the pipeline can dispatch), or
      2. it is a registered promotion-law oracle — an independent replay the
         evaluator seam can reproduce, or
      3. it starts with :data:`HUMAN_VERIFIER_PREFIX` with a non-empty suffix (a
         named human-review oracle — a real, if manual, check).

    A bare/empty string, or a name that resolves to none of these, is NOT
    available. A self-assessing LLM is intentionally excluded. ``registry_names``
    is an injection seam for tests: when supplied it FULLY controls the
    programmatic branch (no oracle fallback), so a test can assert exact
    availability; the live path (``registry_names is None``) consults both live
    registries.
    """
    name = str(verifier or "").strip()
    if not name:
        return False
    if name.startswith(HUMAN_VERIFIER_PREFIX):
        return len(name) > len(HUMAN_VERIFIER_PREFIX)
    if registry_names is not None:
        # Explicit injection fully controls availability (test seam) — no oracle
        # fallback, so an injected-registry test asserts exactly what it declares.
        return name in set(registry_names)
    if name in _live_verification_names():
        return True
    return _promotion_oracle_registered(name)


def _live_verification_names() -> frozenset[str]:
    """Names in the live ``g6.core.verification`` registry, fail-closed to empty."""
    try:  # lazy import: avoid an import cycle at module load
        from g6.core import verification
    except Exception:  # noqa: BLE001 - registry module unavailable ⇒ nothing registered
        return frozenset()
    try:
        # Force-register the built-in methods so ``list_names()`` is populated even
        # when nothing else imported them yet (the component_ledger idiom). Isolated
        # from the read below so a methods-import failure can NEVER drop names the
        # base registry already reports — preserves additivity (never a new cap).
        from g6.core.verification import methods as _methods  # noqa: F401
    except Exception:  # noqa: BLE001,S110 - methods bootstrap best-effort; keep what is registered
        pass
    return frozenset(verification.list_names())


def _promotion_oracle_registered(name: str) -> bool:
    """True iff ``name`` is a registered promotion-law oracle (membership only).

    Fail-closed: a name we cannot confirm is registered (seam unavailable, or the
    oracle module never bootstrapped) is NOT available — it can only withhold a
    promotion, never grant one. Registration is process-global, so an oracle a
    declaration path bootstrapped earlier in the run is still visible here.
    """
    try:  # lazy import: keep the evaluator seam off this module's import path
        from g6.core import promotion_evaluator

        return bool(promotion_evaluator.oracle_registered(name))
    except Exception:  # noqa: BLE001 - seam unavailable ⇒ not confirmable ⇒ fail-closed
        return False


__all__ = [
    "HUMAN_VERIFIER_PREFIX",
    "verifier_available",
]
