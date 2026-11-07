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
each part it lacks; nothing it refuses reaches the issuer as evidence. A kind whose row has no issuer kind (a review, a
step that ran, a proposed check) is a lead, never a source: it is refused as ``G6_E_EVIDENCE_KIND_NOT_PROMOTED``.
``issue_records`` promotes a batch, writes what G6 observed into a fresh ``g6.core.ledger.ChainLedger`` with the issuer's
own ``record``, and asks the issuer for the certificate. The mapping refuses what is missing; the issuer judges what is
present (a control that was not killed, a check frozen after the artefact, a pass taken before its control), so the two
never overlap.

Refusing a record never drops what it observed (review 4's finding 2). Every refusal ``issue_records`` makes, whatever
its code, goes through one step: a refused record that observed anything but a pass on the artefact's bytes, for a
predicate of the standard, reaches the issuer as a caveat (``caveat_of``), the issuer's own input for one: a check with
no control, so it can neither support nor refute, and its failing or unknown receipt. A requirement with an observed
failure is then never verified, whatever another check showed. A pass that is refused withholds nothing, and one taken
on other bytes says nothing of these, as the issuer itself reads one.

What one receipt of a certificate counts for is the issuer's own reading of that receipt alone (``receipt_roles``;
review 4's finding 3), which ECHA's audit view shows: no receipt is lent the role of another receipt of its check, at
any gate of the issuer's rule (``RECEIPT_GATES``).

``CheckBinding`` is ECHA custody's binding of one evaluator receipt to its request, task, predicate, artefact, standard,
fixture and evaluator, graduated here so a receipt keeps that binding when it becomes the issuer's (``from_binding``).

What a record is about is read in one place, ``about``: from its custody binding when it has one, else from its own
fields (a record G6 observed itself, such as the patch review's, has no custody). A field that repeats a part of the
binding (the artefact, the standard, the predicate, the check) must equal it, or the record is refused with the code
that names the part (``bound_parts``, which ECHA's claim rule reads through too). A bound record's check id is derived
from every part of its binding (``check_id_of``), so receipts for two predicates, two requests or two fixtures are
never one check. A record id is one record: reused with any other part, it is refused (``CONFLICTING``), and so is a
check id another record gives another check.

What the issuer is handed is what the record says (review 2's findings 1 and 7). ``promote`` hands the issuer only
values read from the record's own statement; the issuer's other inputs, the mission's facts and the artefact, are the
ones ``issue_records`` passes to ``issue``, and each of their values a record also states (the artefact's bytes, its
producer and its ledger position; the standard and its predicates) must equal the record's, or the record is refused
with the code that names the part (``OTHER_ARTEFACT``, ``OTHER_PRODUCER``, ``OTHER_ORDER``, ``OTHER_STANDARD``). An id
has one body wherever it appears: two controls with one id and two bodies inside one record are refused, as a record,
check or control id another record gives another body is.

An id made from parts is a structure, never a joined string: ``id_of`` gives the canonical JSON of the parts (or its
digest), and every id this module and the patch review's adapter make from parts goes through it (review 2's finding 6).

A custody binding kept as text (ECHA's ``source_ref``) is read by one reader, ``read_binding``, and what a record is
about by one, ``read_about``, which both accessors (``about`` here and ECHA's ``custody.about``) use: no binding and a
binding that does not read as one (``UnreadableBinding``) are different results, and the second is always refused
(``BINDING_UNREADABLE``), never read as no binding.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

from g6.core.ledger import ChainLedger
from g6.core.ledger import canonical as _canonical
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
    grounding,
    issue,
    record,
)
from g6.evidence.certificate import _check_of as _issuers_check   # the issuer's own reading of a check it wrote

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")

#: The stable codes of a refused promotion, each naming the part of the record that is missing or wrong.
UNMAPPED = "G6_E_EVIDENCE_KIND_UNMAPPED"
NOT_PROMOTED = "G6_E_EVIDENCE_KIND_NOT_PROMOTED"
NO_ARTEFACT = "G6_E_EVIDENCE_NO_ARTEFACT"
OTHER_ARTEFACT = "G6_E_EVIDENCE_OTHER_ARTEFACT"
#: A record that names another producer of the artefact, or places it at another ledger position, than the artefact
#: it would count for.
OTHER_PRODUCER = "G6_E_EVIDENCE_OTHER_PRODUCER"
OTHER_ORDER = "G6_E_EVIDENCE_OTHER_ORDER"
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
#: A field of a record that repeats a part of its custody binding with another value, by the part it names; and a
#: binding that does not read as one.
BINDING_ARTEFACT = "G6_E_EVIDENCE_BINDING_ARTEFACT"
BINDING_STANDARD = "G6_E_EVIDENCE_BINDING_STANDARD"
BINDING_PREDICATE = "G6_E_EVIDENCE_BINDING_PREDICATE"
BINDING_CHECK = "G6_E_EVIDENCE_BINDING_CHECK"
BINDING_UNREADABLE = "G6_E_EVIDENCE_BINDING_UNREADABLE"
#: A record its source does not believe, whatever it says (the patch review's integrity problem): refused whole.
NOT_BELIEVED = "G6_E_EVIDENCE_RUN_NOT_BELIEVED"
CODES = (UNMAPPED, NOT_PROMOTED, NO_ARTEFACT, OTHER_ARTEFACT, OTHER_PRODUCER, OTHER_ORDER, NO_MODEL, NO_STANDARD,
         OTHER_STANDARD, NO_CHECK, NO_ENVIRONMENT, NO_CONTROL, NO_INDEPENDENCE, NO_ORDER, NO_SCOPE, NO_RESULT,
         CONFLICTING, BINDING_ARTEFACT, BINDING_STANDARD, BINDING_PREDICATE, BINDING_CHECK, BINDING_UNREADABLE,
         NOT_BELIEVED)
#: The ledger kind under which a promoted receipt's environment, model, scope and source are kept beside it.
PROVENANCE_KIND = "provenance"
#: The issuer kind a refused record's observation is kept as (``caveat_of``): with no control, a check of this kind can
#: neither support nor refute, and its failure is a caveat (the issuer's rule, and this table's row for the kind).
CAVEAT_KIND = "partial_falsifier"


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
        "for a requirement that asks for new behaviour, the check failing on the base commit's code, which G6 runs; for "
        "one that keeps behaviour, none in a review (the base commit is its known-good reference, not a control), so "
        "it is not counted",
        "the repository's test when its file exists unchanged at the base commit; a file the patch adds or changes "
        "is the patch author's, so a partial falsifier", _ORDER + "; the base-commit run is recorded when it ran, and "
        "each check it failed runs again on the patch after it, the receipt its control came before",
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
class UnreadableBinding:
    """A custody binding that is there and does not read as one (``read_binding``): never the same as no binding.
    Every reader refuses a record that holds one (``BINDING_UNREADABLE``)."""

    why: str


_BINDING_FIELDS = frozenset(CheckBinding.__dataclass_fields__)


def _no_repeat(pairs: list[tuple[str, object]]) -> dict:
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("a key is repeated")
        found[key] = value
    return found


def _no_constant(name: str) -> object:
    raise ValueError(f"{name} is not a JSON number")


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("a number is not finite")
    return number


def strict_json(text: str) -> object:
    """``text`` read as JSON, strictly: a repeated key, NaN, an infinity or a number too large to be finite is a
    ``ValueError``, never the last value given or a number that is not one."""
    try:
        return json.loads(text, object_pairs_hook=_no_repeat, parse_constant=_no_constant, parse_float=_finite)
    except RecursionError:
        raise ValueError("it is nested too deeply") from None


def read_binding(text: object) -> CheckBinding | UnreadableBinding | None:
    """The one reader of a custody binding kept as text (ECHA's ``source_ref``, as ``custody.checked_evidence`` writes
    it). None when the text holds no binding: it is not a JSON object (it does not begin with ``{``), so it is a plain
    reference. A JSON object is a binding's form: it reads as one, in full and strictly (``strict_json``; exactly a
    binding's fields, each valid), or it is an ``UnreadableBinding`` that says why, never no binding."""
    if not isinstance(text, str):
        return UnreadableBinding("a custody binding is kept as text")
    if not text.lstrip().startswith("{"):
        return None
    try:
        found = strict_json(text)
    except ValueError as exc:
        return UnreadableBinding(f"it does not read as JSON ({exc})")
    if not isinstance(found, dict) or set(found) != _BINDING_FIELDS:
        return UnreadableBinding("it does not hold exactly a custody binding's fields")
    try:
        return CheckBinding(**found)
    except (TypeError, ValueError) as exc:
        return UnreadableBinding(f"a field is not valid ({exc})")


def id_of(*parts: object, digest: bool = False) -> str:
    """The one way an id is made from parts (review 2's finding 6): the canonical JSON of the parts, in order (the
    ledger's own canonical form), or with ``digest`` its SHA-256, where an id must be a short token. An id is a
    structure, never a joined string: two different lists of parts never give one id, whatever characters a part
    holds, as joining them with a separator would (a pytest node id can hold any separator)."""
    return _digest(list(parts)) if digest else _canonical(list(parts))


def check_id_of(binding: CheckBinding) -> str:
    """The id of the check a custody binding names (``id_of``): its task, then the digest of every part of the
    binding (request, task, predicate, artefact, standard, fixture and evaluator), so two bindings that differ in any
    part never name one check."""
    return id_of(binding.task_id, _digest(binding.to_dict()))


@dataclass(frozen=True)
class About:
    """What a record is about (``about``), each part read once, from its custody binding when it has one."""

    artefact: str
    standard: str
    predicates: tuple[str, ...]
    #: The binding's request, task and fixture ("" for a record with no custody binding).
    request: str
    task: str
    fixture: str
    check_id: str
    check_sha: str
    #: The record's own: a custody binding does not carry the run's environment or what the result covers.
    environment: str
    scope: str


#: The parts of a custody binding a record's own fields may repeat, with the code a disagreeing field is refused with.
_REPEATED = {"artefact": BINDING_ARTEFACT, "standard": BINDING_STANDARD, "predicates": BINDING_PREDICATE,
             "check_id": BINDING_CHECK, "check_sha": BINDING_CHECK}


def bound_parts(binding: object, *, environment: str = "", scope: str = "",
                **given: object) -> tuple[About | None, tuple[tuple[str, str], ...]]:
    """What a record bound by ``binding`` is about, read from the binding, and each of ``given`` (``artefact``,
    ``standard``, ``predicates``, ``check_id``, ``check_sha``: the values the record's own fields repeat) that is not
    the binding's, as (the code that names the part, why). (None, (BINDING_UNREADABLE, why)) when ``binding`` is not a
    custody binding."""
    if not isinstance(binding, CheckBinding):
        why = f": {binding.why}" if isinstance(binding, UnreadableBinding) else ""
        return None, ((BINDING_UNREADABLE, "the record's custody binding does not read as one" + why),)
    parts = About(artefact=binding.artifact_sha256, standard=binding.standard_sha256,
                  predicates=(binding.predicate_id,), request=binding.request_id, task=binding.task_id,
                  fixture=binding.fixture_sha256, check_id=check_id_of(binding), check_sha=binding.evaluator_sha256,
                  environment=environment, scope=scope)
    found: list[tuple[str, str]] = []
    for part, value in given.items():
        held = tuple(value) if part == "predicates" and isinstance(value, (list, tuple)) else value
        if held != getattr(parts, part):
            found.append((_REPEATED[part], f"its {part.replace('_', ' ')} is not the one its custody binding names"))
    return parts, tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class SourceRecord:
    """One receipt as its source gives it, in the parts the mapping table asks of every kind.

    ``controls`` are the issuer's ``Control`` values G6 observed for this check; ``seq`` and the other positions are the
    order G6 observed things in, never one a caller chose. Every reader takes the parts a record is about from
    ``about``, never from these fields alone."""

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
    #: The custody binding that fixes what this record is about, when it came with one (``from_binding``); one that
    #: does not read as one (``UnreadableBinding``) is refused, never read as none.
    binding: CheckBinding | UnreadableBinding | None = None

    @classmethod
    def from_binding(cls, binding: CheckBinding, *, kind: str, record_id: str, result: str, producer: str,
                     artefact_seq: int | None, author: str, frozen_seq: int | None, environment_sha256: str,
                     seq: int | None, scope: str, controls: tuple[Control, ...] = (), positive_seq: int | None = None,
                     model_sha256: str = "", certificate: str = "") -> SourceRecord:
        """A receipt bound by a custody binding: the fields that repeat its parts are the binding's own."""
        parts, _ = bound_parts(binding, environment=environment_sha256, scope=scope)
        if parts is None:
            raise ValueError("a custody binding is a CheckBinding")
        return cls(kind=kind, record_id=record_id, predicates=parts.predicates, result=result,
                   artefact_sha256=parts.artefact, producer=producer, artefact_seq=artefact_seq,
                   standard_sha256=parts.standard, check_id=parts.check_id, check_sha256=parts.check_sha,
                   author=author, frozen_seq=frozen_seq, environment_sha256=environment_sha256, seq=seq, scope=scope,
                   model_sha256=model_sha256, certificate=certificate, controls=controls, positive_seq=positive_seq,
                   binding=binding)


def read_about(binding: object, *, environment: str = "", scope: str = "",
               **own: object) -> tuple[About, tuple[tuple[str, str], ...]]:
    """The one reader of what a record is about, which both accessors use (``about`` here and ECHA's
    ``custody.about``). ``binding``: the record's custody binding, None when it has none; ``own``: the parts the
    record's own fields state (``artefact``, ``standard``, ``predicates``, ``check_id``, ``check_sha``, those it has).

    With no binding, what its fields say. With one, what the binding says, and each part of ``own`` that is not the
    binding's as (the code that names the part, why). With one that does not read as one (``UnreadableBinding``, or
    anything but a ``CheckBinding``), nothing, and ``BINDING_UNREADABLE``: nothing the record says stands in for it."""
    if binding is None:
        return About(artefact=own.get("artefact", ""), standard=own.get("standard", ""),
                     predicates=own.get("predicates", ()), request="", task="", fixture="",
                     check_id=own.get("check_id", ""), check_sha=own.get("check_sha", ""), environment=environment,
                     scope=scope), ()
    parts, found = bound_parts(binding, environment=environment, scope=scope, **own)
    if parts is None:
        return About(artefact="", standard="", predicates=(), request="", task="", fixture="", check_id="",
                     check_sha="", environment=environment, scope=scope), found
    return parts, found


def about(rec: SourceRecord) -> tuple[About, tuple[tuple[str, str], ...]]:
    """What ``rec`` is about, and each of its fields that disagrees with its custody binding, through the one reader
    (``read_about``). A record with no binding is about what its fields say; one with a binding, about what the binding
    says; one whose binding does not read as one, about nothing (``BINDING_UNREADABLE``)."""
    return read_about(rec.binding, environment=rec.environment_sha256, scope=rec.scope, artefact=rec.artefact_sha256,
                      standard=rec.standard_sha256, predicates=rec.predicates, check_id=rec.check_id,
                      check_sha=rec.check_sha256)


def _position(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _controls(rec: SourceRecord) -> list[Control]:
    """The controls ``rec`` states, in its order."""
    return [c for c in rec.controls if isinstance(c, Control)] if isinstance(rec.controls, (tuple, list)) else []


def problems(rec: SourceRecord, *, facts: MissionFacts, artifact: Artifact) -> list[tuple[str, str]]:
    """Each part ``rec`` lacks for its kind's row, or states otherwise than the issuer would be handed it, as (code,
    what is missing or other); empty when it may be promoted. ``facts`` and ``artifact`` are what ``issue`` is handed
    beside the record: the mission and the artefact it would count for. Each of their values the record also states
    is compared with the record's statement (``ISSUER_SIDE``), so the issuer never judges a record by a value the
    record contradicts."""
    row = MAPPING.get(rec.kind)
    if row is None:
        return [(UNMAPPED, f"the kind {rec.kind!r} has no row in the mapping table")]
    if not row.issuer_kind:
        return [(NOT_PROMOTED, f"{rec.kind}: {row.conflicts}")]
    parts, disagree = about(rec)
    found: list[tuple[str, str]] = list(disagree)
    if not isinstance(parts.artefact, str) or not _HEX64.match(parts.artefact):
        found.append((NO_ARTEFACT, "no SHA-256 of the artefact bytes the result was taken on"))
    elif parts.artefact != artifact.sha256:
        found.append((OTHER_ARTEFACT, "the result was taken on other bytes than the artefact it would count for"))
    if row.needs_model and not (isinstance(rec.model_sha256, str) and _HEX64.match(rec.model_sha256)):
        found.append((NO_MODEL, f"a {rec.kind} record names its model: {row.model}"))
    if not isinstance(parts.standard, str) or not _HEX64.match(parts.standard):
        found.append((NO_STANDARD, "no SHA-256 of the frozen standard"))
    elif parts.standard != facts.standard_sha:
        found.append((OTHER_STANDARD, "the result was taken under another standard"))
    named = parts.predicates if isinstance(parts.predicates, (tuple, list)) else ()
    if not named or not all(isinstance(p, str) and p for p in named):
        found.append((NO_STANDARD, "it names no predicate of the standard"))
    elif not set(named) <= set(facts.predicates):
        found.append((OTHER_STANDARD, "it names a predicate the standard does not have"))
    if not _text(parts.check_id) or not isinstance(parts.check_sha, str) or not _HEX64.match(parts.check_sha):
        found.append((NO_CHECK, "no check id and SHA-256 of the check's frozen code"))
    if row.needs_certificate and not (isinstance(rec.certificate, str) and _HEX64.match(rec.certificate)):
        found.append((NO_CHECK, "a formal check names its certificate's digest"))
    if not isinstance(parts.environment, str) or not _HEX64.match(parts.environment):
        found.append((NO_ENVIRONMENT, "no SHA-256 of the environment the check ran in"))
    stated: dict[str, Control] = {}
    if any(stated.setdefault(c.control_id, c) != c for c in _controls(rec)):   # one id, one body, inside a record too
        found.append((CONFLICTING, "two of its controls have one id and two bodies"))
    if not any(c.check_sha == parts.check_sha and c.predicate in named for c in _controls(rec)):
        found.append((NO_CONTROL, f"no negative control for this check and predicate: {row.negative_control}"))
    if not _text(rec.author) or not _text(rec.producer):
        found.append((NO_INDEPENDENCE, "the check's author and the artefact's producer are both named"))
    elif rec.producer != artifact.producer:
        found.append((OTHER_PRODUCER, "it names another producer than the artefact it would count for has"))
    if not all(_position(v) for v in (rec.artefact_seq, rec.frozen_seq, rec.seq)) or (
            rec.positive_seq is not None and not _position(rec.positive_seq)):
        found.append((NO_ORDER, "the ledger positions of the freeze, the artefact and the receipt"))
    elif rec.artefact_seq != artifact.seq:
        found.append((OTHER_ORDER, "it places its artefact at another ledger position than the artefact it would "
                                   "count for has, so its order would be judged against a position it never stated"))
    if not _text(parts.scope):
        found.append((NO_SCOPE, "what the result covers"))
    if rec.result not in RESULTS:
        found.append((NO_RESULT, f"a result among {RESULTS}"))
    return found


def issuer_kind(rec: SourceRecord) -> str:
    """The issuer kind ``rec`` is promoted as: its row's, except that a check its artefact's producer wrote is a
    partial falsifier (obligation A5), whatever its source calls it."""
    kind = MAPPING[rec.kind].issuer_kind
    return "partial_falsifier" if kind in A4_KINDS and rec.author == rec.producer else kind


#: Each value ``issue`` is handed beside the records that a record also states, as (the issuer's input, its field,
#: the record's field, the code a disagreement is refused with). ``problems`` compares every one; an inventory test
#: lists the issuer's inputs from the code and fails on a field a record states that is not here.
ISSUER_SIDE = (("artifact", "sha256", "artefact_sha256", OTHER_ARTEFACT),
               ("artifact", "producer", "producer", OTHER_PRODUCER),
               ("artifact", "seq", "artefact_seq", OTHER_ORDER),
               ("facts", "standard_sha", "standard_sha256", OTHER_STANDARD),
               ("facts", "predicates", "predicates", OTHER_STANDARD))


def promote(rec: SourceRecord, *, facts: MissionFacts, artifact: Artifact) -> tuple[Check, Receipt]:
    """The issuer's check and receipt for ``rec`` under ``facts`` and for ``artifact``, each value read from the
    record's own statement (``about`` for what it is about), or ``PromotionRefused`` naming every part it lacks or
    states otherwise (``problems``)."""
    found = problems(rec, facts=facts, artifact=artifact)
    if found:
        raise PromotionRefused(rec.record_id, found)
    parts, _ = about(rec)
    controls = tuple(c for c in _controls(rec) if c.check_sha == parts.check_sha and c.predicate in parts.predicates)
    check = Check(parts.check_id, tuple(parts.predicates), rec.author, issuer_kind(rec), parts.check_sha,
                  int(rec.frozen_seq), controls, rec.certificate, rec.positive_seq)
    return check, Receipt(rec.record_id, parts.check_id, parts.check_sha, parts.artefact, rec.result, int(rec.seq))


def caveat_of(rec: SourceRecord, ident: str, *, facts: MissionFacts,
              artifact: Artifact) -> tuple[Check, Receipt] | None:
    """What a record the mapping does not promote observed, as the issuer's own input for a caveat (review 4's finding
    2), under the id ``ident``. None when it observed a pass (refusing one withholds nothing), or when what it is about
    (``about``) is not ``artifact``'s bytes or names no predicate of the standard (the issuer counts nothing taken on
    other bytes, a caveat included). Otherwise a check of its own with no control, so it can neither support nor refute
    (``CAVEAT_KIND``), for the predicates it names that the standard has, and its receipt on the artefact's bytes:
    ``fail`` for a failure, ``unknown`` for any other result (an error, or a result that does not read as one).

    What the record could not state is filled only so the issuer reads it, never so it counts: the check names the
    record's code, or with none the digest of its id and check; its receipt is placed where the record placed it, or at
    the artefact when that is not a position or comes earlier (an observation of the artefact's bytes comes after them)."""
    if rec.result == "pass":
        return None
    parts, _ = about(rec)
    if not isinstance(parts.artefact, str) or parts.artefact != artifact.sha256:
        return None
    given = parts.predicates if isinstance(parts.predicates, (tuple, list)) else ()
    named = tuple(p for p in dict.fromkeys(p for p in given if isinstance(p, str)) if p in facts.predicates)
    if not named:
        return None
    sha = parts.check_sha if isinstance(parts.check_sha, str) and _HEX64.match(parts.check_sha) else _digest(
        [str(rec.record_id), str(parts.check_id)])
    seq = rec.seq if _position(rec.seq) and rec.seq >= artifact.seq else artifact.seq
    check = Check(ident, named, rec.author if _text(rec.author) else "unnamed", CAVEAT_KIND, sha,
                  rec.frozen_seq if _position(rec.frozen_seq) else 0)
    return check, Receipt(ident, ident, sha, artifact.sha256, "fail" if rec.result == "fail" else "unknown", seq)


@dataclass(frozen=True)
class Issued:
    """What ``issue_records`` gives: the issuer's certificate, the ledger it rests on, and each refused record."""

    certificate: Certificate
    ledger: ChainLedger
    #: (record id, ((code, what is missing), ...)) for each record the mapping refused.
    refused: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    #: (record id, source kind, issuer kind) for each record promoted.
    promoted: tuple[tuple[str, str, str], ...]
    #: (record id, the caveat's check and receipt id) for each refused record whose observation reached the issuer as
    #: a caveat (``caveat_of``).
    caveats: tuple[tuple[str, str], ...] = ()


def _merged(old: Check, new: Check) -> Check | None:
    """One check from two records of the same check (each a receipt of it, with the controls each saw), or None when
    they disagree about what the check is, its predicates included: two records about other predicates are never one
    check, so neither is given the other's result."""
    same = (old.predicates, old.author, old.kind, old.check_sha, old.frozen_seq, old.certificate, old.positive_seq) == (
        new.predicates, new.author, new.kind, new.check_sha, new.frozen_seq, new.certificate, new.positive_seq)
    if not same:
        return None
    controls = tuple({c.control_id: c for c in (*old.controls, *new.controls)}.values())
    return Check(old.check_id, old.predicates, old.author, old.kind, old.check_sha, old.frozen_seq, controls,
                 old.certificate, old.positive_seq)


def issue_records(facts: MissionFacts, artifact: Artifact, records: Iterable[SourceRecord], *,
                  decisions: Collection[str] = (), extra: Mapping[str, object] | None = None,
                  not_believed: str = "") -> Issued:
    """The issuer's certificate for ``artifact`` from the records the mapping promotes. Each promoted control and
    receipt is written to a fresh ledger with the issuer's ``record``, and beside each receipt its provenance (the
    source kind, the environment, the model and the scope), which the issuer does not read; then ``issue`` decides.
    ``extra`` is added to every provenance entry (what the caller ran, say). ``not_believed``: why the caller believes
    none of the records (the patch review's integrity problem): each is refused (``NOT_BELIEVED``), as any other.

    One id, one body, wherever the two appear: a record id seen before is refused (``CONFLICTING``) when anything else
    of the record differs, what it is about included (its binding, environment and scope), and is the same record when
    nothing does; a control id is refused the same way when another record (or the same record, ``problems``) gives it
    another body, and a check id when another record gives it another check. Ids from two calls never meet here: each
    call's ledger is its own, and a ledger they are both written to refuses any id it already holds.

    Every refusal here goes through one step (``refuse``), whatever its code: what the refused record observed, unless
    it was a pass, reaches the issuer as a caveat (``caveat_of``), written to the ledger beside a provenance entry that
    names the record and why it was refused."""
    refused: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    kept: list[tuple[Check, Receipt, SourceRecord, tuple[tuple[str, str], ...]]] = []   # each refusal's caveat
    checks: dict[str, Check] = {}
    receipts: dict[str, tuple[Receipt, SourceRecord]] = {}
    controls: dict[str, Control] = {}   # the controls the promoted checks rest on, written to the ledger
    bodies: dict[str, Control] = {}     # every control a record states, by id: one body each
    seen: dict[str, SourceRecord] = {}

    def refuse(rec: SourceRecord, codes: tuple[tuple[str, str], ...]) -> None:
        refused.append((rec.record_id, codes))
        caveat = caveat_of(rec, id_of("caveat", len(kept), str(rec.record_id)), facts=facts, artifact=artifact)
        if caveat is not None:
            kept.append((*caveat, rec, codes))

    for rec in records:
        if rec.record_id in seen:
            if seen[rec.record_id] != rec:
                refuse(rec, ((CONFLICTING, "another record with this id is about another part, or says another "
                                           "thing"),))
            continue
        seen[rec.record_id] = rec
        if not_believed:
            refuse(rec, ((NOT_BELIEVED, not_believed),))
            continue
        if any(bodies.get(c.control_id, c) != c for c in _controls(rec)):
            refuse(rec, ((CONFLICTING, "another record gives one of its controls another body"),))
            continue
        for c in _controls(rec):
            bodies.setdefault(c.control_id, c)
        try:
            check, receipt = promote(rec, facts=facts, artifact=artifact)
        except PromotionRefused as exc:
            refuse(rec, exc.codes)
            continue
        except CertificateRefused as exc:   # a value the issuer's own types refuse (a position, a digest)
            refuse(rec, tuple((NO_ORDER, p) for p in exc.problems))
            continue
        merged = check if check.check_id not in checks else _merged(checks[check.check_id], check)
        if merged is None:   # one fact, one body: a check another record gives another body is kept out
            refuse(rec, ((CONFLICTING, "another record gives this check another body"),))
            continue
        checks[check.check_id] = merged
        receipts[receipt.receipt_id] = (receipt, rec)
        controls.update({c.control_id: c for c in check.controls})
    ledger = ChainLedger()
    # the ledger's own order is the order G6 observed things in, the one each record's position states
    record(ledger, *sorted([*controls.values(), *(r for r, _ in receipts.values()), *(r for _, r, _, _ in kept)],
                           key=lambda item: (item.seq, isinstance(item, Receipt))))
    for receipt, rec in receipts.values():
        parts, _ = about(rec)
        ledger.append(PROVENANCE_KIND, receipt.receipt_id, {
            "source_kind": rec.kind, "issuer_kind": checks[receipt.check_id].kind, "environment_sha256":
            parts.environment, "model_sha256": rec.model_sha256, "scope": parts.scope,
            **({"binding": rec.binding.to_dict()} if rec.binding else {}), **dict(extra or {})})
    for _, receipt, rec, codes in kept:   # what a refused record observed, and why it was refused, in words
        ledger.append(PROVENANCE_KIND, receipt.receipt_id, {
            "source_kind": str(rec.kind), "issuer_kind": CAVEAT_KIND, "kept_as": "caveat",
            "record_id": str(rec.record_id), "refused": [[str(code), str(why)] for code, why in codes],
            **dict(extra or {})})
    certificate = issue(facts, artifact, [*checks.values(), *(c for c, _, _, _ in kept)],
                        [*(r for r, _ in receipts.values()), *(r for _, r, _, _ in kept)],
                        decisions=decisions, ledger=ledger)
    promoted = tuple((receipt.receipt_id, rec.kind, checks[receipt.check_id].kind)
                     for receipt, rec in receipts.values())
    return Issued(certificate, ledger, tuple(refused), promoted,
                  tuple((str(rec.record_id), check.check_id) for check, _, rec, _ in kept))


#: Why the issuer, reading one receipt of a certificate alone, counts it neither for nor against a predicate and keeps it
#: as no caveat (``receipt_roles``): the gate of its rule the receipt did not meet, in the issuer's own order and named as
#: it names them in its gaps, or a receipt it cannot read at all. An inventory test lists the gates from the issuer.
RECEIPT_GATES = ("not_a_receipt", "receipt_for_other_bytes", "check_changed_after_freeze", "receipt_before_artifact",
                 "not_grounded", "not_in_ledger", "control_after_receipt")


def _held(body: Mapping[str, object], check: Check, receipt: Receipt) -> ChainLedger:
    """Without the ledger, the certificate's own account of what its ledger held, as ``verify`` takes it offline: the
    check's controls and the receipt, less each it lists as unresolved. Consistency, not custody."""
    missing = {(str(kind), str(ident)) for kind, ident in body.get("unresolved") or []}
    ledger, held = ChainLedger(), set()
    for item in (*check.controls, receipt):
        key = ("control", item.control_id) if isinstance(item, Control) else ("receipt", item.receipt_id)
        if key not in missing and key not in held:
            held.add(key)
            record(ledger, item)
    return ledger


def _gate(check: Check, artifact: Artifact, predicate: str, receipt: Receipt, alone: Certificate) -> str:
    """The gate of the issuer's rule ``receipt`` did not meet when the issuer, reading it alone, neither counts it for
    or against ``predicate`` nor keeps it as a caveat, in the issuer's order: the bytes, its check's frozen code, the
    artefact before it, then (a pass) the grounding rule, the ledger, and a control before it."""
    if receipt.artifact_sha != artifact.sha256:
        return "receipt_for_other_bytes"
    if receipt.check_sha != check.check_sha:
        return "check_changed_after_freeze"
    if receipt.seq < artifact.seq:
        return "receipt_before_artifact"
    if grounding(check, artifact, predicate):
        return "not_grounded"
    return "not_in_ledger" if alone.unresolved else "control_after_receipt"


def receipt_roles(body: Mapping[str, object], *, ledger: ChainLedger | None = None,
                  ) -> list[dict[str, tuple[str, str]]]:
    """For each receipt of the certificate ``body`` (``Certificate.as_dict``), in its order, the role the issuer gives
    that receipt alone for each predicate its check names, with the gate it did not meet (``RECEIPT_GATES``) when it has
    none: ``supports``, ``refutes`` or ``caveat`` exactly as ``issue`` decides for that one receipt, with the checks and
    controls the body records and ``ledger`` (without one, the body's own account of what its ledger held, ``_held``),
    else ``not counted`` (review 4's finding 3). No receipt is lent another's role. A receipt the issuer cannot read (a
    result, a position or a check it has no word for), or a body that does not record what it was decided from, is not
    counted for any predicate."""
    names = tuple(str(statement["predicate"]) for statement in body["predicates"])
    facts = MissionFacts(str(body["mission_sha"]), str(body["standard_sha"]), str(body["standard_sha"]), names, names)
    try:
        artifact = Artifact(body["artifact_sha"], body["producer"], body["artifact_seq"])
        checks = [_issuers_check(raw) for raw in body["checks"]]
    except (CertificateRefused, KeyError, TypeError):   # it does not record what it was decided from (an older one)
        artifact, checks = None, []
    by_id = {check.check_id: check for check in checks}
    found: list[dict[str, tuple[str, str]]] = []
    for raw in body["receipts"]:
        check = by_id.get(raw.get("check_id")) if isinstance(raw, Mapping) else None
        try:
            receipt = Receipt(**raw)
            if artifact is None or check is None:
                raise TypeError("no check or artefact to read it with")
            alone = issue(facts, artifact, checks, [receipt],
                          ledger=ledger if ledger is not None else _held(body, check, receipt))
        except (CertificateRefused, TypeError, ValueError):
            found.append(dict.fromkeys(names, ("not counted", "not_a_receipt")))
            continue
        roles: dict[str, tuple[str, str]] = {}
        for statement in alone.predicates:
            if statement.predicate not in check.predicates:
                continue
            if receipt.check_id in statement.supported_by:
                roles[statement.predicate] = ("supports", "")
            elif receipt.check_id in statement.refuted_by:
                roles[statement.predicate] = ("refutes", "")
            elif receipt.check_id in statement.caveats:
                roles[statement.predicate] = ("caveat", "")
            else:
                roles[statement.predicate] = ("not counted", _gate(check, artifact, statement.predicate, receipt,
                                                                   alone))
        found.append(roles)
    return found


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


__all__ = ["BINDING_ARTEFACT", "BINDING_CHECK", "BINDING_PREDICATE", "BINDING_STANDARD", "BINDING_UNREADABLE",
           "CAVEAT_KIND", "CODES", "COLUMNS", "CONFLICTING", "ISSUER_SIDE", "MAPPING", "NOT_BELIEVED", "NOT_PROMOTED",
           "NO_ARTEFACT", "NO_CHECK", "NO_CONTROL", "NO_ENVIRONMENT", "NO_INDEPENDENCE", "NO_MODEL", "NO_ORDER",
           "NO_RESULT", "NO_SCOPE", "NO_STANDARD", "OTHER_ARTEFACT", "OTHER_ORDER", "OTHER_PRODUCER", "OTHER_STANDARD",
           "PROVENANCE_KIND", "RECEIPT_GATES", "UNMAPPED", "About", "CheckBinding", "Issued", "PromotionRefused", "Row",
           "SourceRecord", "UnreadableBinding", "about", "bound_parts", "caveat_of", "check_id_of",
           "environment_sha256", "id_of", "issue_records", "issuer_kind", "problems", "promote", "read_about",
           "read_binding", "receipt_roles", "render_markdown", "strict_json"]
