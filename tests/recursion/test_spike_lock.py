# SPDX-License-Identifier: Apache-2.0
import json
import os
import subprocess
import sys

import pytest

from g6.recursion.spike.gpu_lock import GPULock, LockBusy


def test_same_process_and_stale_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    lock = GPULock("first")
    with lock:
        assert lock.holder["id"] == "first"
        assert lock.holder["pid"] == os.getpid()
        assert lock.holder["started_at"]
        with pytest.raises(LockBusy) as caught:
            GPULock("second").acquire()
        assert caught.value.code == "G6_E_GPU_BUSY"
    # Metadata from a departed holder is harmless; no PID probing or timeout stealing.
    with GPULock("new") as fresh:
        assert fresh.holder["id"] == "new"
    assert lock.path.exists()  # never unlink a lock file while another opener may hold it


@pytest.mark.parametrize("abrupt", [False, True])
def test_two_processes_refused_then_release_on_exit(tmp_path, monkeypatch, abrupt):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    code = """
import json, os, sys
from g6.recursion.spike.gpu_lock import GPULock
lock = GPULock('child').acquire()
print(json.dumps(lock.holder), flush=True)
sys.stdin.readline()
if sys.argv[1] == 'abrupt':
    os._exit(0)
lock.release()
"""
    child = subprocess.Popen([sys.executable, "-c", code, "abrupt" if abrupt else "normal"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(child.stdout.readline())["id"] == "child"
        with pytest.raises(LockBusy) as caught:
            GPULock("parent").acquire()
        assert caught.value.code == "G6_E_GPU_BUSY"
        child.communicate("exit\n", timeout=15)
        assert child.returncode == 0
        with GPULock("parent"):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=15)
