# SPDX-License-Identifier: Apache-2.0
"""Abandoned rows count outside exit; exit has both a stall and total bound."""
from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import repair_log


@pytest.mark.parametrize("failure", [OSError("disk full"), ValueError("outside containment")])
def test_writer_counts_a_row_it_cannot_write(monkeypatch, failure):
    from g6.core import storage

    def refused(*args):
        raise failure

    writer = repair_log._Writer()
    monkeypatch.setattr(storage, "contained", refused)
    monkeypatch.setattr(repair_log, "_writer", writer)
    writer.submit(Path("synthetic-home"), '{}\n')
    assert repair_log.flush(2)
    assert repair_log.repair_stats(Path("nonexistent-log")).get("dropped", 0) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows byte lock")
def test_real_writer_lock_timeout_counts_the_row_at_lock_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(repair_log, "_writer", repair_log._Writer())
    monkeypatch.setattr(repair_log, "LOCK_WAIT_S", 0.05)
    path = tmp_path / "home" / "repairs" / "repairs.lock"
    path.parent.mkdir(parents=True)
    script = ("import msvcrt,sys,time\n"
              "f=open(sys.argv[1], 'a+b')\n"
              "f.seek(int(sys.argv[2]))\n"
              "msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
              "print('held', flush=True)\n"
              "time.sleep(30)\n")
    holder = subprocess.Popen([sys.executable, "-I", "-c", script, str(path), str(repair_log.LOCK_OFFSET)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        repair_log.append({"event": "refused", "id": "held", "code": "G6_E_TEST"})
        assert repair_log.flush(2)
        assert repair_log.repair_stats().get("dropped", 0) == 1
        assert not path.with_suffix(".jsonl").exists()
    finally:
        holder.kill()
        holder.wait()
    # Once the lock is available again a later write carries the pending count
    # to the shared counter, so a fresh process can see it without double count.
    repair_log.append({"id": "later", "code": "G6_E_TEST"})
    assert repair_log.flush(2)
    read = subprocess.run([sys.executable, '-c',
                           "from g6.core.repair_log import repair_stats; print(repair_stats().get('dropped',0))"],
                          capture_output=True, text=True, timeout=10)
    assert read.returncode == 0, read.stderr
    assert read.stdout.strip() == '1'
    assert repair_log.repair_stats()['dropped'] == 1


def test_disk_write_loss_is_visible_from_another_process(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / 'home'))
    monkeypatch.setattr(repair_log, '_writer', repair_log._Writer())
    original = Path.open

    def disk_full(path, mode='r', *args, **kwargs):
        if path.name == 'repairs.jsonl' and mode == 'a':
            raise OSError(errno.ENOSPC, 'disk full for log')
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', disk_full)
    repair_log.append({'id': 'lost', 'code': 'G6_E_TEST'})
    assert repair_log.flush(2)
    assert repair_log.repair_stats()['dropped'] == 1
    read = subprocess.run([sys.executable, '-c',
                           "from g6.core.repair_log import repair_stats; print(repair_stats().get('dropped',0))"],
                          capture_output=True, text=True, timeout=10)
    assert read.returncode == 0, read.stderr
    assert read.stdout.strip() == '1'


def test_exit_has_a_ten_second_total_bound_even_while_rows_finish(monkeypatch):
    clock = [0.0]
    writer = SimpleNamespace(written=0)

    class Progress:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, seconds):
            clock[0] += min(1.9, seconds)
            if seconds >= 1.9:
                rows.unfinished_tasks -= 1
                writer.written += 1

    rows = SimpleNamespace(unfinished_tasks=12, all_tasks_done=Progress())
    writer.rows = rows
    monkeypatch.setattr(repair_log, "_writer", writer)
    monkeypatch.setattr(repair_log.time, "monotonic", lambda: clock[0])
    repair_log._flush_at_exit()
    assert clock[0] <= 10.0
    assert repair_log.EXIT_TOTAL_S == 10.0
    assert rows.unfinished_tasks > 0


def test_exit_stall_counts_written_rows_not_abandoned_rows(monkeypatch):
    clock = [0.0]

    class Losses:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, seconds):
            clock[0] += seconds
            rows.unfinished_tasks -= 1

    rows = SimpleNamespace(unfinished_tasks=12, all_tasks_done=Losses())
    monkeypatch.setattr(repair_log, "_writer", SimpleNamespace(rows=rows, written=0))
    monkeypatch.setattr(repair_log.time, "monotonic", lambda: clock[0])
    repair_log._flush_at_exit()
    assert clock[0] <= repair_log.EXIT_FLUSH_S


@pytest.mark.parametrize("slow, count", [(True, 12), (False, 10_000)])
def test_child_exit_uses_the_total_cap_while_writes_progress(tmp_path, slow, count):
    import time

    code = ("import time\n"
            "from contextlib import contextmanager\n"
            "from g6.core import repair_log\n"
            "real = repair_log._process_lock\n"
            "@contextmanager\n"
            "def paced(path):\n"
            "    with real(path):\n"
            f"        time.sleep({1.9 if slow else 0})\n"
            "        yield\n"
            "repair_log._process_lock = paced\n"
            f"for i in range({count}):\n"
            "    repair_log.append({'id':str(i), 'code':'G6_E_TEST'})\n"
            "print('queued', flush=True)\n")
    child = subprocess.Popen([sys.executable, '-c', code],
                             env={**os.environ, "G6_HOME": str(tmp_path / 'home')},
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'queued'
        start = time.monotonic()
        child.wait(timeout=repair_log.EXIT_TOTAL_S + 5)
        assert child.returncode == 0
        assert time.monotonic() - start <= repair_log.EXIT_TOTAL_S + 3
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_unsupported_lock_offset_is_not_retried_for_two_minutes(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows lock offset")
    import msvcrt

    calls = []

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def seek(self, offset):
            assert offset == repair_log.LOCK_OFFSET

        def fileno(self):
            return -1

    class LockPath:
        def open(self, mode):
            return Lock()

    def unsupported(*args):
        calls.append(args)
        raise OSError(errno.EINVAL, "lock past end unsupported")

    clock = [0.0]
    monkeypatch.setattr(msvcrt, "locking", unsupported)
    monkeypatch.setattr(repair_log.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(repair_log.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(OSError, match="lock past end unsupported"):
        with repair_log._process_lock(LockPath()):
            pytest.fail("An unsupported lock must never admit the write")
    assert len(calls) == 1
    assert clock[0] == 0
