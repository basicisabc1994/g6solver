# SPDX-License-Identifier: Apache-2.0
"""Completion-owned paths and mutations, checked before each filesystem operation."""
from __future__ import annotations

import contextvars
import errno
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from g6.core import cloud_consent as cloud
from g6.core.storage import PathRefused, g6_home, plain_file
from g6.workspace._coordination import Coordination, bookkeeping_path
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


TASK_UNREADABLE = 'G6_E_COMPLETION_TASK_UNREADABLE'
UNREADABLE_TASK = ('The retained completion task cannot be read against its receipt. '
                   'Start a new session over MCP; the next prompt can start a new loop.')
PUBLICATION_WAIT = 0.3
_LOCKS = contextvars.ContextVar('completion_publication_locks', default=())
_ATTEMPT = contextvars.ContextVar('completion_publication_attempt', default=None)
_TASK_WRITE = contextvars.ContextVar('completion_task_write', default=None)
_RESTORE_FILES = contextvars.ContextVar('completion_restore_files', default=None)
_STAGING_ERRORS: dict[Path, str] = {}


@contextmanager
def _locked(target: Path, *, sweep: bool = True) -> Iterator[None]:
    """Every destination's producers and restorers use the same OS mutex."""
    target = path(target)
    restore = _RESTORE_FILES.get()
    if (restore is not None and restore.active and restore.thread == threading.get_ident()
            and target in restore.paths):
        # Backups/markers belong to the already-held destination mutex, not
        # the home mutex that their UUID filenames would otherwise select.
        target = restore.target
    mkdir(target.parent, parents=True, exist_ok=True)
    task = _TASK_WRITE.get()
    if (task is not None and task.active and task.thread == threading.get_ident()
            and target.parent == task.staging):
        # Exclusively created, attempt-private preparation is not a retained
        # destination and has no other producer or persistent mutex to keep.
        yield
        return
    # A content file, its receipt and both spares share a destination identity.
    family = target.with_name(target.name.removesuffix('.new')).with_suffix('')
    run_owned = family.name in ('state', 'report', '.write-version')
    if run_owned:
        # The loop already pins this OS mutex. Its guard also serializes copied
        # observer contexts; an expired owner must acquire the OS lock anew.
        from g6.harness.completion_loop import _WRITER

        owner = _WRITER.get()
        if owner is not None and owner.session == target.parent:
            with owner.guard:
                if owner.active:
                    yield
                    return
    # Non-run publications share the trusted home's mutex. Its folder exists
    # before entry; no destination-specific lock directory must be published.
    lock = path(target.parent, '.step') if run_owned else home_path()
    identity = (os.path.normcase(str(lock)), threading.get_ident())
    held = _LOCKS.get()
    if any(key == identity and scope[0] for key, scope in held):
        yield
        return
    mkdir(lock, parents=True, exist_ok=True)
    with Coordination(lock).mutex():
        active = [True]
        token = _LOCKS.set((*held, (identity, active)))
        try:
            if sweep and not run_owned:
                _sweep_staging()
            yield
        finally:
            active[0] = False
            _LOCKS.reset(token)


def _snapshot(target: Path) -> dict[Path, bytes | None]:
    return {item: item.read_bytes() if item.is_file() else None for item in _retained_paths(target)[:2]}


class _Publication:
    def __init__(self, target: Path, *, snapshot: bool = True) -> None:
        self.target = path(target)
        self.previous = _snapshot(self.target) if snapshot else {}
        self.written = dict(self.previous)
        self.spares = {}
        self.spare_paths = {item: item for item in _retained_paths(self.target)[2:]}
        self.active = True
        self.thread = threading.get_ident()

    def includes(self, target: Path) -> bool:
        return (self.active and self.thread == threading.get_ident()
                and (target in _retained_paths(self.target) or target in self.spare_paths.values()))

    def wrote(self, target: Path, body: bytes) -> None:
        if target in self.written:
            self.written[target] = body


class _TaskWrite:
    def __init__(self, target: Path) -> None:
        self.target = path(target)
        self.attempt = None
        self.stack = ExitStack()
        self.staging = None
        self.thread = threading.get_ident()
        self.active = True

    def includes(self, target: Path) -> bool:
        return (self.active and self.thread == threading.get_ident()
                and (target in _retained_paths(self.target)
                     or self.attempt is not None and target in self.attempt.spare_paths.values()))


