# SPDX-License-Identifier: Apache-2.0
"""Cross-surface reliability envelope + the kernel promotion law (K-P0-1, plan A1).

The envelope carries the three canonical reliability states
(``verified`` / ``qualified-draft`` / ``blocked-escalated``) across every
surface. A bare envelope is a ``qualified-draft`` (#135).

The promotion law: ``verified`` stands only when G6's own code re-runs a check
and reproduces it. A proposed ``verified`` keeps its word only when the envelope
carries a ``promotion_witness`` whose oracle is one of G6's own re-checkers
(:data:`_G6_RECHECKERS`) and whose re-check, run again every time an envelope is
built, reproduces the witnessed execution identity. A witness the producer hands
over is only a pointer to what to re-run: its claims (who evaluated, that the
evaluation was independent, any signature) are not read. A door builds its own
witness from the result's content with :func:`door_witness` and never forwards the
producer's. The law is DOWN-only: a missing or failing re-check demotes a proposed
``verified`` to ``qualified-draft``; it never upgrades a weaker state.

The law is always on. There is no switch: in one process any switch is a bypass,
so the old off and shadow words, the per-surface scope and the subject-binding
switch were removed, and no environment variable is read here.

G6 re-checks artifacts with its own code; it does not defend against malicious code
in its own process.

``promotion_witness`` rides on the model's ``extra="allow"`` mechanism instead of
being a declared field, so an envelope without one dumps without the key.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReliabilityLabel = Literal["verified", "qualified-draft", "blocked-escalated"]
_LABEL_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}

#: The warning card code of a ``verified`` the promotion law held at ``qualified-draft``.
PROMOTION_LAW = "G6_E_PROMOTION_LAW"

# The witness core is everything except the execution provenance, the digest
# itself, and an outer signature: a witness whose details were rewritten after it
# was sealed does not promote. (Signatures are not read by the law; the keys stay
# excluded so a signed witness seals the same core.)
_WITNESS_NON_CORE_KEYS = frozenset(
    {"execution", "content_digest", "evaluator_signature", "evaluator_key_id"}
)

#: G6's own re-checkers, by the component whose results they re-check: ``component -> (module, oracle)``. The module
#: is G6 code and exposes ``claim(value) -> (execution_id, details) | None`` (what to re-check, taken from the
#: result's own content) and ``replay(witness) -> promotion_evaluator.ReplayOutcome`` (the re-check itself, which
#: derives the execution identity from what it re-ran, never from the witness). Only an oracle named here can
#: promote; ``promotion_evaluator.register_oracle`` is open to any code and is not read by the law.
_G6_RECHECKERS: dict[str, tuple[str, str]] = {}


def _witness_content_digest(witness: Mapping[str, Any]) -> str:
    """Canonical digest of the witness core; raises ValueError fail-closed."""
    core = {k: v for k, v in witness.items() if k not in _WITNESS_NON_CORE_KEYS}
    try:
        canonical = json.dumps(
            core, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"witness core cannot be canonically serialized: {exc}"
        ) from exc
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _g6_rechecker(oracle: str) -> Callable[[Mapping[str, Any]], Any] | None:
    """The replay of G6's own re-checker named ``oracle``, or None (unknown oracle, or its module failed to load)."""
    for module_name, name in _G6_RECHECKERS.values():
        if oracle and name == oracle:
            try:
                replay = getattr(importlib.import_module(module_name), "replay", None)
            except Exception:  # noqa: BLE001 - a re-checker that cannot load re-checks nothing: never a promotion
                return None
            return replay if callable(replay) else None
    return None


def door_witness(component: str, value: Any, *, task_id: str = "", run_id: str = "") -> dict[str, Any] | None:
    """The witness a door builds for ``component``'s result ``value``, or None.

    G6's re-checker for ``component`` names what to re-check from the result's own
    content (``claim``); a ``promotion_witness`` the result carries is never read.
    None when G6 has no re-checker for the component, or the result names nothing it
    can re-check. Building the witness runs nothing: the law re-runs the check
    whenever an envelope carrying it is built.
    """
    entry = _G6_RECHECKERS.get(str(component or "").strip().lower())
    if entry is None or not isinstance(value, Mapping):
        return None
    module_name, oracle = entry
    try:
        claimed = importlib.import_module(module_name).claim(value)
    except Exception:  # noqa: BLE001 - a result the re-checker cannot read is a result it cannot vouch for
        return None
    if not claimed:
        return None
    execution_id, details = claimed
    try:
        return build_promotion_witness(evaluator_id="g6-door", execution_id=str(execution_id), oracle=oracle,
                                       details=details, task_id=task_id, run_id=run_id)
    except ValueError:
        return None


