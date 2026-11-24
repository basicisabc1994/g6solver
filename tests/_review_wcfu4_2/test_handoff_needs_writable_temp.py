"""Reading stdin must not depend on a writable temporary directory.

d1053b2c makes the dup2 target a file in a new temporary directory. Where no temporary directory can be created
(TEMP names a directory that is gone or not writable, a read-only container), a healthy, open stdin is refused with
[G6_E_STDIN_HANDOFF] and the server never starts. At beeb6c4d the target was the null device and the same stdin
handed off. tests/core/test_stdin_placeholder_ownership.py pins the refusal ("directory"); this test states what the
person loses by it.
"""
import io
import os
import tempfile

import anyio
import pytest

from g6.mcp import stdio

REQUEST = b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n'


@pytest.mark.parametrize("temp", ["usable", "missing"], ids=["control_usable_temp", "no_usable_temp"])
def test_an_open_stdin_hands_off_without_a_usable_temporary_directory(tmp_path, monkeypatch, temp):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path if temp == "usable" else tmp_path / "gone"))
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")
    try:
        os.write(write_fd, REQUEST)
        os.close(write_fd)

        async def run():
            with anyio.fail_after(5):
                async with stdio._lines(anyio.wrap_file(source)) as lines:
                    return [line async for line in lines]

        assert anyio.run(run) == [REQUEST.decode()]
    finally:
        source.close()
