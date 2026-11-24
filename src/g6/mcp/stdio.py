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

import logging
import os
import sys
import tempfile
import threading
from contextlib import asynccontextmanager, suppress
from io import FileIO, TextIOWrapper, UnsupportedOperation
from tempfile import TemporaryDirectory as TemporaryDirectory  # retained for callers of the former allocation seam
from uuid import uuid4

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


_retired_input_files = {}


class _InputFile(FileIO):
    """A native file owner that can abandon an uncertain descriptor without another OS close.

    FileIO acquires the placeholder in C, with its closing owner already allocated. Its IOBase finalizer uses
    closed/close too, so releasing the claim also prevents a later finalizer from closing a reused number.
    """

    _released = False
    _retired = _retired_input_files

    @property
    def closed(self):
        return self._released or id(self) in self._retired or super().closed

    def release(self):
        self._released = True

    def fileno(self):
        if self.closed:
            raise ValueError("stdin descriptor ownership was released")
        return super().fileno()

    def close(self):
        if not self.closed:
            try:
                super().close()
            finally:
                # A failed OS close may already have consumed the number. Never retry it or publish it.
                self.release()


def _input_cleanup(close, resource):
    """Report cleanup faults without replacing a startup refusal or preventing the reader's EOF signal."""
    try:
        close()
    except BaseException:  # noqa: BLE001 - cleanup must also finish during interrupted setup or reader shutdown
        with suppress(BaseException):
            logging.getLogger(__name__).warning(
                "[G6_E_STDIN_HANDOFF] stdin %s cleanup failed", resource, exc_info=True,
            )
        return False
    return True


def _placeholder_path():
    # One candidate, no probing/collision loop and no implicit current-directory fallback. Honor an
    # explicitly selected tempfile directory without invoking tempfile's directory-discovery writes.
    folder = tempfile.tempdir
    if folder is None:
        folder = next((os.environ[key] for key in ("TMPDIR", "TEMP", "TMP") if os.environ.get(key)), None)
    if folder is None:
        folder = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp") if os.name == "nt" else "/tmp"
    if not os.path.isabs(folder):
        raise OSError("stdin temporary directory must be absolute")
    return os.path.join(os.fsdecode(folder), "g6-stdin-" + uuid4().hex)


def _open_placeholder(path, flags):
    # FileIO allocates the owning object before its opener is called. O_EXCL is explicit even though
    # mode x already asks for it. Windows deletes the file when dup2 closes this native handle.
    return os.open(path, flags | os.O_EXCL | getattr(os, "O_TEMPORARY", 0), 0o600)


class _PlaceholderName:
    """Only POSIX needs an unlink, immediately after acquiring the file owner."""

    def __init__(self, name, owner):
        self.name = name
        self.owner = owner
        self.owned = False

    def cleanup(self):
        # The marker predates the open, so interruption immediately after acquisition still has an
        # unlink owner. A collision/open failure never authorizes removing somebody else's pathname.
        self.owned = self.owned or self.owner.file is not None
        if not self.owned:
            return
        with suppress(FileNotFoundError):
            os.unlink(self.name)


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
            # The exclusive, unnamed/delete-on-close file has a unique identity. Its lifetime is its
            # native handle, including on refused setup and process termination; no directory is made.
            unique = False
            try:
                path = _placeholder_path()
                if os.name != "nt":
                    self.placeholder = _PlaceholderName(path, self)
                self.file = _InputFile(path, "xb+", opener=_open_placeholder)
                if os.name != "nt":
                    self.placeholder.cleanup()  # interrupts here propagate through startup's cleanup
                    self.placeholder = None
            except OSError:
                # Every failed allocation takes the same fallback.
                self.close()
                self.file = _InputFile(os.devnull, "rb")
            else:
                # A failed query is a probe refusal, leaving the owned file for the caller's cleanup.
                # A successful query with no usable identity instead takes the allocation fallback.
                placeholder = os.stat(self.file.fileno())
                identity = (getattr(placeholder, "st_dev", None), getattr(placeholder, "st_ino", None))
                if any(type(part) is not int for part in identity) or identity[1] <= 0:
                    self.close()
                    self.file = _InputFile(os.devnull, "rb")
                else:
                    unique = True
            if self.file.fileno() == fd:
                # The external owner closed stdin after validation; this slot now owns only the placeholder.
                self.close()
                raise OSError("stdin closed during descriptor handoff")
            try:
                os.dup2(fd, self.file.fileno(), inheritable=False)
            except OSError:
                untouched = False
                if unique:
                    try:
                        target = os.stat(self.file.fileno())
                        untouched = (target.st_dev, target.st_ino) == identity
                    except OSError:
                        pass
                if not untouched:
                    # NUL, pipes and consoles can all report (0,0); even POSIX /dev/null can be reopened.
                    # A failed copy gives no pre-copy guarantee. Retire the claim without touching its
                    # number or asking a factory result for a private method. Keep plain FileIO objects
                    # alive too, so their finalizers cannot close a foreign descriptor after collection.
                    file = self.file
                    _retired_input_files[id(file)] = file
                    # Keep the owner present through its one cleanup transition. Retired files must
                    # not run an OS close, even when a plain factory result cannot mark itself closed.
                    self.close()
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
                self._cleanup_placeholder()
                # The factory returns a file object, not necessarily G6's native owner. Retire our
                # closing claim first; the file's close owns its own bookkeeping, including on failure.
                file, self.file = self.file, None
                if file is not None and id(file) not in _retired_input_files:
                    _input_cleanup(file.close, "descriptor")

    def _cleanup_placeholder(self):
        if self.placeholder is not None and _input_cleanup(self.placeholder.cleanup, "placeholder"):
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
        # Only its claiming thread closes it, after any blocked read ends. Even an unexpected close fault
        # must not prevent the parser seeing EOF.
        _input_cleanup(lambda: descriptor.close(reader=True), "reader descriptor")
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


def _stdin_prefix(cached):
    """Translate cache faults while the caller's uninterrupted startup handler owns the duplicate."""
    try:
        return cached()
    except Exception as exc:  # noqa: BLE001 - keep the stable startup refusal for every cache fault
        raise OSError("[G6_E_STDIN_HANDOFF] stdin descriptor handoff refused") from exc


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
            cached, console = cache_reader(source, defer=True)
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
        # Codec validation must finish on the startup path, where a refusal reaches the caller.
        # The transactional cache probe restores native pending text on any codec refusal.
        # Keep exception translation in a helper: a nested try's header can be an unprotected trace
        # point in this frame. The call and every probe/helper exit stay under startup's BaseException handler.
        prefix = _stdin_prefix(cached)
        threading.Thread(target=_read_lines,
                         args=(descriptor, sender, anyio.lowlevel.current_token(), stopped, lambda: prefix, console),
                         name="g6-stdin-reader", daemon=True).start()
        with receiver:
            yield receiver
    except BaseException:
        # Reclaim only an unclaimed duplicate; cleanup must preserve the original exception.
        _input_cleanup(descriptor.close, "startup descriptor")
        if stopped is not None:
            stopped.set()
        if sender is not None:
            _input_cleanup(sender.close, "sender")
        if receiver is not None:
            _input_cleanup(receiver.close, "receiver")
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
