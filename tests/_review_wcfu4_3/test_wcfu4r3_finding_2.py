"""A placeholder whose first removal failed must not stay on disk after the server ends normally.

4af1c617 removes <temp>/g6-stdin-*/slot right after dup2 and, when that removal fails, keeps the directory object
"for a later attempt" (stdio.py:218-220). The only later attempt is _InputDescriptor.close(), which the reader thread
runs when its blocking read returns. A session that ends while the client keeps stdin open (a write error on stdout,
a cancelled connection) leaves the reader in os.read: nothing retries. TemporaryDirectory.cleanup() detaches its
exit-time finalizer before it tries (tempfile.py:953), so interpreter exit does not remove it either. One directory
and one file stay behind per such session.

At c578ee4f the same run leaves nothing: the directory object was untouched until exit, and its finalizer removed it.

The failed first removal is what a scanner or an indexer causes on Windows: it opens the file G6 has just closed and
lets go a moment later. Here stdio's own view of os.dup2 opens the slot for 0.3 s; nothing else in G6 is changed.
"""
import os
import subprocess
import sys
import textwrap

import pytest

CHILD = textwrap.dedent('''
    import glob
    import os
    import sys
    import tempfile
    import threading
    import time
    from types import SimpleNamespace

    import anyio

    from g6.mcp import stdio

    held = sys.argv[1] == "held"
    real_dup2 = os.dup2
    holders = []

    def dup2_then_a_scanner_looks(fd, target, **kwargs):
        result = real_dup2(fd, target, **kwargs)
        if held and not holders:
            for slot in glob.glob(os.path.join(tempfile.gettempdir(), "g6-stdin-*", "slot")):
                handle = open(slot, "rb")
                holders.append(handle)
                threading.Timer(0.3, handle.close).start()
        return result

    stdio.os = SimpleNamespace(**(vars(os) | {"dup2": dup2_then_a_scanner_looks}))


    class Output:
        async def write(self, data):
            return len(data)

        async def flush(self):
            pass


    async def run():
        async with stdio.stdio_server(stdin=None, stdout=Output()) as (incoming, outgoing):
            print("READY", len(holders), file=sys.stderr, flush=True)
            async for _ in incoming:
                await anyio.sleep(0.8)      # the scanner has let go; the reader is back in its blocking read
                break
            await outgoing.aclose()         # the session ends; the client still holds stdin open

    anyio.run(run)
    time.sleep(0.3)
    print("ENDED", file=sys.stderr, flush=True)
''')


@pytest.mark.skipif(os.name != "nt", reason="an open handle blocks deletion on Windows only")
@pytest.mark.parametrize("mode", ["free", "held"], ids=["control_nobody_holds_it", "first_removal_failed"])
def test_no_placeholder_remains_after_a_session_that_ends_with_stdin_open(tmp_path, mode):
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ, TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
    process = subprocess.Popen([sys.executable, "-B", "-c", CHILD, mode], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        process.stdin.write(b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        process.stdin.flush()
        code = process.wait(timeout=60)     # stdin stays open: the process ends by itself
        errors = process.stderr.read().decode(errors="replace")
        assert code == 0 and "ENDED" in errors, errors
        if mode == "held":
            # The fixture reached the path under test: the slot was open elsewhere when dup2 returned.
            # (4af1c617 also reports "stdin placeholder cleanup failed" here; c578ee4f made no attempt yet.)
            assert "READY 1" in errors, errors
        else:
            assert "READY 0" in errors and "cleanup failed" not in errors, errors
        assert sorted(path.name for path in temp.iterdir()) == [], "a g6-stdin placeholder outlived the server"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()
