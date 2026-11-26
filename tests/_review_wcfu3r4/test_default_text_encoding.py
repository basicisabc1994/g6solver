"""The registered server must start on a stdin whose text layer is not UTF-8 (every piped stdin on Windows).

CPython 3.12/3.13 on Windows gives a piped (or file, or NUL) sys.stdin the ANSI code page (cp1252 here), not UTF-8,
unless the person set PYTHONUTF8 / PYTHONIOENCODING / -X utf8. The command ``g6 setup`` registers sets none of them.
MCP's bytes are UTF-8 whatever the text layer above the descriptor says; at 1eb99a6f G6 read the descriptor's bytes
and the server started.
"""
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


@pytest.mark.parametrize("encoding", ["cp1252", "latin-1"])
def test_untouched_default_stdin_with_a_non_utf8_text_layer_still_delivers_requests(monkeypatch, encoding):
    read_fd, write_fd = os.pipe()
    # The stack CPython builds for a piped sys.stdin on Windows: nothing has been read, so nothing is cached.
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding)
    os.write(write_fd, b'{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\n')
    os.close(write_fd)

    async def run():
        with anyio.fail_after(5):
            async with stdio.stdio_server(stdin=None, stdout=Output()) as (incoming, outgoing):
                got = [(item.message.root.id, item.message.root.method) if isinstance(item, SessionMessage)
                       else type(item).__name__ async for item in incoming]
                await outgoing.aclose()
        return got

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            assert anyio.run(run) == [(7, "p\u00e9ng")]
    finally:
        source.close()


@pytest.mark.skipif(sys.platform != "win32", reason="a piped stdin's default text encoding is the ANSI code page")
def test_the_registered_command_answers_initialize_over_a_pipe_without_utf8_mode(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONUTF8", "PYTHONIOENCODING") and not key.startswith("FASTMCP_")}
    env.update(G6_HOME=str(tmp_path / "state"), FASTMCP_CHECK_FOR_UPDATES="off",
               G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe", G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "review", "version": "0"}}}).encode() + b"\n"
    # Exactly what `g6 setup` registers (tests/core/test_mcp_registered_command.py), started as a client starts it.
    done = subprocess.run([sys.executable, "-P", "-c", "from g6.mcp import main; main()"], input=request,
                          capture_output=True, env=env, cwd=tmp_path, timeout=120)
    replies = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    assert any(reply.get("id") == 1 and "result" in reply for reply in replies), (
        f"no initialize reply; exit {done.returncode}: {done.stderr.decode('utf-8', 'replace')[-600:]}")
    assert done.returncode == 0
