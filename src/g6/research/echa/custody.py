# SPDX-License-Identifier: Apache-2.0
"""Bounded research handoff; digests never establish trusted transport custody."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass

from .flag import require_research
from .records import EvidenceRecord, canonical, digest, hex64_of

MAX_ARTIFACT_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 4096


@dataclass(frozen=True)
class CheckBinding:
    request_id: str
    task_id: str
    predicate_id: str
    artifact_sha256: str
    standard_sha256: str
    fixture_sha256: str
    evaluator_sha256: str

    def __post_init__(self):
        for name in ("request_id", "task_id", "predicate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
                raise ValueError(f"invalid {name}")
        for name in ("artifact_sha256", "standard_sha256", "fixture_sha256", "evaluator_sha256"):
            value = getattr(self, name)
            if len(hex64_of(name, value)) != 64:
                raise ValueError(f"invalid {name}")

    def to_dict(self) -> dict:
        return asdict(self)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _constant(value):
    raise ValueError("nonfinite JSON number")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _decode(raw: bytes, limit: int) -> dict:
    require_research()
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise ValueError("invalid handoff size or byte type")
    try:
        found = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant, parse_float=_float)
    except (UnicodeError, RecursionError) as exc:
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
