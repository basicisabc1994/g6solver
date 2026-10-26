# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""promotion_evaluator — the independent-replay seam, and an evaluator signing key.

The kernel promotion law (``core.reliability_envelope``) lets a proposed
``verified`` stand only when one of G6's own re-checkers re-runs the check and
reproduces it: it calls :func:`replay_reproduces` with that re-checker's replay
(plan A1, DECISIONS #40). Any component can call ``build_promotion_witness`` and
assert ``role="evaluator"``, so the law takes no witness field on the producer's
word, and it reads no signature: a configured key, in process or by path, can
neither promote nor block a result. G6 re-checks artifacts with its own code; it
does not defend against malicious code in its own process.

The signing layers below remain a library for callers that want signed
statements: an HMAC over a witness's sealed core, keyed by the secret
``G6_PROMOTION_EVALUATOR_KEY`` in the person's key file (``g6.core.keys``,
``~/.g6/.env``; never the environment).

Two layers:

1. **Signature/identity** (``sign`` / ``verify`` / ``stamp``): a witness
   HMAC-signed by the evaluator key.
2. **Independent replay** (``register_oracle`` / ``stamp_replayed``): the
   disciplined mint path RE-RUNS the witness's oracle and refuses to sign a
   result that does not independently reproduce (the science_harness
   hidden_evaluation model).

CUSTODY (slice 5): in the in-process mode above the key lives in the key file, which
this process reads, so a *determined* in-process component could read it, and could
call raw ``stamp`` to sign WITHOUT a replay (the replay gate is on the
``stamp_replayed`` mint PATH, not on the signature bytes). Setting
``G6_PROMOTION_EVALUATOR_KEY_PATH`` (with ``G6_PROMOTION_EVALUATOR_KEY`` left out of
the key file) moves the key AND the crypto OUT OF PROCESS: the
custody client (``core.promotion_custody``) answers ``verify`` / ``stamp_replayed``
by spawning a child (``core.promotion_evaluator_service``) that loads the key from
the file, re-runs the replay, and signs — so the generator holds no key and has no
in-process ``stamp`` to abuse. That service is not part of this library, so with the
path set ``promotion_custody.custody_configured`` raises and says so, rather than
every check quietly answering "not signed". (The custody boundary is process-memory + interface, not an OS ACL against
the same user — the key file is same-user-readable, as the hidden seed is.)

Determinism: no wall-clock, no randomness — signing is a pure function of the
witness core and the key, so the same witness signs byte-identically. (A replay
callable is a harness's own re-execution; it must itself be deterministic to
reproduce a content-addressed execution identity.)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: The evaluator secret's name in the person's key file.
_EVALUATOR_KEY = "G6_PROMOTION_EVALUATOR_KEY"
_SIG_PREFIX = "hmac-sha256:"
# A short, non-secret fingerprint that NAMES the authority in the witness
# without leaking the key (so a verifier can tell which authority signed).
_KEY_ID_PREFIX = "evk:"

# ── receipt domain (execution receipts, separate from promotion witnesses) ──
# The SAME key seals two different kinds of statement, so the two signing
# payloads must be DOMAIN-SEPARATED or a signature minted in one domain would be
# replayable in the other. They are, structurally and unambiguously:
#
#   promotion witness -> a JSON ARRAY   '["<digest>","<execution_id>"]'
#   execution receipt -> a JSON OBJECT  '{"content_digest":...,"scheme":"g6-...
#
# The first byte alone ('[' vs '{') decides the domain, and the receipt object
# additionally pins a literal ``scheme`` field. No input to one encoder can ever
# produce a byte string the other encoder produces, so a receipt signature is
# never a valid witness signature and vice versa. The distinct prefix below is
# for human/diagnostic clarity only — the separation is in the signed bytes.
_RECEIPT_SCHEME = "g6-execution-receipt-v1"
_RECEIPT_SIG_PREFIX = "hmac-sha256-receipt:"
#: The exact fields a receipt seal covers. Anything not listed here is NOT
#: sealed and therefore swappable — do not add a field to a receipt without
#: adding it here.
_RECEIPT_CORE_FIELDS: tuple[str, ...] = (
    "content_digest", "execution_id", "kind", "run_id", "task_id", "verdict",
)


def _key_bytes() -> bytes | None:
    """The evaluator secret from the person's key file as bytes, or None when it is not there (or blank)."""
    from g6.core import keys
    raw = keys.get(_EVALUATOR_KEY)
    return raw.encode("utf-8") if raw else None


