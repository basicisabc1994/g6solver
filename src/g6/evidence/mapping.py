# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The mapping table (G6 0.3 design, section 3.1): what each kind of evidence is, fixed before any record is converted.

``g6.evidence.certificate`` is the one rule that decides a requirement's status, and this module never decides one. It
sits beside the issuer and changes nothing in it. For every evidence kind the product or ECHA produces, ``MAPPING`` holds
one row that fixes:

- the identity of the artefact, the model, the standard, the check and the environment;
- its negative control;
- the author's independence;
- its ordering and its scope;
- how conflicting evidence is kept.

``promote`` turns one ``SourceRecord`` into the issuer's inputs, or refuses it with a stable ``G6_E_EVIDENCE_*`` code for
each part it lacks; nothing it refuses reaches the issuer. A kind whose row has no issuer kind (a review, a step that ran,
a proposed check) is a lead, never a source: it is refused as ``G6_E_EVIDENCE_KIND_NOT_PROMOTED``. ``issue_records``
promotes a batch, writes what G6 observed into a fresh ``g6.core.ledger.ChainLedger`` with the issuer's own ``record``, and
asks the issuer for the certificate. The mapping refuses what is missing; the issuer judges what is present (a control
that was not killed, a check frozen after the artefact, a pass taken before its control), so the two never overlap.

``CheckBinding`` is ECHA custody's binding of one evaluator receipt to its request, task, predicate, artefact, standard,
fixture and evaluator, graduated here so a receipt keeps that binding when it becomes the issuer's (``from_binding``).
"""
from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from g6.core.ledger import ChainLedger
from g6.core.ledger import digest as _digest
from g6.evidence.certificate import (
    A4_KINDS,
    RESULTS,
    Artifact,
    Certificate,
    CertificateRefused,
    Check,
    Control,
    MissionFacts,
    Receipt,
    issue,
    record,
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")

#: The stable codes of a refused promotion, each naming the part of the record that is missing or wrong.
UNMAPPED = "G6_E_EVIDENCE_KIND_UNMAPPED"
NOT_PROMOTED = "G6_E_EVIDENCE_KIND_NOT_PROMOTED"
NO_ARTEFACT = "G6_E_EVIDENCE_NO_ARTEFACT"
OTHER_ARTEFACT = "G6_E_EVIDENCE_OTHER_ARTEFACT"
NO_MODEL = "G6_E_EVIDENCE_NO_MODEL"
NO_STANDARD = "G6_E_EVIDENCE_NO_STANDARD"
OTHER_STANDARD = "G6_E_EVIDENCE_OTHER_STANDARD"
NO_CHECK = "G6_E_EVIDENCE_NO_CHECK"
NO_ENVIRONMENT = "G6_E_EVIDENCE_NO_ENVIRONMENT"
NO_CONTROL = "G6_E_EVIDENCE_NO_NEGATIVE_CONTROL"
NO_INDEPENDENCE = "G6_E_EVIDENCE_NO_INDEPENDENCE"
NO_ORDER = "G6_E_EVIDENCE_NO_ORDER"
NO_SCOPE = "G6_E_EVIDENCE_NO_SCOPE"
NO_RESULT = "G6_E_EVIDENCE_NO_RESULT"
CONFLICTING = "G6_E_EVIDENCE_CONFLICTING_RECORD"
CODES = (UNMAPPED, NOT_PROMOTED, NO_ARTEFACT, OTHER_ARTEFACT, NO_MODEL, NO_STANDARD, OTHER_STANDARD, NO_CHECK,
         NO_ENVIRONMENT, NO_CONTROL, NO_INDEPENDENCE, NO_ORDER, NO_SCOPE, NO_RESULT, CONFLICTING)
#: The ledger kind under which a promoted receipt's environment, model, scope and source are kept beside it.
PROVENANCE_KIND = "provenance"


@dataclass(frozen=True)
class Row:
    """One evidence kind: what identifies each part of its records, and what it may become in the issuer's terms."""

    kind: str
    #: Where the kind is produced: ``issuer`` (the issuer's own kinds), ``echa`` (ECHA's oracle categories) or
    #: ``patch_review`` (the patch review's check provenance, and a person's confirmed expectation).
    source: str
    #: The issuer kind a record is promoted as; "" when the kind is a lead and never a source of evidence.
    issuer_kind: str
    artefact: str
    model: str
    standard: str
    check: str
    environment: str
    negative_control: str
    independence: str
    ordering: str
    conflicts: str
    #: Whether a record must name its model (a formalisation, or the reviewing model) to be promoted.
    needs_model: bool = False
    #: Whether a record must name a formal certificate's digest to be promoted.
    needs_certificate: bool = False


