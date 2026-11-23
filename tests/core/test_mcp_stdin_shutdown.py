# SPDX-License-Identifier: Apache-2.0
"""A stopped stdio connection must release the process, even with live input."""
import json
import os
import subprocess
import sys
import textwrap

import pytest


def _connection_process(state, *, wrapped=False, reply=False):
    child = textwrap.dedent('''
        import os
        import sys
        import anyio
        from mcp import types
        from mcp.shared.message import SessionMessage
        from g6.mcp.stdio import stdio_server

        wrapped = sys.argv[1] == "True"
        reply = sys.argv[2] == "True"
        source = None
        if wrapped:
            read_fd, client_fd = os.pipe()
            source = os.fdopen(read_fd, "r", encoding="utf-8")
            if sys.argv[3] == "closed":
                os.close(client_fd)
            elif sys.argv[3] == "mid_line":
                os.write(client_fd, b'{"partial": "')

        class BrokenOutput:
            async def write(self, data):
                raise BrokenPipeError("client closed reply pipe")

            async def flush(self):
                raise AssertionError("write failed before flush")

        async def run():
            async with stdio_server(
                stdin=anyio.wrap_file(source) if wrapped else None,
                stdout=None if reply else BrokenOutput(),
            ) as (_, outgoing):
                await anyio.sleep(0.15)
                await outgoing.send(SessionMessage(types.JSONRPCMessage(
                    types.JSONRPCResponse(jsonrpc="2.0", id=7, result={"text": "x" * 1000})
                )))
                if reply:
                    await outgoing.aclose()
                else:
                    await anyio.sleep_forever()

        anyio.run(run)
        print("CONNECTION_ENDED", file=sys.stderr, flush=True)
    ''')
    process = subprocess.Popen(
        [sys.executable, "-B", "-X", "utf8", "-c", child, str(wrapped), str(reply), state],
        env=dict(os.environ, G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                 G6_CODEX=r"C:\nonexistent\g6-no-host.exe"),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    exited = False
    try:
        if state == "closed":
            process.stdin.close()
        elif state == "mid_line":
            process.stdin.write(b'{"partial": "')
            process.stdin.flush()
        try:
            process.wait(timeout=5)
            exited = True
        except subprocess.TimeoutExpired:
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        output = process.stdout.read()
        process.stdout.close()
        diagnostic = process.stderr.read().decode("utf-8", errors="replace")
        process.stderr.close()
    return exited, process.returncode, output, diagnostic


class TestConnectionProcessExit:
    @pytest.mark.parametrize("wrapped", [False, True], ids=["stdin", "wrapped_pipe"])
    @pytest.mark.parametrize("state", ["open", "closed", "mid_line"])
    def test_failed_writer_exits_with_any_input_state(self, state, wrapped):
        exited, code, _, diagnostic = _connection_process(state, wrapped=wrapped)
        assert "CONNECTION_ENDED" in diagnostic, diagnostic
        assert exited and code == 0, f"stdin={state}: exited={exited}, code={code}\n{diagnostic}"

    def test_complete_reply_is_flushed_before_exit_with_stdin_open(self):
        exited, code, output, diagnostic = _connection_process("open", reply=True)
        assert output.endswith(b"\n"), (output, diagnostic)
        assert json.loads(output) == {"jsonrpc": "2.0", "id": 7, "result": {"text": "x" * 1000}}
        assert "CONNECTION_ENDED" in diagnostic, diagnostic
        assert exited and code == 0, f"reply written but exited={exited}, code={code}\n{diagnostic}"


class TestStdinShapes:
    @pytest.mark.parametrize("shape", ["pipe", "no_fileno", "unsupported", "closed", "oserror"])
    def test_failed_writer_ends_connection_and_process(self, shape):
        child = textwrap.dedent('''
            import io
            import os
            import sys
            import anyio
            from mcp import types
            from mcp.shared.message import SessionMessage
            from g6.mcp.stdio import stdio_server

            shape = sys.argv[1]
            read_fd, client_fd = os.pipe()
            inner = os.fdopen(read_fd, "r", encoding="utf-8")
            os.write(client_fd, b'{"jsonrpc":"2.0","id":11,"method":"ping"}\\n')

            class NoFileno:
                def readline(self):
                    return inner.readline()

            class Unsupported(NoFileno):
                def fileno(self):
                    raise io.UnsupportedOperation("descriptor unavailable")

            class DescriptorError(NoFileno):
                def fileno(self):
                    raise OSError("descriptor unavailable")

            source = {"pipe": inner, "no_fileno": NoFileno(),
                      "unsupported": Unsupported(), "oserror": DescriptorError()}.get(shape)
            if shape == "closed":
                source = inner
                source.close()

            class BrokenOutput:
                async def write(self, data):
                    print("WRITER_FAILED", file=sys.stderr, flush=True)
                    raise BrokenPipeError("reply pipe closed")

                async def flush(self):
                    raise AssertionError("write failed before flush")

            async def run():
                with anyio.fail_after(2):
                    if shape in ("closed", "oserror"):
                        refused = False
                        try:
                            async with stdio_server(stdin=anyio.wrap_file(source), stdout=BrokenOutput()):
                                raise AssertionError("invalid stdin yielded a stream")
                        except* OSError as errors:
                            assert all("[G6_E_STDIN_HANDOFF]" in str(exc) for exc in errors.exceptions), errors
                            refused = True
                        assert refused, "invalid stdin was accepted"
                        print("STDIN_REFUSED", file=sys.stderr, flush=True)
                        return
                    async with stdio_server(stdin=anyio.wrap_file(source), stdout=BrokenOutput()) as streams:
                        incoming, outgoing = streams
                        if shape != "closed":
                            request = await incoming.receive()
                            assert request.message.root.id == 11
                        await outgoing.send(SessionMessage(types.JSONRPCMessage(
                            types.JSONRPCResponse(jsonrpc="2.0", id=11, result={})
                        )))
                        await anyio.sleep_forever()

            try:
                anyio.run(run)
                print("CONNECTION_ENDED", file=sys.stderr, flush=True)
            finally:
                os.close(client_fd)
                inner.close()
        ''')
        process = subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", "-c", child, shape],
            env=dict(os.environ, G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                     G6_CODEX=r"C:\nonexistent\g6-no-host.exe"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        exited = False
        try:
            try:
                process.wait(timeout=5)
                exited = True
            except subprocess.TimeoutExpired:
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            diagnostic = process.stderr.read().decode("utf-8", errors="replace")
            process.stderr.close()
        assert ("STDIN_REFUSED" if shape in ("closed", "oserror") else "WRITER_FAILED") in diagnostic, diagnostic
        assert "CONNECTION_ENDED" in diagnostic, diagnostic
        assert exited and process.returncode == 0, diagnostic


def test_raw_input_refuses_invalid_utf8_and_keeps_following_lines():
    """Lossy decoding must not turn invalid bytes into an accepted JSON request."""
    import anyio
    from mcp.shared.message import SessionMessage

    from g6.mcp.stdio import stdio_server

    async def run():
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, "r", encoding="utf-8")
        with os.fdopen(write_fd, "wb", buffering=0) as client:
            client.write(b'{"jsonrpc":"2.0","id":1,"method":"bad\xff"}\n'
                         b'not json\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')

        class Output:
            async def write(self, data):
                return len(data)

            async def flush(self):
                pass

        try:
            with anyio.fail_after(5):
                async with stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as (incoming, outgoing):
                    got = [item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__
                           async for item in incoming]
                    await outgoing.aclose()
            return got
        finally:
            source.close()

    assert anyio.run(run) == ["ValidationError", "ValidationError", 2]


def test_raw_input_keeps_long_utf8_lines_and_all_text_line_endings():
    """Framing must preserve split UTF-8, CRLF, lone CR, LF and an unterminated final request."""
    child = textwrap.dedent('''
        import json
        import anyio
        from mcp.shared.message import SessionMessage
        from g6.mcp.stdio import stdio_server

        class Output:
            async def write(self, data):
                return len(data)

            async def flush(self):
                pass

        async def run():
            async with stdio_server(stdout=Output()) as (incoming, outgoing):
                got = [[item.message.root.id, len(item.message.root.method)]
                       if isinstance(item, SessionMessage) else type(item).__name__
                       async for item in incoming]
                await outgoing.aclose()
            return got

        print(json.dumps(anyio.run(run)), flush=True)
    ''')
    payload = b""
    for ident, method, ending in ((1, "\u00e9" * 70000, "\r\n"), (2, "ping", "\r"),
                                   (3, "ping", "\n"), (4, "ping", "")):
        payload += (json.dumps({"jsonrpc": "2.0", "id": ident, "method": method}, ensure_ascii=False)
                    + ending).encode("utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", child], input=payload, capture_output=True, timeout=5,
        env=dict(os.environ, G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                 G6_CODEX=r"C:\nonexistent\g6-no-host.exe"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout) == [[1, 70000], [2, 4], [3, 4], [4, 4]]
