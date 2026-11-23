# SPDX-License-Identifier: Apache-2.0
"""Interrupt every setup line after duplication; the caller and reader retain their own fds."""
import os
import sys
import threading
from collections import Counter

import anyio
import pytest

from g6.mcp import stdio


def test_interrupt_after_duplicate_does_not_leak(monkeypatch):
    # Review 1's scenario: the very first _lines line event after os.dup returns.
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    duplicates, closed = [], []
    real_dup, real_close = os.dup, os.close
    original_trace = sys.gettrace()
    target_code = stdio._lines.__wrapped__.__code__

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def close(fd):
        if fd in duplicates:
            closed.append(fd)
        real_close(fd)

    def interrupt(frame, event, arg):
        if frame.f_code is target_code and event == "line" and duplicates:
            raise KeyboardInterrupt("injected startup interruption")
        return interrupt

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "close", close)
            sys.settrace(interrupt)
            try:
                with pytest.raises(KeyboardInterrupt, match="injected startup interruption"):
                    async with stdio._lines(anyio.wrap_file(source)):
                        pytest.fail("interrupted startup yielded a stream")
            finally:
                sys.settrace(original_trace)
        assert len(duplicates) == 1
        leaked = []
        for fd in duplicates:
            try:
                os.fstat(fd)
            except OSError:
                pass
            else:
                leaked.append(fd)
        os.fstat(read_fd)
        assert not leaked, f"unclaimed stdin duplicate still open after KeyboardInterrupt: {leaked}"
        assert closed == duplicates, "the duplicate must close exactly once"

    try:
        anyio.run(run)
    finally:
        sys.settrace(original_trace)
        for fd in duplicates:
            if fd not in closed:
                real_close(fd)
        source.close()
        real_close(write_fd)


def _setup_probe(target=None, error_kind="keyboard"):
    """Record real line events, or interrupt one occurrence while the reader's claim is delayed."""
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    # EOF also lets a reader finish if broken setup failed to signal its stop event.
    os.close(write_fd)
    duplicates, closed, threads, points = [], [], [], []
    occurrences = Counter()
    release = threading.Event()
    real_dup, real_close = os.dup, os.close
    real_start, real_reader = threading.Thread.start, stdio._read_lines
    original_trace = sys.gettrace()
    codes = {stdio._lines.__wrapped__.__code__, stdio._InputDescriptor.__init__.__code__}

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def close(fd):
        if fd in duplicates:
            closed.append(fd)
        real_close(fd)

    def delayed_reader(*args):
        if not release.wait(5):
            return
        real_reader(*args)

    def start(thread):
        real_start(thread)
        threads.append(thread)

    async def run():
        error_type = KeyboardInterrupt if error_kind == "keyboard" else anyio.get_cancelled_exc_class()
        injected = False

        def trace(frame, event, arg):
            nonlocal injected
            if event == "line" and frame.f_code in codes and duplicates:
                key = (frame.f_code.co_name, frame.f_lineno)
                occurrences[key] += 1
                point = (*key, occurrences[key])
                points.append(point)
                if point == target:
                    injected = True
                    raise error_type("injected setup line interruption")
            return trace

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "close", close)
            patch.setattr(stdio, "_read_lines", delayed_reader)
            patch.setattr(stdio.threading.Thread, "start", start)

            async def enter():
                sys.settrace(trace)
                try:
                    async with stdio._lines(anyio.wrap_file(source)):
                        # The inventory ends at the yield: do not inventory teardown.
                        sys.settrace(original_trace)
                finally:
                    sys.settrace(original_trace)

            try:
                if target is None:
                    await enter()
                else:
                    with pytest.raises(error_type, match="injected setup line interruption"):
                        await enter()
                    assert injected, f"setup no longer reaches inventoried event {target}"
                    # Check before releasing the reader, so a later claim cannot hide a setup leak.
                    os.fstat(read_fd)
                    assert closed == duplicates, f"setup event {target} left an unclaimed duplicate open"
                    with pytest.raises(OSError):
                        os.fstat(duplicates[0])
            finally:
                release.set()
                with anyio.fail_after(5):
                    while any(thread.is_alive() for thread in threads):
                        await anyio.sleep(0.001)
                for thread in threads:
                    thread.join()

        assert len(duplicates) == 1
        assert closed == duplicates, f"setup event {target} left an unclaimed duplicate or closed it twice"
        with pytest.raises(OSError):
            os.fstat(duplicates[0])
        os.fstat(read_fd)

    try:
        anyio.run(run)
        return points
    finally:
        sys.settrace(original_trace)
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        for fd in duplicates:
            if fd not in closed:
                real_close(fd)
        source.close()


def pytest_generate_tests(metafunc):
    if "setup_event" in metafunc.fixturenames:
        # Trace discovery, rather than a hand-picked line list, includes multiline start calls
        # and any constructor lines after acquisition. Each occurrence gets its own pytest case.
        points = _setup_probe()
        assert points, "no post-duplication setup lines were inventoried"
        metafunc.parametrize("setup_event", points, ids=[f"{name}-{line}-{nth}" for name, line, nth in points])


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
def test_every_post_duplication_line_releases_unclaimed_descriptor(setup_event, error_kind):
    _setup_probe(setup_event, error_kind)


@pytest.mark.parametrize("exit_kind", ["normal", "cancelled"])
def test_reader_closes_once_and_teardown_preserves_reused_fd(monkeypatch, exit_kind):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    os.close(write_fd)
    duplicates, closed, threads = [], [], []
    reused = None
    real_dup, real_close = os.dup, os.close
    real_start = threading.Thread.start
    caller_thread = threading.get_ident()

    def duplicate(fd):
        copied = real_dup(fd)
        duplicates.append(copied)
        return copied

    def close(fd):
        if fd in duplicates:
            closed.append((fd, threading.get_ident()))
        real_close(fd)

    def start(thread):
        real_start(thread)
        threads.append(thread)

    async def run():
        nonlocal reused
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup", duplicate)
            patch.setattr(stdio.os, "close", close)
            patch.setattr(stdio.threading.Thread, "start", start)
            with anyio.CancelScope() as scope:
                async with stdio._lines(anyio.wrap_file(source)) as lines:
                    with anyio.fail_after(5):
                        assert [line async for line in lines] == []
                        while any(thread.is_alive() for thread in threads):
                            await anyio.sleep(0.001)
                    assert len(duplicates) == len(closed) == 1
                    assert closed[0][0] == duplicates[0]
                    assert closed[0][1] != caller_thread, "only the claiming reader may close its fd"
                    with pytest.raises(OSError):
                        os.fstat(duplicates[0])
                    replacement = os.open(os.devnull, os.O_RDONLY)
                    if replacement != duplicates[0]:
                        os.dup2(replacement, duplicates[0])
                        real_close(replacement)
                    reused = duplicates[0]
                    os.fstat(reused)
                    if exit_kind == "cancelled":
                        scope.cancel()
                        await anyio.lowlevel.checkpoint()
            assert scope.cancelled_caught == (exit_kind == "cancelled")
            os.fstat(reused)
            assert len(closed) == 1, "teardown closed a descriptor number reused by another file"
            os.fstat(read_fd)

    try:
        anyio.run(run)
    finally:
        for thread in threads:
            thread.join(timeout=5)
        if reused is not None:
            real_close(reused)
        source.close()
