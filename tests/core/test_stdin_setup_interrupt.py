# SPDX-License-Identifier: Apache-2.0
"""Interrupt every setup line after duplication; the caller and reader retain their own fds."""
import os
import sys
import threading
from collections import Counter

import anyio
import pytest

from g6.mcp import stdio


def _observe_owner_close(real_close, duplicates, closed, *, thread_ids=False):
    def close(owner, *, reader=False):
        fd = owner.file.fileno() if owner.file is not None and owner.claimed == reader else None
        real_close(owner, reader=reader)
        if fd in duplicates:
            closed.append((fd, threading.get_ident()) if thread_ids else fd)
    return close


def test_interrupt_after_duplicate_does_not_leak(monkeypatch):
    # Review 1's scenario, at the first _lines event after replacement of the pre-owned descriptor.
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    duplicates, closed = [], []
    real_dup2, real_close = os.dup2, os.close
    original_trace = sys.gettrace()
    target_code = stdio._lines.__wrapped__.__code__

    def duplicate(fd, target, **kwargs):
        duplicates.append(target)
        return real_dup2(fd, target, **kwargs)

    close = _observe_owner_close(stdio._InputDescriptor.close, duplicates, closed)

    def interrupt(frame, event, arg):
        if frame.f_code is target_code and event == "line" and duplicates:
            raise KeyboardInterrupt("injected startup interruption")
        return interrupt

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", duplicate)
            patch.setattr(stdio._InputDescriptor, "close", close)
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


def _setup_probe(target=None, error_kind="keyboard", *, opcodes=False, completed_lines=False,
                 validation_lines=False):
    """Inventory setup events, optionally starting at native dup2 completion, with the claim delayed."""
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    # EOF also lets a reader finish if broken setup failed to signal its stop event.
    os.close(write_fd)
    duplicates, closed, threads, points = [], [], [], []
    occurrences = Counter()
    release = threading.Event()
    real_dup2, real_close = os.dup2, os.close
    real_start, real_reader = threading.Thread.start, stdio._read_lines
    original_trace = sys.gettrace()
    owner = None
    acquisition_completed = False
    validation_started = False
    placeholders = []
    real_input_file = stdio._InputFile
    codes = {stdio._lines.__wrapped__.__code__, stdio._InputDescriptor.__init__.__code__}
    acquire_code = getattr(stdio._InputDescriptor, "duplicate", stdio._InputDescriptor.__init__).__code__
    codes.add(acquire_code)

    def duplicate(fd, target, **kwargs):
        if completed_lines:
            # The slot must already have its closing owner when the OS replaces it.
            assert owner is not None and owner.file is not None
            assert owner.file.fileno() == target and not owner.claimed
        duplicates.append(target)
        copied = real_dup2(fd, target, **kwargs)
        return copied

    close = _observe_owner_close(stdio._InputDescriptor.close, duplicates, closed)
    codes.add(duplicate.__code__)
    acquisition_codes = {acquire_code, duplicate.__code__}

    def opened(*args, **kwargs):
        slot = real_input_file(*args, **kwargs)
        placeholders.append(slot)
        return slot

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
            nonlocal injected, owner, acquisition_completed, validation_started
            if validation_lines and frame.f_code is stdio._lines.__wrapped__.__code__ and event == "call":
                validation_started = True
            if completed_lines:
                if frame.f_code is acquire_code and event == "call":
                    owner = frame.f_locals["self"]
                # This is the first line after the real C call, before the wrapper returns.
                if frame.f_code is duplicate.__code__ and "copied" in frame.f_locals:
                    acquisition_completed = True
            if frame.f_code in codes:
                frame.f_trace_opcodes = opcodes and frame.f_code in acquisition_codes
            active = validation_started if validation_lines else (
                acquisition_completed if completed_lines else bool(duplicates))
            if frame.f_code in codes and active and (
                event == "line"
                or (opcodes and frame.f_code in acquisition_codes and event in ("opcode", "return"))
                or (completed_lines and frame.f_code in acquisition_codes and event == "return")
            ):
                if completed_lines:
                    assert owner.file is not None and owner.file.fileno() == duplicates[0]
                    assert not owner.claimed, "reader claimed before the setup interruption check"
                location = frame.f_lasti if event == "opcode" else frame.f_lineno
                name = frame.f_code.co_qualname if completed_lines else frame.f_code.co_name
                key = (name, event, location)
                occurrences[key] += 1
                point = (*key, occurrences[key])
                points.append(point)
                if point == target:
                    injected = True
                    raise error_type("injected setup line interruption")
            return trace

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(stdio.os, "dup2", duplicate)
            patch.setattr(stdio._InputDescriptor, "close", close)
            patch.setattr(stdio, "_read_lines", delayed_reader)
            patch.setattr(stdio.threading.Thread, "start", start)
            if validation_lines:
                patch.setattr(stdio, "_InputFile", opened)

            async def enter():
                sys.settrace(trace)
                try:
                    async with stdio._lines(anyio.wrap_file(source)):
                        # The inventory ends at the yield: do not inventory teardown.
                        sys.settrace(original_trace)
                        if (completed_lines or validation_lines) and target is not None:
                            pytest.fail("interrupted startup yielded a stream")
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
                    if validation_lines:
                        assert all(slot.closed for slot in placeholders), "validation left a placeholder open"
                    if duplicates:
                        with pytest.raises(OSError):
                            os.fstat(duplicates[0])
            finally:
                release.set()
                with anyio.fail_after(5):
                    while any(thread.is_alive() for thread in threads):
                        await anyio.sleep(0.001)
                for thread in threads:
                    thread.join()

        if validation_lines and target is not None:
            assert len(duplicates) <= 1
        else:
            assert len(duplicates) == 1
        assert closed == duplicates, f"setup event {target} left an unclaimed duplicate or closed it twice"
        if validation_lines:
            assert all(slot.closed for slot in placeholders), "validation left a placeholder open"
        if duplicates:
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
        for slot in placeholders:
            slot.close()
        source.close()


