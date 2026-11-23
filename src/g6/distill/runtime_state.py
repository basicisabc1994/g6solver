# SPDX-License-Identifier: Apache-2.0
"""Run-local work and short, optimistic status transactions; no authority writer."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .ledger import failure_writer
from .library import Library


class StatusChanged(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'{code}: library status changed during execution; no authority')


def status_snapshot():
    with failure_writer(reading=True):
        return Library().list()


def require_current(entries):
    """Caller may retain this short transaction through its authority write."""
    with failure_writer(reading=True):
        current = Library().list()
        old = {entry.candidate_id: entry.entry_id for entry in entries}
        if old != {entry.candidate_id: entry.entry_id for entry in current}:
            changed = [entry for entry in current if old.get(entry.candidate_id) != entry.entry_id]
            status = next((status for status in ('withdrawn', 'held')
                           if any(entry.status == status for entry in changed)), None)
            code = (f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN' if status else
                    'G6_E_DISTILL_STATUS_CHANGED_DURING_RUN')
            raise StatusChanged(code)
        return current


@dataclass
class RunMemo:
    entries: list | None = None
    values: dict = field(default_factory=dict)
    checked: set = field(default_factory=set)
    walked: set = field(default_factory=set)
    repeat: int = 0

    def snapshot(self):
        if self.entries is None:
            self.entries = status_snapshot()
        return self.entries


_RUN = ContextVar('distill_run_memo', default=None)


def run_memo():
    memo = _RUN.get()
    if memo is None:
        raise RuntimeError('method work requires a run scope')
    return memo


@contextmanager
def run_scope():
    """Nested calls share work; independent runs and dialogs retain no cache."""
    if _RUN.get() is not None:
        yield _RUN.get()
        return
    token = _RUN.set(RunMemo())
    try:
        yield _RUN.get()
    finally:
        _RUN.reset(token)


@contextmanager
def sample(index):
    """Memoise each of the three flakiness samples without collapsing them."""
    memo = run_memo()
    previous = memo.repeat
    memo.repeat = index
    try:
        yield
    finally:
        memo.repeat = previous