@contextmanager
def _mutation(target: Path) -> Iterator[_Publication | None]:
    target = path(target)
    attempt, task = _ATTEMPT.get(), _TASK_WRITE.get()
    if attempt is None or not attempt.includes(target):
        attempt = None
        if task is not None and task.includes(target):
            # A replacement of the two-argument writer can use the gated raw
            # operations. Pin its destination from its first mutation to exit.
            if task.attempt is None:
                task.stack.enter_context(_locked(task.target))
                task.attempt = _Publication(task.target)
            attempt = task.attempt
    with _locked(attempt.target if attempt is not None else target):
        yield attempt


class TaskUnreadable(ValueError):
    def __init__(self) -> None:
        super().__init__(f'{TASK_UNREADABLE}: {UNREADABLE_TASK}')


class _RestorePending(PermissionError):
    code = 'G6_E_COMPLETION_RESERVATION_UNSAVED'

    def __init__(self, refusal: OSError | None = None) -> None:
        self.refusal = refusal
        super().__init__(errno.EACCES, 'The request reservation could not be saved: the prior retained publication '
                         'is waiting for a sharing refusal to clear. This request was not sent. Retry this call.')


def task_path(target: Path) -> Path:
    """The latest binding, including an interrupted recovery binding."""
    session = path(target.parent)
    # Discover recovery families before choosing the latest binding. A first
    # publication may disappear when its absent prior pair is restored.
    targets = {path(target)}
    if session.is_dir():
        for child in session.iterdir():
            # Include private restore records even if neither prior half nor
            # an owned spare remains to announce the recovery family.
            stem = child.name.removeprefix('.').split('.')[0]
            if stem.startswith('task-') and stem[5:].isdigit():
                targets.add(path(session, stem + '.json'))
    for item in sorted(targets):
        with _pending_access(item):
            pass
    names = [child.name.removesuffix('.new') for child in session.iterdir()] if session.is_dir() else []
    numbers = [int(Path(name).stem[5:]) for name in names
               if name.startswith('task-') and Path(name).suffix in ('.json', '.sha256')
               and Path(name).stem[5:].isdigit()]
    return path(session, f'task-{max(numbers)}.json' if numbers else target.name)


def task_present(target: Path) -> bool:
    target = task_path(target)
    return target.is_file() or any(item.exists() for item in retained_paths(target))


def read_task(target: Path) -> dict:
    target = task_path(target)
    # A recoverable sharing refusal must escape the unreadable-task conversion.
    with task_publication(target):
        return _read_task(target)


def _read_task(target: Path) -> dict:
    try:
        expected = path(target.with_suffix('.sha256')).read_text(encoding='ascii')
        asked = json.loads(cloud.read_retained(target, expected))
        if (not isinstance(asked, dict) or not {'repo', 'task', 'base'} <= asked.keys()
                or any(not isinstance(asked[key], str) for key in ('repo', 'task', 'base'))
                or type(asked.get('run', 0)) is not int or asked.get('run', 0) < 0):
            raise ValueError('malformed retained task')
        return asked
    except (OSError, TypeError, ValueError, RecursionError, MemoryError):
        cloud.record_sources((cloud.UNKNOWN,))
        raise TaskUnreadable() from None


def home_path(*parts: str) -> Path:
    # The home is the trust anchor, just as in state_path. Never resolve a
    # descendant before the gate has inspected its original components.
    return path(g6_home(), *parts)


def path(folder: Path, *parts: str) -> Path:
    given = Path(folder).absolute().joinpath(*parts)
    home = g6_home().absolute()
    root = home.resolve()
    if given.is_relative_to(home):
        given = root / given.relative_to(home)
    checked = bookkeeping_path(given)
    try:
        if os.path.lexists(checked) and not checked.is_dir():
            plain_file(checked)
    except PathRefused as exc:
        raise WorkspaceRefused(OUTSIDE_ROOT, exc.reason) from None
    return checked


def mkdir(folder: Path, **kwargs) -> None:
    path(folder).mkdir(**kwargs)


def write_bytes(target: Path, body: bytes) -> None:
    with _mutation(target) as attempt:
        _reserve_spare(target, attempt)
        path(target).write_bytes(body)
        if attempt is not None:
            attempt.wrote(path(target), body)