def pytest_generate_tests(metafunc):
    if "setup_event" in metafunc.fixturenames:
        # Trace discovery, rather than a hand-picked line list, includes multiline start calls
        # and any constructor lines after acquisition. Each occurrence gets its own pytest case.
        points = _setup_probe()
        assert points, "no post-duplication setup lines were inventoried"
        metafunc.parametrize("setup_event", points,
                             ids=[f"{name}-{event}-{location}-{nth}" for name, event, location, nth in points])
    if "acquire_event" in metafunc.fixturenames:
        points = _setup_probe(opcodes=True)
        assert any(name == "duplicate" and event == "return" for name, event, _, _ in points)
        metafunc.parametrize("acquire_event", points,
                             ids=[f"{name}-{event}-{location}-{nth}" for name, event, location, nth in points])


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
def test_every_post_duplication_line_releases_unclaimed_descriptor(setup_event, error_kind):
    _setup_probe(setup_event, error_kind)


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
def test_every_acquisition_opcode_and_return_releases_unclaimed_descriptor(acquire_event, error_kind):
    _setup_probe(acquire_event, error_kind, opcodes=True)


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
def test_every_native_duplication_line_and_helper_return_is_owned(error_kind):
    # Removing setup's close or moving ownership after dup2 must fail before the reader can hide a leak.
    points = _setup_probe(completed_lines=True)
    assert points, "no events after native duplication were inventoried"
    assert any(name == "_InputDescriptor.duplicate" and event == "return"
               for name, event, _, _ in points), "the acquisition helper return was not reached"
    assert any(name.endswith(".<locals>.duplicate") and event == "line"
               for name, event, _, _ in points), "the first line after native dup2 was not reached"
    assert any(name.endswith(".<locals>.duplicate") and event == "return"
               for name, event, _, _ in points), "the native-call wrapper return was not reached"
    assert any(name == "_lines" and event == "line"
               for name, event, _, _ in points), "setup did not resume after acquisition"
    for point in points:
        _setup_probe(point, error_kind, completed_lines=True)


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
def test_every_validation_and_placeholder_line_releases_unclaimed_descriptor(error_kind):
    # Start at _lines entry, before fileno validation, owner construction or placeholder allocation.
    points = _setup_probe(validation_lines=True)
    assert points, "no validation/setup lines were inventoried"
    assert points[0][0] == "_lines", "validation inventory started after stream entry"
    assert any(name == "duplicate" for name, _, _, _ in points), "descriptor validation was not reached"
    for point in points:
        _setup_probe(point, error_kind, validation_lines=True)


@pytest.mark.parametrize("exit_kind", ["normal", "cancelled"])
def test_reader_closes_once_and_teardown_preserves_reused_fd(monkeypatch, exit_kind):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "r", encoding="utf-8")
    os.close(write_fd)
    duplicates, closed, threads = [], [], []
    reused = None
    real_dup2, real_close = os.dup2, os.close
    real_start = threading.Thread.start
    caller_thread = threading.get_ident()

    def duplicate(fd, target, **kwargs):
        duplicates.append(target)
        return real_dup2(fd, target, **kwargs)

    close = _observe_owner_close(stdio._InputDescriptor.close, duplicates, closed, thread_ids=True)

    def start(thread):
        real_start(thread)
        threads.append(thread)

    async def run():
        nonlocal reused
        with monkeypatch.context() as patch:
            patch.setattr(stdio.os, "dup2", duplicate)
            patch.setattr(stdio._InputDescriptor, "close", close)
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
                        real_dup2(replacement, duplicates[0])
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
