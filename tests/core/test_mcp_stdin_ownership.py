# SPDX-License-Identifier: Apache-2.0
"""Inventory stdin ownership transfers and failures before a reader can own its descriptor."""
import os
import threading

import anyio
import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("entry", ["default", "wrapped"])
@pytest.mark.parametrize("failure", ["streams", "event", "token", "thread", "start"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_every_reader_setup_failure_releases_its_duplicate(monkeypatch, entry, failure, error_type):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    duplicates = []
    real_dup = os.dup
    real_close = os.close
    closed = []

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def close(fd):
        if fd in duplicates:
            closed.append(fd)
        real_close(fd)

    def fail(*args, **kwargs):
        raise error_type("reader setup failed")

    async def run():
        # Patch only inside the running loop, not asyncio's own thread/event setup.
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "close", close)
            patch.setattr(stdio.sys, "stdin", source)
            owner, name = {
                "streams": (stdio.anyio, "create_memory_object_stream"),
                "event": (stdio.threading, "Event"),
                "token": (stdio.anyio.lowlevel, "current_token"),
                "thread": (stdio.threading, "Thread"),
                "start": (stdio.threading.Thread, "start"),
            }[failure]
            patch.setattr(owner, name, fail)
            with pytest.raises(error_type, match="reader setup failed"):
                async with stdio._lines(None if entry == "default" else anyio.wrap_file(source)):
                    pytest.fail("reader started despite setup failure")
        # A resource may be acquired later than the injected failure; every acquired duplicate must close once.
        assert closed == duplicates, "reader setup left a duplicate without a closing owner"
        for fd in duplicates:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert source.fileno() == read_fd
        os.fstat(read_fd)

    try:
        anyio.run(run)
    finally:
        for fd in duplicates:
            if fd not in closed:
                real_close(fd)
        source.close()
        real_close(write_fd)


def test_started_reader_closes_duplicate_once_after_input_failure(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    duplicates = []
    closed = []
    real_dup, real_read, real_close = os.dup, os.read, os.close

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def read(fd, size):
        if fd in duplicates:
            raise OSError("client input failed")
        return real_read(fd, size)

    def close(fd):
        if fd in duplicates:
            closed.append(fd)
        real_close(fd)

    async def run():
        with anyio.fail_after(2):
            async with stdio._lines(anyio.wrap_file(source)) as lines:
                assert [line async for line in lines] == []
        assert closed == duplicates and len(duplicates) == 1
        os.fstat(read_fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "read", read)
            patch.setattr(stdio.os, "close", close)
            anyio.run(run)
    finally:
        source.close()
        real_close(write_fd)


def test_start_error_after_launch_never_closes_a_claimed_descriptor(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    duplicates = []
    closed = []
    reading, release, finished = threading.Event(), threading.Event(), threading.Event()
    real_dup, real_read, real_close = os.dup, os.read, os.close
    real_start = threading.Thread.start

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def read(fd, size):
        if fd not in duplicates:
            return real_read(fd, size)
        reading.set()
        assert release.wait(2), "test did not release its own reader"
        os.fstat(fd)                     # startup must not have closed this while the worker owned it
        return b""

    def close(fd):
        if fd in duplicates:
            closed.append(fd)
            real_close(fd)
            finished.set()
        else:
            real_close(fd)

    def start(thread):
        real_start(thread)
        assert reading.wait(2), "reader did not claim its descriptor"
        raise RuntimeError("start interrupted after launch")

    async def run():
        with pytest.raises(RuntimeError, match="start interrupted after launch"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("interrupted startup yielded a connection")
        assert closed == [], "startup closed a descriptor already owned by a live reader"
        os.fstat(duplicates[0])
        release.set()
        with anyio.fail_after(2):
            while not finished.is_set():
                await anyio.sleep(0.01)
        assert closed == duplicates and len(duplicates) == 1

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "read", read)
            patch.setattr(stdio.os, "close", close)
            patch.setattr(stdio.threading.Thread, "start", start)
            anyio.run(run)
    finally:
        release.set()
        source.close()
        real_close(write_fd)


def test_startup_reclaims_duplicate_before_a_delayed_reader_can_claim_it(monkeypatch):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    waiting, release, finished = threading.Event(), threading.Event(), threading.Event()
    duplicates = []
    real_dup, real_start, real_reader = os.dup, threading.Thread.start, stdio._read_lines

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def delayed(*args):
        waiting.set()
        assert release.wait(2), "test did not release its own delayed reader"
        try:
            real_reader(*args)
        finally:
            finished.set()

    def start(thread):
        real_start(thread)
        assert waiting.wait(2), "reader did not start"
        raise RuntimeError("start interrupted before claim")

    async def run():
        with pytest.raises(RuntimeError, match="start interrupted before claim"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("interrupted startup yielded a connection")
        assert len(duplicates) == 1
        with pytest.raises(OSError):
            os.fstat(duplicates[0])
        reused = real_dup(read_fd)
        try:
            release.set()
            with anyio.fail_after(2):
                while not finished.is_set():
                    await anyio.sleep(0.01)
            os.fstat(reused)              # a delayed reader must not close a newly reused descriptor number
        finally:
            os.close(reused)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio, "_read_lines", delayed)
            patch.setattr(stdio.threading.Thread, "start", start)
            anyio.run(run)
    finally:
        release.set()
        source.close()
        os.close(write_fd)