def write_text(target: Path, text: str, **kwargs) -> None:
    with _mutation(target) as attempt:
        _reserve_spare(target, attempt)
        path(target).write_text(text, **kwargs)
        if attempt is not None:
            attempt.wrote(path(target), path(target).read_bytes())


def unlink(target: Path, **kwargs) -> None:
    target = path(target)
    if kwargs.get('missing_ok') and not target.exists():
        return
    with _locked(target):
        path(target).unlink(**kwargs)


def touch(target: Path) -> None:
    with _locked(target):
        path(target).touch()


def retained_paths(target: Path) -> tuple[Path, ...]:
    with _pending_access(path(target)):
        return _retained_paths(target)


def _retained_paths(target: Path) -> tuple[Path, ...]:
    receipt = target.with_suffix('.sha256')
    return tuple(path(p) for p in (target, receipt, target.with_name(target.name + '.new'),
                                  receipt.with_name(receipt.name + '.new')))


def _file_identity(target: Path) -> tuple[int, int]:
    status = path(target).stat()
    return status.st_dev, status.st_ino


def _reserve_spare(target: Path, attempt: _Publication | None) -> None:
    target = path(target)
    if attempt is None or target not in attempt.spare_paths.values():
        return
    if target.is_file() and attempt.spares.get(target) == _file_identity(target):
        return
    # Reuse the gate's descriptor writer to create an empty spare exclusively.
    # It records the opened file's identity before any content can fail to write.
    append_text(target, '', exclusive=True)


class _RetainedPath(type(Path())):
    """Route the bound producer's spare writes through creation ownership."""

    def with_name(self, name: str) -> Path:
        locations = getattr(self, '_spare_paths', {})
        requested = super().with_name(name)
        selected = _RetainedPath(locations.get(Path(requested), requested))
        selected._spare_paths = locations
        return selected

    def with_suffix(self, suffix: str) -> Path:
        selected = _RetainedPath(super().with_suffix(suffix))
        selected._spare_paths = getattr(self, '_spare_paths', {})
        return selected

    def write_bytes(self, body: bytes) -> int:
        write_bytes(Path(self), body)
        return len(body)


def _clear_spare_cleanup(spare: Path) -> None:
    try:
        unlink(spare.with_name(spare.name + '.cleanup'), missing_ok=True)
    except OSError as exc:
        _STAGING_ERRORS[spare] = str(exc)
    else:
        _STAGING_ERRORS.pop(spare, None)


def _recover_spares(attempt: _Publication, protected: set[Path] | None = None) -> None:
    """Only a destination mutex holder may adopt a leftover as orphaned.

    Inspect once before this attempt creates anything. A later replacement of
    an owned spare must still fail the creation-identity check, never be adopted.
    Cleanup records beside spares are retried here under their destination's
    mutex, rather than by the home-mutex staging-directory sweep.
    """
    try:
        names = {item.name.removesuffix('.cleanup') for item in attempt.target.parent.iterdir()}
    except FileNotFoundError:
        # An absent destination directory cannot hold an orphan. In particular,
        # a replaced two-argument publisher need not create a directory on disk.
        names = set()
    for fixed in attempt.spare_paths:
        prefix = fixed.name.removesuffix('.new') + '.'
        candidates = {fixed}
        for name in names:
            unique = name.removeprefix(prefix).removesuffix('.new')
            if (name.startswith(prefix) and name.endswith('.new') and len(unique) == 32
                    and all(char in '0123456789abcdef' for char in unique)):
                candidates.add(path(fixed.parent, name))
        for spare in sorted(candidates):
            if protected is not None and spare in protected:
                continue
            if not spare.exists():
                _clear_spare_cleanup(spare)
                continue
            _defer_staging(spare, RuntimeError('Orphan spare found under its destination mutex'))
            try:
                unlink(spare)
            except OSError as exc:
                _defer_staging(spare, exc)
                if spare == fixed:
                    attempt.spare_paths[fixed] = path(fixed.parent, prefix + uuid.uuid4().hex + '.new')
            else:
                _clear_spare_cleanup(spare)


def _remove_spares(spares: dict) -> None:
    refused = None
    for item, identity in spares.items():
        item = path(item)
        try:
            if item.is_file() and _file_identity(item) == identity:
                unlink(item)
        except OSError as exc:
            refused = exc
    if refused is not None:
        raise refused


def _sharing(error: OSError) -> bool:
    return isinstance(error, PermissionError) and (
        error.errno in (errno.EACCES, errno.EPERM) or getattr(error, 'winerror', None) in (5, 32, 33))


