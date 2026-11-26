"""A dup2 that fails without touching its target must not leak the placeholder descriptor.

beeb6c4d releases the placeholder on EVERY OSError from os.dup2. CPython closes the target only when the copy
succeeded and clearing its inheritance flag then failed. When dup2 itself fails (EBADF: the borrowed stdin was
closed between os.fstat and os.dup2), the target is untouched: it is still G6's own os.devnull placeholder, and
after release() nothing ever closes it (close() is a no-op, the finalizer sees closed == True).
At a807c3fa the same failure closed the placeholder.
"""
import errno
import gc
import io
import os
from contextlib import suppress

import anyio
import pytest

from g6.mcp import stdio


def test_a_dup2_that_fails_before_touching_its_target_does_not_leak_the_placeholder(monkeypatch):
    low = os.open(os.devnull, os.O_RDONLY)          # a lower number the placeholder will take, not stdin's own
    read_fd, write_fd = os.pipe()
    assert low < read_fd
    source = io.FileIO(read_fd, "rb", closefd=False)
    real_fstat, real_dup2 = os.fstat, os.dup2
    targets, errors, armed = [], [], [True]

    def fstat_then_the_owner_closes_stdin(fd, *args, **kwargs):
        result = real_fstat(fd, *args, **kwargs)
        if armed and fd == read_fd:
            armed.clear()
            os.close(low)
            os.close(read_fd)                       # the external owner closes stdin after G6 validated it
        return result

    def recording_dup2(fd, target, **kwargs):
        targets.append(target)
        try:
            return real_dup2(fd, target, **kwargs)  # the real failure: the source is gone, the target is untouched
        except OSError as exc:
            errors.append(exc.errno)
            raise

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "fstat", fstat_then_the_owner_closes_stdin)
            patch.setattr(stdio.os, "dup2", recording_dup2)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(anyio.wrap_file(source)):
                    pytest.fail("failed duplication yielded input")

    try:
        anyio.run(run)
        gc.collect()
        assert errors == [errno.EBADF] and len(targets) == 1 and targets[0] != read_fd
        # The refusal is over and every G6 object is collected: the placeholder G6 opened must be closed.
        with pytest.raises(OSError):
            os.fstat(targets[0])
    finally:
        for fd in targets:
            with suppress(OSError):
                os.close(fd)
        os.close(write_fd)
