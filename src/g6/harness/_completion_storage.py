# SPDX-License-Identifier: Apache-2.0
"""Completion-owned paths and mutations, checked before each filesystem operation."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from g6.core import cloud_consent as cloud
from g6.core.storage import PathRefused, g6_home, plain_file
from g6.workspace._coordination import bookkeeping_path
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


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


def write_retained(target: Path, body: bytes) -> None:
    # cloud_consent is bound code: its producer owns synchronization and checks
    # every content/receipt/spare name before that producer opens or replaces it.
    target, *_ = retained_paths(target)
    cloud.write_retained(target, body)


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
    shutil.rmtree(check_tree(folder), ignore_errors=True)


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
