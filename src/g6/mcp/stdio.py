# SPDX-License-Identifier: Apache-2.0
"""G6's stdio MCP server, whose writer G6 owns: a reply is counted (its retry, its repair rows) only once its whole
line was written to stdout and flushed without error (``g6.mcp.consent_transport.Witness``, evidence "written").

The SDK's stdio transport (``mcp.server.stdio.stdio_server``) parses stdin's lines, which G6's own daemon thread
reads, so an ended connection never waits on a blocked read; its writer is never given a message.
G6's own writer takes each outgoing message from G6's own stream, serialises it once, writes the whole line (a short
write is continued, never dropped), flushes, and only then fires that message's own callback. A write or flush error
ends the connection, and with it the connection's witness: nothing for that message or after it counts. FastMCP's
``run_stdio_async`` opens the SDK transport with no stdout, so ``serve`` is its body (fastmcp 2.14) less the banner and
log level, over this ``stdio_server``. ``tests/core/test_mcp_delivery_review2.py`` fails if either changes shape.
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager, suppress
from io import FileIO, TextIOWrapper, UnsupportedOperation
from tempfile import TemporaryDirectory

import anyio
import anyio.lowlevel
from mcp.shared.message import SessionMessage

from g6.mcp._stdin_cache import cache_reader
from g6.mcp.consent_transport import Witness, calls_on, unwitness, witness


class _Unused:
    """The stdout handed to the SDK's transport, whose writer G6 never gives a message."""

    async def write(self, data):
        raise RuntimeError("G6's own stdio writer writes every message")

    async def flush(self):
        raise RuntimeError("G6's own stdio writer writes every message")


async def _write_line(stdout, line: str) -> None:
    """Write all of ``line``, continuing a short write. A write that takes nothing, or that does not say how much it
    took (no count: G6's writer cannot know the line was taken), ends the connection."""
    while line:
        taken = await stdout.write(line)
        if type(taken) is not int or taken <= 0:
            raise OSError("stdout did not say it accepted any of the line")
        line = line[taken:]


async def _writer(messages, stdout, stream, seen: Witness, connection: anyio.CancelScope) -> None:
    """G6's stdio writer: each message from ``messages``, its whole line written and flushed, then its callback."""
    try:
        async with messages:
            async for message in messages:
                if not isinstance(message, SessionMessage):
                    continue
                seen.take(message)
                await _write_line(stdout, message.message.model_dump_json(by_alias=True, exclude_none=True) + "\n")
                await stdout.flush()
                seen.written(message)
    except Exception:  # noqa: BLE001 - a write or flush error ends the connection; nothing after it counts
        unwitness(stream)              # ended before it is cancelled: no tool body starts after this
        connection.cancel()
    finally:
        unwitness(stream)
        connection.cancel()              # writer EOF also ends the parser, even while stdin stays open


def _raw_lines(fd, stopped, prefix=b"", read=None):
    """Frame raw input with the text reader's LF, CRLF and CR line endings, including an unterminated final line.

    Keep partial UTF-8 bytes until the line is complete. Like the old readline(), there is no line-size cap. Only
    an unbuffered read waits on the client: no Python buffered stream lock is held on Windows or POSIX.
    """
    pending = bytearray()
    scan = 0
    if read is None:
        def read(size):
            return os.read(fd, size)
    while not stopped.is_set():
        chunk = prefix or read(65536)
        prefix = b""
        if not chunk:
            if pending:
                yield bytes(pending)
            return
        pending.extend(chunk)
        start = 0
        while scan < len(pending):
            byte = pending[scan]
            if byte == 13 and scan + 1 == len(pending):
                break                   # a CR at the chunk boundary may belong to a CRLF
            scan += 1
            if byte in (10, 13):
                if byte == 13 and pending[scan] == 10:
                    scan += 1
                yield bytes(pending[start:scan])
                start = scan
        if start:
            del pending[:start]
            scan -= start


