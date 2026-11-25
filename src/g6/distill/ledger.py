# SPDX-License-Identifier: Apache-2.0
"""Typed, content-addressed stores. One writer per kind, as required by ChainLedger.

Reads reload and validate the entire chain and every referenced object. Hash chains are
unanchored unless a caller supplies an independently retained expect_head. They do not
authenticate an actor who can replace an entire history and its external anchors.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import stat
import threading

from pydantic import ValidationError

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError, canonical, digest
from g6.core.storage import state_path
from .records import (RECORD_KINDS, CapabilityExtension, Concept, FailureMode, FailureRecord, LibraryEntry,
                      MethodContract, Record, Rectification, VerifierCandidate)

_FAILURE_MUTEX = threading.RLock()
_FAILURE_LOCAL = threading.local()
_UNPLANNED = object()


class JournalTargetRefused(LedgerError):
    """A durable undo journal accepts only contained state files."""
    code = 'G6_E_DISTILL_JOURNAL_TARGET'

    def __init__(self):
        super().__init__(self.code + ': journal target must be a file inside the state home')


class PartialAuthorityWrite(OSError):
    """Recovery failed; expose only freshly read state names, never diagnostics."""
    code = 'G6_E_DISTILL_PARTIAL_WRITE'

    def __init__(self, paths, *, committed=False, recovering=False):
        self.paths = tuple(dict.fromkeys((*paths, _intent_path())))
        self.committed = committed
        self.recovering = recovering
        self.publication_committed = committed and not recovering
        self.earlier_publication_committed = committed and recovering
        self.decision = None
        self.reservation = ''
        self.committed_steps = []
        self.cleanup_intent = None
        self.durable_state = durable_state(self.paths)
        state = ('earlier publication committed; cleanup incomplete; this request made no change'
                 if self.earlier_publication_committed else
                 'publication committed; intent cleanup pending' if committed else 'recovery incomplete')
        super().__init__(self.code + ': ' + state + '; durable state: ' + '; '.join(self.durable_state))


def durable_state(paths):
    states = []
    try:
        library = state_path('distill', 'library.jsonl')
        latest = {}
        for row in ChainLedger(library).entries():
            body = row['body']
            latest[body['candidate_id']] = body
        states.extend(f"library {ident} {body['status']}/{body['authority']}" for ident, body in latest.items())
        if not latest:
            states.append('library empty/A0')
    except (OSError, ValueError, KeyError, LedgerError, RecursionError):
        states.append('library unreadable/authority unknown')
    for path in paths:
        try:
            if not path.exists():
                state = 'absent'
            elif path.parent.name == 'acceptance_gate_reports':
                state = 'report/' + json.loads(path.read_text(encoding='utf-8')).get('authority', 'no authority')
            elif path.suffix == '.jsonl':
                state = f'{len(ChainLedger(path).entries())} durable rows'
            else:
                state = 'present'
        except (OSError, ValueError, LedgerError, RecursionError):
            state = 'unreadable/authority unknown'
        states.append(f'{path.name} {state}')
    return tuple(states)


def _intent_path():
    return state_path('distill', 'authority_intent.json')


def _intent_target(name):
    # Writers remain home-relative. Read absolute targets only under this home,
    # then apply the same state_path checks without normalizing malformed names.
    if not isinstance(name, str):
        raise ValueError('invalid intent target')
    if Path(name).is_absolute():
        _, tail = os.path.splitdrive(name)
        names = (tail.replace('\\', '/') if os.name == 'nt' else tail)[1:].split('/')
        if set(names) & {'', '.', '..'}:
            raise ValueError('invalid intent target')
        name = Path(name).relative_to(state_path()).as_posix()
    elif PureWindowsPath(name).anchor:
        raise ValueError('invalid intent target')
    target = state_path(name)
    path = _intent_path()
    if target in {path, path.with_suffix('.pending')}:
        raise ValueError('invalid intent target')
    return target


def _intent_name(path):
    try:
        name = Path(path).absolute().relative_to(state_path()).as_posix()
        _intent_target(name)
        return name
    except (OSError, ValueError, TypeError):
        raise JournalTargetRefused() from None


def _sync_parent(path):
    # Windows fsync flushes the file; directory handles are not openable this way.
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _save_intent(*, committed=False):
    """Replace a flushed intent before allowing any corresponding target write."""
    rows = []
    for path, content in _FAILURE_LOCAL.before.items():
        after = _FAILURE_LOCAL.after.get(path, _UNPLANNED)
        rows.append({'path': _intent_name(path),
                     'before': None if content is None else base64.b64encode(content).decode('ascii'),
                     'operation': 'replace' if after is not _UNPLANNED else 'retain',
                     'after': None if after is _UNPLANNED or after is None else
                     base64.b64encode(after).decode('ascii')})
    body = {'version': 1, 'committed': committed, 'writes': rows}
    _write_intent(body)


def _write_intent(body):
    _replace_intent((canonical({'body': body, 'sha256': digest(body)}) + '\n').encode('utf-8'))


def _replace_intent(content):
    """Publish intent bytes atomically, including an exact prior undo intent."""
    path = _intent_path()
    staging = path.with_suffix('.pending')
    with staging.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, path)
    _sync_parent(path)


def _read_intent():
    path = _intent_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        body = data['body']
        if (data['sha256'] != digest(body) or body['version'] != 1
                or type(body['committed']) is not bool or not isinstance(body['writes'], list)):
            raise ValueError('invalid intent')
        writes = {}
        for row in body['writes']:
            target = _intent_target(row['path'])
            if target in writes or row['operation'] not in {'replace', 'retain'}:
                raise ValueError('invalid intent target')
            if row['after'] is not None:
                base64.b64decode(row['after'], validate=True)
            writes[target] = None if row['before'] is None else base64.b64decode(row['before'], validate=True)
        staging = {}
        for row in body.get('undo_staging', []):
            target, copy = _intent_target(row['target']), _intent_target(row['path'])
            if (target not in writes or writes[target] is None or copy.parent != target.parent
                    or copy != state_path() / row['path']
                    or re.fullmatch(r'\.authority-undo-[0-9a-f]{32}', copy.name) is None
                    or copy in staging or copy in writes):
                raise ValueError('invalid undo staging target')
            staging[copy] = writes[target]
        return body['committed'], writes, staging
    except FileNotFoundError:
        return None
    except Exception:
        # Decoder depth/resource limits and malformed shapes are unreadable
        # intents too. Interrupts and process exits still propagate.
        raise PartialAuthorityWrite((path,), recovering=True) from None


def _retire_intent():
    path = _intent_path()
    path.unlink(missing_ok=True)
    _sync_parent(path)


def _recover_intent():
    """Always undo an uncommitted publication, including an interrupted undo."""
    intent = _read_intent()
    if intent is None:
        return
    committed, writes, staging = intent
    try:
        _remove_undo_staging(staging)
        if not committed:
            for target, content in reversed(tuple(writes.items())):
                _restore(target, content)
        _retire_intent()
    except (OSError, ValueError):
        raise PartialAuthorityWrite(tuple(writes), committed=committed, recovering=True) from None


def _remove_undo_staging(staging):
    """Remove only intent-owned undo copies with the expected name and bytes."""
    for path, content in staging.items():
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            continue
        if path.read_bytes() == content:
            path.unlink()
            _sync_parent(path)


def _plan_undo_staging(target):
    # Declare the exclusive new name durably before creating any copy. A name
    # and matching bytes alone do not establish that an unrelated file is ours.
    if _read_intent() is None:
        # The first intent publication can fail before any target write. The
        # in-memory rollback snapshot still needs durable staging ownership.
        _save_intent()
    pending = _intent_path().read_bytes()
    body = json.loads(pending)['body']
    while True:
        staging = target.parent / ('.authority-undo-' + secrets.token_hex(16))
        try:
            staging.lstat()
        except FileNotFoundError:
            break
    body.setdefault('undo_staging', []).append({'path': _intent_name(staging), 'target': _intent_name(target)})
    _write_intent(body)
    return staging, pending


def retain_before_write(path, *, recover=None, after=_UNPLANNED):
    """Persist the undo bytes and planned write before touching a target.

    Intent grows under the process lock before each newly named durable step.
    No reader trusts any of its targets until the outer transaction commits.
    The optional unplanned form retains the existing internal snapshot API.
    """
    if path is None:   # an in-memory ledger keeps nothing on disk to restore
        return
    path = _intent_target(_intent_name(path))
    before = getattr(_FAILURE_LOCAL, 'before', None)
    if before is not None:
        if path not in before:
            before[path] = path.read_bytes() if path.exists() else None
        if after is not _UNPLANNED:
            _FAILURE_LOCAL.after[path] = after
        _save_intent()
    if before is not None and recover is not None:
        _FAILURE_LOCAL.recover.append((path, recover))


def append_retained(chain, kind, ident, body, *, recover=None):
    """Plan the exact next ChainLedger bytes without changing its file API."""
    if chain.path is not None:
        entry = {'seq': len(chain.entries()), 'kind': kind, 'id': ident, 'body': body,
                 'body_sha256': digest(body), 'prev': chain.head()}
        entry['entry_sha256'] = digest({key: entry[key] for key in
                                       ('seq', 'kind', 'id', 'body_sha256', 'prev')})
        before = chain.path.read_bytes() if chain.path.exists() else b''
        after = before + (json.dumps(entry, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        retain_before_write(chain.path, recover=recover, after=after)
    return chain.append(kind, ident, body)


def _restore(path, content):
    path = _intent_target(_intent_name(path))
    if content is None:
        path.unlink(missing_ok=True)
    else:
        # Never truncate an existing inode: a backup or another home may have
        # a hard link to it. Publish new undo bytes under this name only.
        staging, pending = _plan_undo_staging(path)
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
        except BaseException:
            # Drop this declaration only after its copy is durably gone. If
            # cleanup fails, keep ownership so the next recovery can remove it.
            staging.unlink(missing_ok=True)
            _sync_parent(staging)
            _replace_intent(pending)
            raise
        else:
            staging.unlink(missing_ok=True)
    _sync_parent(path)


@contextmanager
def failure_writer(*, reading=False, finish_committed=None):
    """Short shared transaction for failure intake and library status reads/writes.

    Reentrant within a thread; the same file lock excludes other server processes.
    Method execution and recorded comparison must run outside this transaction.
    Registered writes have durable undo intent. Recovery precedes every trusted
    read/write; a process death leaves either the committed set or the prior set.
    A live reservation close may continue its exact own committed intent without
    repeating cleanup; this never bypasses an uncommitted undo.
    """
    from g6.core.repair_log import _process_lock
    with _FAILURE_MUTEX:
        path = state_path('distill', 'failure_records.lock')
        if getattr(_FAILURE_LOCAL, 'path', None) == path:
            # The outer entry recovered under the same lock. Other writers
            # cannot publish an intent until it exits; our own intent must stay.
            yield True
            return
        if reading and not path.parent.exists():
            yield False
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with _process_lock(path):
            if finish_committed is None:
                _recover_intent()
            else:
                # A live caller may close its own interrupted reservation even
                # while removal of that exact committed intent remains blocked.
                # Never bypass undo or an unrelated caller's pending intent.
                intent = _read_intent()
                if (not finish_committed.committed or finish_committed.recovering
                        or finish_committed.cleanup_intent is None or intent is None or not intent[0]
                        or _intent_path().read_bytes() != finish_committed.cleanup_intent):
                    raise PartialAuthorityWrite((_intent_path(),), recovering=True)
            _FAILURE_LOCAL.path = path
            _FAILURE_LOCAL.before = {}
            _FAILURE_LOCAL.after = {}
            _FAILURE_LOCAL.recover = []
            committing = False
            try:
                yield True
                if _FAILURE_LOCAL.before:
                    # All planned records must be on disk before the commit mark.
                    for target, content in _FAILURE_LOCAL.after.items():
                        if (target.read_bytes() if target.exists() else None) != content:
                            raise OSError('publication differs from its intent')
                        _sync_parent(target)
                    committing = True
                    _save_intent(committed=True)
            except BaseException:
                if committing:
                    # A commit-marker flush/rename can fail after replacement.
                    # Make undo durable again BEFORE changing any target back.
                    try:
                        _save_intent()
                    except OSError:
                        raise PartialAuthorityWrite(tuple(_FAILURE_LOCAL.before)) from None
                failed = []
                for target, content in reversed(tuple(_FAILURE_LOCAL.before.items())):
                    try:
                        _restore(target, content)
                    except (OSError, ValueError):
                        failed.append(target)
                for target, recover in _FAILURE_LOCAL.recover:
                    try:
                        recover()
                    except (OSError, ValueError, LedgerError):
                        failed.append(target)
                if failed:
                    raise PartialAuthorityWrite(tuple(_FAILURE_LOCAL.before)) from None
                if _FAILURE_LOCAL.before:
                    _retire_intent()
                raise
            else:
                if _FAILURE_LOCAL.before:
                    # A failed cleanup leaves a committed intent for the next
                    # lock holder; it must never undo a completed publication.
                    try:
                        _retire_intent()
                    except OSError:
                        error = PartialAuthorityWrite(tuple(_FAILURE_LOCAL.before), committed=True)
                        try:
                            error.cleanup_intent = _intent_path().read_bytes()
                        except OSError:
                            pass
                        raise error from None
            finally:
                _FAILURE_LOCAL.path = None
                _FAILURE_LOCAL.before = None
                _FAILURE_LOCAL.after = None
                _FAILURE_LOCAL.recover = None


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
        with failure_writer(reading=True):
            # Naming the file creates nothing. Even before the state directory
            # exists, direct legacy appends must persist to this same chain.
            self.chain = ChainLedger(self.path, expect_head=expect_head, validate=self._validate)

    def _validate(self, kind, ident, body, seen):
        try:
            record = self.model.model_validate_json(canonical(body), context={'distill_stored_record': True})
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
        with failure_writer(reading=True) as locked:
            if not locked:
                if self.chain.entries() or self._expect_head:
                    raise LedgerCorrupt('previously observed failure ledger is missing')
                return []
            return self._read_locked(references=references)

    def _read_locked(self, *, references=True) -> list[Record]:
        self.chain.reload()
        records = []
        for row in self.chain.entries():
            path = self._object(row["id"])
            try:
                raw = path.read_text(encoding="utf-8")
                value = self.model.model_validate_json(raw, context={'distill_stored_record': True})
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
        with failure_writer():
            return self._put_locked(value)

    def _put_locked(self, value: Record) -> str:
        if type(value) is not self.model:
            raise LedgerError("wrong record model")
        try:
            # A fresh parse defeats model_construct, model_copy(update=...), and mutable nested lists.
            body = value.model_dump(mode="json", warnings="error")
            record = self.model.model_validate_json(canonical(body), context={'distill_stored_record': True})
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
            retain_before_write(obj, after=text.encode('utf-8'))
            with obj.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        append_retained(self.chain, self.kind, ident, body, recover=self._recover_chain)
        return ident

    def _recover_chain(self):
        # A caller may retry with this same store after a failed transaction.
        self.chain = ChainLedger(self.path, expect_head=self._expect_head, validate=self._validate)