def promotion_witness_faults(witness: Any, *, task_id: str = "", run_id: str = "") -> list[str]:
    """Every reason ``witness`` does not let a ``verified`` stand (empty list: G6's re-check reproduced it).

    The witness must name one of G6's own re-checkers, carry a witnessed execution
    identity, belong to this envelope's task and run (a witness never transfers
    between runs), and keep the core its ``content_digest`` sealed. Then the
    re-check runs, now, and must reproduce that execution identity.
    """
    if not isinstance(witness, Mapping):
        return [
            "no promotion witness attached - 'verified' stands only when G6's own code re-runs a check and "
            "reproduces it"
        ]
    faults: list[str] = []
    oracle = str(witness.get("oracle") or "")
    replay = _g6_rechecker(oracle)
    if replay is None:
        faults.append(
            f"oracle {oracle!r} is not one of G6's own re-checkers - a witness the producer hands over is ignored"
        )
    execution = witness.get("execution")
    execution_id = (
        str(execution.get("execution_id") or "")
        if isinstance(execution, Mapping) else ""
    )
    if not execution_id:
        faults.append(
            "witness carries no witnessed execution identity - a verdict "
            "without one is 'did not run', never a promotion"
        )
    for field, expected in (("task_id", task_id), ("run_id", run_id)):
        declared = str(witness.get(field) or "")
        if declared and expected and declared != expected:
            faults.append(
                f"witness {field} {declared!r} does not match the envelope "
                f"{field} {expected!r} - a witness never transfers between runs"
            )
    try:
        expected_digest = _witness_content_digest(witness)
    except ValueError as exc:
        faults.append(str(exc))
    else:
        if witness.get("content_digest") != expected_digest:
            faults.append(
                "witness content_digest does not match its sealed core - "
                "doctored verdicts do not promote"
            )
    if faults or replay is None:
        return faults
    from g6.core import promotion_evaluator

    outcome = promotion_evaluator.replay_reproduces(witness, replay=replay)
    if not outcome.reproduced:
        faults.append(f"G6's re-check did not reproduce the result: {outcome.detail}")
    return faults


