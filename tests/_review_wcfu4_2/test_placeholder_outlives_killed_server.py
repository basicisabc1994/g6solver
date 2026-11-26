"""A server process that is terminated must leave nothing of G6's stdin placeholder on disk.

d1053b2c creates <temp>/g6-stdin-XXXX/slot as the dup2 target. A successful dup2 closes the placeholder's handle at
once, so nothing uses the file or its directory afterwards; yet G6 keeps both until the reader's close(). An MCP host
that ends a stdio server by terminating it (TerminateProcess, SIGKILL, logoff, a crash) leaves one directory and one
file behind per session. At beeb6c4d the placeholder was the null device and nothing was left.
"""
import os
import subprocess
import sys
import textwrap

import pytest

CHILD = textwrap.dedent('''
    import sys
    import anyio
    from g6.mcp.stdio import stdio_server

    class Output:
        async def write(self, data):
            return len(data)

        async def flush(self):
            pass

    async def run():
        async with stdio_server(stdin=None, stdout=Output()) as (incoming, outgoing):
            print("READY", file=sys.stderr, flush=True)
            async for _ in incoming:
                pass
            await outgoing.aclose()

    anyio.run(run)
''')


@pytest.mark.parametrize("ending", ["stdin_closed", "terminated"], ids=["control_stdin_closed", "terminated"])
def test_no_placeholder_remains_on_disk_after_the_server_process_ends(tmp_path, ending):
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ, TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
    process = subprocess.Popen([sys.executable, "-B", "-c", CHILD], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert process.stderr.readline().strip() == b"READY", process.stderr.read().decode(errors="replace")
        if ending == "terminated":
            process.kill()                           # how a host ends a stdio server it does not wait for
            process.wait(timeout=20)
        else:
            process.stdin.close()
            assert process.wait(timeout=20) == 0
        assert sorted(path.name for path in temp.iterdir()) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()
