# SPDX-License-Identifier: Apache-2.0
"""The input-file factory's cleanup contract is fileno/close, including after a close fault."""
import io
import os

import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("reader", [False, True], ids=["startup", "reader"])
@pytest.mark.parametrize("error_type", [None, OSError, KeyboardInterrupt], ids=["normal", "oserror", "interrupt"])
@pytest.mark.parametrize("factory_result", ["fileio", "protocol"])
def test_file_factory_cleanup_needs_only_fileno_and_close(
    tmp_path, monkeypatch, caplog, reader, error_type, factory_result,
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    owner = stdio._InputDescriptor()
    slots, closes = [], []

    def opened(*args, **kwargs):
        slot = io.FileIO(*args, **kwargs)
        native_close = slot.close
        slots.append(slot)

        def close():
            closes.append(True)
            native_close()
            if error_type is not None:
                raise error_type("file close consumed its descriptor before reporting a fault")

        if factory_result == "fileio":
            slot.close = close
            return slot

        class FileProtocol:
            def fileno(self):
                return slot.fileno()

            def close(self):
                close()

        return FileProtocol()

    monkeypatch.setattr(stdio, "_InputFile", opened)
    path = tmp_path / "wire"
    path.write_bytes(b"request\n")
    with io.FileIO(path, "rb") as source:
        try:
            owner.duplicate(source.fileno())
            number = owner.take() if reader else owner.file.fileno()
            assert os.read(number, 65536) == b"request\n"
            owner.close(reader=reader)
            assert slots[0].closed
            assert owner.file is None
            assert owner.take() is None
            with pytest.raises(OSError):
                os.fstat(number)
            with io.FileIO(path, "rb") as bystander:
                owner.close(reader=reader)
                owner.close(reader=not reader)
                assert bystander.read() == b"request\n"
            assert closes == [True], "cleanup retried the factory result's closing claim"
            os.fstat(source.fileno())
            assert list(tmp_path.glob("g6-stdin-*")) == []
            if error_type is not None:
                assert "[G6_E_STDIN_HANDOFF]" in caplog.text
        finally:
            owner.close(reader=reader)
            for slot in slots:
                # The test owns these objects; bypass its injected close fault during cleanup.
                io.FileIO.close(slot)
