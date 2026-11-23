# SPDX-License-Identifier: Apache-2.0
"""Buffered and raw stdin handoffs must give the SDK the same remaining requests."""
import codecs
import io
import os

import anyio
import pytest
from mcp.shared.message import SessionMessage

from g6.mcp import stdio


class Output:
    async def write(self, data):
        return len(data)

    async def flush(self):
        pass


@pytest.mark.parametrize("entry", ["default", "wrapped"])
@pytest.mark.parametrize("cache", ["raw", "bytes", "text_raw", "text", "both"])
@pytest.mark.parametrize("split", range(1, 18))
def test_every_file_handoff_keeps_cached_and_unread_requests(monkeypatch, entry, cache, split):
    # Small buffers force boundaries through UTF-8, CRLF and JSON tokens, including multiple cached requests.
    read_fd, write_fd = os.pipe()
    raw = io.FileIO(read_fd, "rb")
    source = raw if cache in ("raw", "text_raw") else io.BufferedReader(raw, buffer_size=split)
    if cache in ("text_raw", "text", "both"):
        source = io.TextIOWrapper(source, encoding="utf-8")
    request = b'{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\r\n'
    tail = b'{"jsonrpc":"2.0","id":8,"method":"ping"}\r'
    tail += b'{"jsonrpc":"2.0","id":9,"method":"ping"}'
    preamble = b"launcher\n" if cache in ("text_raw", "text", "both") else b""
    try:
        os.write(write_fd, preamble + request + tail)
        os.close(write_fd)
        write_fd = None
        if preamble:
            assert source.readline() == "launcher\n"
        if cache in ("bytes", "both"):
            buffer = source.buffer if cache == "both" else source
            buffer.peek(1)
        # Preserve any caller method overrides, including their absence, during the finite cache drain.
        buffer = source.buffer if isinstance(source, io.TextIOWrapper) else source
        saved_raw_attrs = dict(raw.__dict__)
        saved_buffer_attrs = dict(buffer.__dict__)

        async def run():
            with anyio.fail_after(2):
                async with stdio.stdio_server(
                    stdin=None if entry == "default" else anyio.wrap_file(source), stdout=Output(),
                ) as (incoming, outgoing):
                    got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            got = anyio.run(run)
        assert got == [7, 8, 9], "a stdio handoff lost, split or reordered a request"
        assert raw.__dict__ == saved_raw_attrs
        assert buffer.__dict__ == saved_buffer_attrs
        assert not source.closed
        assert source.read() == ("" if isinstance(source, io.TextIOWrapper) else b"")
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)


@pytest.mark.parametrize("prefix", [b"", b"\xc3", b"\xe2\x82", b"\xf0\x9f\x98"])
@pytest.mark.parametrize("pending_cr", [False, True])
def test_text_decoder_tail_and_invalid_cached_bytes_keep_following_requests(prefix, pending_cr):
    # The launcher consumes one line, leaving decoded text, a possible pending CR and incomplete UTF-8.
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb"), buffer_size=16), encoding="utf-8")
    initial = b"launcher\n" + (b"\r" if pending_cr else b"") + prefix
    os.write(write_fd, initial)
    assert source.readline() == "launcher\n"
    os.write(write_fd, b'\xff\n{"jsonrpc":"2.0","id":8,"method":"ping"}\n')
    os.close(write_fd)

    async def run():
        with anyio.fail_after(2):
            async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as (incoming, outgoing):
                got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                       async for item in incoming]
                await outgoing.aclose()
        return got

    try:
        got = anyio.run(run)
        assert got == (["ValidationError"] if pending_cr else []) + ["ValidationError", 8]
        assert source.read() == "", "decoder recovery left synthetic content in the caller's stream"
    finally:
        source.close()


def test_async_iterable_handoff_preserves_the_original_stream():
    async def run():
        sender, receiver = anyio.create_memory_object_stream(1)
        async with sender, receiver:
            await sender.send("cached request\n")
            await sender.aclose()
            async with stdio._lines(receiver) as lines:
                assert [line async for line in lines] == ["cached request\n"]
    anyio.run(run)


REQUESTS = (b'{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80"}\r\n'
            b'{"jsonrpc":"2.0","id":8,"method":"ping"}\n')


@pytest.mark.parametrize("split", range(1, len(REQUESTS)))
@pytest.mark.parametrize("prefetch", [False, True], ids=["kernel_tail", "buffered_tail"])
def test_every_decoder_boundary_keeps_the_complete_requests(split, prefetch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    try:
        os.write(write_fd, b"launcher\n" + REQUESTS[:split])
        assert source.readline() == "launcher\n"
        os.write(write_fd, REQUESTS[split:])
        os.close(write_fd)
        write_fd = None
        if prefetch:
            source.buffer.peek(1)

        async def run():
            with anyio.fail_after(2):
                async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as streams:
                    incoming, outgoing = streams
                    got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        assert anyio.run(run) == [7, 8], "a decoder boundary changed the unread request sequence"
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)


