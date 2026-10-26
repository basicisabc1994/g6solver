# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""
approval_authority — the server-side record of who approved what.

WHY THIS EXISTS
---------------
Human approval and "verified" were, across every path in this codebase, asserted
by the party being approved:

* ``job_block._preflight_hitl`` gated regulated operations (pharma, medical,
  legal, tax, finance, cyber-ops) on ``context.hitl_approved`` — a plain boolean
  the caller sets. The refusal message even told the caller which flag to flip.
* ``job_flagship_orchestrator`` / ``job_skill_orchestrator`` derive that boolean
  straight from ``request.inputs["hitl_approved"]`` — the customer's own request
  body.
* ``_caller_verified_is_earned`` accepted any ``verification_report`` dict whose
  five keys were truthy, with no provenance binding.
* ``core.validation_gates`` registered every named gate — INCLUDING
  ``"human_approval"`` — as a no-op returning True.
* Even ``_has_human_approval`` only tested that ``approval_id`` was *truthy*.

None of those is an approval. They are the subject of the decision reporting its
own verdict. Under the per-tenant architecture the caller may be an UNTRUSTED
tenant worker running customer-supplied code, so "the caller says it was
approved" is exactly the threat model, not a control.

THE INVARIANT THIS MODULE ENFORCES
----------------------------------
An approval is a RECORD THE CONTROL PLANE ISSUED, and the only thing a caller can
do is *present its identifier*. Approval identifiers are unguessable
(``secrets.token_urlsafe``), so a fabricated id does not resolve. Verification is
resolution against server-side state — never inspection of caller-supplied data.

Fail-closed at every step: an unknown, expired, revoked, already-consumed,
wrong-scope, wrong-run, or non-approved record yields ``False``. There is no
input that makes ``check()`` return True without a matching issued record.

TRUST BOUNDARY
--------------
``issue()`` mints authority and is CONTROL-PLANE ONLY. It must never be reachable
from a tenant worker or from any generic/untrusted invoke surface — a tenant able
to issue its own approvals would defeat the entire mechanism. The store is
deliberately process-local-and-server-side; it is NOT routed through the
per-tenant connection seam (see the P2-B classification: authority stores stay
control-plane, never worker-side).

SCOPE BINDING
-------------
An approval is minted for a specific ``scope`` string and optionally pinned to
one ``run_id``. For a job agent the scope is
``approval_scope_for_operation(agent, operation)``, and the agent half is the
BARE domain: ``"job:lawyer:file_motion"``, NOT ``"job:job_lawyer:file_motion"``.
This example used to show the prefixed form, which no minting surface should
copy — the gate normalises the block name through
``hitl_policies.policy_key`` before checking, so a prefixed scope would never
match and the approval would be silently unusable. This
stops replay: an approval for a low-risk operation cannot be presented to
authorize a different, higher-risk one, and a single-use approval cannot be
replayed across runs.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Approval ids are compared with a constant-time helper and must be unguessable.
_TOKEN_BYTES = 32

#: Default lifetime of an issued approval. Human review is a bounded event; an
#: approval that lives forever becomes a standing bypass.
_DEFAULT_TTL_SECONDS = 24 * 60 * 60


class ApprovalError(RuntimeError):
    """Raised when an approval cannot be issued (never when one merely fails to verify)."""


#: An approval minted because a PERSON decided.
ISSUER_HUMAN = "human"
#: An approval minted because the caller's OWN vetted standard clearly permitted it.
ISSUER_POLICY_ORACLE = "policy_oracle"
ISSUER_KINDS = (ISSUER_HUMAN, ISSUER_POLICY_ORACLE)


