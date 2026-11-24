"""An untouched text layer must hand off whatever its newline setting is.

c578ee4f finds "decoded chunks" as every non-empty str among gc.get_referents(source) except the encoding and errors
objects. CPython's TextIOWrapper also visits its readnl string, which is set whenever newline is "\\n", "\\r" or
"\\r\\n". CPython creates sys.stdin with newline="\\n" on every platform but Windows (Python/pylifecycle.c,
create_stdio), so on Linux and macOS the list is never empty: a stdin nobody has read is treated as holding decoded
text. With a lossy error handler (PYTHONIOENCODING=utf-8:replace) or a codec on the opaque list (a ja_JP.eucJP,
ko_KR.eucKR or zh_CN.GB18030 locale) the server refuses to start with [G6_E_STDIN_HANDOFF] before reading a byte.
The branch's own tests state that untouched input hands off for every codec and for lossy handlers
(tests/core/test_stdin_codec_matrix.py, tests/core/test_stdin_text_encoding.py); they build the layer with
newline=None, the Windows shape.
"""
import io
import os

import anyio
import pytest

from g6.mcp import stdio

REQUEST = b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n'


def _delivered(encoding, errors, newline):
    read_fd, write_fd = os.pipe()
    # The stack CPython builds for sys.stdin; newline="\n" is its POSIX value, None its Windows value.
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding, errors=errors,
                              newline=newline)
    try:
        os.write(write_fd, REQUEST)
        os.close(write_fd)

        async def run():
            with anyio.fail_after(5):
                async with stdio._lines(anyio.wrap_file(source)) as lines:
                    return [line async for line in lines]

        return anyio.run(run)
    finally:
        source.close()


@pytest.mark.parametrize("encoding,errors", [
    ("utf-8", "strict"), ("utf-8", "replace"), ("utf-8", "ignore"), ("utf-8", "backslashreplace"),
    ("euc_jp", "strict"), ("euc_kr", "strict"), ("gb18030", "strict"),
])
@pytest.mark.parametrize("newline", [None, "\n"], ids=["control_windows_newline_none", "posix_newline_lf"])
def test_an_untouched_stdin_hands_off_with_the_newline_setting_of_each_platform(encoding, errors, newline):
    assert _delivered(encoding, errors, newline) == [REQUEST.decode()]