@pytest.mark.parametrize("errors", ["strict", "surrogateescape", "surrogatepass"])
def test_lossless_text_error_modes_preserve_cached_invalid_input(errors):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8", errors=errors)
    try:
        # Keep invalid bytes in the byte buffer until G6 owns input, rather than decoding them in the launcher.
        os.write(write_fd, b'bad\xff\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        os.close(write_fd)
        write_fd = None
        source.buffer.peek(1)

        async def run():
            with anyio.fail_after(2):
                async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as streams:
                    incoming, outgoing = streams
                    got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        assert anyio.run(run) == ["ValidationError", 7]
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)


@pytest.mark.parametrize("shape", ["opaque", "encoding", "lossy", "custom_method"])
def test_unsupported_descriptor_stacks_fail_before_reading_or_duplicating(monkeypatch, shape):
    read_fd, write_fd = os.pipe()
    inner = os.fdopen(read_fd, "r", encoding="latin-1" if shape == "encoding" else "utf-8",
                      errors="replace" if shape == "lossy" else "strict")

    class Opaque:
        def fileno(self):
            return inner.fileno()

        def readline(self):
            pytest.fail("opaque stdin was consumed")

    source = Opaque() if shape == "opaque" else inner
    if shape == "custom_method":
        source.buffer.read1 = Opaque().readline
        original = source.buffer.read1

    def duplicate(fd, target, **kwargs):
        pytest.fail("unsupported stdin acquired a duplicate")

    async def run():
        with pytest.raises((TypeError, ValueError), match="stdin"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("unsupported stdin yielded a connection")

    try:
        os.write(write_fd, b"still owned by caller\n")
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", duplicate)
            anyio.run(run)
        if shape == "custom_method":
            assert source.buffer.read1 is original
            del source.buffer.read1
        assert inner.readline() == "still owned by caller\n"
    finally:
        inner.close()
        os.close(write_fd)


def test_descriptorless_file_handoff_keeps_its_existing_position():
    source = io.StringIO('launcher\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    assert source.readline() == "launcher\n"

    async def run():
        with anyio.fail_after(2):
            async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as streams:
                incoming, outgoing = streams
                got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                       async for item in incoming]
                await outgoing.aclose()
        return got

    try:
        assert anyio.run(run) == [7]
        assert not source.closed
    finally:
        source.close()


# Each malformed line must end in one parser error; IDs 71 and 72 must survive every handoff split.
BAD_UTF8 = (b"\xff", b"\x80", b"\xc0\xaf", b"\xe0\x80\xaf", b"\xf0\x80\x80\xaf",
            b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xc3", b"\xe2\x82", b"\xf0\x9f\x98")
BAD_SPLITS = [(bad, split) for bad in BAD_UTF8 for split in range(len(bad) + 1)]


def _can_hold_prefix(prefix):
    try:
        codecs.getincrementaldecoder("utf-8")().decode(prefix, final=False)
    except UnicodeDecodeError:
        return False
    return True


@pytest.mark.parametrize("bad,split", BAD_SPLITS)
@pytest.mark.parametrize("pending_cr", [False, True])
@pytest.mark.parametrize("prefetch", [False, True], ids=["kernel_tail", "buffered_tail"])
@pytest.mark.parametrize("errors", ["strict", "surrogateescape", "surrogatepass"])
def test_malformed_utf8_at_every_handoff_split_keeps_message_order(bad, split, pending_cr, prefetch, errors):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8", errors=errors)
    try:
        # Strict decoders cannot pre-read an already invalid prefix. For those splits warm the byte cache;
        # all other splits exercise decoded text and the decoder's actual incomplete/invalid byte state.
        text_prefix = _can_hold_prefix(bad[:split]) or errors == "surrogateescape"
        preamble = b"launcher\n" + (b"\r" if pending_cr else b"")
        os.write(write_fd, preamble + (bad[:split] if text_prefix else b""))
        assert source.readline() == "launcher\n"
        if not text_prefix and split:
            os.write(write_fd, bad[:split])
            source.buffer.peek(1)
        os.write(write_fd, bad[split:] + b'\r\n{"jsonrpc":"2.0","id":71,"method":"ping"}\r'
                 b'{"jsonrpc":"2.0","id":72,"method":"ping"}\n')
        os.close(write_fd)
        write_fd = None
        if prefetch:
            source.buffer.peek(1)

        async def run():
            with anyio.fail_after(5):
                async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as streams:
                    incoming, outgoing = streams
                    got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        assert anyio.run(run) == (["ValidationError"] if pending_cr else []) + ["ValidationError", 71, 72]
        assert source.read() == "", "handoff left decoder bytes or synthetic content behind"
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)
