# SPDX-License-Identifier: Apache-2.0
"""The sole owner of Python stream bindings; scopes and workers select targets."""
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import io
import os
import sys
import threading


_lock = threading.RLock()
_thread_owners = ContextVar('g6_stream_owners', default=())
_thread_users = 0
_thread_start = None


class Buffer(io.StringIO):
    """A bounded sink that stays usable by workers after the initiating call."""

    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError('text required')
        with self.lock:
            super().write(text[:max(0, 4096 - self.tell())])
        return len(text)

    def close(self):
        pass


class _Unattributed(Buffer):
    def __init__(self, stderr):
        super().__init__()
        self.stderr = stderr
        self.reported = False

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError('text required')
        with _lock:
            if text and not self.reported:
                self.reported = True
                try:
                    self.stderr.write('G6: captured unattributed stdout output.\n')
                    self.stderr.flush()
                except Exception:
                    pass
        return len(text)


class _ShutdownSink:
    """A failed console stream must not fail again during interpreter shutdown."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _Stream:
    def __init__(self, owner, index):
        self.owner, self.index = owner, index

    def target(self):
        owner = self.owner
        target = owner.local[self.index].get()
        if target is not None:
            return target
        with _lock:
            target = owner.defaults[self.index]
            if self.index == 0 and target is owner.originals[0] and (owner.users or owner.protocol):
                return owner.unattributed
            return target

    def write(self, text):
        return self.target().write(text)

    def flush(self):
        return self.target().flush()

    def __getattr__(self, name):
        return getattr(self.target(), name)


class Routing:
    def __init__(self):
        self.originals = sys.stdout, sys.stderr
        self.restore_targets = self.originals
        self.defaults = self.originals
        self.local = ContextVar('g6_stdout', default=None), ContextVar('g6_stderr', default=None)
        self.users = 0
        self.protocol = False
        self.process_owned = False
        self.unattributed = _Unattributed(self.originals[1])
        self.streams = _Stream(self, 0), _Stream(self, 1)
        sys.stdout, sys.stderr = self.streams

    def restore(self):
        # Called by the installing owner, never by workers or target releases.
        sys.stdout, sys.stderr = self.restore_targets

    def prepare_console_shutdown(self, *, stdout_failed, stderr_failed):
        # Select the final bindings before the owner's single restoration. An
        # embedding main() never calls this: its caller keeps the exact originals.
        self.restore_targets = tuple(
            _ShutdownSink() if failed else original
            for original, failed in zip(self.originals, (stdout_failed, stderr_failed))
        )

    def targets(self):
        return tuple(stream.target() for stream in self.streams)

    def set_defaults(self, targets):
        with _lock:
            previous = self.defaults
            self.defaults = targets
            return previous

    def acquire(self):
        global _thread_users, _thread_start
        with _lock:
            if not _thread_users:
                _thread_start = threading.Thread.start
                threading.Thread.start = _start_thread
            _thread_users += 1
            self.users += 1

    def release(self):
        global _thread_users
        with _lock:
            self.users -= 1
            _thread_users -= 1
            if not _thread_users:
                threading.Thread.start = _thread_start

    @contextmanager
    def stdout(self, target):
        self.acquire()
        token = self.local[0].set(target)
        owners = _thread_owners.set((*_thread_owners.get(), self))
        try:
            yield
        finally:
            _thread_owners.reset(owners)
            self.local[0].reset(token)
            self.release()


def _start_thread(thread, *args, **kwargs):
    with _lock:
        start = _thread_start
        owners = tuple(set(_thread_owners.get()))
        if not owners:
            return start(thread, *args, **kwargs)
        # Inherit only output routing, not unrelated request/consent authority.
        targets = [(local, local.get()) for owner in owners for local in owner.local]
        previous_run = thread.run
        for owner in owners:
            owner.acquire()

        def run():
            tokens = [(local, local.set(target)) for local, target in targets]
            owner_token = _thread_owners.set(owners)
            try:
                return previous_run()
            finally:
                _thread_owners.reset(owner_token)
                for local, token in reversed(tokens):
                    local.reset(token)
                thread.run = previous_run
                for owner in owners:
                    owner.release()

        thread.run = run
        try:
            return start(thread, *args, **kwargs)
        except BaseException:
            thread.run = previous_run
            for owner in owners:
                owner.release()
            raise


def routing():
    """Reuse the installed pair; standalone helpers retain an idle forwarding pair.

    Retaining that pair lets detached workers finish without any worker changing
    global bindings. Explicit owners (CLI calls and the server) use routing_owner.
    """
    with _lock:
        if isinstance(sys.stdout, _Stream) and isinstance(sys.stderr, _Stream) \
                and sys.stdout.owner is sys.stderr.owner:
            return sys.stdout.owner
        return Routing()


@contextmanager
def routing_owner(*, process=False):
    with _lock:
        before = sys.stdout, sys.stderr
        owner = routing()
        installed = not (before[0] is owner.streams[0] and before[1] is owner.streams[1])
        if installed and process:
            owner.process_owned = True
            # Register before resolving operations, so their exit callbacks and
            # non-daemon workers finish before the console server restores once.
            atexit.register(owner.restore)
    try:
        yield owner
    finally:
        if installed and not process:
            owner.restore()


@contextmanager
def stdout_target(target):
    """An embedding scope owns installation; nested scopes only select a target."""
    with routing_owner() as owner, owner.stdout(target):
        yield


def seal_console():
    """Discard teardown output after the console owner's final report/restoration."""
    null = open(os.devnull, 'w', encoding='utf-8')
    os.dup2(null.fileno(), 1)
    os.dup2(null.fileno(), 2)
    sys.__stdout__ = sys.__stderr__ = null
