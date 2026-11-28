# SPDX-License-Identifier: Apache-2.0
"""Placeholder lifetime and cleanup faults must not control stdin availability or EOF."""
import io
import os
import threading
from types import SimpleNamespace

import anyio
import pytest

from g6.mcp import stdio


def test_successful_copy_removes_the_placeholder_before_the_reader_starts(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    owner = stdio._InputDescriptor()
    with io.FileIO(os.devnull, "rb") as source:
        try:
            owner.duplicate(source.fileno())
            assert list(tmp_path.iterdir()) == []
            assert owner.placeholder is None
            assert not owner.file.closed
            assert os.get_inheritable(owner.file.fileno()) is False
        finally:
            owner.close()


@pytest.mark.parametrize("allocation", ["directory", "slot"])
def test_unavailable_unique_placeholder_falls_back_without_refusing_stdin(tmp_path, monkeypatch, allocation):
    owner = stdio._InputDescriptor()
    real_file = stdio._InputFile
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def no_directory(*args, **kwargs):
        raise OSError("temporary directory unavailable")

    def opened(path, *args, **kwargs):
        if path != os.devnull:
            raise OSError("unique slot unavailable")
        return real_file(path, *args, **kwargs)

    if allocation == "directory":
        monkeypatch.setattr(stdio, "TemporaryDirectory", no_directory)
    else:
        monkeypatch.setattr(stdio, "_InputFile", opened)
    path = tmp_path / "wire"
    path.write_bytes(b"remaining wire bytes\n")
    with io.FileIO(path, "rb") as source:
        try:
            owner.duplicate(source.fileno())
            assert os.read(owner.take(), 65536) == b"remaining wire bytes\n"
            assert sorted(item.name for item in tmp_path.iterdir()) == ["wire"]
        finally:
            owner.close(reader=True)
            owner.close()


# [closed] and [replaced] were superseded by decd7a04's redesign (lead ruling, decd7a04): a failed copy retires its
# claim without a null identity decision.
@pytest.mark.parametrize("outcome", ["untouched"])
def test_fallback_copy_failure_compares_the_null_placeholder_identity(tmp_path, monkeypatch, outcome):
    owner = stdio._InputDescriptor()
    real_dup2 = os.dup2
    real_file = stdio._InputFile
    targets = []
    replacements = set()
    path = tmp_path / "source"
    path.write_bytes(b"source")

    class TrackedFile(real_file):
        def close(self):
            if not self.closed:
                # Even a defective G6 close consumes the test's closing claim. Never retry its number.
                replacements.discard(self.fileno())
            super().close()

    def no_directory(*args, **kwargs):
        raise OSError("temporary directory unavailable")

    def duplicate(fd, target, **kwargs):
        assert fd == source.fileno()
        assert target == owner.file.fileno()
        targets.append(target)
        if outcome != "untouched":
            real_dup2(fd, target, **kwargs)
        if outcome == "closed":
            os.close(target)
        elif outcome == "replaced":
            replacements.add(target)
        raise OSError("injected copy failure")

    monkeypatch.setattr(stdio, "TemporaryDirectory", no_directory)
    monkeypatch.setattr(stdio, "_InputFile", TrackedFile)
    # stdio.os is the shared os module: patching its dup2 would also intercept pytest capture teardown.
    monkeypatch.setattr(stdio, "os", SimpleNamespace(**(vars(os) | {"dup2": duplicate})))
    with io.FileIO(path, "rb") as source:
        try:
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                owner.duplicate(source.fileno())
            slot = owner.file
            owner.close()
            assert slot.closed
            with pytest.raises(ValueError):
                slot.fileno()
            assert len(targets) == 1
            if outcome == "replaced":
                os.fstat(targets[0])
            else:
                with pytest.raises(OSError):
                    os.fstat(targets[0])
            os.fstat(source.fileno())
        finally:
            owner.close()
            while replacements:
                os.close(replacements.pop())


@pytest.mark.parametrize("reader", [False, True], ids=["startup", "reader"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_close_fault_forgets_the_descriptor_before_its_number_can_be_reused(
    tmp_path, monkeypatch, caplog, reader, error_type,
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    owner = stdio._InputDescriptor()
    path = tmp_path / "bystander"
    path.write_bytes(b"another owner\n")
    calls = []
    with io.FileIO(os.devnull, "rb") as source:
        try:
            owner.duplicate(source.fileno())
            slot = owner.file
            if reader:
                owner.take()
            native_close = slot.close

            def consumed_then_failed():
                native_close()  # FileIO forgets its number even if the injected outer cleanup fails.
                calls.append(True)
                raise error_type("close consumed the descriptor before failing")

            monkeypatch.setattr(slot, "close", consumed_then_failed)
            owner.close(reader=reader)
            with io.FileIO(path, "rb") as bystander:
                assert owner.file is None, "a failed close retained a closing claim"
                assert owner.take() is None, "a closed owner published a descriptor again"
                owner.close(reader=reader)
                owner.close(reader=not reader)
                assert calls == [True], "a second close retried a consumed descriptor"
                assert bystander.read() == b"another owner\n"
            os.fstat(source.fileno())
            assert "[G6_E_STDIN_HANDOFF]" in caplog.text
        finally:
            owner.close(reader=reader)


# test_placeholder_cleanup_fault_is_reported_and_retried_after_copy was superseded by decd7a04's placeholder redesign
# (lead ruling, decd7a04): the placeholder's native lifetime leaves no cleanup to retry after the copy.


def test_reader_closes_the_line_stream_even_if_descriptor_cleanup_raises(monkeypatch, caplog):
    closed = []

    def cleanup(*args, **kwargs):
        raise OSError("injected descriptor close failure")

    def run_sync(function, *, token):
        function()

    monkeypatch.setattr(stdio.anyio.from_thread, "run_sync", run_sync)
    descriptor = SimpleNamespace(take=lambda: None, close=cleanup)
    lines = SimpleNamespace(close=lambda: closed.append(True))
    stdio._read_lines(descriptor, lines, object(), threading.Event(), lambda: b"")
    assert closed == [True]
    assert "[G6_E_STDIN_HANDOFF]" in caplog.text


@pytest.mark.parametrize("fault", ["file", "placeholder"])
def test_cleanup_double_fault_keeps_the_stable_startup_refusal(monkeypatch, caplog, fault):
    real_duplicate = stdio._InputDescriptor.duplicate
    real_file_close = stdio._InputFile.close
    owners = []

    def cleanup():
        raise OSError("injected cleanup failure")

    def duplicate(owner, fd):
        owners.append(owner)
        real_duplicate(owner, fd)
        if fault == "file":
            slot = owner.file

            def file_cleanup():
                real_file_close(slot)
                cleanup()

            monkeypatch.setattr(slot, "close", file_cleanup)
        else:
            owner.placeholder = SimpleNamespace(cleanup=cleanup)
        raise OSError("[G6_E_STDIN_HANDOFF] original startup refusal")

    monkeypatch.setattr(stdio._InputDescriptor, "duplicate", duplicate)

    async def run(source):
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\] original startup refusal"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("failed setup yielded a stream")

    with io.FileIO(os.devnull, "rb") as source:
        try:
            anyio.run(run, source)
            assert "cleanup" in caplog.text
            os.fstat(source.fileno())
        finally:
            for owner in owners:
                if owner.file is not None:
                    real_file_close(owner.file)
                owner.placeholder = None
