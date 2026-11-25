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


@contextmanager
def _locked(target: Path) -> Iterator[None]:
    """Every destination's producers and restorers use the same OS mutex."""
    target = path(target)
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
            yield
        finally:
            active[0] = False
            _LOCKS.reset(token)


def _snapshot(target: Path) -> dict[Path, bytes | None]:
    return {item: item.read_bytes() if item.is_file() else None for item in retained_paths(target)[:2]}


class _Publication:
    def __init__(self, target: Path) -> None:
        self.target = path(target)
        self.previous = _snapshot(self.target)
        self.written = dict(self.previous)
        self.spares = {}
        self.active = True
        self.thread = threading.get_ident()

    def includes(self, target: Path) -> bool:
        return self.active and self.thread == threading.get_ident() and target in retained_paths(self.target)

    def wrote(self, target: Path, body: bytes) -> None:
        if target in self.written:
            self.written[target] = body
        else:
            self.spares[target] = (body, _file_identity(target))


class _TaskWrite:
    def __init__(self, target: Path) -> None:
        self.target = path(target)
        self.attempt = None
        self.stack = ExitStack()
        self.staging = None
        self.thread = threading.get_ident()
        self.active = True

    def includes(self, target: Path) -> bool:
        return self.active and self.thread == threading.get_ident() and target in retained_paths(self.target)


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


def task_path(target: Path) -> Path:
    """The latest binding, including an interrupted recovery binding."""
    session = path(target.parent)
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
        path(target).write_bytes(body)
        if attempt is not None:
            attempt.wrote(path(target), body)


def write_text(target: Path, text: str, **kwargs) -> None:
    with _mutation(target) as attempt:
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
    receipt = target.with_suffix('.sha256')
    return tuple(path(p) for p in (target, receipt, target.with_name(target.name + '.new'),
                                  receipt.with_name(receipt.name + '.new')))


def _file_identity(target: Path) -> tuple[int, int, int]:
    status = path(target).stat()
    return status.st_dev, status.st_ino, status.st_mtime_ns


def _remove_spares(spares: dict) -> None:
    for item, (body, identity) in spares.items():
        item = path(item)
        if item.is_file() and _file_identity(item) == identity and item.read_bytes() == body:
            unlink(item)


def _restore_retained(target: Path, previous: dict[Path, bytes | None], *,
                      written: dict[Path, bytes | None] | None = None,
                      spares: dict | None = None) -> None:
    with _locked(target):
        target, receipt, content_spare, receipt_spare = retained_paths(target)
        # Check the pair, including the unchanged half of a partial write.
        # A later producer owns any different pair; it is never restored over.
        current = _snapshot(target)
        owned_spares = dict(spares or {})
        if written is not None and current != written:
            _remove_spares(owned_spares)
            return
        for item, old in previous.items():
            if current[item] == old:
                continue
            if old is None:
                unlink(item, missing_ok=True)
            else:
                spare = content_spare if item == target else receipt_spare
                write_bytes(spare, old)
                owned_spares[spare] = (old, _file_identity(spare))
                replace(spare, item)
        _remove_spares(owned_spares)


@contextmanager
def task_publication(target: Path) -> Iterator[None]:
    """Roll back a caught task write error, including through a replaced writer."""
    task = _TaskWrite(target)
    token = _TASK_WRITE.set(task)
    try:
        yield
    except OSError:
        if task.attempt is not None:
            attempt = task.attempt
            _restore_retained(target, attempt.previous, written=attempt.written, spares=attempt.spares)
        raise
    finally:
        task.active = False
        task.stack.close()
        _TASK_WRITE.reset(token)


def write_retained(target: Path, body: bytes) -> None:
    """Retry identified sharing errors and restore the prior publication on exhaustion."""
    target, receipt, content_spare, receipt_spare = retained_paths(target)
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
        with _locked(target):
            attempt = _Publication(target)
            if task is not None and task.includes(target):
                task.attempt = attempt
            token = _ATTEMPT.set(attempt)
            deadline = time.monotonic() + PUBLICATION_WAIT
            try:
                while True:
                    try:
                        retained_paths(target)
                        cloud.write_retained(target, body)
                        attempt.written = {target: body, receipt: cloud.sha256(body).encode('ascii')}
                        return
                    except OSError as exc:
                        current = _snapshot(target)
                        completed = {target: body, receipt: cloud.sha256(body).encode('ascii')}
                        partial = {target: body, receipt: attempt.written[receipt]}
                        owned = current in (attempt.written, partial, completed)
                        if owned:
                            attempt.written = current
                        else:
                            # Do not adopt another producer's bytes as ours.
                            attempt.written = completed
                        if owned:
                            for spare, expected in ((content_spare, body), (receipt_spare, completed[receipt])):
                                if path(spare).is_file() and path(spare).read_bytes() == expected:
                                    attempt.spares[spare] = (expected, _file_identity(spare))
                        sharing = isinstance(exc, PermissionError) and (
                            exc.errno in (errno.EACCES, errno.EPERM) or getattr(exc, 'winerror', None) in (5, 32, 33))
                        if sharing and owned and time.monotonic() < deadline:
                            time.sleep(0.01)
                            continue
                        if sharing or task is not None and task.includes(target):
                            _restore_retained(target, attempt.previous, written=attempt.written, spares=attempt.spares)
                        # Unclassified state tears retain their unknown count.
                        raise
            finally:
                attempt.active = False
                _ATTEMPT.reset(token)
    finally:
        if staging is not None:
            try:
                remove_tree(staging)
            finally:
                task.staging = None


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


def append_text(target: Path, text: str) -> None:
    with _locked(target):
        with os.fdopen(os.open(path(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600),
                       'a', encoding='utf-8', newline='\n') as out:
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
