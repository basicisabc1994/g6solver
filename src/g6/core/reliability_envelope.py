# SPDX-License-Identifier: Apache-2.0
"""Cross-surface reliability envelope + the kernel promotion law (K-P0-1).

The envelope carries the three canonical reliability states
(``verified`` / ``qualified-draft`` / ``blocked-escalated``) across every
surface. Historically it was fail-open: a bare envelope defaulted to
``verified`` and any producer could self-assign that word.

The kernel promotion law (ported from the science_harness S3
status-authority law: "a generator structurally cannot promote its own
theory") closes that gap: under the law, ``verified`` stands only when the
envelope carries a ``promotion_witness`` — an evaluator-stamped,
digest-sealed record of an oracle execution (test run, formal check, or
human decision). The law is DOWN-only: a missing/invalid witness demotes a
proposed ``verified`` to ``qualified-draft``; a witness never upgrades a
weaker state. Producers propose, the evaluator seam disposes.

Staged via ``G6_KERNEL_PROMOTION_LAW`` (D1-W4 dark-wire pattern):

- unset / ``0`` / ``off`` / ``false`` / ``no`` — OFF (default): behavior is
  byte-identical to the pre-law envelope.
- ``shadow`` / ``observe`` — compute the verdict, log the would-demote
  (A7 observe-only pattern), apply nothing.
- any other non-empty value — ENFORCE. Unknown words fail toward the
  strictest mode, never silently off (the G6_DISABLE_LLM lesson).

``promotion_witness`` intentionally rides on the model's ``extra="allow"``
mechanism instead of being a declared field, so an envelope without one
dumps byte-identically to before the law landed (golden/parity suites see
no change while the flag is OFF).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReliabilityLabel = Literal["verified", "qualified-draft", "blocked-escalated"]
_LABEL_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}

_LOGGER = logging.getLogger(__name__)

_LAW_OFF_WORDS = frozenset({"", "0", "off", "false", "no"})
_LAW_SHADOW_WORDS = frozenset({"shadow", "observe", "observe-only", "log"})

# Safe-path S4: ``G6_PROMOTION_ENFORCE_SURFACES`` scopes the law's ``enforce``
# mode to named surface components so it can be enabled one cohort at a time with
# instant rollback (unset the var → global). A wildcard entry means "no scoping".
_ENFORCE_SCOPE_ENV = "G6_PROMOTION_ENFORCE_SURFACES"
_ENFORCE_SCOPE_WILDCARDS = frozenset({"*", "all", "__all__"})
# The witness core is everything except the execution provenance, the digest
# itself, and the evaluator's outer signature (the hidden_evaluator sealing
# rule): a doctored verdict with an intact shape but rewritten content must not
# promote. The evaluator signature/key_id (K-P0-4 structural seam) are applied
# OVER the sealed digest, so they are excluded from the core the digest seals —
# signing must not change the content_digest.
_WITNESS_NON_CORE_KEYS = frozenset(
    {"execution", "content_digest", "evaluator_signature", "evaluator_key_id"}
)


def promotion_law_mode() -> str:
    """Resolve ``G6_KERNEL_PROMOTION_LAW`` to ``off`` / ``shadow`` / ``enforce``.

    Recognized off-words and shadow-words are honored; ANY other non-empty
    value enforces — a garbled value must fail toward the strictest mode,
    never silently disable the law.
    """
    raw = (os.environ.get("G6_KERNEL_PROMOTION_LAW") or "").strip().lower()
    if raw in _LAW_OFF_WORDS:
        return "off"
    if raw in _LAW_SHADOW_WORDS:
        return "shadow"
    return "enforce"


def promotion_enforce_scope() -> frozenset[str] | None:
    """Resolve ``G6_PROMOTION_ENFORCE_SURFACES`` — the per-surface enforce scope
    (safe-path S4: gradual per-surface enable).

    Returns:
      - ``None`` when the var is unset/empty, or names a wildcard (``*`` / ``all``):
        ``enforce`` is UNSCOPED (global) — byte-identical to the pre-S4 law.
      - a ``frozenset`` of surface component names otherwise: under ``enforce`` the
        law demotes ONLY those surfaces; every other surface — and the
        identity-less core door — degrades to ``shadow`` (would-demote logged,
        nothing applied).

    Scoping can only NARROW enforce, never widen it: a scoped set demotes a strict
    subset of what global enforce would, so enabling a cohort is always fail-safe.
    Rollback is instant — unset the var (→ global) or set the law ``off``.
    """
    raw = os.environ.get(_ENFORCE_SCOPE_ENV)
    if not raw:
        return None
    # Case-insensitive, mirroring promotion_law_mode's own .strip().lower(): a
    # wildcard such as "ALL" is honored, and cohort names match regardless of case.
    names = {part.strip().lower() for part in raw.split(",")}
    names.discard("")
    if not names or names & _ENFORCE_SCOPE_WILDCARDS:
        return None
    return frozenset(names)


def effective_promotion_law_mode(component: str | None = None) -> str:
    """The promotion-law mode in effect for ``component`` (safe-path S4 scoping).

    ``off`` / ``shadow`` are global and returned unchanged. ``enforce`` is filtered
    through :func:`promotion_enforce_scope`: an unscoped set enforces everywhere
    (today's behavior); a scoped set enforces only for a listed component and
    returns ``shadow`` for anything else. The core door has no component identity
    (``component is None``), so under a scoped set it always returns ``shadow`` —
    the product path participates in enforce only via the global/wildcard setting
    (safe-path S5), never a per-surface cohort.
    """
    mode = promotion_law_mode()
    if mode != "enforce":
        return mode
    scope = promotion_enforce_scope()
    if scope is None:
        return "enforce"
    if component and component.strip().lower() in scope:
        return "enforce"
    return "shadow"


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


# ── C1: subject binding (does the witness certify THIS output?) ─────────────
# The checks below prove a witness is well-formed, evaluator-signed, and that its
# oracle independently reproduced. They do NOT, by themselves, prove it certifies
# the output it is attached to: a witness genuinely EARNED for output A promotes a
# DIFFERENT output B when lifted onto B's payload (measured — see
# ``.planning/kernel/kp0_c1_witness_binding_probe.py``, where a wrong implementation
# that FAILS the witnessed gold tests was promoted on the correct one's witness).
#
# A component closes that hole by registering a SUBJECT BINDER: given the surface
# result, its params, and the (signed) witness, recompute the execution identity the
# RESULT implies. The law then requires it to equal the witness's signed
# ``execution_id``. Because the identity is content-addressed and the witness core is
# digest-sealed under the evaluator signature, an attacker cannot restate either side.
_SubjectBinder = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], str]
_SUBJECT_BINDERS: dict[str, _SubjectBinder] = {}

# Strict mode: treat "this component registered no binder" as a fault, so a surface
# whose binding cannot be confirmed does not promote. OFF by default (staged like
# every other law control) because it would demote every verified from a surface that
# has not yet registered a binder; enable per the C1 runbook after a shadow run.
_REQUIRE_SUBJECT_BINDING_ENV = "G6_PROMOTION_REQUIRE_SUBJECT_BINDING"


def register_subject_binder(component: str, binder: _SubjectBinder) -> None:
    """Register the subject binder for a surface ``component`` (C1).

    LOAD-BEARING CONTRACT (the binding is only as strong as this): the binder MUST
    RECOMPUTE the identity from the RESULT's own promoted artifact. Two ways to make
    the check vacuous, both forbidden:

    - never echo ``witness["execution"]["execution_id"]`` (the witnessed id itself);
    - never read a SELF-DECLARED identity field FROM THE RESULT (e.g. a
      ``result["artifact_hash"]`` the producer wrote). The producer controls that
      field, so an attacker sets it to the witnessed id and the check passes for any
      output. Derive the identity from the promoted CONTENT (the code, the answer,
      the claims), then hash it — do not trust a producer-stamped identity.

    Reading non-artifact material (e.g. the gold tests, the source corpus) from the
    witness is fine: the witness core is digest-sealed under the evaluator signature,
    so those bytes cannot be restated, and any change to the promoted artifact still
    yields a different identity. The binding is therefore only as strong as the
    evaluator signature — with NO evaluator authority configured, an attacker can
    rebuild the whole witness (capsule included) so the recomputed id matches, and
    the binding adds nothing (it rides the same signature precondition as the rest of
    the promotion law). Return ``""`` when the identity cannot be derived — the law
    treats that as "unknown" (a fault only under strict mode), never as a match; so a
    payload that drops/blanks the bound artifact field is "unknown", NOT bound (close
    that with strict mode, not by trusting the blank). Idempotent: a later
    registration for the same component replaces the earlier one.
    """
    if not str(component or "").strip():
        raise ValueError("component is required")
    if not callable(binder):
        raise TypeError("binder must be callable")
    _SUBJECT_BINDERS[component.strip().lower()] = binder


def subject_binder_registered(component: str) -> bool:
    """True when a subject binder is registered for ``component``."""
    return str(component or "").strip().lower() in _SUBJECT_BINDERS


# Deterministic binder registration. A subject binder registers as an IMPORT SIDE
# EFFECT of its component's promotion module (``coder.promotion_replay.register()``).
# A process — or a door — that never imported that module would compute "unknown" for
# a genuinely bindable output and, with strict mode off, let a TRANSFERRED witness
# promote (the C1 review's finding 1: the binding was void until something happened to
# import the module). So the law BOOTSTRAPS the binder: the first
# ``subject_execution_id`` call for a known component imports its module AND invokes
# its idempotent ``register()`` — registering the binder wherever the law runs, the
# SAME best-effort discipline as ``promotion_custody``'s ``G6_PROMOTION_ORACLE_MODULES``
# oracle bootstrap. Each mapped module MUST expose an idempotent ``register()``
# (``coder.promotion_replay``, ``ctx_rag.promotion``, ``self_model.promotion_replay`` and
# ``template_synthesis.promotion_replay`` do): a plain re-import of an already-cached
# module does NOT re-run its top-level side
# effects, so relying on the import alone would leave the binding void in exactly the
# "module was imported before the registry was reset" case. Only components that actually
# register a binder appear here. An unknown component or a failed import/register leaves
# the binding "unknown", which never matches a witness (fail-closed).
_BINDER_BOOTSTRAP_MODULES: dict[str, str] = {
    "coder": "g6.coder.promotion_replay",
    "ctx_rag": "g6.ctx_rag.promotion",
    "self_model": "g6.self_model.promotion_replay",
    # template_synthesis binds under both its surface-door name and the block's own
    # ``name`` alias; both map to the module whose ``register()`` wires both.
    "template_synthesis": "g6.template_synthesis.promotion_replay",
    "template_synthesis_mcp": "g6.template_synthesis.promotion_replay",
}


def ensure_subject_binder(component: str) -> None:
    """Import + register ``component``'s binder so it is present wherever the law runs.

    Idempotent and cheap after the first call (a registered component short-circuits
    on a dict lookup). Never raises: a failed import/register leaves the binding
    "unknown", which the law treats as un-promotable under strict mode and as the
    pre-C1 baseline otherwise — it can never turn "unknown" into a spurious match. A
    component already registered (e.g. a test's custom binder) short-circuits before
    the import, so the bootstrap never clobbers a deliberately-installed binder.
    """
    key = str(component or "").strip().lower()
    if not key or key in _SUBJECT_BINDERS:
        return
    module_name = _BINDER_BOOTSTRAP_MODULES.get(key)
    if module_name is None:
        return
    try:
        module = importlib.import_module(module_name)
        # The import side effect registers the binder on FIRST import; if the module
        # was already imported (cached), that side effect did not re-run, so invoke its
        # idempotent register() explicitly. Skip when the import already registered it.
        if key not in _SUBJECT_BINDERS:
            register = getattr(module, "register", None)
            if callable(register):
                register()
    except Exception:
        # Best-effort bootstrap: an unregistered binder yields "unknown" (never a
        # match), so a failed import/register cannot forge a promotion. Logged at
        # debug so a misconfigured bootstrap is diagnosable without adding noise.
        _LOGGER.debug("subject binder bootstrap failed for %r", key, exc_info=True)


def require_subject_binding() -> bool:
    """True when an unbindable ``verified`` must not promote (strict mode, off by default)."""
    raw = (os.environ.get(_REQUIRE_SUBJECT_BINDING_ENV) or "").strip().lower()
    return raw not in _LAW_OFF_WORDS


def subject_execution_id(
    component: str, result: Any, params: Any = None, witness: Any = None
) -> str:
    """The execution identity ``result`` implies, via its component's binder.

    Returns ``""`` when no binder is registered, when the inputs are not mappings,
    or when the binder declines/raises — fail-closed: an empty id never matches a
    witness, so a broken binder can only withhold a promotion, never grant one.
    """
    ensure_subject_binder(component)  # deterministic registration at the door
    binder = _SUBJECT_BINDERS.get(str(component or "").strip().lower())
    if binder is None or not isinstance(result, Mapping):
        return ""
    try:
        return str(
            binder(
                result,
                params if isinstance(params, Mapping) else {},
                witness if isinstance(witness, Mapping) else {},
            )
            or ""
        )
    except Exception:  # noqa: BLE001 - a binder is untrusted component code; ANY
        # failure means "identity unknown", which must never read as a match.
        return ""


def promotion_witness_faults(
    witness: Any,
    *,
    task_id: str = "",
    run_id: str = "",
    subject_execution_id: str = "",
    subject_binding_required: bool = False,
) -> list[str]:
    """Every law violation in ``witness`` (empty list == a valid witness).

    Mirrors ledger._check_status_authority: separated evaluator role,
    independent evaluation, witnessed execution identity, and a
    content_digest that seals the verdict core. ``task_id``/``run_id`` are
    the envelope's own identity — a witness declaring a different one is a
    replay from another run and never transfers.

    ``subject_execution_id`` (C1) is the identity the PROMOTED OUTPUT implies, as
    recomputed by the component's registered subject binder. When non-empty it must
    equal the witness's witnessed execution identity, so a witness earned for a
    different artifact does not transfer onto this one. When empty the binding is
    unknown: a fault only if ``subject_binding_required`` (strict mode).
    """
    if not isinstance(witness, Mapping):
        return [
            (
                "no promotion witness attached - 'verified' requires an "
                "evaluator-stamped oracle witness"
            )
        ]
    faults: list[str] = []
    evaluator = witness.get("evaluator")
    role = evaluator.get("role") if isinstance(evaluator, Mapping) else None
    if role != "evaluator":
        faults.append(
            f"witness role {role!r} is not the separated 'evaluator' role "
            "(S3 status-authority law) and may not assert 'verified'"
        )
    if witness.get("independent_evaluation") is not True:
        faults.append(
            "witness does not assert independent_evaluation: true - a "
            "generator's own evaluation never promotes"
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
    # C1 subject binding: the witness must certify THIS output, not merely SOME
    # output. Without this, a witness genuinely earned for another artifact
    # promotes whatever payload it is attached to.
    if subject_execution_id:
        if subject_execution_id != execution_id:
            faults.append(
                f"witness certifies execution identity {execution_id!r}, but the "
                f"promoted output implies {subject_execution_id!r} - a witness "
                "earned for a different artifact does not transfer"
            )
    elif subject_binding_required:
        faults.append(
            "no subject binding available for the promoted output - the witness "
            "cannot be confirmed to certify THIS artifact "
            f"({_REQUIRE_SUBJECT_BINDING_ENV} is enabled)"
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
    # Evaluator-authority signature (K-P0-4 structural seam): when an evaluator
    # authority is configured, a promotable witness must be signed by it. A
    # self-minted witness (structurally perfect but unsigned, or signed with a
    # key the generator forged) does not promote, because the generator does not
    # hold the evaluator key. With no authority configured this is a no-op —
    # the structural checks above stand alone and behavior is byte-identical for
    # callers that have not opted into an evaluator key.
    from g6.core import promotion_evaluator
    if promotion_evaluator.evaluator_configured() and not promotion_evaluator.verify(
        witness
    ):
        faults.append(
            "witness is not signed by the evaluator authority - a self-minted "
            "witness does not promote when an evaluator key is configured (the "
            "generator does not hold the signing key)"
        )
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
    """Seal a promotion witness for an evaluator seam (K-P0-4 wiring).

    ``witnessed_at`` is caller-supplied (no wall-clock coupling — the
    ledger's determinism rule). The builder asserts the evaluator role; the
    structural maker≠checker separation (subprocess / separate identity) is
    the calling seam's obligation, exactly as in science_harness.
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
        """K-P0-1: a proposed 'verified' stands only with a valid evaluator
        witness (module docstring). DOWN-only by construction — this method
        is only reached when the reconciled state is 'verified', so it can
        demote but never upgrade. Runs inside the model validator, i.e. at
        EVERY door an envelope can be built through (the D2S2 parity lesson:
        enforce at both doors). Returns the state that stands.
        """
        # Safe-path S4: the core door has no surface identity, so under a scoped
        # enforce (G6_PROMOTION_ENFORCE_SURFACES set) it resolves to shadow — the
        # product path enforces only via a global/wildcard setting (S5).
        mode = effective_promotion_law_mode(None)
        if mode == "off":
            return "verified"
        faults = promotion_witness_faults(
            getattr(self, "promotion_witness", None),
            task_id=self.task_id,
            run_id=self.run_id,
        )
        if not faults:
            return "verified"
        if mode == "shadow":
            # A7 observe-only pattern: compute the verdict, log it, apply
            # nothing. The envelope bytes stay untouched in shadow mode.
            payload = {
                "mode": "shadow",
                "would_demote_to": "qualified-draft",
                "faults": faults,
                "request_id": self.request_id,
                "task_id": self.task_id,
                "run_id": self.run_id,
            }
            if promotion_law_mode() == "enforce":
                # Enforce is on globally but this door is scoped out (S4).
                payload["enforce_scoped_out"] = True
            _LOGGER.warning(
                "G6_KERNEL_PROMOTION_LAW shadow would-demote (NOT applied): %s",
                json.dumps(payload, sort_keys=True),
            )
            return "verified"
        # enforce: demote with the honest reason surfaced; the INV-STATE-2
        # machinery in the model validator fills known_limitations /
        # recommended_next_steps from these seeds.
        if not self.degradation_reason.strip():
            self.degradation_reason = "kernel promotion law: " + "; ".join(faults)
        note = (
            "Evaluator-witnessed oracle evidence required for 'verified' by "
            "the kernel promotion law (G6_KERNEL_PROMOTION_LAW) was not "
            "presented."
        )
        if note not in self.missing_checks:
            self.missing_checks.append(note)
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
    "EvidenceEntry",
    "ReliabilityEnvelope",
    "ReliabilityLabel",
    "WarningCard",
    "build_promotion_witness",
    "effective_promotion_law_mode",
    "ensure_subject_binder",
    "envelope_fields",
    "promotion_enforce_scope",
    "promotion_law_mode",
    "promotion_witness_faults",
    "register_subject_binder",
    "require_subject_binding",
    "subject_binder_registered",
    "subject_execution_id",
    "warning_card",
]