def _sharing_retry(operation, deadline: float) -> None:
    while True:
        try:
            operation()
            return
        except OSError as exc:
            if not _sharing(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _restore_markers(target: Path) -> tuple[Path, ...]:
    # Restore records use the existing spare.cleanup namespace. The content
    # filename family carries the record for the whole pair, including absence.
    # New records are private metadata; still finish older, visible records.
    return tuple(path(item) for prefix in (target.name, '.' + target.name)
                 for item in target.parent.glob(prefix + '.*.new.cleanup'))


class _RestoreFiles:
    def __init__(self, target: Path) -> None:
        self.target = path(target)
        self.paths = set()
        self.thread = threading.get_ident()
        self.active = True


@contextmanager
def _restoring(target: Path) -> Iterator[_RestoreFiles]:
    with _locked(target):
        restore = _RestoreFiles(target)
        token = _RESTORE_FILES.set(restore)
        try:
            yield restore
        finally:
            restore.active = False
            _RESTORE_FILES.reset(token)


def _restore_spare(item: Path, body: bytes, owned: dict, *, suffix: str = '.new') -> Path:
    spare = path(item.with_name(item.name + '.' + uuid.uuid4().hex + suffix))
    _RESTORE_FILES.get().paths.add(spare)
    append_text(spare, body, exclusive=True, owned=owned)
    sync_state(spare.parent, (spare.name,))
    return spare


def _discard_restore_spares(owned: dict) -> None:
    for spare, identity in owned.items():
        try:
            # The destination mutex is already held. A UUID backup is not a
            # different destination that needs the home's publication mutex.
            if spare.is_file() and _file_identity(spare) == identity:
                unlink(spare)
        except OSError as exc:
            _defer_staging(spare, exc)


def _restore_record(target: Path, marker: Path) -> dict | None:
    """A marker grants authority only over this pair and its creation-owned backups."""
    try:
        record = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('restore') != target.name:
            return None
        entries = record['entries']
        names = [target.name, target.with_suffix('.sha256').name]
        if not isinstance(entries, list) or len(entries) != 2:
            return None
        for entry, name in zip(entries, names):
            if not isinstance(entry, dict) or entry.get('name') != name:
                return None
            spare, identity = entry.get('spare'), entry.get('identity')
            if spare is None:
                if identity is not None:
                    return None
                continue
            prefix = name + '.'
            unique = spare.removeprefix(prefix).removesuffix('.new') if isinstance(spare, str) else ''
            if (not isinstance(spare, str) or not spare.startswith(prefix) or not spare.endswith('.new')
                    or len(unique) != 32 or any(char not in '0123456789abcdef' for char in unique)
                    or not isinstance(identity, list) or len(identity) != 2
                    or any(type(value) is not int or value < 0 for value in identity)):
                return None
        spares = record.get('spares', [])
        if not isinstance(spares, list):
            return None
        for entry in spares:
            if not isinstance(entry, dict):
                return None
            name, identity = entry.get('name'), entry.get('identity')
            fixed = [item.name for item in _retained_paths(target)[2:]]
            private = any(isinstance(name, str) and name.startswith(item.removesuffix('.new') + '.')
                          and name.endswith('.new') and len(name) == len(item) + 33
                          and all(char in '0123456789abcdef' for char in name[-36:-4]) for item in fixed)
            if (name not in fixed and not private or not isinstance(identity, list) or len(identity) != 2
                    or any(type(value) is not int or value < 0 for value in identity)):
                return None
        expected = record.get('expected')
        if expected is not None and (not isinstance(expected, list) or not expected or any(
                not isinstance(pair, list) or len(pair) != 2 or any(
                    digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                            or any(char not in '0123456789abcdef' for char in digest))
                    for digest in pair) for pair in expected)):
            return None
        return record
    except (FileNotFoundError, ValueError, KeyError, TypeError, RecursionError):
        return None


def _finish_restore(target: Path, marker: Path) -> None:
    try:
        with _restoring(target) as restore:
            restore.paths.add(path(marker))
            _finish_restore_record(target, marker)
    except OSError as exc:
        if _sharing(exc):
            raise _RestorePending(exc) from None
        raise


