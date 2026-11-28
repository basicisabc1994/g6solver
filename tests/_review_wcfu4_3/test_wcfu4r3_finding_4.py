"""A refused handoff must leave the caller the text its stdin had already decoded.

9f1f1360 says it keeps "lossy and opaque codec refusals before consuming caller text", and
tests/core/test_stdin_newline_matrix.py::test_opaque_decoded_cache_refusal_keeps_the_callers_text pins that for four
codecs. The rule holds only for refusals decided before _stdin_cache.py:157 takes the decoded characters out of the
wrapper one by one. Every refusal decided after that point has already emptied the caller's text layer, and nothing
puts the characters back:
* a pending character with two byte spellings in a codec that is not on the opaque list (cp932: U+2252 is 81 E0 and
  87 90), refused at _stdin_cache.py:165/180;
* a decoder whose state the re-encoding cannot match (utf-16 and utf-32 input that began with a big-endian mark on
  a little-endian machine), refused at _stdin_cache.py:174-176;
* any descriptor refusal, because stdio.py:286 runs the text recovery before stdio.py:307 duplicates the descriptor.

No descriptor is duplicated here: the first two cases stop in cache_reader, the third replaces duplicate() with the
refusal itself, as tests/core/test_stdin_placeholder_lifecycle.py does.
"""
import codecs
import io
import os
import sys

import anyio
import pytest

from g6.mcp import stdio
from g6.mcp._stdin_cache import cache_reader

OWNED = "≒ still owned by the caller\n"


def _launcher_stdin(encoding, wire):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, wire)
    os.close(write_fd)
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding=encoding)
    assert source.read(len("launcher\n")) == "launcher\n"
    return source


CASES = {
    "control_euc_jp_opaque": ("euc_jp", "launcher\n".encode("euc_jp") + OWNED.encode("euc_jp")),
    "cp932_second_spelling": ("cp932", b"launcher\n\x87\x90" + OWNED[1:].encode("cp932")),
    "utf16_big_endian_mark": ("utf-16", codecs.BOM_UTF16_BE + ("launcher\n" + OWNED).encode("utf-16-be")),
    "utf32_big_endian_mark": ("utf-32", codecs.BOM_UTF32_BE + ("launcher\n" + OWNED).encode("utf-32-be")),
}


@pytest.mark.skipif(sys.byteorder != "little", reason="the big-endian cases are the unnatural order here")
@pytest.mark.parametrize("case", list(CASES))
def test_a_codec_refusal_keeps_the_callers_decoded_text(case):
    encoding, wire = CASES[case]
    source = _launcher_stdin(encoding, wire)
    try:
        with pytest.raises(ValueError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            cache_reader(source)
        assert source.read() == OWNED, "the refusal emptied the caller's text layer"
    finally:
        source.close()


def test_control_cp932_text_with_one_spelling_hands_off():
    wire = "launcher\n日本 still owned\n".encode("cp932")
    source = _launcher_stdin("cp932", wire)
    try:
        cached, _ = cache_reader(source)
        assert cached() == "日本 still owned\n".encode("cp932")
    finally:
        source.close()


@pytest.mark.parametrize("pending", [False, True], ids=["control_nothing_pending", "decoded_text_pending"])
def test_a_descriptor_refusal_keeps_the_callers_decoded_text(monkeypatch, pending):
    owned = "still owned by the caller\n"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"launcher\n" + owned.encode("cp1252"))
    os.close(write_fd)
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="cp1252")
    if pending:
        assert source.read(len("launcher\n")) == "launcher\n"   # the rest of the chunk is decoded and waiting

    def refuse(owner, fd):
        raise OSError("[G6_E_STDIN_HANDOFF] stdin descriptor handoff refused")

    monkeypatch.setattr(stdio._InputDescriptor, "duplicate", refuse)

    async def run():
        with pytest.raises(OSError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            async with stdio._lines(anyio.wrap_file(source)):
                pytest.fail("a refused handoff yielded input")

    try:
        anyio.run(run)
        expected = owned if pending else "launcher\n" + owned
        assert source.read() == expected, "the refusal emptied the caller's text layer"
    finally:
        source.close()
