# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The trace of a claim (Checkpoint 1 exit: "a user or reviewer can trace any local claim to its exact evidence and
standard"): the claim, the standard it was held to, each piece of evidence with its negative control, the episodes that
used the evidence, the computed verdict, and whether the ledger's chain and references still check out."""
from __future__ import annotations

from g6.core.ledger import LedgerError
from g6.evidence.mapping import read_about, read_binding

import hashlib
from typing import Any

from .claims import evaluate_claim
from .custody import fixture_contaminated
from .records import digest


def _body_ok(ledger: Any, kind: str, ident: str) -> bool:
    try:
        entry = ledger.entry(kind, ident)
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
    return digest(entry["body"]) == entry["body_sha256"]


def trace_claim(ledger: Any, claim_id: str) -> dict:
    try:
        claim = ledger.get("claim", claim_id)
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
    try:
        problems = list(ledger.verify())
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
    try:
        standard = next((e for e in ledger.entries() if e["kind"] == "standard"
                         and e["body"]["standard_sha256"] == claim["standard_sha256"]), None)
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
    standard_out = None
    if standard is not None:
        text = standard["body"]["text"]
        standard_out = {"standard_id": standard["id"], "standard_sha256": standard["body"]["standard_sha256"],
                        "text": text, "source_provenance": standard["body"]["source_provenance"],
                        "hash_ok": hashlib.sha256(text.encode("utf-8")).hexdigest() == standard["body"]["standard_sha256"]
                        and _body_ok(ledger, "standard", standard["id"])}
    contaminated = ledger.contaminated_fixtures()
    verdict = evaluate_claim(ledger, claim_id)
    roles = {item["evidence_id"]: item["role"] for item in verdict["evidence"]}
    evidence = []
    for eid in claim["evidence_ids"]:
        try:
            try:
                body = ledger.get("evidence", eid)
            except (RecursionError, MemoryError):
                raise LedgerError("a stored JSON record exceeds decoder limits") from None
        except KeyError:
            problems.append(f"evidence {eid!r} named by the claim is not in the ledger")
            continue
        control = None
        if body["negative_control_ref"]:
            try:
                try:
                    cbody = ledger.get("evidence", body["negative_control_ref"])
                except (RecursionError, MemoryError):
                    raise LedgerError("a stored JSON record exceeds decoder limits") from None
                control = {"evidence_id": body["negative_control_ref"], "result": cbody["result"],
                           "scope": cbody["scope"], "hash_ok": _body_ok(ledger, "evidence", body["negative_control_ref"])}
            except KeyError:
                problems.append(f"negative control {body['negative_control_ref']!r} is not in the ledger")
        parts, _ = read_about(read_binding(body["source_ref"]))
        evidence.append({"evidence_id": eid, "purpose": body["purpose"], "role": roles.get(eid, "ignored"),
                         "oracle_category": body["oracle_category"],
                         "result": body["result"], "scope": body["scope"], "source_ref": body["source_ref"],
                         "fixture_version": body["fixture_version"], "known_limitations": body["known_limitations"],
                         "hash_ok": _body_ok(ledger, "evidence", eid), "negative_control": control,
                         # Trace metadata retains incidents against either name, even when custody refuses the record.
                         "contaminated": (fixture_contaminated(parts.fixture, contaminated)
                                          or fixture_contaminated(body["fixture_version"], contaminated))})
    cited = set(claim["evidence_ids"])
    try:
        episodes = [{"episode_id": e["id"], "outcome_state": e["body"]["outcome_state"], "head": e["body"]["head"],
                     "known_unknowns": e["body"]["known_unknowns"]}
                    for e in ledger.entries() if e["kind"] == "episode" and cited & set(e["body"]["evidence_refs"])]
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
    try:
        return {"claim": claim, "standard": standard_out, "evidence": evidence, "episodes": episodes, "verdict": verdict,
                "limits": verdict["limits"], "ledger_head": ledger.head(), "ledger_ok": not problems, "problems": problems,
                "anchored": getattr(ledger, "expect_head", None) is not None}
    except (RecursionError, MemoryError):
        raise LedgerError("a stored JSON record exceeds decoder limits") from None
