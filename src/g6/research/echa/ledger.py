# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The evidence ledger: append-only, content-hashed, and hash-chained (ECHA spec 4; Checkpoint 1 "trace integrity").

The chain itself is product code, ``g6.core.ledger`` (graduated, audit 037). This module adds ECHA's rules: an evidence
record names a standard that is in the ledger and a negative control that failed; a claim names evidence that is there;
an episode's body checks out. ``verify`` and ``Ledger.audit`` recompute the chain and re-check every reference, so an
edited, removed, added or reordered entry is found. A chain cut at its tail is still a consistent chain: only a head
recorded elsewhere shows it, so ``audit`` takes the head a reviewer kept.

In memory when no path is given; one JSON line per entry in a file when one is. The ledger holds ids, hashes, codes and
short texts the records allow, never a probe's input or a counterexample.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError

from .episodes import EpisodeRecord, verify_episode_body
from .flag import require_research
from .records import UNSEALED, ClaimRecord, EvidenceRecord, StandardRef

__all__ = ["CONTAMINATING", "KINDS", "Ledger", "LedgerCorrupt", "LedgerError"]

KINDS = ("standard", "evidence", "claim", "episode", "vault", "incident")
CONTAMINATING = ("access_denied", "fixture_modified")
NOT_A_CONTROL = ("HEURISTIC_REVIEW", "NO_DISCRIMINATING_EVIDENCE")


def _semantic(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    """What is wrong with one entry's references, given the entries before it."""
    standards = {b.get("standard_sha256") for (k, _), b in seen.items() if k == "standard"}
    vaults = {b.get("digest") for (k, _), b in seen.items() if k == "vault"}
    found: list[str] = []
    try:
        if kind == "standard":
            StandardRef.from_dict(body)
        elif kind == "evidence":
            rec = EvidenceRecord.from_dict(body)
            if rec.standard_sha256 not in standards:
                found.append(f"evidence {ident}: no standard with this standard_sha256 is in the ledger")
            if rec.fixture_version != UNSEALED and rec.fixture_version not in vaults:
                found.append(f"evidence {ident}: fixture_version names no sealed vault in the ledger (seal the fixture "
                             f"first, or say {UNSEALED})")
            if rec.negative_control_ref:
                control = seen.get(("evidence", rec.negative_control_ref))
                if (control is None or control.get("purpose") != "negative_control" or control.get("result") != "fail"
                        or control.get("standard_sha256") != rec.standard_sha256
                        or control.get("predicate_id") != rec.predicate_id
                        or control.get("oracle_version") != rec.oracle_version
                        or control.get("oracle_category") in NOT_A_CONTROL):
                    found.append(f"evidence {ident}: negative_control_ref {rec.negative_control_ref!r} must name an "
                                 f"earlier negative_control record, for the same standard, predicate and oracle, by an "
                                 f"executable check, that failed (it rejected its injected defect)")
        elif kind == "claim":
            claim = ClaimRecord.from_dict(body)
            if claim.standard_sha256 not in standards:
                found.append(f"claim {ident}: no standard with this standard_sha256 is in the ledger")
            for eid in claim.evidence_ids:
                if ("evidence", eid) not in seen:
                    found.append(f"claim {ident}: evidence {eid!r} is missing from the ledger")
        elif kind == "episode":
            found += [f"episode {ident}: {p}" for p in verify_episode_body(body)]
            if body.get("standard_hash") not in standards:
                found.append(f"episode {ident}: no standard with this standard_hash is in the ledger")
            for eid in body.get("evidence_refs") or []:
                if ("evidence", eid) not in seen:
                    found.append(f"episode {ident}: evidence {eid!r} is missing from the ledger")
        elif kind == "vault":
            if not isinstance(body.get("digest"), str) or len(body["digest"]) != 64:
                found.append(f"vault {ident}: digest must be a sha256 hex digest")
        elif kind == "incident":
            if not isinstance(body.get("type"), str) or not isinstance(body.get("fixture_digest"), str):
                found.append(f"incident {ident}: type and fixture_digest are required")
        else:
            found.append(f"entry {ident!r} has an unknown kind {kind!r}")
    except (ValueError, TypeError) as exc:
        found.append(f"{kind} {ident}: {exc}")
    return found


class Ledger:
    def __init__(self, path: str | Path | None = None, *, expect_head: str | None = None) -> None:
        """``expect_head``: the head a reviewer kept elsewhere; a ledger whose head differs (entries cut from its tail or
        added to it) does not open. Without one the ledger is unanchored: a clean chain says nothing about its tail."""
        require_research()
        self._chain = ChainLedger(path, expect_head=expect_head, validate=_semantic)

    @property
    def expect_head(self) -> str | None:
        return self._chain.expect_head

    @classmethod
    def audit(cls, path: str | Path, *, expect_head: str | None = None) -> tuple[str, ...]:
        """What is wrong with a ledger file, without opening it for writing; ``expect_head`` is the head a reviewer kept."""
        require_research()
        return ChainLedger.audit(path, expect_head=expect_head, validate=_semantic)

    def _add(self, kind: str, ident: str, body: dict) -> str:
        require_research()  # every write checks the flag, including on a ledger built while it was on
        return self._chain.append(kind, ident, body)

    def add_standard(self, ref: StandardRef) -> str:
        return self._add("standard", ref.standard_id, ref.to_dict())

    def add_evidence(self, rec: EvidenceRecord) -> str:
        return self._add("evidence", rec.evidence_id, rec.to_dict())

    def add_claim(self, rec: ClaimRecord) -> str:
        return self._add("claim", rec.claim_id, rec.to_dict())

    def add_episode(self, rec: EpisodeRecord) -> str:
        return self._add("episode", rec.episode_id, rec.to_dict())

    def add_vault(self, vault_id: str, fixture_digest: str, *, files: int, roots: int) -> str:
        return self._add("vault", vault_id, {"vault_id": vault_id, "digest": fixture_digest, "files": files, "roots": roots})

    def add_incident(self, kind: str, **fields: Any) -> str:
        ident = f"incident-{sum(1 for e in self._chain.entries() if e['kind'] == 'incident') + 1}"
        return self._add("incident", ident, {**fields, "incident_id": ident, "type": kind})

    def get(self, kind: str, ident: str) -> dict:
        return self._chain.get(kind, ident)

    def entry(self, kind: str, ident: str) -> dict:
        return self._chain.entry(kind, ident)

    def entries(self) -> list[dict]:
        return self._chain.entries()

    def head(self) -> str:
        return self._chain.head()

    def verify(self) -> tuple[str, ...]:
        return self._chain.verify()

    def contaminated_fixtures(self) -> set[str]:
        """Fixture digests with a recorded incident (a forbidden read, a change after sealing): evidence against them is void."""
        return {e["body"]["fixture_digest"] for e in self._chain.entries()
                if e["kind"] == "incident" and e["body"].get("type") in CONTAMINATING}
