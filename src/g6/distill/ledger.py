# SPDX-License-Identifier: Apache-2.0
"""Typed, content-addressed stores. One writer per kind, as required by ChainLedger.

Reads reload and validate the entire chain and every referenced object. Hash chains are
unanchored unless a caller supplies an independently retained expect_head. They do not
authenticate an actor who can replace an entire history and its external anchors.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import threading

from pydantic import ValidationError

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError, canonical
from g6.core.storage import state_path
from .records import (RECORD_KINDS, CapabilityExtension, Concept, FailureMode, FailureRecord, LibraryEntry,
                      MethodContract, Record, Rectification, VerifierCandidate)

_FAILURE_MUTEX = threading.RLock()
_FAILURE_LOCAL = threading.local()


@contextmanager
def failure_writer(*, reading=False):
    """One reentrant file lock for every failure writer, including other server processes."""
    from g6.core.repair_log import _process_lock
    with _FAILURE_MUTEX:
        path = state_path('distill', 'failure_records.lock')
        if getattr(_FAILURE_LOCAL, 'path', None) == path:
            yield True
            return
        if reading and not path.parent.exists():
            yield False
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with _process_lock(path):
            _FAILURE_LOCAL.path = path
            try:
                yield True
            finally:
                _FAILURE_LOCAL.path = None


class Stores:
    """Store factory; reads never append records. Failure readers share the writer's lock."""

    def for_model(self, model: type[Record], *, expect_head: str | None = None) -> RecordStore:
        for kind, cls in RECORD_KINDS.items():
            if model is cls:
                return RecordStore(self, kind, cls, expect_head=expect_head)
        raise ValueError("unknown record model")


