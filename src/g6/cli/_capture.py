# SPDX-License-Identifier: Apache-2.0
"""Capture Python invocation output for a call or the console process."""
from __future__ import annotations

import io
import logging
import sys
import threading
import warnings
from contextlib import ExitStack, contextmanager

from g6._streams import routing_owner, seal_console


_active_capture = None
# Global Python streams/hooks make the CLI a serial API. RLock preserves nested
# calls on the owning thread; another thread waits through installation/restoration.
invocation_lock = threading.RLock()


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
        if _active_capture is not None:
            raise RuntimeError("an invocation capture is already installed")
        self.cleanup = ExitStack()
        try:
            self._install()
        except BaseException:
            # Roll back every completed step, including a failed installation.
            # ExitStack still runs later restorations if one cleanup raises.
            self.restore()
            raise

    def _install(self):
        global _active_capture
        self.routing = self.cleanup.enter_context(routing_owner())
        self.stdout, self.stderr = self.routing.targets()
        self.lock = threading.RLock()
        self.counts = dict.fromkeys(("warnings", "thread failures", "unraisable exceptions", "uncaught exceptions",
                                    "log records", "stdout writes", "stderr writes"), 0)
        self.reported = self.counts.copy()
        self.hooks = (warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook)
        self.warning_context = warnings.catch_warnings()
        self.cleanup.callback(self._restore_warnings)
        self.warning_context.__enter__()
        self.root = logging.getLogger()
        self.logging = (self.root.handlers, self.root.level, logging.lastResort)
        self.cleanup.callback(self._restore_logging)
        self.out_buffer = _Buffer(self, "stdout writes")
        self.err_buffer = _Buffer(self, "stderr writes")
        self.cleanup.callback(self.routing.set_defaults, self.routing.defaults)
        self.routing.set_defaults((self.out_buffer, self.err_buffer))
        # Named/non-propagating loggers emit before the root counter sees them.
        # Rebind every existing handler using either entry stream, including the
        # previous lastResort, and restore each exact object at owner exit.
        # Logger creation mutates loggerDict under logging's module lock. Copy
        # under that same lock, then inspect only the detached inventory.
        with logging._lock:
            inventory = list(logging.Logger.manager.loggerDict.values())
        loggers = [self.root, *(logger for logger in inventory
                                if isinstance(logger, logging.Logger))]
        handlers = {handler for logger in loggers for handler in logger.handlers}
        if logging.lastResort is not None:
            handlers.add(logging.lastResort)
        for handler in handlers:
            stream = getattr(handler, "stream", None)
            for index, original in enumerate(self.routing.originals):
                if stream is original:
                    self.cleanup.callback(self._restore_handler, handler, stream)
                    handler.acquire()
                    try:
                        handler.stream = self.routing.streams[index]
                    finally:
                        handler.release()
                    break
        warnings.showwarning = self.warning
        threading.excepthook = self.thread_failed
        sys.unraisablehook = self.unraisable
        sys.excepthook = self.uncaught
        handler = _LogCounter(self)
        self.root.handlers = [handler]
        self.root.setLevel(logging.NOTSET)
        logging.lastResort = handler
        _active_capture = self

    def _restore_warnings(self):
        if self.warning_context._entered:
            # Restore the filter object and invalidate Python's warning cache.
            self.warning_context.__exit__(None, None, None)
        warnings.showwarning, threading.excepthook, sys.unraisablehook, sys.excepthook = self.hooks

    def _restore_logging(self):
        self.root.handlers, level, logging.lastResort = self.logging
        self.root.setLevel(level)

    @staticmethod
    def _restore_handler(handler, stream):
        handler.acquire()
        try:
            handler.stream = stream
        finally:
            handler.release()

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
        try:
            self.report("after the reply")
        finally:
            # Python restores the dunder streams during module teardown, after
            # atexit. Seal the console descriptors only after our final flush.
            # In-process main() never registers this callback.
            try:
                self.routing.prepare_console_shutdown(
                    stdout_failed=self.stdout is None, stderr_failed=self.stderr is None,
                )
                self.restore()
            finally:
                seal_console()

    def restore(self):
        """Return all streams and hooks to the caller that installed this capture."""
        global _active_capture
        try:
            self.cleanup.close()
        finally:
            _active_capture = None


def current_capture():
    return _active_capture


@contextmanager
def capture_invocation():
    """Reuse an outer tally and original streams; restore only a capture we own."""
    with invocation_lock:
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
