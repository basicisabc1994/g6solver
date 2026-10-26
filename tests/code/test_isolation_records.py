# SPDX-License-Identifier: Apache-2.0
"""The code family's two runs of a caller's code keep how it ran: meta_programming's ``run_tests`` (its answer and the
op's evidence) and adapt_healing's shown and held-back cases (the run's record). Both dropped the sandbox's
``isolation``. Offline."""
from __future__ import annotations

import pytest

from g6.code.adapt_healing import cases
from g6.code.meta_programming import runner
from g6.code.meta_programming.block import MetaProgrammingInput, run_tests_op
from g6.core import confine

OFF = "unavailable: switched off by this test"
CODE = "def add(a, b):\n    return a + b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


@pytest.fixture(autouse=True)
def _off(monkeypatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead


def test_meta_programmings_test_run_keeps_its_isolation() -> None:
    ran = runner.run_tests(CODE, TESTS, "calc", timeout_s=30)
    # fails if run_tests drops the sandbox run's isolation record
    assert ran["status"] == "ok" and ran["isolation"]["why"] == OFF
    assert ran["isolation"]["warning"] == confine.UNCONFINED_WARNING


def test_meta_programmings_run_tests_op_puts_it_in_its_evidence() -> None:
    reply = run_tests_op(MetaProgrammingInput(op="run_tests", source=CODE, tests=TESTS, module="calc", timeout_s=30))
    # fails if the op's evidence says where the tests ran but not whether the operating system confined them
    assert reply["evidence"][0]["isolation"]["why"] == OFF


def test_adapt_healings_case_runs_keep_their_isolation() -> None:
    found = cases.run_shown(CODE, ["add(2, 3) == 5"], 30)
    # fails if the case run's record drops the sandbox's isolation
    assert found["run"]["status"] == "ok" and found["run"]["isolation"]["why"] == OFF
