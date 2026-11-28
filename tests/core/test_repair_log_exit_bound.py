"""A process never waits at exit for another server's hold on the repair log's lock."""
import subprocess
import sys
import textwrap
import time

from g6.core import repair_log


def test_the_exit_hook_is_bounded_and_not_the_unbounded_flush():
    assert repair_log.EXIT_FLUSH_S <= 2.0 < repair_log.LOCK_WAIT_S


def test_a_process_with_a_queued_row_exits_within_the_bound_while_the_lock_is_held(tmp_path):
    """Before: the exit hook waited up to LOCK_WAIT_S + 5 seconds for the row; after: about EXIT_FLUSH_S."""
    home = tmp_path / "home"
    script = textwrap.dedent(f"""
        import os, threading
        from contextlib import contextmanager
        os.environ["G6_HOME"] = {str(home)!r}
        from g6.core import repair_log
        held = threading.Event()

        @contextmanager
        def never_free(path):
            held.wait()            # another server holds the lock for longer than this process lives
            yield

        repair_log._process_lock = never_free
        repair_log.append({{"event": "repair", "id": "r1", "code": "G6_E_TEST"}})
        print("queued", flush=True)
    """)
    start = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    took = time.monotonic() - start
    assert done.returncode == 0 and "queued" in done.stdout, done.stderr
    assert took < repair_log.EXIT_FLUSH_S + 8.0, f"exit held for {took:.1f} s"


def test_the_lock_file_stays_readable_while_the_lock_is_held(tmp_path):
    """Before: Windows locked byte 0 of the lock file, so a reader of the home met a PermissionError mid-write."""
    lock = tmp_path / "repairs.lock"
    with repair_log._process_lock(lock):
        lock.read_bytes()