class _InputFile(FileIO):
    """A native file owner that can abandon an uncertain descriptor without another OS close.

    FileIO acquires the placeholder in C, with its closing owner already allocated. Its IOBase finalizer uses
    closed/close too, so releasing the claim also prevents a later finalizer from closing a reused number.
    """

    _released = False

    @property
    def closed(self):
        return self._released or super().closed

    def release(self):
        self._released = True

    def close(self):
        if not self._released:
            super().close()


class _InputDescriptor:
    """Reserve an owning file before dup2: replacement never returns an unowned descriptor integer."""

    def __init__(self):
        self.lock = threading.Lock()
        self.file = None
        self.claimed = False
        self.placeholder = None

    def duplicate(self, fd):
        # Setup has not published this owner to a reader yet. Its outer handler covers every return/opcode.
        try:
            os.fstat(fd)                 # validate the borrowed source before an allocation can reuse its number
            # A null-device identity also matches another owner's newly opened null device. A private,
            # unique file makes the placeholder distinguishable even when the target number is reused.
            self.placeholder = TemporaryDirectory(prefix="g6-stdin-")
            self.file = _InputFile(os.path.join(self.placeholder.name, "slot"), "xb+")
            if self.file.fileno() == fd:
                # The external owner closed stdin after validation; this slot now owns only the placeholder.
                self.close()
                raise OSError("stdin closed during descriptor handoff")
            placeholder = os.stat(self.file.fileno())
            if not placeholder.st_ino:
                raise OSError("stdin placeholder has no file identity")
            try:
                os.dup2(fd, self.file.fileno(), inheritable=False)
            except OSError:
                try:
                    target = os.stat(self.file.fileno())
                except OSError:
                    self.file.release()  # consumed, or no longer identifiable: never close that number again
                else:
                    if (target.st_dev, target.st_ino) != (placeholder.st_dev, placeholder.st_ino):
                        self.file.release()  # replaced: the native call or another owner has the closing claim
                    # The unique placeholder survived: normal cleanup still owes exactly one close.
                raise
            if os.get_inheritable(self.file.fileno()) is not False:
                raise OSError("stdin duplicate inheritance state is unknown or inheritable")
        except (OSError, ValueError, TypeError, OverflowError, NotImplementedError) as exc:
            raise OSError("[G6_E_STDIN_HANDOFF] stdin descriptor handoff refused") from exc

    def take(self):
        with self.lock:
            if self.file is None or self.claimed:
                return None
            self.claimed = True
            return self.file.fileno()

    def close(self, *, reader=False):
        with self.lock:
            if self.claimed == reader:
                try:
                    if self.file is not None:
                        self.file.close()
                        self.file = None
                finally:
                    if self.placeholder is not None:
                        self.placeholder.cleanup()
                        self.placeholder = None


def _read_lines(descriptor, lines, token, stopped, cached, console=None) -> None:
    """Transfer existing caches, then hand raw lines to the SDK parser. A blocked raw read may outlive the
    connection, but the daemon holds no buffered reader lock during that wait, so interpreter shutdown can finish."""
    fd = None
    try:
        fd = descriptor.take()
        if fd is None:
            return                      # startup already reclaimed the duplicate before this thread ran
        # Keep the console's unbuffered ReadConsoleW adapter, including its own partial UTF-8 byte cache.
        # It waits without a Python buffered lock or CRT read lock. CRT os.read would use a console code page.
        for raw in _raw_lines(fd, stopped, cached(), read=console.read if console is not None else None):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw              # the SDK returns its UTF-8/JSON ValidationError; never replace bad bytes
            anyio.from_thread.run(lines.send, line, token=token)
    except BaseException:  # noqa: BLE001 - a read error, or the connection or its event loop ended: input ends here
        pass
    finally:
        descriptor.close(reader=True)  # only its claiming thread closes it, after any blocked read ends
        with suppress(BaseException):
            anyio.from_thread.run_sync(lines.close, token=token)


