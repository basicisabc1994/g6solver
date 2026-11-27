# SPDX-License-Identifier: Apache-2.0
"""An unsuccessful copy leaves either our placeholder or somebody else's descriptor."""
import io
import os
from types import SimpleNamespace

import pytest

from g6.mcp import stdio


# test_duplicate_failure_disposes_only_the_owned_placeholder (5 cases) was superseded by decd7a04's placeholder
# redesign (lead ruling, decd7a04): one exclusive native-lifetime placeholder and a uniform allocation fallback.


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


# [zero_identity] was superseded by decd7a04's redesign (lead ruling, decd7a04): an unusable identity now takes the
# uniform fallback. [directory] is superseded with it (rc-030 fix round, lead ruling R2): the placeholder makes no
# directory any more, and an unavailable placeholder falls back to the null device without refusing stdin
# (test_stdin_placeholder_lifecycle's test_unavailable_unique_placeholder_falls_back_without_refusing_stdin); the copy
# is still checked non-inheritable, so a child gets no caller stdin either way.
@pytest.mark.parametrize("failure", ["file"])
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
