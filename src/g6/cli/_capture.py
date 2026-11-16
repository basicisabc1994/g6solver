# SPDX-License-Identifier: Apache-2.0
"""Capture Python invocation output for a call or the console process."""
from __future__ import annotations

import io
import logging
import sys
import threading
import warnings
from contextlib import contextmanager


_active_capture = None


class _Buffer(io.StringIO):
    """A bounded private buffer; only write counts may leave it."""

    def __init__(self, owner, kind):
        super().__init__()
        self.owner = owner
        self.kind = kind

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError("text required")
        if text:
            with self.owner.lock:
                self.owner.counts[self.kind] += 1
                # Keep at most a small buffer, even if an op prints without bound.
                super().write(text[:max(0, 4096 - self.tell())])
        return len(text)

    def close(self):
        # An op closing its stream must not reopen Python's fallback output path.
        self.flush()


class _LogCounter(logging.Handler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def emit(self, record):
        self.owner.count("log records")


class InvokeCapture:
    """One installed capture; its owner decides when to restore the caller."""

    def __init__(self):
        global _active_capture
        if _active_capture is not None:
            raise RuntimeError("an invocation capture is already installed")
        self.stdout, self.stderr = sys.stdout, sys.stderr
        # A failed write disables replies, but must not change what restoration saves.
        self.streams = self.stdout, self.stderr
        self.lock = threading.RLock()
        self.counts = dict.fromkeys(("warnings", "thread failures", "unraisable exceptions", "uncaught exceptions",
                                    "log records", "stdout writes", "stderr writes"), 0)
        self.reported = self.counts.copy()
        self.hooks = (warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook)
        self.filters = warnings.filters[:]
        self.root = logging.getLogger()
        self.logging = (self.root.handlers, self.root.level, logging.lastResort)
        self.out_buffer = _Buffer(self, "stdout writes")
        self.err_buffer = _Buffer(self, "stderr writes")
        sys.stdout, sys.stderr = self.out_buffer, self.err_buffer
        warnings.showwarning = self.warning
        warnings.simplefilter("always")
        threading.excepthook = self.thread_failed
        sys.unraisablehook = self.unraisable
        sys.excepthook = self.uncaught
        handler = _LogCounter(self)
        self.root.handlers = [handler]
        self.root.setLevel(logging.NOTSET)
        logging.lastResort = handler
        _active_capture = self

    def count(self, kind):
        with self.lock:
            self.counts[kind] += 1

    def warning(self, *args, **kwargs):
        self.count("warnings")

    def thread_failed(self, args):
        self.count("thread failures")

    def unraisable(self, args):
        self.count("unraisable exceptions")

    def uncaught(self, exc_type, value, traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            self.hooks[3](exc_type, value, traceback)
        else:
            self.count("uncaught exceptions")

    def diagnostic(self, text):
        if self.stderr is None:
            return
        try:
            print(text, file=self.stderr)
            self.stderr.flush()
        except KeyboardInterrupt:
            raise
        except BaseException:  # noqa: BLE001 - never display stream failures
            self.stderr = None

    def output(self, text):
        try:
            if self.stdout is None:
                raise OSError
            print(text, file=self.stdout)
            self.stdout.flush()
        except KeyboardInterrupt:
            raise
        except BaseException:  # noqa: BLE001 - an output stream may execute Python
            self.stdout = None
            self.diagnostic("[G6_E_INVOKE_FAILED] G6: output could not be written.")
            return False
        return True

    def report(self, when):
        with self.lock:
            delta = {kind: total - self.reported[kind] for kind, total in self.counts.items()}
            self.reported = self.counts.copy()
            # Captured content is never replayed, including at shutdown.
            for buffer in (self.out_buffer, self.err_buffer):
                buffer.seek(0)
                buffer.truncate()
        if any(delta.values()):
            kinds = ", ".join(f"{count} {kind}" for kind, count in delta.items() if count)
            self.diagnostic(f"G6: captured {kinds} {when}.")

    def report_late(self):
        self.report("after the reply")

    def restore(self):
        """Return all streams and hooks to the caller that installed this capture."""
        global _active_capture
        sys.stdout, sys.stderr = self.streams
        warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook = self.hooks
        warnings.filters[:] = self.filters
        self.root.handlers, level, logging.lastResort = self.logging
        self.root.setLevel(level)
        _active_capture = None


def current_capture():
    return _active_capture


@contextmanager
def capture_invocation():
    """Reuse an outer tally and original streams; restore only a capture we own."""
    capture = current_capture()
    owns_capture = capture is None
    if owns_capture:
        capture = InvokeCapture()
    try:
        yield capture
    finally:
        try:
            capture.report("during the invocation")
        finally:
            if owns_capture:
                capture.restore()
