# SPDX-License-Identifier: Apache-2.0
"""Every source inspection maps a broken wrapper to the same handoff refusal."""
import io
import os

import anyio
import pytest

from g6.mcp import stdio


@pytest.mark.parametrize("entry", ["default", "wrapped"])
def test_detached_text_source_has_a_stable_refusal(monkeypatch, entry):
    read_fd, write_fd = os.pipe()
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="utf-8")
    buffer = source.detach()

    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(stdio.sys, "stdin", source)
            with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                async with stdio._lines(None if entry == "default" else anyio.wrap_file(source)):
                    pytest.fail("detached source yielded input")

    try:
        anyio.run(run)
    finally:
        buffer.close()
        os.close(write_fd)


@pytest.mark.parametrize("probe", ["closed", "fileno"])
@pytest.mark.parametrize("error", [ValueError, OSError, TypeError, RuntimeError])
def test_source_property_failure_has_a_stable_refusal(probe, error):
    class BrokenSource:
        def __getattribute__(self, name):
            if name == probe:
                raise error("broken source property")
            return super().__getattribute__(name)

        def fileno(self):
            return 0

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(BrokenSource())):
                pytest.fail("broken source yielded input")

    anyio.run(run)
