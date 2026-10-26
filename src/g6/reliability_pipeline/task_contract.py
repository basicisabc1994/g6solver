# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""TaskContract v1 value types (K-P0-2) — the fit-for-purpose vocabulary.

This module adds the *task-level* fit-for-purpose vocabulary the assurance
kernel needs so that a ``verified`` means not only "an oracle reproduced the
output" (the promotion law, K-P0-1) but also "the output meets the task's
critical criteria that we can actually check" (fit for purpose, problem 18).

Scope note — task contract vs block contract
--------------------------------------------
``core.block_contract.BlockContract`` is a *per-block* static promise (a
component's declared verification_method / evidence_standard / forbidden_claims
/ escalation_triggers). The objects here are *per-task*: they describe THIS
request's mode and success criteria. TaskContract v1 does not merge the static
per-block contract into the per-task one; it draws the same field *vocabulary*
at task scope. The per-task carrier is
``reliability_pipeline.intake.IntakeContract`` (already threaded through every
pipeline stage as ``task_contract``); this module supplies the value types it
carries.

Staging
-------
Everything here is **additive and inert on its own**: constructing richer
criteria or a mode changes no reliability state until a downstream gate
consumes it (a later, flag-gated slice). Bare acceptance-criteria strings map
to non-critical criteria with no verifier, so the verifier-required rule can
never fire on a legacy caller — the rule only bites a criterion a caller
*explicitly* marked ``critical`` (and even then only once the gate is enabled).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# The shared "available verifier" predicate lives in ``core`` (trust-critical);
# re-exported here so existing callers are unchanged.
from g6.core.task_contract_verifier import (
    HUMAN_VERIFIER_PREFIX,
    verifier_available,
)


class TaskMode(StrEnum):
    """How the deliverable is meant to be judged.

    - ``evidentiary`` (default): the deliverable is a claim/artifact that must
      be *earned* — full verification applies and it may reach ``verified``.
    - ``exploratory``: brainstorm/ideation where verification is not the
      deliverable. Caps at ``qualified-draft`` by construction (a later,
      flag-gated gate enforces the cap); it never *relaxes* a safety gate, only
      lowers the ceiling. Fit-for-purpose is then a declared field, not a vibe.
    """

    EVIDENTIARY = "evidentiary"
    EXPLORATORY = "exploratory"


def coerce_task_mode(value: Any, *, default: TaskMode = TaskMode.EVIDENTIARY) -> TaskMode:
    """Resolve an arbitrary caller value to a :class:`TaskMode`, fail-safe.

    An unset or unrecognized value resolves to ``default`` (``evidentiary``),
    NEVER silently to ``exploratory`` — the relaxed mode must be opted into
    explicitly, so a typo can only make the run stricter, never laxer.
    """
    if isinstance(value, TaskMode):
        return value
    raw = str(value or "").strip().lower()
    if raw == TaskMode.EXPLORATORY.value:
        return TaskMode.EXPLORATORY
    return default


@dataclass(frozen=True)
class SuccessCriterion:
    """One success criterion at task scope.

    - ``text``: the human-readable criterion (e.g. "cites a primary source for
      every numeric claim").
    - ``critical``: whether failing this criterion means the deliverable is not
      fit for purpose. Default ``False`` so a bare acceptance-criteria string
      lifted into a criterion is non-critical (the verifier-required rule never
      fires on it).
    - ``verifier``: how this criterion is checked — the name of a registered
      ``mvp.core.verification`` method, or a ``"human:<level>"`` named
      human-review oracle, or ``""`` (none declared). A *self-assessing LLM* is
      deliberately not a verifier: pure model self-report can never make a
      critical criterion "checkable".
    """

    text: str
    critical: bool = False
    verifier: str = ""

    def has_available_verifier(self) -> bool:
        """True iff this criterion's declared verifier is actually runnable."""
        return verifier_available(self.verifier)


def _coerce_one(item: Any) -> SuccessCriterion | None:
    """Coerce a single criterion (str | Mapping | SuccessCriterion) or drop it."""
    if isinstance(item, SuccessCriterion):
        return item
    if isinstance(item, str):
        text = item.strip()
        return SuccessCriterion(text=text) if text else None
    if isinstance(item, Mapping):
        text = str(item.get("text") or item.get("criterion") or "").strip()
        if not text:
            return None
        return SuccessCriterion(
            text=text,
            critical=bool(item.get("critical", False)),
            verifier=str(item.get("verifier") or "").strip(),
        )
    return None


