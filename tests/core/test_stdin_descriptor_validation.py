# SPDX-License-Identifier: Apache-2.0
"""Invalid borrowed stdin must be refused before an owned placeholder can replace it."""
import builtins
import io
import os

import anyio
import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_fileno_failure_is_a_refusal_instead_of_descriptorless_eof(error_type):
    class BrokenDescriptor:
        def fileno(self):
            raise error_type("stdin descriptor unavailable")

        def readline(self):
            pytest.fail("invalid stdin fell back to readline")

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(BrokenDescriptor())):
                pytest.fail("invalid stdin yielded a stream")

    anyio.run(run)


def test_closed_descriptorless_stdin_is_refused():
    source = io.StringIO("request\n")
    source.close()

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("closed descriptorless stdin yielded a stream")

    anyio.run(run)


def test_none_fileno_is_invalid_instead_of_descriptorless():
    class InvalidDescriptor:
        def fileno(self):
            return None

        def readline(self):
            pytest.fail("invalid stdin fell back to readline")

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(InvalidDescriptor())):
                pytest.fail("invalid stdin yielded a stream")

    anyio.run(run)


@pytest.mark.parametrize("entry", ["default", "wrapped"])
@pytest.mark.parametrize("closed_object", [False, True])
def test_closed_stdin_refuses_before_allocating_or_yielding(monkeypatch, entry, closed_object):
    source = io.FileIO(os.open(os.devnull, os.O_RDONLY), "rb", closefd=False)
    fd = source.fileno()

    def no_placeholder(*args, **kwargs):
        pytest.fail("closed stdin allocated a placeholder")

    async def run():
        # Close only after the event loop is running, so it cannot reuse our descriptor first.
        os.close(fd)
        if closed_object:
            source.close()
        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            patch.setattr(stdio, "_InputFile", no_placeholder)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(None if entry == "default" else anyio.wrap_file(source)):
                    pytest.fail("closed stdin yielded a stream")

    try:
        anyio.run(run)
    finally:
        source.close()


@pytest.mark.parametrize("fd", [-1, None, "invalid", 2**100])
def test_invalid_descriptor_has_stable_refusal_without_placeholder(monkeypatch, fd):
    owner = stdio._InputDescriptor()

    def no_placeholder(*args, **kwargs):
        pytest.fail("invalid stdin allocated a placeholder")

    with monkeypatch.context() as patch:
        patch.setattr(stdio, "_InputFile", no_placeholder)
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            owner.duplicate(fd)
    assert owner.file is None


def test_same_number_after_validation_closes_placeholder_and_refuses(monkeypatch):
    source = io.FileIO(os.open(os.devnull, os.O_RDONLY), "rb", closefd=False)
    fd = source.fileno()
    placeholders = []
    real_dup2 = os.dup2

    def raced_open(*args, **kwargs):
        # Model the external owner closing stdin between validation and allocation.
        slot = builtins.open(*args, **kwargs)
        try:
            os.close(fd)
            real_dup2(slot.fileno(), fd, inheritable=False)
        finally:
            slot.close()
        replacement = io.FileIO(fd, "rb")
        placeholders.append(replacement)
        return replacement

    def no_duplicate(*args, **kwargs):
        pytest.fail("same-number placeholder was used as stdin")

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio, "_InputFile", raced_open)
            patch.setattr(stdio.os, "dup2", no_duplicate)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(anyio.wrap_file(source)):
                    pytest.fail("same-number placeholder yielded a stream")
        assert len(placeholders) == 1
        assert placeholders[0].closed, "refused placeholder still has an open descriptor"
        with pytest.raises(OSError):
            os.fstat(fd)

    try:
        anyio.run(run)
    finally:
        for slot in placeholders:
            slot.close()
        source.close()


@pytest.mark.parametrize("failure", ["duplicate", "inheritance_error", "inheritable", "unknown"])
def test_uncertain_duplicate_refuses_without_yielding_and_closes_once(monkeypatch, failure):
    with builtins.open(os.devnull, "rb", buffering=0) as source:
        placeholders, closed = [], []
        real_close = stdio._InputDescriptor.close
        real_inheritable = os.get_inheritable
        real_input_file = stdio._InputFile
        real_dup2 = os.dup2

        def opened(*args, **kwargs):
            slot = real_input_file(*args, **kwargs)
            placeholders.append(slot)
            return slot

        def close(owner, *, reader=False):
            if owner.file is not None and owner.claimed == reader:
                closed.append(owner.file)
            real_close(owner, reader=reader)

        def duplicate_error(fd, target, **kwargs):
            # Model the native failure after the copy exists, rather than an error leaving fd2 untouched.
            real_dup2(fd, target, **kwargs)
            os.close(target)
            raise OSError("cannot establish a non-inheritable duplicate")

        def inheritance(fd):
            if placeholders and fd == placeholders[0].fileno():
                if failure == "inheritance_error":
                    raise OSError("inheritance query unavailable")
                return True if failure == "inheritable" else None
            return real_inheritable(fd)

        async def run():
            with monkeypatch.context() as patch:
                patch.setattr(stdio, "_InputFile", opened)
                patch.setattr(stdio._InputDescriptor, "close", close)
                if failure == "duplicate":
                    patch.setattr(stdio.os, "dup2", duplicate_error)
                else:
                    patch.setattr(stdio.os, "get_inheritable", inheritance)
                with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                    async with stdio._lines(anyio.wrap_file(source)):
                        pytest.fail("uncertain stdin duplicate yielded a stream")
            assert len(placeholders) == 1
            assert closed == placeholders, "refused duplicate did not close exactly once"
            assert placeholders[0].closed
            os.fstat(source.fileno())

        try:
            anyio.run(run)
        finally:
            for slot in placeholders:
                slot.close()


def test_source_is_checked_once_before_placeholder_and_duplicate_is_non_inheritable(monkeypatch):
    with builtins.open(os.devnull, "rb", buffering=0) as source:
        owner = stdio._InputDescriptor()
        checked = []
        real_fstat = os.fstat
        real_input_file = stdio._InputFile

        def fstat(fd):
            checked.append(fd)
            return real_fstat(fd)

        def opened(*args, **kwargs):
            assert checked == [source.fileno()], "stdin was not checked before placeholder allocation"
            return real_input_file(*args, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(stdio.os, "fstat", fstat)
                patch.setattr(stdio, "_InputFile", opened)
                owner.duplicate(source.fileno())
            assert checked == [source.fileno()]
            assert owner.file.fileno() != source.fileno()
            assert os.get_inheritable(owner.file.fileno()) is False
        finally:
            owner.close()
        os.fstat(source.fileno())
