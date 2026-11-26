"""A text layer whose every decoded character was consumed has nothing left to return: the handoff must proceed.

8a3b9844 says only data a text layer has consumed and cannot return losslessly is refused. Outside four encodings
(utf-8, utf-8-sig, cp1252, latin-1) its check for "nothing was read" is TextIOWrapper.reconfigure(), which refuses
whenever the wrapper still holds its last chunk string, even when every character of it was already taken. A
launcher that read one whole line of its own from stdin through cp1252 (Western Windows) hands off; the same
launcher through cp1250 or cp932 (the ANSI code page of Central European or Japanese Windows) is refused with
[G6_E_STDIN_HANDOFF] before the client's first request is read.
"""
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


@pytest.mark.parametrize("encoding", ["cp1252", "cp1250", "cp932", "cp1251"])
def test_a_launcher_line_fully_consumed_through_the_ansi_code_page_still_hands_off(monkeypatch, encoding):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding)
    try:
        os.write(write_fd, b"launcher\n")
        assert source.readline() == "launcher\n"   # the whole decoded chunk is consumed: nothing is pending
        os.write(write_fd, b'{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\r\n')
        os.close(write_fd)
        write_fd = None

        async def run():
            with anyio.fail_after(5):
                async with stdio.stdio_server(stdin=None, stdout=Output()) as (incoming, outgoing):
                    got = [(item.message.root.id, item.message.root.method)
                           if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got

        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            assert anyio.run(run) == [(7, "péng")]
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)
