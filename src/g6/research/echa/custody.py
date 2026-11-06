# SPDX-License-Identifier: Apache-2.0
"""Bounded research handoff; digests never establish trusted transport custody.

The binding structure, ``CheckBinding``, graduated into the product (``g6.evidence.mapping``), where it binds a receipt
to its artefact, standard and check when the receipt becomes the issuer's; it is the same class here. The records this
module writes are partial falsifiers, never independent support, and ECHA's records are not a source of evidence for
the issuer (``view.source_record``).

``about`` is where ECHA reads what one of its records is about, through the mapping's one reader
(``g6.evidence.mapping.read_about``): from its custody binding when ``source_ref`` holds one (read by the mapping's one
reader of a binding, ``read_binding``), else from the record's own fields. A ``source_ref`` that holds a binding that
does not read as one is never read as holding none: the record is about nothing and is refused
(``G6_E_EVIDENCE_BINDING_UNREADABLE``). A record whose standard or predicate is not its binding's is named with the code
for that part, and the claim rule counts it for nothing.

``evaluator_of`` is the evaluator a record names, compared by bytes: its binding's evaluator digest, the one the
accessor gives (``About.check_sha``). A record with no binding names only a declared label, which is never the same
evaluator as one named by its bytes, so a label never lets one evaluator's control activate another's check.
"""
from __future__ import annotations

from g6.evidence.mapping import BINDING_UNREADABLE, About, CheckBinding, UnreadableBinding, read_about, read_binding
from g6.evidence.mapping import strict_json as _strict_json

from .records import EvidenceRecord, canonical, digest

MAX_ARTIFACT_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 4096


def binding_of(rec: EvidenceRecord) -> CheckBinding | UnreadableBinding | None:
    """The custody binding a record's ``source_ref`` holds, as ``checked_evidence`` writes it, by the mapping's one
    reader (``read_binding``): None when it holds none, an ``UnreadableBinding`` when it holds one that does not read
    as one (a repeated key, a number that is not finite, a field missing, extra or not valid), never None for that."""
    return read_binding(rec.source_ref)


def about(rec: EvidenceRecord) -> tuple[About, tuple[tuple[str, str], ...]]:
    """What ``rec`` is about, and each of its fields that disagrees with its custody binding, as (code, why), through
    the mapping's one reader (``read_about``). With no binding, its own standard and predicate, and no artefact ("");
    with one that does not read as one, nothing, and ``BINDING_UNREADABLE``."""
    return read_about(binding_of(rec), scope=rec.scope, standard=rec.standard_sha256, predicates=(rec.predicate_id,))


def evaluator_of(rec: EvidenceRecord) -> tuple[str, str]:
    """The evaluator ``rec`` names, as (how it is named, the name): ("sha256", its binding's evaluator digest, the one
    the accessor gives) for a bound record; ("declared", its oracle label) for a record with no binding, which is never
    equal to one named by bytes. A ``ValueError`` for a record whose binding does not read as one: it names nothing."""
    parts, refused = about(rec)
    if any(code == BINDING_UNREADABLE for code, _ in refused):
        raise ValueError("a record whose custody binding does not read as one names no evaluator")
    return ("sha256", parts.check_sha) if parts.check_sha else ("declared", rec.oracle_version)


def _decode(raw: bytes, limit: int) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise ValueError("invalid handoff size or byte type")
    try:   # read as strictly as a custody binding is: one strict JSON rule (g6.evidence.mapping.strict_json)
        found = _strict_json(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("invalid UTF-8 JSON object") from exc
    if not isinstance(found, dict):
        raise ValueError("handoff must be a JSON object")  # noqa: TRY004 - one refusal type for untrusted bytes
    return found


def decode_artifact(raw: bytes) -> dict:
    """Decode bounded untrusted data. No field supplies authority or evidence."""
    return _decode(raw, MAX_ARTIFACT_BYTES)


def checked_evidence(raw: bytes, expected: CheckBinding, *, predicate_id: str,
                     purpose: str = "check", negative_control_ref: str = "") -> EvidenceRecord:
    """Adapt a trusted evaluator-channel receipt to the existing research ledger.

    The caller MUST obtain these bytes from its owned evaluator execution, after
    that process terminated successfully. Never call this on executor output.
    Matching public hashes cannot authenticate a channel. This adapter deliberately
    grants no independent/blinded status and does not discharge product obligations.
    Ledger.add_evidence rejects repeated identities, including a changed replay.
    """
    found = _decode(raw, MAX_RECEIPT_BYTES)
    binding = expected.to_dict()
    if predicate_id != expected.predicate_id:
        raise ValueError("predicate differs from the bound check")
    if set(found) != set(binding) | {"result"} or any(found[name] != value for name, value in binding.items()):
        raise ValueError("evaluator receipt binding mismatch")
    if found["result"] not in ("pass", "fail", "unknown"):
        raise ValueError("invalid evaluator result")
    return EvidenceRecord(
        evidence_id="custody-" + digest(binding),
        predicate_id=predicate_id, oracle_category="PARTIAL_FALSIFIER",
        oracle_version="sha256:" + expected.evaluator_sha256, fixture_version="unsealed",
        independent=False, independence_group="", independence_rationale="Public development controls; unblinded",
        result=found["result"], scope="Only the named predicate on this bound artifact and public fixture",
        known_limitations=("Author-known public fixture; not independent or blinded",
                           "Caller owns evaluator transport custody; hashes do not authenticate a channel",
                           "No production mission verification or domain-general correctness claim"),
        source_ref=canonical(binding), standard_sha256=expected.standard_sha256,
        purpose=purpose, negative_control_ref=negative_control_ref,
    )