class RecordStore:
    def __init__(self, stores: Stores, kind: str, model: type[Record], *, expect_head: str | None = None):
        self.stores, self.kind, self.model = stores, kind, model
        self.path = state_path("distill", f"{kind}.jsonl")
        self._expect_head = expect_head
        with failure_writer(reading=True) if model is FailureRecord else nullcontext(True) as locked:
            self.chain = ChainLedger(self.path if locked else None,
                                     expect_head=expect_head, validate=self._validate)

    def _validate(self, kind, ident, body, seen):
        try:
            record = self.model.model_validate_json(canonical(body))
            if kind != self.kind or record.content_id != ident:
                return ["record kind or digest identity mismatch"]
        except (ValueError, TypeError) as error:
            return [f"invalid record: {error}"]
        return []

    def _object(self, ident):
        # ident is validated before becoming a path; state_path also refuses escape.
        from .records import Sha256
        from pydantic import TypeAdapter
        TypeAdapter(Sha256).validate_python(ident)
        return state_path("distill", "objects", f"{ident}.json")

    def _read(self, *, references=True) -> list[Record]:
        if self.model is FailureRecord:
            with failure_writer(reading=True) as locked:
                if not locked:
                    if self.chain.entries() or self._expect_head:
                        raise LedgerCorrupt('previously observed failure ledger is missing')
                    return []
                if self.chain.path is None:
                    self.chain = ChainLedger(self.path, expect_head=self._expect_head, validate=self._validate)
                return self._read_locked(references=references)
        return self._read_locked(references=references)

    def _read_locked(self, *, references=True) -> list[Record]:
        self.chain.reload()
        records = []
        for row in self.chain.entries():
            path = self._object(row["id"])
            try:
                raw = path.read_text(encoding="utf-8")
                value = self.model.model_validate_json(raw)
                # Canonical comparison also detects unknown/duplicate keys in object JSON.
                if raw != canonical(row["body"]) + "\n" or value.content_id != row["id"]:
                    raise ValueError("object differs from its ledger body")
            except (ValueError, FileNotFoundError) as error:
                raise LedgerCorrupt(f"invalid content-addressed object {row['id']}: {error}") from error
            records.append(value)
        if references:
            for record in records:
                try:
                    self._references(record, writing=False)
                except LedgerError as error:
                    raise LedgerCorrupt(f"invalid record reference: {error}") from error
            if self.model is LibraryEntry:
                versions = {}
                for record in records:
                    if record.version <= versions.get(record.candidate_id, 0):
                        raise LedgerCorrupt("library versions must increase for a candidate")
                    versions[record.candidate_id] = record.version
        return records

    def list(self) -> list[Record]:
        return self._read()

    def get(self, ident: str) -> Record:
        for value in self._read():
            if value.content_id == ident:
                return value
        raise KeyError(ident)

    def _references(self, record: Record, *, writing=True, stack=frozenset()):
        key = (type(record), record.content_id)
        if key in stack:
            raise LedgerError("cyclic record reference")
        stack = stack | {key}

        def get(cls, ident):
            store = self.stores.for_model(cls)
            found = next((value for value in store._read(references=False) if value.content_id == ident), None)
            if found is None:
                raise LedgerError(f"missing {cls.__name__} reference {ident}")
            store._references(found, writing=False, stack=stack)
            return found

        if isinstance(record, FailureRecord):
            for ident in record.met_before:
                get(FailureRecord, ident)
        if isinstance(record, Concept):
            for ident in record.from_failures:
                failure = get(FailureRecord, ident)
                if failure.split != "training" or failure.observed_by != "independent":
                    raise LedgerError("concept requires independent training failures")
        if isinstance(record, VerifierCandidate):
            get(Concept, record.concept_id)
            failures = self.stores.for_model(FailureRecord)._read(references=False)
            for ident in record.training_case_ids:
                if not any(f.case_ref == ident and f.split == "training" for f in failures):
                    raise LedgerError("candidate requires training case references")
            for ident in record.lineage:
                get(LibraryEntry, ident)
            if record.form == "reuse":
                get(LibraryEntry, record.body)
            if record.form == "composition":
                for ident in record.body.entries:
                    get(LibraryEntry, ident)
        if isinstance(record, LibraryEntry):
            candidate = get(VerifierCandidate, record.candidate_id)
            contract = get(MethodContract, record.contract_sha256)
            if (candidate.concept_id != record.concept_id or candidate.contract != contract
                    or contract.family != record.family):
                raise LedgerError("entry and candidate contract/concept disagree")
            for ident in record.parents:
                get(LibraryEntry, ident)
            if writing:
                previous = self._read()
                versions = [v.version for v in previous if v.candidate_id == record.candidate_id]
                if versions and record.version <= max(versions):
                    raise LedgerError("library version must increase for a candidate")
        if isinstance(record, CapabilityExtension):
            get(FailureRecord, record.gap_record)
        if isinstance(record, FailureMode) and record.removing_check is not None:
            removal = get(Rectification, record.removing_check)
            if (removal.failure_mode != record.mode_code or not removal.class_removed or removal.outcome != "held"):
                raise LedgerError("removing check reference does not name a held class removal")
        if isinstance(record, Rectification):
            from .intake import class_key, earlier
            if record.pinning is not None:
                for ident in record.pinning.replayed:
                    if class_key(get(FailureRecord, ident)) != record.failure_mode:
                        raise LedgerError("replay references a different failure class")
            if writing:
                # Legacy unpinned open rows can be read, but no new unpinned
                # rectification (even an open proposal) can enter the log.
                from .rectify_gate import Rectifier, _key
                Rectifier(self.stores)._pinning(record, require_current=record.outcome != "failed")
                failures = self.stores.for_model(FailureRecord)._read(references=False)
                same = [row for row in failures if _key(row) == record.failure_mode]
                met = any(row.met_before or earlier([f for f in failures if f != row], row) for row in same)
                if met and record.instance_or_class != "class":
                    raise LedgerError("a class met before requires class removal")
                if record.class_removed:
                    if record.instance_or_class != "class":
                        raise LedgerError("an instance patch cannot claim class removal")
                    if not record.pinning.fixed_passed:
                        raise LedgerError("class replay must pass the fixed code")
                    if not same or set(record.pinning.replayed) != {row.record_id for row in same}:
                        raise LedgerError("class removal requires replay of every known failure")

    def put(self, value: Record) -> str:
        with failure_writer() if self.model is FailureRecord else nullcontext():
            return self._put_locked(value)

    def _put_locked(self, value: Record) -> str:
        if type(value) is not self.model:
            raise LedgerError("wrong record model")
        try:
            # A fresh parse defeats model_construct, model_copy(update=...), and mutable nested lists.
            body = value.model_dump(mode="json", warnings="error")
            record = self.model.model_validate_json(canonical(body))
        except (ValueError, TypeError, ValidationError) as error:
            raise LedgerError(f"invalid record: {error}") from error
        self._read()
        self._references(record)
        ident = record.content_id
        body = record.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        obj = self._object(ident)
        obj.parent.mkdir(parents=True, exist_ok=True)
        text = canonical(body) + "\n"
        if obj.exists():
            if obj.read_text(encoding="utf-8") != text:
                raise LedgerCorrupt("existing content-addressed object was edited")
        else:
            with obj.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        self.chain.append(self.kind, ident, body)
        return ident