def _finish_restore_record(target: Path, marker: Path) -> None:
    record = _restore_record(target, marker)
    entries = record['entries'] if record is not None else None
    prior, backups = {}, {}
    if entries is not None:
        for entry in entries:
            item = path(target.parent, entry['name'])
            if entry['spare'] is None:
                prior[item] = None
                continue
            spare = path(target.parent, entry['spare'])
            identity = tuple(entry['identity'])
            try:
                if _file_identity(spare) != identity:
                    entries = None
                    break
                body = spare.read_bytes()
                if _file_identity(spare) != identity:
                    entries = None
                    break
            except FileNotFoundError:
                entries = None
                break
            backups[spare], prior[item] = identity, body
            _RESTORE_FILES.get().paths.add(spare)
    if entries is None:
        # Lost/replaced backups confer no restoration authority. Preserve the
        # existing reader's refusal, and never remove a foreign replacement.
        unlink(marker, missing_ok=True)
        return
    owned_spares = {path(target.parent, entry['name']): tuple(entry['identity'])
                    for entry in record.get('spares', [])}
    _RESTORE_FILES.get().paths.update(owned_spares)
    current = _snapshot(target)
    expected = record.get('expected')
    if expected is not None and _pair_digest(current) not in expected:
        # A later producer owns a different pair. The recorded attempt grants
        # no authority over those bytes, even after a delayed restoration.
        _remove_spares(owned_spares)
        unlink(marker, missing_ok=True)
        _discard_restore_spares(backups)
        return
    installs = {}
    deadline = time.monotonic() + PUBLICATION_WAIT
    try:
        for item, body in prior.items():
            if current[item] == body:
                continue
            if body is None:
                _sharing_retry(lambda: unlink(item, missing_ok=True), deadline)
            else:
                spare = _restore_spare(item, body, installs)
                _sharing_retry(lambda: replace(spare, item), deadline)
        # Sources stay intact until BOTH halves are installed and flushed, so
        # a failed receipt restore can resume after a successful content restore.
        sync_state(target.parent, tuple(item.name for item, body in prior.items() if body is not None))
        # A restored pair may still have a held attempt-owned spare. Keep the
        # record (and backups) until its identities no longer need cleanup;
        # a later read has no authority to adopt an unmarked spare as orphaned.
        _remove_spares(owned_spares)
        unlink(marker)
    finally:
        _discard_restore_spares(installs)
    _discard_restore_spares(backups)


@contextmanager
def _pending_access(target: Path) -> Iterator[None]:
    # The no-record path retains task preparation before the home-mutex wait,
    # and the existing read-only fallback while another writer owns a run.
    attempt = _ATTEMPT.get()
    if attempt is not None and attempt.includes(target):
        yield
        return
    if not _restore_markers(target):
        yield
        return
    with _locked(target):
        for marker in _restore_markers(target):
            _finish_restore(target, marker)
        yield


def _pair_digest(pair: dict[Path, bytes | None]) -> list[str | None]:
    return [cloud.sha256(body) if body is not None else None for body in pair.values()]


def _restore_retained(target: Path, previous: dict[Path, bytes | None], *,
                      written: dict[Path, bytes | None] | None = None,
                      spares: dict | None = None, spare_paths: dict | None = None,
                      alternatives: tuple[dict, ...] = ()) -> None:
    with _restoring(target) as restore:
        target = path(target)
        owned_spares = dict(spares or {})
        restore.paths.update(owned_spares)
        try:
            backups, records, entries = {}, {}, []
            marker = None
            try:
                for item, old in previous.items():
                    spare = _restore_spare(item, old, backups) if old is not None else None
                    entries.append({'name': item.name, 'spare': spare.name if spare is not None else None,
                                    'identity': backups[spare] if spare is not None else None})
                # Use the existing spare.cleanup record namespace, including
                # when both prior halves were absent and need only deletion.
                record = {'restore': target.name, 'entries': entries,
                          'spares': [{'name': item.name, 'identity': identity}
                                     for item, identity in owned_spares.items()]}
                if written is not None:
                    expected = []
                    for candidate in (written, *alternatives):
                        # Recovery can be interrupted after either half. Admit
                        # only the attempt's known pair and its restoration prefixes.
                        pair = dict(candidate)
                        expected.append(_pair_digest(pair))
                        for item, old in previous.items():
                            pair[item] = old
                            expected.append(_pair_digest(pair))
                    record['expected'] = expected
                marker = _restore_spare(target.with_name('.' + target.name), json.dumps(record).encode('utf-8'),
                                        records, suffix='.new.cleanup')
                sync_state(target.parent, (marker.name,))
            except BaseException:
                _discard_restore_spares(backups)
                _discard_restore_spares(records)
                raise
            _finish_restore(target, marker)
        finally:
            # A held spare must not prevent cleanup of its siblings. The
            # durable marker retains identities for anything still held.
            try:
                _remove_spares(owned_spares)
            except OSError:
                # The pending record keeps these identities even after both
                # prior halves are restored. Only a writer may adopt orphans.
                pass


