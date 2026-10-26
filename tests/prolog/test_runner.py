# SPDX-License-Identifier: Apache-2.0
"""g6.prolog.runner with a stand-in for ``swipl`` (this Python) that writes the driver's records and then never exits,
as SWI-Prolog 9.0.4 on Linux sometimes does inside ``halt``: its time library deadlocks in its cleanup after the run's
work and records are complete, about one run in 300 (CI's ubuntu-latest job, 2026-10-26). Without the fix such a run
waited for the whole time limit and was refused as too slow. No SWI-Prolog needed."""
from __future__ import annotations

import os
import sys
import time

import pytest

from g6.prolog import driver, ops, runner
from g6.prolog.envelope import TIME_LIMIT, Refusal

NONCE = "ab" * 16
needs_pread = pytest.mark.skipif(not hasattr(os, "pread"),
                                 reason="the output is read in place with os.pread, which Windows does not have")
# argv: nonce, write the end record (1/0), exit afterwards (1/0)
STAND_IN = ("import sys, time\n"
            "nonce, end, exits = sys.argv[1], sys.argv[2] == '1', sys.argv[3] == '1'\n"
            "sys.stdout.write('\\n' + nonce + ' {\"kind\":\"version\", \"version\":\"9.0.4\"}\\n')\n"
            "if end:\n"
            "    sys.stdout.write('\\n' + nonce + ' {\"kind\":\"end\"}\\n')\n"
            "sys.stdout.flush()\n"
            "if not exits:\n"
            "    time.sleep(120)\n")


def _run(*, end: bool, exits: bool, seconds: float, record: dict) -> tuple[int, str]:
    return runner.run(sys.executable, ["-c", STAND_IN, NONCE, "1" if end else "0", "1" if exits else "0"], files={},
                      seconds=seconds, memory_mb=256, cap=64 * 1024, record=record, end=driver.end_record(NONCE))


def test_the_end_record_is_the_line_the_driver_writes_last() -> None:
    assert driver.records("\n" + driver.end_record(NONCE).decode() + "\n", NONCE) == [{"kind": "end"}]


@needs_pread
def test_a_run_that_wrote_its_last_record_but_never_exits_is_stopped_and_read() -> None:
    record: dict = {}
    started = time.monotonic()
    code, text = _run(end=True, exits=False, seconds=60, record=record)
    assert time.monotonic() - started < runner.HALT_GRACE_S + 10   # not the 60 s time limit
    assert [r["kind"] for r in driver.records(text, NONCE)] == ["version", "end"] and code == 0
    assert record["exit_code"] is None and "did not exit" in record["halt"]


def test_a_run_that_never_writes_its_last_record_still_meets_the_time_limit() -> None:
    record: dict = {}
    with pytest.raises(Refusal) as refused:
        _run(end=False, exits=False, seconds=2, record=record)
    assert refused.value.code == TIME_LIMIT and "halt" not in record


def test_a_run_that_writes_its_last_record_and_exits_is_read_as_before() -> None:
    record: dict = {}
    code, text = _run(end=True, exits=True, seconds=60, record=record)
    assert code == 0 and record["exit_code"] == 0 and "halt" not in record
    assert [r["kind"] for r in driver.records(text, NONCE)] == ["version", "end"]


def test_each_driver_run_names_its_last_record_to_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def stopped(swipl: str, arguments: list[str], **kw) -> tuple[int, str]:  # noqa: ANN003
        seen.update(kw)
        raise Refusal(TIME_LIMIT, "stopped here")

    monkeypatch.setattr(runner, "find_swipl", lambda: "swipl")
    monkeypatch.setattr(runner, "run", stopped)
    ops.query("p(1).\n", "p(X)")
    nonce, rest = seen["end"].decode().split(" ", 1)
    assert len(nonce) == 32 and rest == '{"kind":"end"}'
