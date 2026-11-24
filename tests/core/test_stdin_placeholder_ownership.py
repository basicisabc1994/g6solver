# SPDX-License-Identifier: Apache-2.0
"""An unsuccessful copy leaves either our placeholder or somebody else's descriptor."""
import errno
import gc
import io
import os
from contextlib import suppress
from types import SimpleNamespace

import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("outcome", ["untouched", "closed", "reused_null", "reused_file", "replaced"])
def test_duplicate_failure_disposes_only_the_owned_placeholder(monkeypatch, tmp_path, outcome):
    other_path = tmp_path / "other"
    other_path.write_bytes(b"other owner")
    source = io.FileIO(os.devnull, "rb")
    owner = stdio._InputDescriptor()
    targets, slots, closes, bystanders = [], [], [], []
    real_dup2, real_file = os.dup2, stdio._InputFile

    class TrackedFile(real_file):
        def close(self):
            if not self.closed:
                closes.append(self.fileno())
            super().close()

    def opened(*args, **kwargs):
        slot = TrackedFile(*args, **kwargs)
        slots.append(slot)
        return slot

    def failure(fd, target, **kwargs):
        targets.append(target)
        if outcome == "untouched":
            raise OSError(errno.EBADF, "borrowed source disappeared before the copy")
        real_dup2(fd, target, **kwargs)
        if outcome != "replaced":
            os.close(target)
        if outcome.startswith("reused"):
            other = os.open(os.devnull if outcome == "reused_null" else other_path, os.O_RDONLY)
            if other != target:
                try:
                    real_dup2(other, target)
                finally:
                    os.close(other)
            bystanders.append(target)
        elif outcome == "replaced":
            # No native close: the failed operation already replaced the placeholder. Ownership is uncertain.
            bystanders.append(target)
        raise OSError(errno.EIO, "copy could not establish inheritance")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio, "_InputFile", opened)
            patch.setattr(stdio.os, "dup2", failure)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                owner.duplicate(source.fileno())
            owner.close()
            owner.close()
        gc.collect()
        assert len(targets) == len(slots) == 1
        assert closes == (targets if outcome == "untouched" else [])
        assert slots[0].closed
        if bystanders:
            os.fstat(bystanders[0])
        else:
            with pytest.raises(OSError):
                os.fstat(targets[0])
        os.fstat(source.fileno())
        assert not os.path.exists(os.path.dirname(slots[0].name)), "placeholder directory survived cleanup"
    finally:
        owner.close()
        source.close()
        for fd in bystanders:
            with suppress(OSError):
                os.close(fd)


@pytest.mark.parametrize("failure", ["identity", "inheritance", "inheritable", "unknown"])
def test_placeholder_probe_refusals_close_the_owned_file_once(monkeypatch, failure):
    source = io.FileIO(os.devnull, "rb")
    owner = stdio._InputDescriptor()
    real_stat, real_file = os.stat, stdio._InputFile
    slots, closes = [], []

    class TrackedFile(real_file):
        def close(self):
            if not self.closed:
                closes.append(self.fileno())
            super().close()

    def opened(*args, **kwargs):
        slot = TrackedFile(*args, **kwargs)
        slots.append(slot)
        return slot

    def stat(path, *args, **kwargs):
        if failure == "identity" and isinstance(path, int) and slots and not slots[0].closed and path == slots[0].fileno():
            raise OSError("cannot inspect the placeholder")
        return real_stat(path, *args, **kwargs)

    def inheritance(fd):
        if failure == "inheritance":
            raise OSError("cannot query inheritance")
        return True if failure == "inheritable" else None

    try:
        with monkeypatch.context() as patch:
            patch.setattr(stdio, "_InputFile", opened)
            patch.setattr(stdio.os, "stat", stat)
            patch.setattr(stdio.os, "get_inheritable", inheritance)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                owner.duplicate(source.fileno())
            number = slots[0].fileno()
            owner.close()
            owner.close()
        assert closes == [number]
        with pytest.raises(OSError):
            os.fstat(number)
        os.fstat(source.fileno())
    finally:
        owner.close()
        source.close()


@pytest.mark.parametrize("failure", ["directory", "file", "zero_identity"])
def test_placeholder_allocation_refusals_leave_no_owned_resource(monkeypatch, failure):
    owner = stdio._InputDescriptor()
    directories, slots, closes = [], [], []
    real_directory, real_file, real_stat = stdio.TemporaryDirectory, stdio._InputFile, os.stat

    class TrackedFile(real_file):
        def close(self):
            if not self.closed:
                closes.append(self.fileno())
            super().close()

    def directory(*args, **kwargs):
        if failure == "directory":
            raise OSError("temporary filesystem unavailable")
        result = real_directory(*args, **kwargs)
        directories.append(result.name)
        return result

    def opened(*args, **kwargs):
        if failure == "file":
            raise OSError("placeholder allocation unavailable")
        result = TrackedFile(*args, **kwargs)
        slots.append(result)
        return result

    def stat(path, *args, **kwargs):
        if isinstance(path, int) and slots and not slots[0].closed and path == slots[0].fileno():
            return SimpleNamespace(st_ino=0)
        return real_stat(path, *args, **kwargs)

    with io.FileIO(os.devnull, "rb") as source:
        try:
            with monkeypatch.context() as patch:
                patch.setattr(stdio, "TemporaryDirectory", directory)
                patch.setattr(stdio, "_InputFile", opened)
                patch.setattr(stdio.os, "stat", stat)
                with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                    owner.duplicate(source.fileno())
                numbers = [slot.fileno() for slot in slots]
                owner.close()
                owner.close()
            assert closes == numbers
            assert all(slot.closed for slot in slots)
            assert all(not os.path.exists(path) for path in directories)
            for number in numbers:
                with pytest.raises(OSError):
                    os.fstat(number)
            os.fstat(source.fileno())
        finally:
            owner.close()