def _read_file_lines(readline, lines, token, stopped) -> None:
    """Keep descriptor-less files on their own readline interface without waiting for a blocked read at teardown."""
    try:
        while not stopped.is_set() and (line := readline()):
            anyio.from_thread.run(lines.send, line, token=token)
    except BaseException:  # noqa: BLE001 - a read error, or an ended connection or event loop: input ends here
        pass
    finally:
        with suppress(BaseException):
            anyio.from_thread.run_sync(lines.close, token=token)


@asynccontextmanager
async def _lines(stdin):
    """Transfer file caches before raw reads; use readline without descriptors. Keep other async iterables."""
    if stdin is None:
        source = sys.stdin
    elif isinstance(stdin, anyio.AsyncFile):
        source = stdin.wrapped
    else:
        yield stdin
        return
    fd = None
    try:
        if getattr(source, "closed", False):
            raise ValueError("stdin is closed")
        fileno = getattr(source, "fileno", None)
        if callable(fileno):
            try:
                fd = fileno()
                if fd is None:
                    raise ValueError("stdin fileno returned no descriptor")
            except UnsupportedOperation:
                pass                    # only an explicitly descriptor-less source takes readline's path
        if fd is None:
            readline = source.readline
        else:
            cached, console = cache_reader(source)
    except Exception as exc:  # noqa: BLE001 - every source probe has the same stable refusal
        raise OSError("[G6_E_STDIN_HANDOFF] stdin descriptor handoff refused") from exc
    if fd is None:
        sender, receiver = anyio.create_memory_object_stream(0)
        stopped = threading.Event()
        threading.Thread(target=_read_file_lines,
                         args=(readline, sender, anyio.lowlevel.current_token(), stopped),
                         name="g6-stdin-reader", daemon=True).start()
        try:
            with receiver:
                yield receiver
        finally:
            stopped.set()
        return
    # Windows' CRT also locks a descriptor during os.read. Give the thread its own descriptor so finalization of
    # the caller's file cannot wait on that CRT lock. An unbuffered file owns the duplicate throughout setup.
    descriptor = _InputDescriptor()
    sender = receiver = stopped = None
    # One try covers startup through yield; a nested try can put its line event outside the outer handler.
    try:
        descriptor.duplicate(fd)
        sender, receiver = anyio.create_memory_object_stream(0)
        stopped = threading.Event()
        threading.Thread(target=_read_lines,
                         args=(descriptor, sender, anyio.lowlevel.current_token(), stopped, cached, console),
                         name="g6-stdin-reader", daemon=True).start()
        with receiver:
            yield receiver
    except BaseException:
        descriptor.close()              # reclaim only an unclaimed duplicate; never close a live reader's fd
        if stopped is not None:
            stopped.set()
        if sender is not None:
            sender.close()
        if receiver is not None:
            receiver.close()
        raise
    finally:
        if stopped is not None:
            stopped.set()                # never join a reader blocked on outside input


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None):
    """Stdio over ``stdin`` and ``stdout`` (the process's own by default): G6's stdin reader under the SDK's parser,
    and G6's writer. A write or flush error ends the whole connection: no tool body starts after it, and the reader
    and the server running over it are cancelled."""
    from mcp.server.stdio import stdio_server as sdk_stdio_server

    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    write_stream, messages = anyio.create_memory_object_stream(0)
    seen = witness(write_stream)
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_writer, messages, stdout, write_stream, seen, tg.cancel_scope)
            async with _lines(stdin) as lines, sdk_stdio_server(stdin=lines, stdout=_Unused()) as (read_stream,
                                                                                                unused_stream):
                await unused_stream.aclose()           # the SDK's writer ends at once: it never writes
                with calls_on(seen):
                    yield read_stream, write_stream
    finally:
        unwitness(write_stream)            # the connection ended: a reply never written is never counted


async def serve(server) -> None:
    """Run a FastMCP ``server`` over G6's ``stdio_server``, as FastMCP's ``run_stdio_async`` does."""
    from mcp.server.lowlevel.server import NotificationOptions

    async with server._lifespan_manager():
        async with stdio_server() as (read_stream, write_stream):
            await server._mcp_server.run(read_stream, write_stream, server._mcp_server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)))


__all__ = ["serve", "stdio_server"]
