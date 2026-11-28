# SPDX-License-Identifier: Apache-2.0
"""The wire is UTF-8 even when a launcher chose another text encoding."""
import io
import json
import os
import subprocess
import sys

import anyio
import pytest
from mcp.shared.message import SessionMessage

from g6.mcp import stdio


class Output:
    async def write(self, data):
        return len(data)

    async def flush(self):
        pass


@pytest.mark.parametrize("encoding", ["cp1252", "latin-1", "utf-8", "utf-8-sig"])
@pytest.mark.parametrize("warm", [False, True], ids=["untouched", "partly_read"])
@pytest.mark.parametrize("prefetch", [False, True], ids=["kernel_bytes", "buffered_bytes"])
@pytest.mark.parametrize("entry", ["default", "wrapped"])
def test_text_encoding_keeps_cached_and_unread_wire_bytes(monkeypatch, encoding, warm, prefetch, entry):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding)
    request = b'{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\r\n'
    try:
        # Leave decoded text AND a partial wire character in the launcher's cache, then append more bytes.
        if warm:
            split = request.index(b"\xc3") + 1
            os.write(write_fd, b"launcher\n" + request[:split])
            assert source.readline() == "launcher\n"
            os.write(write_fd, request[split:])
        else:
            os.write(write_fd, request)
        os.write(write_fd, b'{"jsonrpc":"2.0","id":8,"method":"ping"}\n')
        os.close(write_fd)
        write_fd = None
        if prefetch:
            source.buffer.peek(1)

        async def run():
            with anyio.fail_after(5):
                async with stdio.stdio_server(
                    stdin=None if entry == "default" else anyio.wrap_file(source), stdout=Output(),
                ) as (incoming, outgoing):
                    got = [(item.message.root.id, item.message.root.method)
                           if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            assert anyio.run(run) == [(7, "p\u00e9ng"), (8, "ping")]
        assert not source.closed
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)


@pytest.mark.parametrize("encoding", ["cp1252", "latin-1", "utf-8", "utf-8-sig", "utf-16"])
@pytest.mark.parametrize("errors", ["replace", "ignore"])
def test_untouched_lossy_text_layer_does_not_decode_wire_bytes(encoding, errors):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding, errors=errors)
    os.write(write_fd, b'bad\xff\n{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\n')
    os.close(write_fd)
    source.buffer.peek(1)

    async def run():
        with anyio.fail_after(5):
            async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as (incoming, outgoing):
                got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                       async for item in incoming]
                await outgoing.aclose()
        return got

    try:
        assert anyio.run(run) == ["ValidationError", 7]
    finally:
        source.close()


@pytest.mark.parametrize("encoding", ["cp1252", "latin-1", "utf-8", "utf-8-sig"])
@pytest.mark.parametrize("errors", ["replace", "ignore"])
def test_already_decoded_lossy_text_is_refused_before_duplication(monkeypatch, encoding, errors):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding, errors=errors)
    os.write(write_fd, b"launcher\nbad\xff\n")
    os.close(write_fd)
    assert source.readline() == "launcher\n"

    def no_duplicate(*args, **kwargs):
        pytest.fail("unrecoverable text acquired a duplicate")

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("unrecoverable text yielded input")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", no_duplicate)
            anyio.run(run)
    finally:
        source.close()


def test_registered_command_initializes_with_non_ascii_without_utf8_mode(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING") and not key.startswith("FASTMCP_")}
    env.update(G6_HOME=str(tmp_path / "state"), FASTMCP_CHECK_FOR_UPDATES="off",
               G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe", G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
    request = json.dumps({"jsonrpc": "2.0", "id": "p\u00e9ng", "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "p\u00e9ng", "version": "0"},
    }}, ensure_ascii=False).encode("utf-8") + b"\n"
    result = subprocess.run([sys.executable, "-P", "-c", "from g6.mcp import main; main()"],
                            input=request, capture_output=True, env=env, cwd=tmp_path, timeout=120)
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert any(reply.get("id") == "p\u00e9ng" and "result" in reply for reply in replies), result.stderr
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("warm", [False, True], ids=["untouched_signature", "consumed_signature"])
def test_utf8_sig_keeps_unread_signature_bytes_and_does_not_insert_new_ones(warm):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="utf-8-sig")
    os.write(write_fd, b"\xef\xbb\xbf" + (b"launcher\n" if warm else b"")
             + b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    os.close(write_fd)
    if warm:
        assert source.readline() == "launcher\n"
    else:
        source.buffer.peek(1)

    async def run():
        with anyio.fail_after(5):
            async with stdio._lines(anyio.wrap_file(source)) as lines:
                return [line async for line in lines]

    try:
        assert anyio.run(run) == [('\ufeff' if not warm else '')
                                 + '{"jsonrpc":"2.0","id":7,"method":"ping"}\n']
    finally:
        source.close()
