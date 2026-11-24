# SPDX-License-Identifier: Apache-2.0
"""Completion-owned paths and mutations, checked before each filesystem operation."""
from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from g6.core import cloud_consent as cloud
from g6.core.storage import PathRefused, g6_home, plain_file
from g6.workspace._coordination import bookkeeping_path
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


TASK_UNREADABLE = 'G6_E_COMPLETION_TASK_UNREADABLE'
UNREADABLE_TASK = ('The retained completion task cannot be read against its receipt. '
                   'Start a new session over MCP; the next prompt can start a new loop.')
PUBLICATION_WAIT = 0.3


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
    path(target).write_bytes(body)


def write_text(target: Path, text: str, **kwargs) -> None:
    path(target).write_text(text, **kwargs)


def unlink(target: Path, **kwargs) -> None:
    path(target).unlink(**kwargs)


def touch(target: Path) -> None:
    path(target).touch()


def retained_paths(target: Path) -> tuple[Path, ...]:
    receipt = target.with_suffix('.sha256')
    return tuple(path(p) for p in (target, receipt, target.with_name(target.name + '.new'),
                                  receipt.with_name(receipt.name + '.new')))


def _restore_retained(target: Path, previous: dict[Path, bytes | None]) -> None:
    target, receipt, content_spare, receipt_spare = retained_paths(target)
    # An unchanged held receipt needs no replacement.
    for item, old in previous.items():
        current = path(item).read_bytes() if path(item).is_file() else None
        if current == old:
            continue
        if old is None:
            unlink(item, missing_ok=True)
        else:
            spare = content_spare if item == target else receipt_spare
            write_bytes(spare, old)
            replace(spare, item)
    unlink(content_spare, missing_ok=True)
    unlink(receipt_spare, missing_ok=True)


@contextmanager
def task_publication(target: Path) -> Iterator[None]:
    """Roll back a caught task write error, including through a replaced writer."""
    target, receipt, *_ = retained_paths(target)
    previous = {item: item.read_bytes() if item.is_file() else None for item in (target, receipt)}
    try:
        yield
    except OSError:
        _restore_retained(target, previous)
        raise


def write_retained(target: Path, body: bytes) -> None:
    """Retry identified sharing errors and restore the prior publication on exhaustion."""
    # cloud_consent is bound code: its producer owns synchronization and checks
    # every content/receipt/spare name before that producer opens or replaces it.
    target, receipt, *_ = retained_paths(target)
    previous = {item: item.read_bytes() if item.is_file() else None for item in (target, receipt)}
    deadline = time.monotonic() + PUBLICATION_WAIT
    while True:
        try:
            # Recheck all four names on every attempt, including the receipt
            # after a previous attempt replaced only the content.
            retained_paths(target)
            cloud.write_retained(target, body)
            return
        except OSError as exc:
            sharing = isinstance(exc, PermissionError) and (
                exc.errno in (errno.EACCES, errno.EPERM) or getattr(exc, 'winerror', None) in (5, 32, 33))
            if sharing and time.monotonic() < deadline:
                time.sleep(0.01)
                continue
            if not sharing:
                # An unclassified state failure can leave an interrupted
                # publication. Preserve its bytes and unknown send count.
                raise
            _restore_retained(target, previous)
            raise


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
    shutil.rmtree(check_tree(folder))


def append_text(target: Path, text: str) -> None:
    with os.fdopen(os.open(path(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600),
                   'a', encoding='utf-8', newline='\n') as out:
        out.write(text)


def replace(source: Path, target: Path) -> None:
    os.replace(path(source), path(target))


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