_BYTES = "the SHA-256 of the exact artefact bytes the result was taken on"
_RUN = "the SHA-256 of the run's environment record: interpreter, platform and operating-system boundary"
_ORDER = ("ledger positions: the check frozen before the artefact, its control after the freeze and before the "
          "receipt it backs, the receipt after the artefact; the scope is what the result covers and nothing wider")
_BELNAP = ("support and refutation are kept apart (Belnap's four states); a conflict is never averaged, and a record "
           "with an id already promoted under another body is refused")
_LEAD = "a lead only: it is never promoted, so it supports and refutes nothing"
_NO_CONTROL = "none can be shown: a judgement or a step cannot be seeded with a defect it must reject"
_A5 = ("the check's author is named beside the artefact's producer; a check its producer wrote is promoted as a "
       "partial falsifier whatever its source calls it")

MAPPING: dict[str, Row] = {row.kind: row for row in (
    # --- the issuer's own kinds -----------------------------------------------------------------------------------
    Row("independent_executable", "issuer", "independent_executable", _BYTES,
        "none: the check is code; a check that involves a model is a heuristic review",
        "the frozen standard's SHA-256, which the mission cites", "the check's id and the SHA-256 of its frozen code",
        _RUN, "a defect G6 seeded for this predicate and this check's code, killed by the check", _A5, _ORDER, _BELNAP),
    Row("formal_checked", "issuer", "formal_checked", _BYTES,
        "the SHA-256 of the formalisation the checker decided, which the claim is relative to",
        "the frozen standard's SHA-256", "the checker's id and version, and the certificate's digest", _RUN,
        "a defect G6 seeded for this predicate that the formal check rejects; a formal check needs one too", _A5,
        _ORDER, _BELNAP, needs_model=True, needs_certificate=True),
    Row("partial_falsifier", "issuer", "partial_falsifier", _BYTES,
        "none: the check is code", "the frozen standard's SHA-256", "the check's id and the SHA-256 of its code", _RUN,
        "a defect G6 seeded for this predicate, killed by the check: without one its failure is a caveat",
        "author-controlled: it can refute with its controls, and never supports", _ORDER, _BELNAP),
    Row("heuristic_review", "issuer", "", "the artefact the reviewer read", "the reviewing model's id and family",
        "the standard it was read against", "the review prompt's SHA-256", "the model call's record", _NO_CONTROL,
        "reviews from one family are one voice", _ORDER, _LEAD, needs_model=True),
    Row("process", "issuer", "", "the artefact the step produced", "none", "the standard of the mission",
        "the step's operation", "the run's record", _NO_CONTROL, "a step is its own author", _ORDER,
        _LEAD),
    Row("no_evidence", "issuer", "", "none", "none", "none", "none", "none", _NO_CONTROL, "none", "none", _LEAD),
    # --- ECHA's oracle categories: its own records are retired as a source of evidence ----------------------------
    Row("FORMAL_CHECK", "echa", "formal_checked",
        "a custody binding's artifact_sha256 only: an ECHA record has no artefact field",
        "the certificate digest ECHA keeps as fixture_version", "standard_sha256", "oracle_version is a label, not a "
        "code digest: a custody binding's evaluator_sha256", "none recorded by ECHA",
        "negative_control_ref, an ECHA record: never one G6 generated in the issuer's ledger", "independent, a "
        "caller's flag", "ECHA's ledger order, not the issuer's", _BELNAP, needs_model=True, needs_certificate=True),
    Row("INDEPENDENT_EXECUTABLE", "echa", "independent_executable",
        "a custody binding's artifact_sha256 only", "none", "standard_sha256",
        "a custody binding's evaluator_sha256", "none recorded by ECHA",
        "negative_control_ref, an ECHA record", "independent and independence_group, a caller's flag",
        "ECHA's ledger order, not the issuer's", _BELNAP),
    Row("PARTIAL_FALSIFIER", "echa", "partial_falsifier", "a custody binding's artifact_sha256 only", "none",
        "standard_sha256", "a custody binding's evaluator_sha256", "none recorded by ECHA",
        "negative_control_ref, an ECHA record", "author-controlled", "ECHA's ledger order, not the issuer's", _BELNAP),
    Row("HEURISTIC_REVIEW", "echa", "", "none", "independence_group names the family", "standard_sha256",
        "oracle_version", "none", _NO_CONTROL, "one group is one voice", "ECHA's ledger order", _LEAD, needs_model=True),
    Row("NO_DISCRIMINATING_EVIDENCE", "echa", "", "none", "none", "standard_sha256", "none", "none", _NO_CONTROL,
        "none", "none", _LEAD),
    # --- the patch review's check provenance --------------------------------------------------------------------
    Row("stated", "patch_review", "independent_executable",
        "the review's artifact digest of the working tree", "none: the check is the repository's test code",
        "the SHA-256 of the review's assessed requirements", "the pytest node id and the SHA-256 of its file",
        "the SHA-256 of the review's environment record",
        "for a requirement that asks for new behaviour, the check failing on the base commit's code, which G6 runs",
        "the repository's test when its file exists unchanged at the base commit; a file the patch adds or changes "
        "is the patch author's, so a partial falsifier", _ORDER + "; the base-commit run is recorded when it ran",
        _BELNAP),
    Row("derived", "patch_review", "", "the review's artifact digest", "none", "none: it names no requirement",
        "the test file", "the review's environment record", "the run at the base commit, for the outcome only",
        "the repository's", "after the stated checks' run", "a regression it finds is the review's defect, never "
        "support"),
    Row("structural", "patch_review", "", "the files before and after", "none", "the stated contract",
        "the template's fixed text", "none: no code runs", _NO_CONTROL, "the contract's author",
        "read once", "a contract that holds passes no requirement (the owner's ruling R-C1)"),
    Row("proposed", "patch_review", "", "the patched and base copies", "the proposer, when a model proposed it",
        "the requirement it names", "the check's text", "the confined copies", "the base-commit run, as a "
        "classification only", "the caller or a model", "after tier 2", "a proposed check never passes a requirement"),
    Row("adopted_proposal", "patch_review", "", "none yet", "none yet", "none yet", "none yet", "none yet",
        "none yet", "none yet", "none yet", "kept for verification search's later phases; no check has it yet"),
    Row("person_confirmed", "patch_review", "independent_executable",
        "the artefact the person's answer was asked about, and each later patch it is enforced on",
        "none: the person's answer, not a model, fixes the expected outcome",
        "the confirmed expectation's standard: the question, options, answer, baseline and scope digests",
        "the example.v1 check G6 compiles from the answer: its id and its instance digest (the template, its fixed "
        "text, the bindings and their provenance)",
        "the run's environment record", "the compiled check failing on a defect G6 seeds, or on the base commit's code "
        "for new behaviour", "the person is the author; the patch's producer never is", _ORDER +
        "; a contradicting answer starts a superseding standard", _BELNAP),
)}