def disclosure_digest(text: str) -> str:
    """Stable digest of a disclosure notice, or ``""`` when there is none.

    Domain-separated so a digest from this namespace cannot be confused with one
    computed over the same bytes somewhere else.

    Returns ``""`` for a blank disclosure rather than the digest of the empty
    string, so an audit row for a record that HAS no disclosure shows no digest.
    A record with no disclosure and a plausible-looking hash beside it would
    invite exactly the wrong reading — that something was shown.
    """
    text = text or ""
    if not text.strip():
        return ""
    h = hashlib.sha256()
    h.update(b"g6-approval-disclosure-v1\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class ApprovalRecord:
    """One approval decision, recorded server-side.

    NOT necessarily a human one — see :data:`ISSUER_KINDS`. This docstring used to
    say "one human decision", which was true when a person was the only possible
    issuer and became false the moment the policy oracle could mint one.

    ``issuer_kind`` exists because the two are NOT interchangeable downstream:

    * for CORROBORATION both count — both are server-issued, control-plane-minted
      evidence, which is the founder's basis 3 and the whole reason the oracle
      reuses this authority instead of adding a fourth basis;
    * for a ``human_review_passed`` claim only ``human`` counts. An oracle pass
      means "your standard clearly permitted this", not "a person read it". The
      human judgement in the oracle model happens ONCE, when the standard is
      authored and vetted — not per request.

    Without this field the two are type-indistinguishable, and a
    ``human_review_passed: true`` backed by an oracle approval would be a false
    statement about a real output. That is exactly the class of unbacked
    human-review claim the F-5 work removed.
    """

    approval_id: str
    reviewer_id: str
    scope: str
    decision: str  # "approved" | "rejected"
    created_at: float
    expires_at: float | None = None
    run_id: str | None = None
    single_use: bool = True
    consumed_at: float | None = None
    revoked_at: float | None = None
    note: str = ""
    #: Defaults to ``human`` so every pre-existing record and call site keeps its
    #: current meaning; only the oracle has to say what it is.
    issuer_kind: str = ISSUER_HUMAN
    #: For an oracle-issued record: the content hash of the policy version that
    #: governed the decision. Liability transfer is worthless if we cannot say
    #: WHICH standard was in force for a given run.
    policy_version: str = ""
    #: For an ACCOUNT-HOLDER acceptance: the exact text the approver was shown,
    #: verbatim. Composed server-side — see
    #: :func:`mvp.core.account_holder_approval.compose_disclosure`.
    #:
    #: This field is the whole legal point of that flow. A record that says
    #: ``decision="approved"`` proves consent to NOTHING unless it also says what
    #: the person was told they were consenting to, so the disclosure is stored
    #: in the hash-chained record beside the decision rather than being rendered
    #: at display time from text that could since have changed.
    disclosure: str = ""
    #: The affirmation the approver submitted, verbatim. Recorded even though the
    #: issuer requires it to match a fixed phrase: the audit answers "what did
    #: they actually send", not "what were they supposed to send".
    acknowledgement: str = ""

    def is_live(self, *, now: float | None = None) -> bool:
        """True only while this record can still authorize something."""
        now = time.time() if now is None else now
        if self.decision != "approved":
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return not (self.single_use and self.consumed_at is not None)

    def redacted(self) -> dict[str, object]:
        """Audit view WITHOUT the approval_id (which is a bearer credential).

        Carries ``issuer_kind``/``policy_version`` because they are the whole
        point: an audit view that cannot distinguish a person's sign-off from
        the oracle applying a standard cannot answer the question an audit is
        for. ``reviewer_id`` alone cannot make that distinction — it is free
        text, so an oracle can spell it however it likes.

        Carries the disclosure DIGEST rather than the disclosure: this view is
        for logs and audit listings, where a multi-paragraph notice per row is
        noise, and the digest is enough to prove which notice was shown. The
        verbatim text is on the record and in the event log.
        """
        return {
            "reviewer_id": self.reviewer_id,
            "issuer_kind": self.issuer_kind,
            "policy_version": self.policy_version,
            "disclosure_digest": disclosure_digest(self.disclosure),
            "acknowledgement": self.acknowledgement,
            "scope": self.scope,
            "decision": self.decision,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "run_id": self.run_id,
            "single_use": self.single_use,
            "consumed": self.consumed_at is not None,
            "revoked": self.revoked_at is not None,
        }


class ApprovalAuthority:
    """Issues and verifies approval records. Trusted, control-plane-side only.

    ``store`` is where records live. It defaults to an in-memory one, so a bare
    ``ApprovalAuthority()`` behaves exactly as it always has — but the PROCESS-WIDE
    authority returned by :func:`get_approval_authority` takes its store from the
    environment, because a per-process dict is wrong on the shipping topology for
    two reasons spelled out in :mod:`mvp.core.approval_store`: separate ``web`` /
    ``rest`` / ``mcp`` / ``celery`` processes cannot see each other's approvals,
    and nothing survives a restart.
    """

    def __init__(self, store: Any = None) -> None:
        if store is None:
            # Deferred: approval_store imports ApprovalRecord from this module,
            # so importing it at module scope would be circular.
            from g6.core.approval_store import InMemoryApprovalStore

            store = InMemoryApprovalStore()
        self._store = store
        self._lock = threading.RLock()

    # -- minting (CONTROL PLANE ONLY) ------------------------------------

    def issue(
        self,
        *,
        reviewer_id: str,
        scope: str,
        decision: str = "approved",
        ttl_seconds: float | None = _DEFAULT_TTL_SECONDS,
        run_id: str | None = None,
        single_use: bool = True,
        note: str = "",
        issuer_kind: str = ISSUER_HUMAN,
        policy_version: str = "",
        disclosure: str = "",
        acknowledgement: str = "",
    ) -> ApprovalRecord:
        """Record an approval decision and return it (the caller gets the id).

        Raises ApprovalError on a missing reviewer or scope: an approval that
        cannot say WHO approved WHAT is not an approval.
        """
        reviewer_id = (reviewer_id or "").strip()
        scope = (scope or "").strip()
        if not reviewer_id:
            raise ApprovalError("an approval requires a reviewer_id")
        if not scope:
            raise ApprovalError("an approval requires a scope")
        if decision not in ("approved", "rejected"):
            raise ApprovalError(f"decision must be 'approved' or 'rejected', got {decision!r}")
        if issuer_kind not in ISSUER_KINDS:
            # Refuse rather than default: an unrecognised issuer silently treated
            # as `human` is precisely the false human-review claim this exists to
            # prevent, and a typo must not be the thing that creates one.
            raise ApprovalError(
                f"issuer_kind must be one of {ISSUER_KINDS}, got {issuer_kind!r}"
            )
        if issuer_kind == ISSUER_POLICY_ORACLE and not str(policy_version or "").strip():
            # An oracle decision that cannot name the standard it applied is not
            # auditable, and an unauditable approval transfers no liability.
            raise ApprovalError(
                "a policy_oracle approval requires the governing policy_version"
            )

        now = time.time()
        record = ApprovalRecord(
            approval_id=secrets.token_urlsafe(_TOKEN_BYTES),
            reviewer_id=reviewer_id,
            scope=scope,
            decision=decision,
            created_at=now,
            expires_at=(now + ttl_seconds) if ttl_seconds else None,
            run_id=run_id,
            single_use=single_use,
            note=note,
            issuer_kind=issuer_kind,
            policy_version=str(policy_version or "").strip(),
            # NOT stripped. These two are evidence of what a person was shown and
            # what they sent back; normalising them would edit the record.
            disclosure=disclosure or "",
            acknowledgement=acknowledgement or "",
        )
        with self._lock:
            self._store.put(record)
        return record

    # -- verification (the only thing untrusted input touches) -----------

    def check(
        self,
        approval_id: object,
        *,
        scope: str,
        run_id: str | None = None,
        consume: bool = False,
        require_human: bool = True,
    ) -> bool:
        """Whether *approval_id* is a live approval FOR THIS scope. FAIL CLOSED.

        Returns False — never raises — for every failure mode, so a caller can
        never distinguish "malformed" from "not found" and callers cannot turn a
        verification failure into an exception-handling bypass.

        ``require_human`` additionally refuses a record the POLICY ORACLE minted.
        **It defaults to True, and the default is load-bearing.** MEASURED across
        every call site of this method: three of the four ask a human-labelled
        question — ``validation_gates._human_approval_gate`` (the gate literally
        named ``human_approval``), ``deliverable_gate._verify_override`` (whose
        own withheld message says "a reviewer must record an approval" and which
        stamps ``reviewer_id`` into a card reading "A reviewer released..."), and
        the job HITL gate via :func:`check_operation_approval`. Only basis-3
        corroboration asks the weaker "did the control plane authorize this".

        So the strict reading is the majority contract AND the safe one, and the
        single permissive site opts out explicitly. An earlier revision of this
        docstring claimed the opposite — that the permissive reading was "this
        method's historical contract" — which an independent review measured as
        false. Getting that backwards is how an oracle pass becomes a sentence
        that says a person read the work.
        """
        if not isinstance(approval_id, str) or not approval_id:
            return False
        try:
            return self._check_unguarded(
                approval_id,
                scope=scope,
                run_id=run_id,
                consume=consume,
                require_human=require_human,
            )
        except Exception:
            # "Returns False -- NEVER raises" is a promise, and a durable store
            # can fail in ways a dict never did: the database file is missing,
            # unreadable, corrupt, or the disk is full. Two callers
            # (`validation_gates._human_approval_gate`, and the job HITL gate via
            # `check_operation_approval`) do NOT wrap this, so an escaping
            # exception would become a 500 on a regulated request instead of a
            # refusal. Refusing is both the promise and the safe answer: a broken
            # approval database must BLOCK regulated work, never crash past it.
            logger.exception("approval check failed; refusing")
            return False

    def _check_unguarded(
        self,
        approval_id: str,
        *,
        scope: str,
        run_id: str | None,
        consume: bool,
        require_human: bool,
    ) -> bool:
        with self._lock:
            record = self._store.get(approval_id)
            if record is None:
                return False
            if record.scope != scope:
                # Replay across operations: an approval for X cannot authorize Y.
                return False
            if require_human and record.issuer_kind != ISSUER_HUMAN:
                # An oracle pass means "your standard permitted this", never
                # "a person read it". Refuse before it can be reported as review.
                return False
            if record.run_id is not None and (run_id is None or record.run_id != run_id):
                # F-3. A run-PINNED approval requires the run to be PRESENTED and
                # to match. The previous form was
                #     run_id is not None and record.run_id is not None and ...
                # so presenting NO run_id skipped the check entirely: a caller
                # replayed a run-pinned approval into any other run simply by
                # omitting the field. Omission must not be able to disable a pin,
                # or the pin is advisory. Unpinned records (record.run_id is None)
                # are unaffected -- they were never run-scoped.
                return False
            if not record.is_live():
                return False
            if consume and record.single_use:
                # ATOMIC, not read-then-write. The liveness test and the append
                # happen inside one transaction in the store, so two processes
                # racing a single-use approval cannot both win. Doing it here as
                # get-validate-mark was measured to double-spend across
                # processes: both saw it live, both consumed, both got True.
                return self._store.try_consume(approval_id)
            return True

    def resolve(self, approval_id: object) -> ApprovalRecord | None:
        """Return the record for audit/logging, or None. Does NOT imply validity."""
        if not isinstance(approval_id, str) or not approval_id:
            return None
        try:
            with self._lock:
                return self._store.get(approval_id)
        except Exception:  # a store fault is not a record
            logger.exception("approval resolve failed")
            return None

    def revoke(self, approval_id: str) -> bool:
        """Invalidate an approval. True if a live record was revoked."""
        if not isinstance(approval_id, str) or not approval_id:
            return False
        try:
            with self._lock:
                record = self._store.get(approval_id)
                if record is None or record.revoked_at is not None:
                    return False
                self._store.mark_revoked(approval_id, time.time())
                return True
        except Exception:  # report the failure to revoke
            logger.exception("approval revoke failed")
            return False

    def purge_expired(self, *, now: float | None = None) -> int:
        """Drop records that can never authorize again. Returns the count removed.

        A DURABLE store returns 0 here on purpose — see
        ``SqliteApprovalStore.purge_expired``. Deleting from an append-only legal
        record is not hygiene, it is destruction of evidence, and nothing needs
        it: a dead approval is already refused by ``is_live()``.
        """
        now = time.time() if now is None else now
        with self._lock:
            return self._store.purge_expired(now=now)

    def clear(self) -> None:
        """Drop all records (teardown/tests)."""
        with self._lock:
            self._store.clear()

    def store_is_durable(self) -> bool:
        """Whether records survive a restart and are visible to other processes.

        Exists so an ISSUING surface can refuse to mint an approval that would
        silently evaporate. On the shipping topology an in-memory approval is
        worse than no approval: the customer completes the flow, the record is
        invisible to the process running the gate and gone at the next deploy,
        and the thing it was supposed to be — evidence — never existed.

        Unknown stores count as NOT durable. A store that does not say is a
        store we cannot make a durability claim about, and defaulting the other
        way would turn silence into a guarantee.
        """
        return bool(getattr(self._store, "is_durable", False))


_authority: ApprovalAuthority | None = None
_authority_lock = threading.Lock()


def get_approval_authority() -> ApprovalAuthority:
    """The process-wide authority (control-plane side).

    Its store comes from the environment: durable when ``G6_APPROVAL_DB_PATH``
    names a database, in-memory otherwise. In-memory remains the default ONLY
    because nothing in the tree mints approvals yet — it must be configured
    before any approval-issuing surface ships, or approvals are invisible across
    the ``web``/``rest``/``mcp``/``celery`` process boundary and are erased by
    every deploy.
    """
    global _authority
    if _authority is None:
        with _authority_lock:
            if _authority is None:
                from g6.core.approval_store import configured_store

                _authority = ApprovalAuthority(store=configured_store())
    return _authority


def reset_approval_authority() -> None:
    """Drop the process-wide authority so the next call rebuilds it.

    Needed because the store is chosen ONCE, at first use: a test that sets
    ``G6_APPROVAL_DB_PATH`` after something has already touched the authority
    would otherwise silently keep the in-memory one and prove nothing.
    """
    global _authority
    with _authority_lock:
        _authority = None


# ---------------------------------------------------------------------------
# Enforcement posture
# ---------------------------------------------------------------------------

def require_approval_record() -> bool:
    """Whether a caller-asserted approval boolean is REFUSED (default: yes).

    Set ``G6_HITL_REQUIRE_RECORD=0`` ONLY for a legacy migration window: it
    restores the historical behavior in which ``context.hitl_approved=True`` (a
    value the caller controls) satisfies a human-approval requirement. That is a
    self-approval bypass on regulated work and must not be used in production.
    """
    return os.environ.get("G6_HITL_REQUIRE_RECORD", "1") != "0"


def require_review_approval_record() -> bool:
    """Whether a caller-supplied review-approval DICT is refused (default: yes).

    THE DEFECT THIS CLOSES.
    ``job_framework.regulated_job_controls.validate_review_approval`` read
    ``inputs["approval"]`` — a dict the CALLER writes — built a
    ``RegulatedReviewApprovalRecord`` from it, and then validated only that the
    dict was internally consistent: does it parse, has the ``expires_at`` it
    chose passed, does the ``workflow_id`` it chose match, does the
    ``artifact_hash`` it chose match. There was no store lookup and no signature
    check, and the record model carries no issuer, nonce, signature or
    server-side issued-at field to check against. MEASURED: a request naming
    the customer as their own approver, with an invented ``credential_id`` and
    an ``approval_id`` of ``"totally-made-up-by-the-caller"``, was accepted as
    "review approval record is valid" and released regulated legal work.

    That is caller-asserted evidence presented as ``verified``, which the
    standing ruling rejects permanently: ``verified`` may rest only on
    control-plane-minted evidence.

    WHAT TURNING THIS OFF RE-OPENS. ``G6_REGULATED_REQUIRE_APPROVAL_RECORD=0``
    restores exactly that behaviour — the caller's own JSON object becomes
    sufficient to satisfy the qualified-review gate on every regulated domain
    (legal, tax, pharmaceutical, medical, psychology, allied health, finance,
    cybersecurity). It does NOT merely relax an internal check: it lets the
    subject of a regulated review approve their own work and have the result
    labelled review-approved. Provided for a migration window only, on the same
    shape and the same fail-closed default as
    :func:`require_approval_record`, and it must not be set in production.
    """
    return os.environ.get("G6_REGULATED_REQUIRE_APPROVAL_RECORD", "1") != "0"


#: Job agent blocks are named ``job_lawyer``, ``job_tax``, ``job_pharmaceutical``
#: (``job_block.name``), while the HITL policy table that decides an operation is
#: regulated is keyed by the BARE domain — ``lawyer``, ``tax``, ``pharmaceutical``
#: (``hitl_policies.HITL_REQUIRED``). Both forms reach this module.
_AGENT_PREFIX = "job_"


def normalize_agent_name(agent_name: str) -> str:
    """Collapse ``job_lawyer`` and ``lawyer`` to one name.

    WHY THIS EXISTS. Two spellings of the same agent are in live use (see
    :data:`_AGENT_PREFIX`), and which one a caller holds depends on whether it
    came from the block or from the policy table. Without a single spelling, an
    approval minted by the ISSUING surface and the scope computed by the GATE
    differ by a prefix, both sides look correct in isolation, and every approval
    is refused — a gate whose approval cannot be granted, which is precisely the
    defect this whole slice exists to fix. Getting it wrong is invisible: there
    is no error, only a permanent refusal.
    """
    return (agent_name or "").strip().removeprefix(_AGENT_PREFIX)


def approval_scope_for_operation(agent_name: str, operation: str) -> str:
    """Canonical scope string for an agent/operation approval.

    ONE derivation shared by every issuer and checker, so an approval minted for
    an operation is verifiable by the gate that guards it (and by nothing else).

    The agent name is NORMALISED here rather than at the call sites, so that
    being handed the block spelling or the policy spelling cannot change the
    answer. Putting it at the call sites is what would let the two drift.
    """
    return f"job:{normalize_agent_name(agent_name)}:{(operation or '').strip()}"


def check_operation_approval(
    approval_id: object,
    *,
    agent_name: str,
    operation: str,
    run_id: str | None = None,
    consume: bool = True,
    require_human: bool = True,
) -> bool:
    """Convenience: verify an approval for one agent/operation. FAIL CLOSED.

    ``require_human`` is stated explicitly here rather than inherited, because
    this is the job HITL gate's verifier and whether an ORACLE pass may release
    regulated work is a product decision, not a default. Today only humans mint
    approvals, so True refuses nothing; when the policy oracle exists, changing
    this must happen in the same change that fixes the gate's human wording.
    """
    try:
        authority = get_approval_authority()
    except Exception:
        # Building the authority can now fail: it opens a database. This is the
        # job HITL gate's verifier and the gate does not wrap it, so a
        # misconfigured or unreadable G6_APPROVAL_DB_PATH must REFUSE regulated
        # work rather than raise past the gate.
        logger.exception("approval authority unavailable; refusing operation")
        return False
    return authority.check(
        approval_id,
        scope=approval_scope_for_operation(agent_name, operation),
        run_id=run_id,
        consume=consume,
        require_human=require_human,
    )


__all__ = [
    "ISSUER_HUMAN",
    "ISSUER_KINDS",
    "ISSUER_POLICY_ORACLE",
    "ApprovalAuthority",
    "ApprovalError",
    "ApprovalRecord",
    "approval_scope_for_operation",
    "check_operation_approval",
    "disclosure_digest",
    "get_approval_authority",
    "normalize_agent_name",
    "require_approval_record",
    "require_review_approval_record",
    "reset_approval_authority",
]
