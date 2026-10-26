# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The "available verifier" predicate for task-contract fit-for-purpose gating.

This is the trust-critical primitive shared by BOTH doors the fit-for-purpose cap
runs at: the reliability-pipeline ``verified_ready`` seam
(``reliability_pipeline.task_contract``) and the kernel promotion-law / orchestrator
door (``core.reliability_envelope``). It lives in ``core`` so the promotion-law door
can reuse the EXACT same definition of "an available verifier" without a layering
inversion (core never imports the pipeline) and without duplicating a
security-relevant predicate that could drift between the two doors.

``reliability_pipeline.task_contract`` re-exports these names, so existing callers
importing them from there are unchanged.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

#: Marker prefix for a named human-review oracle used as a criterion's verifier
#: (e.g. ``"human:expert_review"``). A criterion whose verifier names a registered
#: programmatic method OR a registered promotion-law oracle OR carries this prefix
#: is considered to have an available verifier; anything else does not.
HUMAN_VERIFIER_PREFIX = "human:"


def verifier_available(verifier: str, *, registry_names: Iterable[str] | None = None) -> bool:
    """Whether ``verifier`` names a verifier this system can actually run.

    Available iff any of:
      1. it is a non-empty name registered in ``mvp.core.verification`` (a
         programmatic method the pipeline can dispatch), or
      2. it is a registered promotion-law oracle (K-P0-1/K-P0-4) — an INDEPENDENT
         replay the evaluator seam can reproduce (e.g. ``coder_gold_oracle``,
         ``document_grounding_oracle``); the strongest kind of available verifier, or
      3. it starts with :data:`HUMAN_VERIFIER_PREFIX` with a non-empty suffix (a
         named human-review oracle — a real, if manual, check).

    A bare/empty string, or a name that resolves to none of these, is NOT
    available. This is the K-P0-2 definition of "an available verifier"; a
    self-assessing LLM is intentionally excluded. ``registry_names`` is an
    injection seam for tests: when supplied it FULLY controls the programmatic
    branch (no oracle fallback), so a test can assert exact availability; the live
    path (``registry_names is None``) consults both live registries.
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
    """Names in the live ``mvp.core.verification`` registry, fail-closed to empty."""
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


# ── enforce-mode staging flag (shared by both doors) ────────────────────────
_TASK_CONTRACT_ENFORCE_ENV = "G6_TASK_CONTRACT_ENFORCE"
_OFF_WORDS = frozenset({"", "0", "off", "false", "no"})
_SHADOW_WORDS = frozenset({"shadow", "observe", "observe-only", "log"})

# ── SP2 declaration-reach flag (single reader) ───────────────────────────────
_METHOD_CONTRACT_ENV = "G6_METHOD_CONTRACT"
_METHOD_CONTRACT_TRUTHY_WORDS = frozenset({"1", "true", "yes"})


def method_contract_enabled() -> bool:
    """Whether ``G6_METHOD_CONTRACT`` is set to a truthy value.

    The single reader of this flag (mirrors :func:`task_contract_enforce_mode`
    being the single reader of ``G6_TASK_CONTRACT_ENFORCE``). This flag is
    zero-coupled to ``enforce_mode``: it controls REACH only — whether SP1's
    declared method contract (domain, mode, criteria) is allowed to reach the
    existing fit-for-purpose seams at all. Disposition (off/shadow/enforce) at
    those seams stays entirely with :func:`task_contract_enforce_mode`; a
    caller must check both.

    Truthy set is exactly ``{"1", "true", "yes"}`` (case-insensitive, after
    stripping whitespace). Anything else — unset, ``""``, ``"0"``, ``"off"``,
    ``"no"``, ``"false"``, or an unrelated word like ``"enforce"``/``"shadow"``
    — is ``False``. Default OFF.
    """
    raw = (os.environ.get(_METHOD_CONTRACT_ENV) or "").strip().lower()
    return raw in _METHOD_CONTRACT_TRUTHY_WORDS


def task_contract_enforce_mode() -> str:
    """Resolve ``G6_TASK_CONTRACT_ENFORCE`` to ``off`` / ``shadow`` / ``enforce``.

    Tri-state, default ``off`` (unset). The fit-for-purpose cap is DEMOTE-ONLY, so
    a garbled value failing toward ``enforce`` is the safe direction — it can only
    lower a ceiling, never silently disable the cap or raise a state. Shared by the
    pipeline ``verified_ready`` seam and the promotion-law door so both stage on the
    same flag.
    """
    raw = (os.environ.get(_TASK_CONTRACT_ENFORCE_ENV) or "").strip().lower()
    if raw in _OFF_WORDS:
        return "off"
    if raw in _SHADOW_WORDS:
        return "shadow"
    return "enforce"


# ── per-surface enforce scope (twin of the promotion law's S4 scoping) ──────
_TASK_CONTRACT_SCOPE_ENV = "G6_TASK_CONTRACT_ENFORCE_SURFACES"
_TASK_CONTRACT_SCOPE_WILDCARDS = frozenset({"*", "all", "__all__"})


def task_contract_enforce_scope() -> frozenset[str] | None:
    """Resolve ``G6_TASK_CONTRACT_ENFORCE_SURFACES`` — the per-surface enforce
    scope for the fit-for-purpose floor (twin of the promotion law's
    ``G6_PROMOTION_ENFORCE_SURFACES`` / ``promotion_enforce_scope``).

    Returns ``None`` when the var is unset/empty or names a wildcard
    (``*`` / ``all`` / ``__all__``): ``enforce`` is UNSCOPED (global) —
    byte-identical to the pre-scope floor. Otherwise a ``frozenset`` of
    lower-cased surface component names: under ``enforce`` the floor caps ONLY
    those surfaces; every other seam — including the identity-less core door and
    the pipeline ``verified_ready`` seam — degrades to ``shadow`` (would-cap
    logged, nothing applied). Scoping can only NARROW enforce, never widen it.
    Rollback is instant — unset the var (→ global) or set the floor ``off``.
    """
    raw = os.environ.get(_TASK_CONTRACT_SCOPE_ENV)
    if not raw:
        return None
    names = {part.strip().lower() for part in raw.split(",")}
    names.discard("")
    if not names or names & _TASK_CONTRACT_SCOPE_WILDCARDS:
        return None
    return frozenset(names)


def effective_task_contract_enforce_mode(component: str | None = None) -> str:
    """The floor mode in effect for ``component`` (per-surface staging).

    ``off`` / ``shadow`` are global and returned unchanged. ``enforce`` is
    filtered through :func:`task_contract_enforce_scope`: unscoped enforces
    everywhere (today's behavior); a scoped set enforces only for a listed
    component and returns ``shadow`` for anything else. Seams with no surface
    identity (``component is None`` — the core envelope door and the pipeline
    ``verified_ready`` seam) always resolve to ``shadow`` under a scoped set:
    they participate in enforce only via the global/wildcard setting, never a
    per-surface cohort (same posture as ``effective_promotion_law_mode``).
    """
    mode = task_contract_enforce_mode()
    if mode != "enforce":
        return mode
    scope = task_contract_enforce_scope()
    if scope is None:
        return "enforce"
    if component and component.strip().lower() in scope:
        return "enforce"
    return "shadow"


# ── the fit-for-purpose reasons a door must cap a `verified` (shared) ────────
#: The exploratory-mode cap reason (verification is not the deliverable). Kept as a
#: module constant so both the pipeline seam and the promotion-law doors emit the
#: identical string.
_EXPLORATORY_CAP_REASON = (
    "exploratory mode: verification is not the deliverable (caps at qualified-draft)"
)


def task_contract_floor_reasons(mode: Any, criteria: Any) -> list[str]:
    """The fit-for-purpose reasons a door must cap a proposed ``verified``.

    Single-sourced across ALL THREE seams the fit-for-purpose floor runs at — the
    reliability-pipeline ``verified_ready`` seam and BOTH promotion-law doors (the
    core ``ReliabilityEnvelope`` validator and the surface
    ``attach_surface_reliability`` door) — so "unfit for verified" means exactly the
    same thing everywhere and cannot drift. DEMOTE-only: returns the reasons a still-
    ``verified`` result must be lowered to ``qualified-draft`` for; an empty list means
    nothing to cap.

    - ``mode``: the declared task mode (any value; only the literal ``"exploratory"``
      caps — an unset / unknown / evidentiary mode never does).
    - ``criteria``: an iterable of dict-shaped success criteria; a ``critical`` one
      whose ``verifier`` is not an available verifier (:func:`verifier_available`)
      caps.

    NEVER raises: a duck-typed / malformed ``criteria`` field or a malformed criterion
    yields no reason (a broken contract can only WITHHOLD the cap, never spuriously
    demote a genuine ``verified``). This is the fail-open-toward-``verified`` direction
    w.r.t. the cap, which is safe because the cap is itself demote-only.
    """
    reasons: list[str] = []
    try:
        if str(mode or "").strip().lower() == "exploratory":
            reasons.append(_EXPLORATORY_CAP_REASON)
        for item in criteria or ():
            try:
                if not isinstance(item, Mapping) or not bool(item.get("critical", False)):
                    continue
                if not verifier_available(str(item.get("verifier") or "").strip()):
                    reasons.append(
                        "critical success criterion has no available verifier: "
                        f"{str(item.get('text') or '')!r}"
                    )
            except Exception:  # noqa: BLE001,S112 - a malformed criterion never forces a demote
                continue
    except Exception:  # noqa: BLE001 - a duck-typed contract never forces a demote
        return reasons
    return reasons


__all__ = [
    "HUMAN_VERIFIER_PREFIX",
    "effective_task_contract_enforce_mode",
    "method_contract_enabled",
    "task_contract_enforce_mode",
    "task_contract_enforce_scope",
    "task_contract_floor_reasons",
    "verifier_available",
]