def build_promotion_witness(
    *,
    evaluator_id: str,
    execution_id: str,
    oracle: str = "",
    verdict: str = "pass",
    task_id: str = "",
    run_id: str = "",
    witnessed_at: str = "",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal a promotion witness: what to re-check (``oracle`` and ``details``) and the
    execution identity the re-check must reproduce.

    Anyone can build one, so it carries no authority of its own: it promotes only
    when ``oracle`` is one of G6's own re-checkers and that re-check reproduces
    ``execution_id`` (:func:`promotion_witness_faults`). ``witnessed_at`` is
    caller-supplied (no wall-clock coupling).
    """
    if not str(evaluator_id or "").strip():
        raise ValueError("evaluator_id must be non-empty")
    if not str(execution_id or "").strip():
        raise ValueError(
            "execution_id must be non-empty - a verdict without a witnessed "
            "execution identity is 'did not run'"
        )
    witness: dict[str, Any] = {
        "evaluator": {"agent_id": str(evaluator_id), "role": "evaluator"},
        "independent_evaluation": True,
        "oracle": str(oracle or ""),
        "verdict": str(verdict or ""),
        "details": dict(details or {}),
        "execution": {"execution_id": str(execution_id)},
    }
    if task_id:
        witness["task_id"] = str(task_id)
    if run_id:
        witness["run_id"] = str(run_id)
    if witnessed_at:
        witness["witnessed_at"] = str(witnessed_at)
    witness["content_digest"] = _witness_content_digest(witness)
    return witness


def _evidence_line(e: Any) -> str:
    """Render ONE evidence item as one human-readable line.

    Review finding F3 on #234. `evidence_collected` is typed list[str] and is
    documented human-readable, but the old expression fell through to `str(e)`
    for a mapping with no summary/note/source -- which, once structured evidence
    stopped being destroyed upstream, meant a PYTHON REPR in a customer-facing
    field (measured 501 chars on one real certificate; 102,012 in a synthetic
    worst case). And when `source` sat alongside other keys only the source was
    rendered, silently HIDING its siblings, which is worse than verbose.

    ⚠️ CLAIM CORRECTED (review round 2). The commit that introduced this said
    "the single-key `{"source": ...}` shape is preserved byte-for-byte so no
    existing output is rewritten." The first half is true; **the second half is
    false and I should not have written it.** `{"source": U, "score": 0.9}` --
    the canonical grounding shape -- rendered as bare `U` before and renders as
    JSON now. That is the INTENDED fix (the old form hid `score` entirely), but
    it IS a change to output someone may depend on, and saying otherwise
    described the change as safer than it is. Pinned in
    `tests/mvp/core/test_evidence_redaction.py` so the behaviour is asserted
    rather than asserted-about.
    """
    if not isinstance(e, dict):
        return str(e)
    for key in ("summary", "note"):
        if e.get(key):
            return str(e[key])
    if list(e) == ["source"] and e.get("source"):
        return str(e["source"])
    try:
        return json.dumps(e, default=str, sort_keys=True)
    except Exception:
        return str(e)


class WarningCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str = ""
    suggested_action: str = "Inspect the warning and retry with corrected inputs if needed."
    # INV-ENV-2: a warning card carries a severity (green/amber/red/blocked) + why + risks
    # + a verified flag + a next step. ``why`` / ``next_step`` mirror message /
    # suggested_action when a producer does not set them explicitly.
    severity: str = "amber"
    why: str = ""
    risks: list[str] = Field(default_factory=list)
    verified: bool = False
    next_step: str = ""

    @model_validator(mode="after")
    def _mirror_message_and_action(self) -> "WarningCard":
        """``why`` and ``next_step`` default to message / suggested_action so a card is
        self-consistent however it was built."""
        if not self.why:
            self.why = self.message
        if not self.next_step:
            self.next_step = self.suggested_action
        return self


class EvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = ""
    summary: str = ""


class ReliabilityEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    completion_state: str = "qualified-draft"
    reliability_label: ReliabilityLabel = "qualified-draft"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    egress_notice: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence_entries(cls, value: Any) -> Any:
        """Components legitimately emit string evidence items; wrap them
        instead of rejecting the whole invocation (nav_delegate broke on
        meta_programming's 'node_counted' entry)."""
        if isinstance(value, list):
            return [v if isinstance(v, dict) else {"note": str(v)} for v in value]
        return value
    request_id: str = ""
    task_id: str = ""
    run_id: str = ""
    # #135: this default is inert, and deliberately left alone. `_reconcile_
    # reliability_state` recomputes `degraded = weakest != "verified"` on every
    # construction, so with the label default now 'qualified-draft' an envelope
    # built with no arguments reports degraded=True without needing a new validator.
    degraded: bool = False
    degradation_reason: str = ""
    # INV-STATE-2: a 'qualified-draft' MUST surface its limitations, the checks it did
    # NOT run, recommended next steps, a risk level and a confidence (None when nothing measured one). INV-STATE-3: a
    # 'blocked-escalated' MUST surface why it blocked, the human decision required, the
    # evidence collected, a safe next action and the rollback/containment status. These
    # are guaranteed (auto-derived when a producer omits them) by the model validator
    # below, so the envelope NEVER silently omits them for a non-verified state.
    known_limitations: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    risk_level: str = ""
    confidence: float | None = None
    block_reason: str = ""
    required_human_decision: str = ""
    evidence_collected: list[str] = Field(default_factory=list)
    safe_next_action: str = ""
    rollback_or_containment: str = ""

    @model_validator(mode="after")
    def _enforce_state_required_fields(self) -> "ReliabilityEnvelope":
        """INV-STATE-2/3 (full ratification): guarantee the spec-mandated fields are
        present for the two non-verified states, deriving honest defaults from the
        warning_card / evidence / degradation_reason already on the envelope when a
        producer did not supply them. The fields are therefore ALWAYS present for a
        qualified-draft / blocked-escalated, so a downstream client can always read the
        limitations or the required human decision. A 'verified' envelope is unaffected."""
        self._reconcile_reliability_state()

        wc = self.warning_card or {}
        wc_msg = str(wc.get("message") or wc.get("why") or "").strip()
        wc_next = str(wc.get("suggested_action") or wc.get("next_step") or "").strip()
        wc_risks = wc.get("risks") if isinstance(wc.get("risks"), list) else []
        reason = (self.degradation_reason or "").strip()

        if self.reliability_label == "qualified-draft":
            if not self.known_limitations:
                self.known_limitations = (
                    [str(r) for r in wc_risks] if wc_risks
                    else [wc_msg] if wc_msg
                    else [reason] if reason
                    else ["Qualified draft: this output has not been independently verified."]
                )
            if not self.missing_checks:
                self.missing_checks = ["Independent verification was not completed for this output."]
            if not self.recommended_next_steps:
                self.recommended_next_steps = (
                    [wc_next] if wc_next
                    else ["Review the warning card and verify this output before relying on it."]
                )
            if not self.risk_level:
                self.risk_level = "medium"
            # confidence stays None unless a producer measured one: a filled-in constant would read as a measurement
        elif self.reliability_label == "blocked-escalated":
            if not self.block_reason:
                self.block_reason = (
                    wc_msg or reason
                    or "Work was blocked before completion; a human decision is required."
                )
            if not self.required_human_decision:
                self.required_human_decision = (
                    wc_next or "A human must review the block and decide how to proceed."
                )
            if not self.evidence_collected:
                self.evidence_collected = [
                    _evidence_line(e) for e in (self.evidence or [])
                ]
            if not self.safe_next_action:
                self.safe_next_action = (
                    wc_next or "Do not proceed automatically; escalate to a human reviewer."
                )
            if not self.rollback_or_containment:
                self.rollback_or_containment = (
                    "The operation was blocked before any state change; no rollback is required."
                )

        # INV-ENV-2: when a warning_card is present, guarantee it carries the documented
        # structure (severity green/amber/red/blocked + why + risks + verified + next step),
        # deriving from the existing card and the state fields above. A producer's richer
        # values are preserved; only missing keys are filled.
        if isinstance(self.warning_card, dict):
            wc2 = dict(self.warning_card)
            _sev = {"verified": "green", "qualified-draft": "amber", "blocked-escalated": "blocked"}
            if not wc2.get("severity"):
                wc2["severity"] = _sev.get(self.reliability_label, "amber")
            if not wc2.get("why"):
                wc2["why"] = (
                    wc2.get("message") or self.block_reason
                    or (self.known_limitations[0] if self.known_limitations else "")
                )
            if not wc2.get("risks"):
                wc2["risks"] = (
                    list(self.known_limitations) if self.known_limitations
                    else ([self.block_reason] if self.block_reason else [])
                )
            # The card's ``verified`` flag is a derived echo of the (already
            # reconciled) envelope state, never an independent input: a verified
            # envelope's card reads True, any non-verified state reads False.
            wc2["verified"] = self.reliability_label == "verified"
            if not wc2.get("next_step"):
                wc2["next_step"] = (
                    wc2.get("suggested_action")
                    or (self.recommended_next_steps[0] if self.recommended_next_steps else self.safe_next_action)
                )
            self.warning_card = wc2

        return self

    def _reconcile_reliability_state(self) -> None:
        """Keep the two public reliability state fields on the weakest honest label.

        Producers can populate ``completion_state`` and ``reliability_label``
        independently. If one is degraded, the other cannot remain ``verified``;
        likewise explicit degradation signals cap the envelope at
        ``qualified-draft``.

        #135: a clean DEFAULT envelope is no longer ``verified``. The field
        defaults are now ``qualified-draft``, so ``verified`` is something a
        producer must state outright rather than something it receives by saying
        nothing. A stated ``verified`` still has to survive the promotion law
        (``_apply_promotion_law``) exactly as before — this removes the free ride,
        it does not weaken the gate.

        The warning_card ``verified`` flag is intentionally NOT a degradation
        signal here: per INV-ENV-2 that flag is a *derived echo* of the envelope
        state (set later in ``_enforce_state_required_fields``), and its model
        default is ``False``. A legitimate ``verified`` envelope is allowed to
        carry a green/amber warning card, so keying off it would wrongly
        downgrade verified results across the product.
        """
        candidates = [self.reliability_label]
        if self.completion_state in _LABEL_RANK:
            candidates.append(self.completion_state)
        if self.degraded or self.degradation_reason.strip():
            candidates.append("qualified-draft")
        weakest = max(candidates, key=lambda label: _LABEL_RANK.get(label, 1))
        if weakest == "verified":
            weakest = self._apply_promotion_law()
        self.reliability_label = weakest  # type: ignore[assignment]
        self.completion_state = weakest
        self.degraded = weakest != "verified"

    def _apply_promotion_law(self) -> str:
        """K-P0-1: a proposed 'verified' stands only when G6's own re-check of
        the attached witness reproduces it (module docstring). DOWN-only by
        construction: this method is only reached when the reconciled state is
        'verified', so it can demote but never upgrade. Runs inside the model
        validator, i.e. at EVERY door an envelope can be built through, and
        re-runs the check each time. Returns the state that stands.
        """
        faults = promotion_witness_faults(
            getattr(self, "promotion_witness", None),
            task_id=self.task_id,
            run_id=self.run_id,
        )
        if not faults:
            return "verified"
        # Demote with the honest reason surfaced; the INV-STATE-2 machinery in
        # the model validator fills known_limitations / recommended_next_steps.
        if not self.degradation_reason.strip():
            self.degradation_reason = "kernel promotion law: " + "; ".join(faults)
        note = (
            "G6 did not re-run a check that reproduces this result, so its "
            "'verified' does not stand (the kernel promotion law)."
        )
        if note not in self.missing_checks:
            self.missing_checks.append(note)
        # The held envelope keeps what was claimed and, when the producer gave no card, says why it was held.
        self.claimed_completion_state = "verified"
        if self.warning_card is None:
            self.warning_card = warning_card(
                PROMOTION_LAW,
                "It was reported verified, but G6 did not re-run a check that reproduces it, so it is held at "
                "qualified-draft.",
                "Treat it as a qualified draft: check it yourself, or use a component whose results G6 re-checks.",
            )
        return "qualified-draft"


def warning_card(code: str, message: str, suggested_action: str | None = None) -> dict[str, Any]:
    card = WarningCard(
        code=code,
        message=message,
        suggested_action=suggested_action
        or "Inspect the degraded invocation envelope before trusting the result.",
    )
    return card.model_dump()


def envelope_fields(
    *,
    reliability_label: ReliabilityLabel = "qualified-draft",
    warning_card: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    request_id: str = "",
    task_id: str = "",
    run_id: str = "",
    degradation_reason: str = "",
    egress_notice: list[dict[str, Any]] | None = None,
    promotion_witness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # The witness is attached only when present so an envelope without one
    # dumps byte-identically to the pre-law shape (see module docstring).
    extra: dict[str, Any] = {}
    if promotion_witness is not None:
        extra["promotion_witness"] = promotion_witness
    envelope = ReliabilityEnvelope(
        completion_state=reliability_label,
        reliability_label=reliability_label,
        warning_card=warning_card,
        evidence=evidence or [],
        egress_notice=egress_notice or [],
        request_id=request_id,
        task_id=task_id,
        run_id=run_id,
        degraded=reliability_label != "verified",
        degradation_reason=degradation_reason,
        **extra,
    )
    return envelope.model_dump()


__all__ = [
    "PROMOTION_LAW",
    "EvidenceEntry",
    "ReliabilityEnvelope",
    "ReliabilityLabel",
    "WarningCard",
    "build_promotion_witness",
    "door_witness",
    "envelope_fields",
    "promotion_witness_faults",
    "warning_card",
]
