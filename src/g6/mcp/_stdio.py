# SPDX-License-Identifier: Apache-2.0
"""Per-call stdout buffers, shared safely by concurrent synchronous MCP calls."""
from contextlib import contextmanager
from functools import wraps
import io
import sys
import threading


_lock = threading.RLock()
_local = threading.local()
_users = 0
_router = None


class _Stdout:
    def __init__(self, original):
        self.original = original

    def _target(self):
        return getattr(_local, "buffer", None) or self.original

    def write(self, text):
        return self._target().write(text)

    def flush(self):
        return self._target().flush()

    def __getattr__(self, name):
        return getattr(self._target(), name)


class _Buffer(io.StringIO):
    def write(self, text):
        # Buffer only a bounded prefix; the remainder is discarded too.
        super().write(text[:max(0, 4096 - self.tell())])
        return len(text)


@contextmanager
def _capture():
    global _users, _router
    with _lock:
        if not _users:
            _router = _Stdout(sys.stdout)
            sys.stdout = _router
        _users += 1
        previous = getattr(_local, "buffer", None)
        _local.buffer = _Buffer()
    try:
        yield
    finally:
        with _lock:
            _local.buffer.close()
            _local.buffer = previous
            _users -= 1
            if not _users:
                sys.stdout = _router.original
                _router = None


def capture_stdout(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _capture():
            return function(*args, **kwargs)
    return call