def _unset() -> str:
    """Why no in-process authority is configured: the key file does not hold the key."""
    from g6.core import keys
    return f"{_EVALUATOR_KEY} is not in {keys.path()}"


def _custody():
    """The out-of-process custody client (lazy import — no cycle, no import-time
    cost when custody is unconfigured)."""
    from g6.core import promotion_custody
    return promotion_custody


def evaluator_configured() -> bool:
    """True when an evaluator signing authority is available.

    True for an in-process key (``G6_PROMOTION_EVALUATOR_KEY`` in the key file) OR an
    out-of-process custody service (``G6_PROMOTION_EVALUATOR_KEY_PATH``, slice 5).
    The promotion law does not read it.
    """
    return _key_bytes() is not None or _custody().custody_configured()


def evaluator_key_id() -> str:
    """Non-secret fingerprint naming the current authority ('' when unset).

    A truncated (unsalted) digest of the key: enough to distinguish authorities
    and to detect a key rotation. It is NOT the secret, but it IS an offline
    oracle for a *weak* key — so the evaluator key MUST be high-entropy random
    (for which inverting this 64-bit fingerprint is infeasible); a low-entropy
    key is brute-forceable against it.
    """
    key = _key_bytes()
    if key is None:
        return ""
    return _KEY_ID_PREFIX + hashlib.sha256(key).hexdigest()[:16]


