"""Refusal paths of the stdin handoff: one closing owner, and the stable refusal, on every path."""
import io
import os

import anyio
import pytest

from g6.mcp import stdio


def _native_failure(real_dup2, reused):
    """os.dup2(fd, fd2, inheritable=False) as CPython fails it once the copy exists: when it cannot clear the new
    descriptor's inherit flag, os_dup2_impl closes fd2 itself and then raises (Modules/posixmodule.c, the Windows
    branch and the dup2 fallback branch). ``reused`` models another thread's open taking the freed number."""
    def dup2(fd, target, **kwargs):
        real_dup2(fd, target)
        os.close(target)
        if reused is not None:
            other = os.open(os.devnull, os.O_RDONLY)
            if other != target:
                real_dup2(other, target)
                os.close(other)
            reused.append(target)
        raise OSError("cannot clear the duplicate's inherit flag")
    return dup2


def test_dup2_that_failed_after_closing_its_target_still_gives_the_stable_refusal(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", _native_failure(os.dup2, None))
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(anyio.wrap_file(source)):
                    pytest.fail("a failed duplicate yielded a stream")

    try:
        anyio.run(run)
    finally:
        source.close()
        os.close(write_fd)


def test_dup2_that_failed_after_closing_its_target_does_not_close_the_numbers_next_owner(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    reused = []

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", _native_failure(os.dup2, reused))
            with pytest.raises(OSError):
                async with stdio._lines(anyio.wrap_file(source)):
                    pytest.fail("a failed duplicate yielded a stream")
        assert len(reused) == 1
        try:
            os.fstat(reused[0])
        except OSError:
            reused.clear()
            pytest.fail("G6 closed a descriptor number it no longer owned: another file had reused it")

    try:
        anyio.run(run)
    finally:
        for fd in reused:
            os.close(fd)
        source.close()
        os.close(write_fd)


@pytest.mark.parametrize("entry", ["default", "wrapped"])
def test_detached_text_stdin_gets_the_stable_refusal(monkeypatch, entry):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="utf-8")
    buffer = source.detach()            # e.g. a launcher took sys.stdin's binary layer for itself

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(None if entry == "default" else anyio.wrap_file(source)):
                    pytest.fail("detached stdin yielded a stream")

    try:
        anyio.run(run)
    finally:
        buffer.close()
        os.close(write_fd)
