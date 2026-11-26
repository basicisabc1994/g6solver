# SPDX-License-Identifier: Apache-2.0
"""A failed native duplicate can have already closed and freed its target number."""
import gc
import io
import os
from contextlib import suppress

import anyio
import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("reuse", [False, True], ids=["closed_target", "new_owner"])
def test_failed_dup2_releases_the_number_without_closing_it_again(monkeypatch, reuse):
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")
    real_dup2 = os.dup2
    targets, bystanders = [], []

    def failed_dup2(fd, target, **kwargs):
        real_dup2(fd, target, **kwargs)
        targets.append(target)
        os.close(target)
        if reuse:
            other = os.open(os.devnull, os.O_RDONLY)
            if other != target:
                real_dup2(other, target)
                os.close(other)
            bystanders.append(target)
        raise OSError("duplicate could not clear inheritance")

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", failed_dup2)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(anyio.wrap_file(source)):
                    pytest.fail("failed duplication yielded input")
        gc.collect()  # The abandoned file's finalizer must also leave the number's next owner alone.
        assert len(targets) == 1
        if reuse:
            os.fstat(bystanders[0])
        else:
            with pytest.raises(OSError):
                os.fstat(targets[0])
        os.fstat(source.fileno())

    try:
        anyio.run(run)
    finally:
        for fd in bystanders:
            with suppress(OSError):
                os.close(fd)
        source.close()
        os.close(write_fd)