def _signing_payload(content_digest: str, execution_id: str) -> bytes:
    """Bind the signature to this exact sealed witness + witnessed execution.

    Injective, delimiter-safe encoding (a JSON array, not a ``|`` join) so
    distinct ``(digest, execution_id)`` pairs never collide on one payload.

    NOTE (for the capsule-replay slice): this binds ``execution_id`` only, and
    the digest core excludes the whole ``execution`` subtree. Any OTHER key added
    under ``execution`` (e.g. a content-addressed artifact pointer) would be
    neither digest-sealed nor signature-bound and thus swappable — it MUST be
    folded into this signed payload when that slice lands.
    """
    return json.dumps(
        [content_digest, execution_id], ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def sign(content_digest: str, execution_id: str) -> str:
    """HMAC-sign a sealed witness core. Raises when no authority is configured."""
    key = _key_bytes()
    if key is None:
        raise RuntimeError(
            f"no evaluator authority configured ({_unset()}); "
            "cannot sign a promotion witness"
        )
    mac = hmac.new(key, _signing_payload(content_digest, execution_id), hashlib.sha256)
    return _SIG_PREFIX + mac.hexdigest()


def verify(witness: Any) -> bool:
    """True iff ``witness`` carries a signature valid under the configured key.

    False when no authority is configured, when the signature is absent/blank,
    or when it does not match the witness's ``content_digest`` + witnessed
    ``execution_id`` (constant-time compare). The promotion law does not call this.

    When an out-of-process custody service is configured (slice 5), the check is
    answered by that service (the key lives only there); otherwise it runs in
    process against ``G6_PROMOTION_EVALUATOR_KEY`` in the key file.
    """
    if _custody().custody_configured():
        return _custody().verify_via_custody(witness)
    key = _key_bytes()
    if key is None or not isinstance(witness, Mapping):
        return False
    sig = witness.get("evaluator_signature")
    if not isinstance(sig, str) or not sig:
        return False
    if not sig.isascii():
        # A valid signature is hex ("hmac-sha256:" + 64 hex chars) and always
        # ASCII. Guard here so a non-ASCII attacker-supplied signature is a clean
        # False, NOT a TypeError out of hmac.compare_digest (which rejects
        # non-ASCII str) propagating through the law. Fail closed.
        return False
    execution = witness.get("execution")
    execution_id = (
        str(execution.get("execution_id") or "")
        if isinstance(execution, Mapping) else ""
    )
    content_digest = str(witness.get("content_digest") or "")
    expected = _SIG_PREFIX + hmac.new(
        key, _signing_payload(content_digest, execution_id), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def stamp(witness: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``witness`` signed by the evaluator authority.

    The ONLY mint path for a promotable witness when a key is configured. A
    generator that does not hold the key cannot produce a witness ``verify``
    accepts, so it cannot self-promote past the law. Raises when no authority
    is configured (fail-closed: you cannot stamp without an evaluator).
    """
    if not isinstance(witness, Mapping):
        raise TypeError("witness must be a mapping")
    content_digest = str(witness.get("content_digest") or "")
    if not content_digest:
        raise ValueError("witness has no content_digest to sign")
    execution = witness.get("execution")
    execution_id = (
        str(execution.get("execution_id") or "")
        if isinstance(execution, Mapping) else ""
    )
    signed = dict(witness)
    signed["evaluator_signature"] = sign(content_digest, execution_id)
    signed["evaluator_key_id"] = evaluator_key_id()
    return signed


# ── execution-receipt seals ─────────────────────────────────────────────────
# A receipt is NOT a promotion witness and must never be read as one. A witness
# says "an independent evaluator RE-RAN this oracle and it reproduced". A receipt
# says only "the control plane's custody perimeter sealed THIS content under THIS
# run" — it binds content and run so a genuine receipt cannot be reattached to a
# different answer or replayed into a later run, and it cannot be produced by
# anyone who cannot reach the custody interface. It does NOT attest that any
# execution happened; the mint site's honesty is what carries that, exactly as
# before. Anyone inside the perimeter can have any content sealed.
#
# These two functions run IN PROCESS against ``G6_PROMOTION_EVALUATOR_KEY`` in the
# key file. In a custody deployment the key file does not hold it (only
# ``..._KEY_PATH`` is set), so they raise/refuse there and the only reachable
# sealing path is ``promotion_custody.sign_receipt_via_custody`` — the child.


def _receipt_signing_payload(core: Mapping[str, Any]) -> bytes:
    """Canonical, domain-separated bytes for a receipt core.

    A JSON OBJECT with sorted keys and a literal ``scheme`` field, so it can
    never collide with ``_signing_payload``'s JSON ARRAY (see the domain note at
    the top of this module). Every field in ``_RECEIPT_CORE_FIELDS`` is included
    — a missing field is sealed as ``""`` rather than omitted, so two cores that
    differ only in which fields are present never seal identically.
    """
    sealed = {name: str(core.get(name) or "") for name in _RECEIPT_CORE_FIELDS}
    sealed["scheme"] = _RECEIPT_SCHEME
    return json.dumps(
        sealed, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def sign_receipt(core: Mapping[str, Any]) -> str:
    """HMAC-seal an execution-receipt core. Raises when no authority is configured."""
    if not isinstance(core, Mapping):
        raise TypeError("receipt core must be a mapping")
    key = _key_bytes()
    if key is None:
        raise RuntimeError(
            f"no evaluator authority configured ({_unset()}); "
            "cannot seal an execution receipt"
        )
    mac = hmac.new(key, _receipt_signing_payload(core), hashlib.sha256)
    return _RECEIPT_SIG_PREFIX + mac.hexdigest()


def verify_receipt(core: Any, signature: Any) -> bool:
    """True iff ``signature`` seals exactly this receipt core. Fails closed.

    False for a non-mapping core, a non-string/blank/non-ASCII signature, no
    configured key, or any mismatch (constant-time compare). Non-ASCII is
    rejected explicitly so an attacker-supplied signature is a clean False and
    never a TypeError out of ``hmac.compare_digest``.
    """
    key = _key_bytes()
    if key is None or not isinstance(core, Mapping):
        return False
    if not isinstance(signature, str) or not signature or not signature.isascii():
        return False
    expected = _RECEIPT_SIG_PREFIX + hmac.new(
        key, _receipt_signing_payload(core), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# ── G6-authored oracle attestation ──────────────────────────────────────────
# A test suite G6 itself wrote (the Gate-1 benchmark's reference tests) is G6's
# oracle; a suite a caller hands ``coder_solve`` is the caller's (FOUNDER RULING
# 2026-09-23) and never earns ``verified``. Both arrive as the same string, so
# the difference is carried by an attestation only the evaluator key-holder can
# produce: an HMAC over the suite's exact source. No MCP/CLI/REST surface accepts
# an attestation, and a caller without the key cannot forge one.
#
# Domain-separated from both other signed payloads: a JSON OBJECT whose keys
# ({scheme, tests_sha256}) differ from the receipt core's, with its own scheme
# literal and its own signature prefix, and never a witness's JSON ARRAY.
#
# In process only. Under out-of-process custody the env key is unset here, so
# signing raises and verification is False: the suite stays capped (fail closed).

_ORACLE_SCHEME = "g6-authored-oracle-v1"
_ORACLE_SIG_PREFIX = "hmac-sha256-oracle:"


def _oracle_signing_payload(test_source: str) -> bytes:
    digest = hashlib.sha256(test_source.encode("utf-8")).hexdigest()
    return json.dumps(
        {"scheme": _ORACLE_SCHEME, "tests_sha256": digest},
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")


def sign_authored_oracle(test_source: str) -> str:
    """Attest that ``test_source`` is a G6-authored oracle. Raises without a key."""
    if not isinstance(test_source, str) or not test_source.strip():
        raise ValueError("cannot attest an empty test suite")
    key = _key_bytes()
    if key is None:
        raise RuntimeError(
            f"no evaluator authority configured ({_unset()}); "
            "cannot attest a G6-authored oracle"
        )
    mac = hmac.new(key, _oracle_signing_payload(test_source), hashlib.sha256)
    return _ORACLE_SIG_PREFIX + mac.hexdigest()


def verify_authored_oracle(test_source: Any, attestation: Any) -> bool:
    """True iff ``attestation`` attests exactly this ``test_source``. Fails closed.

    False for a blank or non-string suite or attestation, a non-ASCII
    attestation, no configured key, or any mismatch (constant-time compare).
    """
    key = _key_bytes()
    if key is None:
        return False
    if not isinstance(test_source, str) or not test_source.strip():
        return False
    if not isinstance(attestation, str) or not attestation or not attestation.isascii():
        return False
    expected = _ORACLE_SIG_PREFIX + hmac.new(
        key, _oracle_signing_payload(test_source), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(attestation, expected)


# ── independent-replay layer ────────────────────────────────────────────────
# The signature layer above proves an evaluator KEY-HOLDER attested a witness. It
# does not, by itself, prove the underlying oracle result reproduces: a key-holder
# can sign any digest. The de Bruijn property wants more — the evaluator must
# INDEPENDENTLY RE-RUN the oracle and observe the claimed verdict + execution
# identity reproduce BEFORE it signs (the science_harness hidden_evaluation /
# capsule-replay model). This layer adds that gate on the mint path.
#
# Oracles register a replay callable keyed by the witness ``oracle`` name. The
# callable re-executes the oracle from the witness's own record and returns a
# ``ReplayOutcome``: whether it reproduced, and the execution identity observed on
# re-execution (which must match the witness's witnessed execution_id — a
# reproduction of a DIFFERENT artifact does not certify THIS one).


@dataclass(frozen=True)
class ReplayOutcome:
    """The result of an evaluator independently re-running an oracle.

    ``reproduced`` is True only when the re-execution observed the SAME verdict
    the witness claims. ``execution_id`` is the execution identity the replay
    OBSERVED (e.g. the artifact hash the Lean kernel produced on re-check); the
    mint path requires it to equal the witness's witnessed execution_id, so a
    reproduction of a different artifact never certifies the witnessed one.

    CONTRACT (load-bearing — see ``register_oracle``): ``execution_id`` MUST be
    derived from the replay's OWN re-execution output, never echoed from the input
    witness. An honest reproduction and an echo look identical (both equal the
    witnessed id), so the seam cannot police this at runtime — it is a trust
    obligation on the replay author, and an echoing replay makes the gate vacuous.
    """

    reproduced: bool
    execution_id: str = ""
    detail: str = ""
    #: The re-check ran and found the artifact contradicts the claim (not merely "could not reproduce"): the doors
    #: then block the result instead of holding it as a draft.
    refuted: bool = False


_OracleReplay = Callable[[Mapping[str, Any]], ReplayOutcome]
_ORACLE_REPLAYS: dict[str, _OracleReplay] = {}


def register_oracle(name: str, replay: _OracleReplay) -> None:
    """Register an independent replay for an oracle ``name``.

    This is how a harness (e.g. lean_prover) tells the evaluator seam HOW to
    independently reproduce its oracle before signing. The replay re-executes the
    oracle from the witness's own record and returns a ``ReplayOutcome``.
    Idempotent: a later registration for the same name replaces the earlier one.

    LOAD-BEARING CONTRACT for the ``replay`` author (the gate is only as strong as
    this): the replay MUST actually RE-RUN the oracle and set
    ``ReplayOutcome.execution_id`` from that re-execution's own output (e.g. the
    content-addressed artifact hash the checker RECOMPUTES), and set
    ``reproduced=True`` only when the re-executed verdict matches. It MUST NOT echo
    ``witness["execution"]["execution_id"]`` back, and MUST NOT trust the witness's
    claimed verdict — either would make the identity check vacuous (a self-minted
    non-reproducing result would then stamp). The seam cannot detect an echo at
    runtime, so this is on you.
    """
    if not str(name or "").strip():
        raise ValueError("oracle name is required")
    if not callable(replay):
        raise TypeError("replay must be callable")
    _ORACLE_REPLAYS[name] = replay


def oracle_registered(name: str) -> bool:
    """True when an independent replay is registered for ``name``."""
    return name in _ORACLE_REPLAYS


def replay_reproduces(witness: Any, *, replay: _OracleReplay | None = None) -> ReplayOutcome:
    """Independently re-run the witness's oracle; fail-closed on every gap.

    ``replay`` is the re-check to run; by default the one registered for the
    witness's oracle (the promotion law passes G6's own re-checker, never the open
    registry). Returns ``reproduced=False`` when the witness is malformed, when no
    replay is known, when the replay raises or returns the wrong type, when it does
    not reproduce, or when the identity it observes does not match the witness's
    witnessed execution_id. Only a clean, identity-matching reproduction returns
    ``reproduced=True``.
    """
    if not isinstance(witness, Mapping):
        return ReplayOutcome(False, detail="witness is not a mapping")
    oracle = str(witness.get("oracle") or "")
    replay = replay if replay is not None else _ORACLE_REPLAYS.get(oracle)
    if replay is None:
        return ReplayOutcome(
            False, detail=f"no independent replay registered for oracle {oracle!r}"
        )
    try:
        outcome = replay(witness)
    except Exception as exc:  # noqa: BLE001 - fail closed: a replay is untrusted
        # harness code; ANY exception (toolchain, IO, bug) means it did not
        # reproduce and must never leak out as a promotion.
        return ReplayOutcome(False, detail=f"replay raised: {exc!r}")
    if not isinstance(outcome, ReplayOutcome):
        return ReplayOutcome(False, detail="replay did not return a ReplayOutcome")
    if outcome.reproduced is not True:
        # Strict: only an explicit True promotes. A stray truthy value (1, "yes")
        # from a buggy replay must not slip a result past the gate.
        if outcome.reproduced:
            return ReplayOutcome(
                False,
                execution_id=outcome.execution_id,
                detail=f"replay reported a non-bool reproduced={outcome.reproduced!r}",
            )
        return outcome  # honest False — preserve the replay's own detail
    execution = witness.get("execution")
    witnessed_id = (
        str(execution.get("execution_id") or "")
        if isinstance(execution, Mapping) else ""
    )
    if not witnessed_id or outcome.execution_id != witnessed_id:
        return ReplayOutcome(
            False,
            execution_id=outcome.execution_id,
            detail=(
                f"replay reproduced execution identity {outcome.execution_id!r}, "
                f"which does not match the witnessed execution_id {witnessed_id!r}"
            ),
        )
    return outcome


def stamp_replayed(witness: Mapping[str, Any]) -> dict[str, Any]:
    """Independently replay the oracle, then stamp — the de Bruijn mint path.

    The disciplined way to mint a promotable witness: the evaluator RE-RUNS the
    witness's oracle and only signs when it independently reproduces the claimed
    verdict AND execution identity. Holding the key is not enough — a result that
    does not reproduce cannot be stamped this way. Raises ``RuntimeError`` when no
    authority is configured and ``ValueError`` when the oracle does not
    independently reproduce (fail-closed).

    HONEST SCOPE: the replay gate is on THIS mint path, not on the signature
    bytes. In one process a key-holder can still call ``stamp`` directly and skip
    the replay. That bypass closes when the evaluator key lives OUT OF PROCESS and
    ``stamp_replayed`` is the sole interface to it — configured via
    ``G6_PROMOTION_EVALUATOR_KEY_PATH`` (slice 5, ``promotion_custody``), which
    routes this call to a child process that holds the key and re-runs the replay
    before signing; the generator then has no in-process ``stamp`` to abuse.
    """
    if _custody().custody_configured():
        return _custody().stamp_replayed_via_custody(witness)
    if not evaluator_configured():
        raise RuntimeError(
            f"no evaluator authority configured ({_unset()}); "
            "cannot stamp a replayed witness"
        )
    outcome = replay_reproduces(witness)
    if not outcome.reproduced:
        raise ValueError(
            "cannot stamp a witness whose oracle does not independently "
            f"reproduce: {outcome.detail}"
        )
    return stamp(witness)


__all__ = [
    "ReplayOutcome",
    "evaluator_configured",
    "evaluator_key_id",
    "oracle_registered",
    "register_oracle",
    "replay_reproduces",
    "sign",
    "sign_authored_oracle",
    "sign_receipt",
    "stamp",
    "stamp_replayed",
    "verify",
    "verify_authored_oracle",
    "verify_receipt",
]
