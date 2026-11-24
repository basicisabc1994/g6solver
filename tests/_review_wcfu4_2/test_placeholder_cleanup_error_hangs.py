"""End of input must end the session even when the placeholder file cannot be deleted.

d1053b2c keeps the placeholder's temporary directory for the whole session and removes it in
_InputDescriptor.close(). The reader thread calls that close() first in its finally block, ahead of lines.close.
When the removal raises (on Windows: any other handle on <temp>/g6-stdin-*/slot, such as a scanner, an indexer or a
backup tool), the exception leaves the finally block before lines.close runs: the SDK parser never sees the end of
input, so a client that closed stdin leaves a server that never ends.
At beeb6c4d the placeholder was the null device: nothing on disk, nothing to fail.
"""
import glob
import io
import os
import tempfile

import anyio
import pytest
from mcp.shared.message import SessionMessage

from g6.mcp import stdio


class Output:
    async def write(self, data):
        return len(data)

    async def flush(self):
        pass


@pytest.mark.skipif(os.name != "nt", reason="an open handle blocks deletion on Windows only")
@pytest.mark.parametrize("held", [False, True], ids=["control_nobody_holds_it", "another_handle_holds_it"])
def test_end_of_input_ends_the_session_when_the_placeholder_cannot_be_deleted(tmp_path, monkeypatch, held):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    read_fd, write_fd = os.pipe()
    source = io.FileIO(read_fd, "rb")
    holders = []

    async def run():
        async with stdio.stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as (incoming, outgoing):
            if held:
                # What a scanner or indexer does: open the new file in G6's temporary directory and keep it.
                for slot in glob.glob(os.path.join(str(tmp_path), "g6-stdin-*", "slot")):
                    holders.append(open(slot, "rb"))
            os.write(write_fd, b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
            os.close(write_fd)                       # the client closes stdin: input ends here
            got = []
            with anyio.move_on_after(5) as scope:
                async for item in incoming:
                    got.append(item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__)
            await outgoing.aclose()
            return got, scope.cancelled_caught

    try:
        got, never_ended = anyio.run(run)
        assert got == [7]
        assert not never_ended, "the client closed stdin, but the session's input never ended"
    finally:
        for holder in holders:
            holder.close()
        source.close()