@contextmanager
def task_publication(target: Path) -> Iterator[None]:
    """Finish deferred restoration before a task write or a retained read.

    With no marker, writers retain their preparation order and two-argument
    seam; readers retain their existing digest-checked, read-only path.
    """
    with _pending_access(path(target)):
        with _task_publication(target):
            yield


@contextmanager
def _task_publication(target: Path) -> Iterator[None]:
    """Roll back a caught task write error, including through a replaced writer."""
    task = _TaskWrite(target)
    token = _TASK_WRITE.set(task)
    try:
        yield
    except OSError as exc:
        if task.attempt is not None and not _restore_markers(path(target)):
            attempt = task.attempt
            try:
                _restore_retained(target, attempt.previous, written=attempt.written, spares=attempt.spares,
                                  spare_paths=attempt.spare_paths)
            except _RestorePending:
                exc.pending = bool(_restore_markers(path(target)))
                raise exc from None
        raise
    finally:
        task.active = False
        task.stack.close()
        _TASK_WRITE.reset(token)


def write_retained(target: Path, body: bytes) -> None:
    """Retry identified sharing errors and restore the prior publication on exhaustion."""
    target, receipt, content_spare, receipt_spare = _retained_paths(target)
    mkdir(target.parent, parents=True, exist_ok=True)
    # A task's preparation is private. Its destination is untouched until the
    # common mutex is held. State/report producers already share the run mutex.
    staging = None
    task = _TASK_WRITE.get()
    try:
        if task is not None and task.includes(target):
            staging = home_path('completion_staging', uuid.uuid4().hex)
            mkdir(staging, parents=True, mode=0o700)
            task.staging = staging
            prepared, prepared_receipt = path(staging, content_spare.name), path(staging, receipt_spare.name)
            write_bytes(prepared, body)
            write_bytes(prepared_receipt, cloud.sha256(body).encode('ascii'))
            # Publish the same already-serialized bytes. Preparation has no
            # authority to replace them with bytes read back from scratch.
        # The home-lock path sweeps before publishing. Run-owned writes keep
        # their run mutex; a prompt can save its new run before staging a task.
        with _locked(target):
            for marker in _restore_markers(target):
                try:
                    _finish_restore(target, marker)
                except _RestorePending as pending:
                    # This is a writer, so return the recovery operation's
                    # own failure without the reader's retry statement.
                    pending.refusal.pending = True
                    raise pending.refusal from None
            attempt = _Publication(target)
            if task is not None and task.includes(target):
                task.attempt = attempt
            token = _ATTEMPT.set(attempt)
            try:
                _recover_spares(attempt)
                producer = _RetainedPath(target)
                producer._spare_paths = attempt.spare_paths
                deadline = time.monotonic() + PUBLICATION_WAIT
                while True:
                    try:
                        _retained_paths(target)
                        cloud.write_retained(producer, body)
                        attempt.written = {target: body, receipt: cloud.sha256(body).encode('ascii')}
                        return
                    except OSError as exc:
                        completed = {target: body, receipt: cloud.sha256(body).encode('ascii')}
                        partial = {target: body, receipt: attempt.written[receipt]}
                        sharing = _sharing(exc)
                        if sharing and time.monotonic() < deadline:
                            time.sleep(0.01)
                            continue
                        if sharing or task is not None and task.includes(target):
                            try:
                                _restore_retained(target, attempt.previous, written=attempt.written, spares=attempt.spares,
                                                  spare_paths=attempt.spare_paths, alternatives=(partial, completed))
                            except _RestorePending:
                                # Rollback may be deferred; the save still owns
                                # its original failure, never reservation wording.
                                exc.pending = bool(_restore_markers(target))
                                raise exc from None
                        # Unclassified state tears retain their unknown count.
                        raise
            finally:
                attempt.active = False
                _ATTEMPT.reset(token)
    finally:
        if staging is not None:
            try:
                remove_tree(staging)
            except Exception as exc:
                _defer_staging(staging, exc)
            finally:
                task.staging = None


