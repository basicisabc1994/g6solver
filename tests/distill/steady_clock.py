# SPDX-License-Identifier: Apache-2.0
"""A deterministic execution clock for the distill gates' cost measurement in tests.

The gates time each method execution with ``time.monotonic`` against the candidate's cost bound (1 s in the
fixtures). On a loaded shared CI runner one slow execution in three refuses admission ("cost bound exceeded on more
than 10% of runs"), so the outcome depended on the machine. The bound and its rule are unchanged: only the clock the
gate modules read is replaced, with one that advances 1 ms per reading.
"""
import itertools
import time
from types import SimpleNamespace


def steady_clock(monkeypatch) -> None:
    from g6.distill import acceptance_gate, execution

    for module in (acceptance_gate, execution):
        ticks = itertools.count()
        clock = SimpleNamespace(**{name: getattr(time, name) for name in dir(time) if not name.startswith("__")})
        clock.monotonic = lambda ticks=ticks: next(ticks) / 1000
        monkeypatch.setattr(module, "time", clock)
