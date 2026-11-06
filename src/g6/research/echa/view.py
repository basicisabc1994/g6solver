# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA as an audit and analysis view over the issuer's records (G6 0.3 design, section 6). Not a second authority.

``certificate_view`` reads a certificate body as ``g6.evidence.certificate`` wrote it (``Certificate.as_dict``) and, given
the ledger it was issued against, the receipts, controls and provenance entries there. Each predicate's ``status`` is the
issuer's statement, copied as it is: support, refutation, the checks behind each and the caveats. Nothing here decides
or recomputes a status, so the view can never give a requirement another status than the issuer gave it. What the view
adds is explanation: each check's kind and role, whether each record the support rests on is in the ledger with the
body the certificate names, and the scope and environment the mapping table kept beside each receipt.

The view is bound to its subject's bytes: a certificate for other bytes than the subject named is refused, never shown
as the subject's. Check the body with ``g6.evidence.verify`` first; the view reads, it does not vouch.

ECHA's own records are retired as a source of evidence. ``source_record`` gives one as the mapping table reads it, with
only what ECHA holds: its artefact, standard and check only through a custody binding (one that does not read as one is
handed on as it is, and the mapping refuses it), and no control G6 generated in the issuer's ledger, no author or
producer, no environment and no ledger position, so the mapping refuses every one.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from g6.core.ledger import ChainLedger
from g6.core.ledger import digest as _digest
from g6.evidence.mapping import SourceRecord

from .custody import about, binding_of
from .records import EvidenceRecord

#: The issuer's statement fields the view copies, unchanged, as each predicate's status.
STATUS_FIELDS = ("support", "refute", "supported_by", "refuted_by", "caveats")


def _resolved(ledger: ChainLedger | None, kind: str, ident: str, body: Mapping[str, Any]) -> bool | None:
    """Whether ``ledger`` holds that record with that body; None when there is no ledger to look in."""
    if ledger is None:
        return None
    try:
        return ledger.entry(kind, ident)["body_sha256"] == _digest(dict(body))
    except (KeyError, ValueError):
        return False


def _provenance(ledger: ChainLedger | None, receipt_id: str) -> dict | None:
    if ledger is None:
        return None
    try:
        return dict(ledger.get("provenance", receipt_id))
    except (KeyError, ValueError):
        return None


def _role(result: object, check_id: str, status: Mapping[str, Any]) -> str:
    """One receipt's role, from its own result: a pass of a check the issuer counts in support supports; a failure of a
    check it counts against refutes, and of one it keeps as a caveat is a caveat, as is a result that is none; anything
    else is not counted. A check's other receipts never lend it theirs."""
    if result == "pass" and check_id in status["supported_by"]:
        return "supports"
    if result == "fail" and check_id in status["refuted_by"]:
        return "refutes"
    if result in ("fail", "unknown") and check_id in status["caveats"]:
        return "caveat"
    return "not counted"


def certificate_view(body: Mapping[str, Any], *, artifact_sha256: str, ledger: ChainLedger | None = None) -> dict:
    """The audit view of the certificate ``body`` for the subject whose bytes hash to ``artifact_sha256``.

    A ``ValueError`` when the certificate is for other bytes, or does not read as a certificate."""
    if body.get("artifact_sha") != artifact_sha256:
        raise ValueError("the certificate is for other bytes than the subject named: a view never retargets it")
    try:
        return _view(body, artifact_sha256, ledger)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"the body does not read as an issuer certificate: {exc!r}") from None


def _view(body: Mapping[str, Any], artifact_sha256: str, ledger: ChainLedger | None) -> dict:
    checks = {c["check_id"]: c for c in body["checks"]}
    gaps = {str(subject): str(why) for subject, why in body["gaps"]}
    receipts = list(body["receipts"])
    statements = list(body["predicates"])
    predicates = []
    for statement in statements:
        name = statement["predicate"]
        status = {field: statement[field] for field in STATUS_FIELDS}
        counted = set(status["supported_by"]) | set(status["refuted_by"]) | set(status["caveats"])
        evidence = []
        for receipt in receipts:
            check = checks.get(receipt["check_id"], {})
            if name not in check.get("predicates", ()):
                continue
            cid = receipt["check_id"]
            role = _role(receipt["result"], cid, status)
            evidence.append({
                "receipt_id": receipt["receipt_id"], "check_id": cid, "kind": check.get("kind"),
                "result": receipt["result"], "role": role,
                "receipt_in_ledger": _resolved(ledger, "receipt", receipt["receipt_id"], receipt),
                "controls": [{"control_id": k["control_id"], "killed": k["killed"],
                              "in_ledger": _resolved(ledger, "control", k["control_id"], k)}
                             for k in check.get("controls", ()) if k.get("predicate") == name],
                "provenance": _provenance(ledger, receipt["receipt_id"])})
        predicates.append({"predicate": name, "status": status, "gap": gaps.get(f"predicate:{name}", ""),
                           "evidence": evidence,
                           "uncounted": sorted({e["check_id"] for e in evidence} - counted)})
    return {"artifact_sha256": artifact_sha256, "state": body.get("state"), "standard_sha256": body.get("standard_sha"),
            "certificate_digest": body.get("digest"), "predicates": predicates,
            "unresolved": [list(u) for u in body.get("unresolved", [])],
            "authority": "g6.evidence.certificate: every status here is the issuer's, copied"}


def source_record(rec: EvidenceRecord) -> SourceRecord:
    """An ECHA record as the mapping table reads it, with only the parts ECHA holds (the module docstring): what it is
    about is read through ``custody.about``, so a bound record's parts are its binding's and never its own fields."""
    parts, _ = about(rec)
    binding = binding_of(rec)
    formal = rec.oracle_category == "FORMAL_CHECK"
    return SourceRecord(
        kind=rec.oracle_category, record_id=rec.evidence_id, predicates=parts.predicates,
        result=rec.result if rec.result in ("pass", "fail", "unknown") else "unknown",
        artefact_sha256=parts.artefact, producer="", artefact_seq=None, standard_sha256=parts.standard,
        check_id=parts.check_id or rec.evidence_id, check_sha256=parts.check_sha, author="", frozen_seq=None,
        environment_sha256="", seq=None, scope=parts.scope, certificate=rec.fixture_version if formal else "",
        binding=binding)


__all__ = ["STATUS_FIELDS", "certificate_view", "source_record"]