def derive_criteria(
    acceptance_criteria: Iterable[str] = (),
    structured: Iterable[Any] | None = None,
) -> tuple[SuccessCriterion, ...]:
    """Build the criterion tuple for a task contract, additively.

    When ``structured`` is provided (caller-supplied criteria carrying
    criticality/verifier — as ``SuccessCriterion`` or mappings), it is
    authoritative. Otherwise each plain ``acceptance_criteria`` string is lifted
    into a non-critical, verifier-less :class:`SuccessCriterion` — so legacy
    callers get criteria that never trip the verifier-required rule.
    """
    source: Iterable[Any]
    if structured is not None:
        source = structured
    else:
        source = acceptance_criteria
    out: list[SuccessCriterion] = []
    for item in source:
        criterion = _coerce_one(item)
        if criterion is not None:
            out.append(criterion)
    return tuple(out)


# ── Slice 2: the fit-for-purpose caps ───────────────────────────────────────
# Both caps are DEMOTE-ONLY and OPT-IN (a legacy contract — evidentiary mode,
# non-critical criteria — yields no reasons). Nothing in the library consumes
# them yet; the canonical state machine, never this module, sets a state.


def task_contract_cap_reasons(contract: Any) -> list[str]:
    """Fit-for-purpose reasons a ``verified`` must cap to ``qualified-draft``.

    DEMOTE-ONLY and OPT-IN. Returns ``[]`` for a legacy contract (evidentiary
    mode, non-critical criteria). Two reason classes:

    - **exploratory mode** — verification is not the deliverable, so it caps by
      construction; and
    - a **critical success criterion with no available verifier** — a critical
      thing the system cannot check must not be stamped ``verified``. (Scope,
      honestly: this requires a criterion to be *checkable*, i.e. to name an
      available verifier; it does not itself re-run that verifier — the pipeline's
      existing verification machinery does the checking.)

    Never raises: a missing / duck-typed contract, or a malformed criterion,
    yields no reason — a broken contract can only withhold the cap, never
    spuriously demote a genuine ``verified``.
    """
    if contract is None:
        return []
    reasons: list[str] = []
    mode = getattr(contract, "mode", None)
    if mode == TaskMode.EXPLORATORY or str(mode or "").strip().lower() == TaskMode.EXPLORATORY.value:
        reasons.append(
            "exploratory mode: verification is not the deliverable (caps at qualified-draft)"
        )
    for criterion in getattr(contract, "criteria", ()) or ():
        try:
            critical = bool(getattr(criterion, "critical", False))
            if not critical:
                continue
            available = bool(getattr(criterion, "has_available_verifier", lambda: False)())
            text = str(getattr(criterion, "text", "") or "")
        except Exception:  # noqa: BLE001,S112 - a malformed criterion never forces a demote
            continue
        if not available:
            reasons.append(f"critical success criterion has no available verifier: {text!r}")
    return reasons


def task_contract_human_verifier_audit(contract: Any) -> list[dict[str, str]]:
    """Audit entries for critical criteria cleared by a *self-declared* human verifier.

    A ``human:<level>`` marker makes a critical criterion "available" — it names a
    real, if manual, review — so it CLEARS the verifier-required cap rather than
    tripping it. But unlike a registered programmatic verifier it leaves no
    machine-checkable evidence of its own; it is a *claim* that a human will
    review. This returns one entry per such reliance so a ``verified`` that leaned
    on a self-declared human oracle is legible for audit and quality assurance
    (the founder sign-off condition on the ``human:`` marker). The actual human
    review is enforced elsewhere by the risk-tier HITL gate; this only records
    the reliance, it never demotes.

    Never raises: a missing / non-iterable / duck-typed contract yields ``[]`` and
    a malformed criterion is skipped — an audit record can only be *added*, never
    withheld into a demote (the whole scan is guarded, not just the loop body, so
    even a non-iterable ``criteria`` attribute cannot crash a promotion decision).
    """
    if contract is None:
        return []
    records: list[dict[str, str]] = []
    try:
        for criterion in getattr(contract, "criteria", ()) or ():
            try:
                if not bool(getattr(criterion, "critical", False)):
                    continue
                verifier = str(getattr(criterion, "verifier", "") or "").strip()
                if not verifier.startswith(HUMAN_VERIFIER_PREFIX):
                    continue
                level = verifier[len(HUMAN_VERIFIER_PREFIX):].strip()
                if not level:  # bare ``human:`` is not an available verifier (the cap catches it)
                    continue
                records.append(
                    {
                        "criterion": str(getattr(criterion, "text", "") or ""),
                        "verifier": verifier,
                        "human_level": level,
                    }
                )
            except Exception:  # noqa: BLE001,S112 - a malformed criterion is never an audit obligation
                continue
    except Exception:  # noqa: BLE001 - a non-iterable/duck-typed ``criteria`` attr is never an audit obligation
        return records
    return records


__all__ = [
    "HUMAN_VERIFIER_PREFIX",
    "SuccessCriterion",
    "TaskMode",
    "coerce_task_mode",
    "derive_criteria",
    "task_contract_cap_reasons",
    "task_contract_human_verifier_audit",
    "verifier_available",
]
