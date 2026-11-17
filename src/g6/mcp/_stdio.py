# SPDX-License-Identifier: Apache-2.0
"""Bounded call output and a separate descriptor for the stdio protocol writer."""
from contextlib import contextmanager
from functools import wraps
import io
import os
import sys
import threading


_lock = threading.RLock()
_local = threading.local()
_users = 0
_router = None
_thread_start = None


class _Unattributed(io.StringIO):
    """Unowned writes are never protocol messages. Emit at most one masked line."""

    def __init__(self, stderr):
        super().__init__()
        self.stderr = stderr
        self.reported = False

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError("text required")
        with _lock:
            if text and not self.reported:
                self.reported = True
                if self.stderr is not None:
                    try:
                        self.stderr.write("G6: captured unattributed stdout output.\n")
                        self.stderr.flush()
                    except Exception:  # No stream failure may reopen protocol stdout.
                        pass
        return len(text)


class _Stdout:
    def __init__(self, original):
        self.original = original
        self.unattributed = _Unattributed(sys.stderr)

    def _target(self):
        buffer = getattr(_local, "buffer", None)
        return buffer if buffer is not None else self.unattributed

    def write(self, text):
        return self._target().write(text)

    def flush(self):
        return self._target().flush()

    def __getattr__(self, name):
        return getattr(self._target(), name)


class _Buffer(io.StringIO):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def write(self, text):
        with self.lock:
            # Buffer only a bounded prefix; the remainder is discarded too.
            super().write(text[:max(0, 4096 - self.tell())])
        return len(text)

    def close(self):
        # Workers may outlive a call. Their buffers remain private and bounded.
        pass


def _start_thread(thread, *args, **kwargs):
    buffer = getattr(_local, "buffer", None)
    with _lock:
        start = _thread_start
        if buffer is None:
            return start(thread, *args, **kwargs)
        _acquire_router()
        previous_run = thread.run

        def run():
            previous = getattr(_local, "buffer", None)
            _local.buffer = buffer
            try:
                return previous_run()
            finally:
                _local.buffer = previous
                thread.run = previous_run
                _release_router()

        thread.run = run
        try:
            return start(thread, *args, **kwargs)
        except BaseException:
            thread.run = previous_run
            _release_router()
            raise


def _acquire_router():
    global _users, _router, _thread_start
    with _lock:
        if not _users:
            _router = _Stdout(sys.stdout)
            sys.stdout = _router
            _thread_start = threading.Thread.start
            threading.Thread.start = _start_thread
        _users += 1


def _release_router():
    global _users, _router, _thread_start
    with _lock:
        _users -= 1
        if not _users:
            sys.stdout = _router.original
            threading.Thread.start = _thread_start
            _router = None
            # Retain the original callable: another thread may have fetched our
            # bound start method just before restoration and invoke it afterwards.


@contextmanager
def _routing():
    _acquire_router()
    try:
        yield
    finally:
        _release_router()


@contextmanager
def _capture():
    with _routing():
        previous = getattr(_local, "buffer", None)
        _local.buffer = _Buffer()
        try:
            yield
        finally:
            _local.buffer = previous


def capture_stdout(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _capture():
            return function(*args, **kwargs)
    return call


def _redirect_fd_stdout(target):
    os.dup2(target, 1)
    if sys.platform == "win32":
        # Children inheriting stdio use Win32's standard handle, not necessarily
        # the CRT descriptor changed by dup2.
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle.argtypes = (ctypes.c_ulong, ctypes.c_void_p)
        kernel32.SetStdHandle.restype = ctypes.c_int
        if not kernel32.SetStdHandle(-11 & 0xffffffff, msvcrt.get_osfhandle(1)):
            raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _protocol_stdout():
    # Only the protocol writer gets this non-inherited descriptor. Children and
    # descriptor writes receive stderr as fd/standard handle 1.
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    try:
        os.set_inheritable(protocol_fd, False)
        _redirect_fd_stdout(2)
        with os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n", closefd=False) as protocol:
            with _routing():
                yield protocol
    finally:
        # This is the console server, not an embedding helper. Keep public fd 1
        # on stderr even after serving stops: a buffered dunder write or an exit
        # callback must never regain access to the client's protocol pipe.
        os.close(protocol_fd)


async def _serve_stdio(server, protocol):
    import anyio
    from fastmcp.utilities.version_check import check_for_newer_version
    from mcp.server.stdio import stdio_server
    from mcp.server.lowlevel import NotificationOptions

    # FastMCP.run_stdio_async doesn't accept streams. Preserve its lifespan and
    # low-level run sequence, using MCP's supported explicit stdout argument.
    # Its banner normally runs this setting-aware check. Preserve the person's
    # opt-in with the private transport too; G6's default remains off.
    check_for_newer_version()
    async with server._lifespan_manager():
        async with stdio_server(stdout=anyio.wrap_file(protocol)) as (read_stream, write_stream):
            await server._mcp_server.run(
                read_stream, write_stream,
                server._mcp_server.create_initialization_options(
                    notification_options=NotificationOptions(tools_changed=True)),
            )


def run_stdio(build_server):
    import anyio

    with _protocol_stdout() as protocol:
        anyio.run(_serve_stdio, build_server(), protocol)
