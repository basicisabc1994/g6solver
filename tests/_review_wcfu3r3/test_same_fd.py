"""Closed Windows stdin must not be resurrected by the owned NUL placeholder."""
import io
import os
import sys
from pathlib import Path

import anyio
import pytest

from g6.mcp import stdio


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permits dup2(fd, fd, inheritable=False)")
def test_closed_stdin_is_not_silently_replaced_by_nul(monkeypatch):
    path = Path(__file__).with_name("_closed_stdin.jsonl")
    with path.open("xb") as fixture:
        fixture.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    read_fd = os.open(path, os.O_RDONLY | os.O_BINARY)
    # A borrowed FileIO remains open as an object when its external owner closes
    # the CRT descriptor. This is a supported exact FileIO stack, not a fake fd.
    source = io.FileIO(read_fd, "rb", closefd=False)
    os.close(read_fd)
    calls, received = [], []
    real_dup2 = os.dup2

    def observe_dup2(fd, target, **kwargs):
        calls.append((fd, target))
        return real_dup2(fd, target, **kwargs)

    async def run():
        with anyio.fail_after(2):
            async with stdio._lines(anyio.wrap_file(source)) as lines:
                received.extend([line async for line in lines])

    error = None
    try:
        with pytest.raises(OSError):
            os.dup(read_fd)
        monkeypatch.setattr(stdio.os, "dup2", observe_dup2)
        try:
            anyio.run(run)
        except OSError as exc:
            error = exc
        assert error is not None, (
            f"closed stdin was accepted as clean EOF: dup2 calls={calls}, received={received}"
        )
    finally:
        source.close()
        path.unlink()
