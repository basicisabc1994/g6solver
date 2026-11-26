# SPDX-License-Identifier: Apache-2.0
"""Bounded call output and a separate descriptor for the stdio protocol writer."""
from contextlib import contextmanager
from functools import wraps
import os
import sys

from g6._streams import Buffer, routing, routing_owner


@contextmanager
def _capture():
    # The stream pair is owned centrally. Releasing a target, even on a detached
    # worker, never changes a Python stream binding.
    owner = routing()
    with owner.stdout(Buffer()):
        yield


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
    protocol = None
    try:
        os.set_inheritable(protocol_fd, False)
        _redirect_fd_stdout(2)
        protocol = os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n")
        with routing_owner(process=True) as owner:
            previous = owner.protocol
            owner.protocol = True
            try:
                yield _ProtocolPipe(protocol)
            finally:
                if not owner.process_owned:
                    owner.protocol = previous
    finally:
        # This is the console server, not an embedding helper. Keep public fd 1
        # on stderr even after serving stops: a buffered dunder write or an exit
        # callback must never regain access to the client's protocol pipe.
        # A peer can close its reader, or the private descriptor can already be
        # closed. Close exactly once, without replacing the serving exception.
        serving_error = sys.exception()
        try:
            if protocol is None:
                os.close(protocol_fd)
            else:
                protocol.close()
        except (OSError, ValueError):
            if serving_error is None:
                raise _ProtocolClosed from None


class _ProtocolClosed(Exception):
    pass


class _ProtocolPipe:
    """Identify closed-file ValueErrors at the I/O boundary, not in tool code."""

    def __init__(self, stream):
        self.stream = stream
        self.failed = False

    def write(self, text):
        try:
            return self.stream.write(text)
        except (OSError, ValueError):
            self.failed = True
            raise _ProtocolClosed from None

    def flush(self):
        try:
            return self.stream.flush()
        except (OSError, ValueError):
            self.failed = True
            raise _ProtocolClosed from None

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _pipe_failure(error):
    import anyio

    if isinstance(error, BaseExceptionGroup):
        return all(_pipe_failure(child) for child in error.exceptions)
    return isinstance(error, (OSError, _ProtocolClosed, anyio.BrokenResourceError, anyio.ClosedResourceError))


async def _serve_stdio(server, protocol):
    import anyio
    from fastmcp.utilities.version_check import check_for_newer_version
    from g6.mcp.stdio import serve

    # FastMCP.run_stdio_async doesn't accept streams. g6.mcp.stdio.serve keeps
    # its lifespan and low-level run sequence over G6's own writer, which counts
    # each reply once written, here on the private protocol descriptor.
    # Its banner normally runs this setting-aware check. Preserve the person's
    # opt-in with the private transport too; G6's default remains off.
    check_for_newer_version()
    try:
        # A protocol descriptor already closed ends serving before G6's stdin
        # reader starts: that reader's own descriptor could otherwise reuse the
        # closed number, which the protocol's close would then close again.
        os.fstat(protocol.fileno())
    except (OSError, ValueError):
        raise _ProtocolClosed from None
    await serve(server, stdout=anyio.wrap_file(protocol))
    if getattr(protocol, "failed", False):
        # G6's writer ends the connection on a failed write; the pipe is still closed.
        raise _ProtocolClosed


def run_stdio(build_server):
    from g6._warnings import install_process_warning_policy

    # Install before anyio, server construction (_quiet imports FastMCP), and
    # _serve_stdio's FastMCP imports, including import-time Authlib warnings.
    install_process_warning_policy()
    import anyio

    stderr = sys.stderr
    try:
        with _protocol_stdout() as protocol:
            anyio.run(_serve_stdio, build_server(), protocol)
    except BaseException as error:
        if not _pipe_failure(error):
            raise
        try:
            stderr.write("[G6_E_STDIO_CLOSED] G6: protocol pipe closed.\n")
            stderr.flush()
        except Exception:
            pass
        # Do not leave Python's final stdout flush aimed at a failed pipe.
        try:
            _redirect_fd_stdout(2)
        except OSError:
            pass
        raise SystemExit(1) from None
