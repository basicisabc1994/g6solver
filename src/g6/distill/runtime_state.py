# SPDX-License-Identifier: Apache-2.0
"""Run-local work and short, optimistic status transactions; no authority writer."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from g6.core import stop

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
    measurements: dict = field(default_factory=dict)
    measuring: bool = False
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


def cost_exceeded(costs, bound):
    """A cached verdict supplies no new cost evidence; require three observations."""
    if len(costs) < 3:
        raise ValueError('three real executions required for timing evidence')
    return sum(cost > bound for cost in costs) / len(costs) > .1


def measure_executions(candidate, observations, *, key_for, execute, clock):
    """One measurement rule for evaluator and training/recorded replay.

    A measurement miss executes the complete method three times with all verdict
    caches bypassed, including dependencies. A hit reuses those original samples
    once per case in this set; it never measures or counts a lookup.
    """
    memo = run_memo()
    snapshot = memo.snapshot()
    verdicts, costs, seen = [], [], set()
    for observation in observations:
        key = key_for(observation)
        require_current(snapshot)
        if stop.engaged() is not None:
            raise ValueError('stop engaged during evaluation')
        if key not in memo.measurements:
            repeats, measured = [], []
            previous = memo.measuring
            memo.measuring = True
            try:
                for index in range(3):
                    if stop.engaged() is not None:
                        raise ValueError('stop engaged during evaluation')
                    start = clock()
                    with sample(index):
                        repeats.append(execute(observation))
                    measured.append(max(0, clock() - start))
            finally:
                memo.measuring = previous
            if len(set(repeats)) != 1:
                raise ValueError('flaky verdict across three runs')
            memo.measurements[key] = (tuple(repeats), tuple(measured))
        repeats, measured = memo.measurements[key]
        if len(measured) != 3:
            raise ValueError('three real executions required for timing evidence')
        if len(set(repeats)) != 1:
            raise ValueError('flaky verdict across three runs')
        if key not in seen:
            costs.extend(measured)
            seen.add(key)
        verdicts.append(repeats[0])
    require_current(snapshot)
    if cost_exceeded(costs, candidate.contract.cost_bound.wall_s):
        raise ValueError('cost bound exceeded on more than 10% of runs')
    return verdicts, costs