def _defer_staging(staging: Path, error: Exception) -> None:
    # A cleanup error must never escape into the caller's publication rollback,
    # including an error while persisting its sweep record. Orphan-spare
    # records use this path too, but are drained only under their own mutex.
    _STAGING_ERRORS[staging] = str(error)
    try:
        with _locked(home_path('completion_staging'), sweep=False):
            write_text(staging.with_name(staging.name + '.cleanup'), str(error), encoding='utf-8')
    except Exception:
        pass


def _sweep_staging() -> None:
    root = home_path('completion_staging')
    with _locked(root, sweep=False):
        if not root.is_dir():
            return
        # Only a failed cleanup authorizes sweeping. An unmarked directory may
        # belong to another process still preparing or paused before cleanup.
        pending = {item.with_name(item.name.removesuffix('.cleanup')) for item in root.iterdir()
                   if item.name.endswith('.cleanup') and item.is_file()}
        pending.update(item for item in _STAGING_ERRORS if item.parent == root)
        for child in pending:
            try:
                remove_tree(path(child))
                unlink(child.with_name(child.name + '.cleanup'), missing_ok=True)
            except (OSError, WorkspaceRefused) as exc:
                _STAGING_ERRORS[child] = str(exc)
            else:
                _STAGING_ERRORS.pop(child, None)


def sync_state(session: Path, names: tuple[str, ...]) -> None:
    for name in names:
        with path(session, name).open('r+b') as stream:
            os.fsync(stream.fileno())
    if os.name != 'nt':
        descriptor = os.open(path(session), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def check_tree(folder: Path) -> Path:
    folder = path(folder)
    if folder.is_dir():
        for child in folder.iterdir():
            checked = path(child)
            if checked.is_dir():
                check_tree(checked)
    return folder


def remove_tree(folder: Path) -> None:
    folder = check_tree(folder)
    if not folder.exists():
        return
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        if folder.exists():
            raise


def append_text(target: Path, text: str | bytes, *, exclusive: bool = False, owned: dict | None = None) -> None:
    with _mutation(target) as attempt:
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_APPEND)
        fd = os.open(path(target), flags, 0o600)
        try:
            if exclusive and (attempt is not None or owned is not None):
                opened = os.fstat(fd)
                identity = (opened.st_dev, opened.st_ino)
                if attempt is not None:
                    attempt.spares[path(target)] = identity
                if owned is not None:
                    owned[path(target)] = identity
            binary = isinstance(text, bytes)
            options = {} if binary else {'encoding': 'utf-8', 'newline': '\n'}
            stream = os.fdopen(fd, 'ab' if binary else 'a', **options)
        except BaseException:
            os.close(fd)
            raise
        with stream as out:
            # Exclusive creation already reserves the empty spare. A wrapped
            # row writer may only accept complete nonempty rows.
            if text or not exclusive:
                out.write(text)


def replace(source: Path, target: Path) -> None:
    with _mutation(target) as attempt:
        body = path(source).read_bytes() if attempt is not None else None
        os.replace(path(source), path(target))
        if attempt is not None:
            attempt.wrote(path(target), body)


def temporary_root() -> Path:
    """The system scratch root, with run-relative links checked before resolution."""
    raw = Path(tempfile.gettempdir()).absolute()
    home = g6_home().absolute()
    if raw.is_relative_to(home) or raw.is_relative_to(home.resolve()):
        return path(raw)
    # A system temp root outside G6 state has its own configured anchor. Its
    # canonical path also handles the system's /var alias on macOS.
    return path(raw.resolve())


def _copy_tree(source: Path, target: Path) -> None:
    mkdir(target, parents=True, exist_ok=True)
    for child in source.iterdir():
        child, destination = path(child), path(target, child.name)
        if child.is_dir():
            _copy_tree(child, destination)
        else:
            shutil.copy2(path(child), path(destination))


def visible_copy(source: Path) -> Path:
    source = check_tree(source)
    target = path(temporary_root(), f'g6_scout_{uuid.uuid4().hex}')
    # Exclusive creation never adopts an existing directory or link.
    mkdir(target, mode=0o700)
    _copy_tree(source, target)
    return target