class PromotionRefused(ValueError):
    """A record that lacks a part the mapping table asks of its kind: never promoted, with a code for each part."""

    def __init__(self, record_id: str, codes: Sequence[tuple[str, str]]) -> None:
        self.record_id = record_id
        self.codes = tuple(codes)
        super().__init__(f"{record_id}: " + "; ".join(f"[{code}] {why}" for code, why in self.codes))


@dataclass(frozen=True)
class CheckBinding:
    """One evaluator receipt's binding (graduated from ``research.echa.custody``): the request, task and predicate it
    answers, and the digests of the artefact, standard, fixture and evaluator it was taken with."""

    request_id: str
    task_id: str
    predicate_id: str
    artifact_sha256: str
    standard_sha256: str
    fixture_sha256: str
    evaluator_sha256: str

    def __post_init__(self) -> None:
        for name in ("request_id", "task_id", "predicate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError(f"invalid {name}")
        for name in ("artifact_sha256", "standard_sha256", "fixture_sha256", "evaluator_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX64.match(value):
                raise ValueError(f"{name} must be a sha256 hex digest")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceRecord:
    """One receipt as its source gives it, in the parts the mapping table asks of every kind.

    ``controls`` are the issuer's ``Control`` values G6 observed for this check; ``seq`` and the other positions are the
    order G6 observed things in, never one a caller chose."""

    kind: str
    record_id: str
    predicates: tuple[str, ...]
    result: str
    artefact_sha256: str
    producer: str
    artefact_seq: int | None
    standard_sha256: str
    check_id: str
    check_sha256: str
    author: str
    frozen_seq: int | None
    environment_sha256: str
    seq: int | None
    scope: str
    model_sha256: str = ""
    certificate: str = ""
    controls: tuple[Control, ...] = ()
    positive_seq: int | None = None
    #: The identities a custody binding gave this record, when it came from one (``from_binding``).
    binding: CheckBinding | None = field(default=None, compare=False)

    @classmethod
    def from_binding(cls, binding: CheckBinding, *, kind: str, record_id: str, result: str, producer: str,
                     artefact_seq: int | None, author: str, frozen_seq: int | None, environment_sha256: str,
                     seq: int | None, scope: str, controls: tuple[Control, ...] = (), positive_seq: int | None = None,
                     model_sha256: str = "", certificate: str = "") -> SourceRecord:
        """A receipt bound by a custody binding: its artefact, standard and check are the binding's digests."""
        return cls(kind=kind, record_id=record_id, predicates=(binding.predicate_id,), result=result,
                   artefact_sha256=binding.artifact_sha256, producer=producer, artefact_seq=artefact_seq,
                   standard_sha256=binding.standard_sha256, check_id=f"{binding.task_id}:{binding.evaluator_sha256[:16]}",
                   check_sha256=binding.evaluator_sha256, author=author, frozen_seq=frozen_seq,
                   environment_sha256=environment_sha256, seq=seq, scope=scope, model_sha256=model_sha256,
                   certificate=certificate, controls=controls, positive_seq=positive_seq, binding=binding)


def _position(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def problems(rec: SourceRecord, *, artefact_sha256: str, standard_sha256: str,
             predicates: Collection[str]) -> list[tuple[str, str]]:
    """Each part ``rec`` lacks for its kind's row, as (code, what is missing); empty when it may be promoted."""
    row = MAPPING.get(rec.kind)
    if row is None:
        return [(UNMAPPED, f"the kind {rec.kind!r} has no row in the mapping table")]
    if not row.issuer_kind:
        return [(NOT_PROMOTED, f"{rec.kind}: {row.conflicts}")]
    found: list[tuple[str, str]] = []
    if not isinstance(rec.artefact_sha256, str) or not _HEX64.match(rec.artefact_sha256):
        found.append((NO_ARTEFACT, "no SHA-256 of the artefact bytes the result was taken on"))
    elif rec.artefact_sha256 != artefact_sha256:
        found.append((OTHER_ARTEFACT, "the result was taken on other bytes than the artefact it would count for"))
    if row.needs_model and not (isinstance(rec.model_sha256, str) and _HEX64.match(rec.model_sha256)):
        found.append((NO_MODEL, f"a {rec.kind} record names its model: {row.model}"))
    if not isinstance(rec.standard_sha256, str) or not _HEX64.match(rec.standard_sha256):
        found.append((NO_STANDARD, "no SHA-256 of the frozen standard"))
    elif rec.standard_sha256 != standard_sha256:
        found.append((OTHER_STANDARD, "the result was taken under another standard"))
    if not rec.predicates or not all(isinstance(p, str) and p for p in rec.predicates):
        found.append((NO_STANDARD, "it names no predicate of the standard"))
    elif not set(rec.predicates) <= set(predicates):
        found.append((OTHER_STANDARD, "it names a predicate the standard does not have"))
    if not _text(rec.check_id) or not isinstance(rec.check_sha256, str) or not _HEX64.match(rec.check_sha256):
        found.append((NO_CHECK, "no check id and SHA-256 of the check's frozen code"))
    if row.needs_certificate and not (isinstance(rec.certificate, str) and _HEX64.match(rec.certificate)):
        found.append((NO_CHECK, "a formal check names its certificate's digest"))
    if not isinstance(rec.environment_sha256, str) or not _HEX64.match(rec.environment_sha256):
        found.append((NO_ENVIRONMENT, "no SHA-256 of the environment the check ran in"))
    if not any(isinstance(c, Control) and c.check_sha == rec.check_sha256 and c.predicate in rec.predicates
               for c in rec.controls):
        found.append((NO_CONTROL, f"no negative control for this check and predicate: {row.negative_control}"))
    if not _text(rec.author) or not _text(rec.producer):
        found.append((NO_INDEPENDENCE, "the check's author and the artefact's producer are both named"))
    if not all(_position(v) for v in (rec.artefact_seq, rec.frozen_seq, rec.seq)) or (
            rec.positive_seq is not None and not _position(rec.positive_seq)):
        found.append((NO_ORDER, "the ledger positions of the freeze, the artefact and the receipt"))
    if not _text(rec.scope):
        found.append((NO_SCOPE, "what the result covers"))
    if rec.result not in RESULTS:
        found.append((NO_RESULT, f"a result among {RESULTS}"))
    return found


def issuer_kind(rec: SourceRecord) -> str:
    """The issuer kind ``rec`` is promoted as: its row's, except that a check its artefact's producer wrote is a
    partial falsifier (obligation A5), whatever its source calls it."""
    kind = MAPPING[rec.kind].issuer_kind
    return "partial_falsifier" if kind in A4_KINDS and rec.author == rec.producer else kind


def promote(rec: SourceRecord, *, artefact_sha256: str, standard_sha256: str,
            predicates: Collection[str]) -> tuple[Check, Receipt]:
    """The issuer's check and receipt for ``rec``, or ``PromotionRefused`` naming every part it lacks."""
    found = problems(rec, artefact_sha256=artefact_sha256, standard_sha256=standard_sha256, predicates=predicates)
    if found:
        raise PromotionRefused(rec.record_id, found)
    controls = tuple(c for c in rec.controls if c.check_sha == rec.check_sha256 and c.predicate in rec.predicates)
    check = Check(rec.check_id, tuple(rec.predicates), rec.author, issuer_kind(rec), rec.check_sha256,
                  int(rec.frozen_seq), controls, rec.certificate, rec.positive_seq)
    return check, Receipt(rec.record_id, rec.check_id, rec.check_sha256, rec.artefact_sha256, rec.result, int(rec.seq))


@dataclass(frozen=True)
class Issued:
    """What ``issue_records`` gives: the issuer's certificate, the ledger it rests on, and each refused record."""

    certificate: Certificate
    ledger: ChainLedger
    #: (record id, ((code, what is missing), ...)) for each record the mapping refused.
    refused: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    #: (record id, source kind, issuer kind) for each record promoted.
    promoted: tuple[tuple[str, str, str], ...]


def _merged(old: Check, new: Check) -> Check | None:
    """One check from two records of the same check, or None when they disagree about what the check is."""
    same = (old.author, old.kind, old.check_sha, old.frozen_seq, old.certificate, old.positive_seq) == (
        new.author, new.kind, new.check_sha, new.frozen_seq, new.certificate, new.positive_seq)
    if not same:
        return None
    controls = tuple({c.control_id: c for c in (*old.controls, *new.controls)}.values())
    return Check(old.check_id, tuple(dict.fromkeys((*old.predicates, *new.predicates))), old.author, old.kind,
                 old.check_sha, old.frozen_seq, controls, old.certificate, old.positive_seq)


def issue_records(facts: MissionFacts, artifact: Artifact, records: Iterable[SourceRecord], *,
                  decisions: Collection[str] = (), extra: Mapping[str, object] | None = None) -> Issued:
    """The issuer's certificate for ``artifact`` from the records the mapping promotes. Each promoted control and
    receipt is written to a fresh ledger with the issuer's ``record``, and beside each receipt its provenance (the
    source kind, the environment, the model and the scope), which the issuer does not read; then ``issue`` decides.
    ``extra`` is added to every provenance entry (what the caller ran, say)."""
    refused: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    checks: dict[str, Check] = {}
    receipts: dict[str, tuple[Receipt, SourceRecord]] = {}
    controls: dict[str, Control] = {}
    for rec in records:
        try:
            check, receipt = promote(rec, artefact_sha256=artifact.sha256, standard_sha256=facts.standard_sha,
                                     predicates=facts.predicates)
        except PromotionRefused as exc:
            refused.append((exc.record_id, exc.codes))
            continue
        except CertificateRefused as exc:   # a value the issuer's own types refuse (a position, a digest)
            refused.append((rec.record_id, tuple((NO_ORDER, p) for p in exc.problems)))
            continue
        merged = check if check.check_id not in checks else _merged(checks[check.check_id], check)
        if (merged is None or receipts.get(receipt.receipt_id, (receipt,))[0] != receipt
                or any(controls.get(c.control_id, c) != c for c in check.controls)):
            # one fact, one body: a check, receipt or control another record gives another body is kept out
            refused.append((rec.record_id, ((CONFLICTING, "another record gives this check, receipt or control "
                                                          "another body"),)))
            continue
        checks[check.check_id] = merged
        receipts[receipt.receipt_id] = (receipt, rec)
        controls.update({c.control_id: c for c in check.controls})
    ledger = ChainLedger()
    # the ledger's own order is the order G6 observed things in, the one each record's position states
    record(ledger, *sorted([*controls.values(), *(r for r, _ in receipts.values())],
                           key=lambda item: (item.seq, isinstance(item, Receipt))))
    for receipt, rec in receipts.values():
        ledger.append(PROVENANCE_KIND, receipt.receipt_id, {
            "source_kind": rec.kind, "issuer_kind": checks[rec.check_id].kind, "environment_sha256":
            rec.environment_sha256, "model_sha256": rec.model_sha256, "scope": rec.scope,
            **({"binding": rec.binding.to_dict()} if rec.binding else {}), **dict(extra or {})})
    certificate = issue(facts, artifact, list(checks.values()), [r for r, _ in receipts.values()],
                        decisions=decisions, ledger=ledger)
    promoted = tuple((receipt.receipt_id, rec.kind, checks[rec.check_id].kind) for receipt, rec in receipts.values())
    return Issued(certificate, ledger, tuple(refused), promoted)


def environment_sha256(environment: Mapping[str, object]) -> str:
    """The digest that names a run's environment record (canonical JSON, the ledger's own)."""
    return _digest(dict(environment))


#: The table's columns as the rendered page names them, each with the row field it shows.
COLUMNS = (("Kind", "kind"), ("Source", "source"), ("Promoted as", "issuer_kind"), ("Artefact", "artefact"),
           ("Model", "model"), ("Standard", "standard"), ("Check", "check"), ("Environment", "environment"),
           ("Negative control", "negative_control"), ("Author's independence", "independence"),
           ("Ordering and scope", "ordering"), ("Conflicting evidence", "conflicts"))
PAGE_HEAD = ("# The evidence mapping table\n\n"
             "Generated from `g6.evidence.mapping.MAPPING` by `render_markdown()`; a test keeps this page equal to the "
             "code. One row per evidence kind the product or ECHA produces (G6 0.3 design, section 3.1). A record that "
             "lacks a part its row asks for is refused promotion with a `G6_E_EVIDENCE_*` code naming the part. "
             "\"never\" under \"Promoted as\" means the kind is a lead: it supports and refutes nothing.\n\n")


def render_markdown() -> str:
    """The mapping table as the Markdown page in the design's docs folder."""
    head = "| " + " | ".join(name for name, _ in COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"
    rows = ["| " + " | ".join((getattr(row, attr) or "never").replace("|", "/") if attr == "issuer_kind" else
                              str(getattr(row, attr)).replace("|", "/") for _, attr in COLUMNS) + " |"
            for row in MAPPING.values()]
    codes = "\n".join(f"- `{code}`" for code in CODES)
    return f"{PAGE_HEAD}{head}\n{rule}\n" + "\n".join(rows) + f"\n\n## Refusal codes\n\n{codes}\n"


__all__ = ["CODES", "COLUMNS", "CONFLICTING", "MAPPING", "NOT_PROMOTED", "NO_ARTEFACT", "NO_CHECK", "NO_CONTROL",
           "NO_ENVIRONMENT", "NO_INDEPENDENCE", "NO_MODEL", "NO_ORDER", "NO_RESULT", "NO_SCOPE", "NO_STANDARD",
           "OTHER_ARTEFACT", "OTHER_STANDARD", "PROVENANCE_KIND", "UNMAPPED", "CheckBinding", "Issued",
           "PromotionRefused", "Row", "SourceRecord", "environment_sha256", "issue_records", "issuer_kind", "problems",
           "promote", "render_markdown"]
