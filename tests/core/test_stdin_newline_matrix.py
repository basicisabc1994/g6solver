# SPDX-License-Identifier: Apache-2.0
"""Extend the codec matrix across Windows and fixed platform newline configurations."""
import codecs
import encodings
import encodings.aliases
import io
import os
import pkgutil
from contextlib import contextmanager

import pytest

from g6.mcp._stdin_cache import cache_reader


_OPAQUE_INVERSES = frozenset({
    "utf-7", "unicode-escape", "raw-unicode-escape", "punycode", "idna", "hz", "gb18030",
    "euc-jp", "euc-jis-2004", "euc-jisx0213", "euc-kr", "iso2022-jp", "iso2022-jp-1", "iso2022-jp-2",
    "iso2022-jp-2004", "iso2022-jp-3", "iso2022-jp-ext", "iso2022-kr", "mbcs", "oem",
})


def _text_codecs():
    names = set(encodings.aliases.aliases.values())
    names.update(module.name for module in pkgutil.iter_modules(encodings.__path__))
    result = set()
    for name in sorted(names):
        try:
            codec = codecs.lookup(name)
        except LookupError:
            continue
        if codec._is_text_encoding and codec.incrementaldecoder is not None:
            result.add(codec.name)
    return sorted(result)


@contextmanager
def _source(tmp_path, transport, payload, encoding, errors, newline):
    if transport == "pipe":
        read_fd, write_fd = os.pipe()
        try:
            assert os.write(write_fd, payload) == len(payload)
        finally:
            os.close(write_fd)
        raw = io.FileIO(read_fd, "rb")
    else:
        path = tmp_path / "input"
        path.write_bytes(payload)
        raw = io.FileIO(path, "rb")
    try:
        with io.TextIOWrapper(io.BufferedReader(raw), encoding=encoding, errors=errors, newline=newline) as source:
            yield source
    finally:
        raw.close()


def _wire(source):
    cached, _ = cache_reader(source)
    chunks = [cached()]
    while chunk := os.read(source.fileno(), 65536):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize("encoding", _text_codecs())
@pytest.mark.parametrize("state", ["untouched", "consumed_line", "partly_consumed"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
@pytest.mark.parametrize("newline", [None, "\n", "\r\n"], ids=["universal", "lf", "crlf"])
def test_all_text_codecs_with_platform_newlines(tmp_path, encoding, state, transport, newline):
    if state == "untouched":
        payload = b'bad\xff\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'
        expected = payload
    else:
        text = "launcher\n" + ("remaining\n" if state == "partly_consumed" else "")
        try:
            payload, prefix = text.encode(encoding), "launcher\n".encode(encoding)
        except (UnicodeError, LookupError) as exc:
            pytest.skip(f"codec cannot represent a launcher line: {exc}")
        expected = payload[len(prefix):] if payload.startswith(prefix) else None
    with _source(tmp_path, transport, payload, encoding, "strict", newline) as source:
        if state != "untouched":
            # Fixed CRLF does not recognize a lone LF as a line boundary. Read the exact launcher prefix.
            assert source.read(len("launcher\n")) == "launcher\n"
        try:
            got = _wire(source)
        except (ValueError, OSError) as exc:
            assert "[G6_E_STDIN_HANDOFF]" in str(exc)
            assert state != "untouched", f"untouched {encoding}/{newline!r} refused: {exc}"
            if state == "consumed_line":
                assert encoding.replace("_", "-") in _OPAQUE_INVERSES, f"empty {encoding} cache refused: {exc}"
        else:
            assert expected is not None, "non-prefix-preserving codec needs an explicit refusal"
            assert got == expected


@pytest.mark.parametrize("errors", ["replace", "ignore", "backslashreplace"])
@pytest.mark.parametrize("newline", [None, "\n", "\r", "\r\n"], ids=["universal", "lf", "cr", "crlf"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
@pytest.mark.parametrize("warm", [False, True], ids=["untouched", "decoded"])
def test_lossy_newline_settings_preserve_untouched_bytes_and_refuse_decoded_text(
    tmp_path, errors, newline, transport, warm,
):
    tail = b"bad\xff\n"
    payload = (b"launcher\n" if warm else b"") + tail
    with _source(tmp_path, transport, payload, "utf-8", errors, newline) as source:
        if warm:
            assert source.read(len("launcher\n")) == "launcher\n"
            with pytest.raises(ValueError, match=r"\[G6_E_STDIN_HANDOFF\]"):
                cache_reader(source)
            assert source.read() == tail.decode("utf-8", errors)
        else:
            source.buffer.peek(1)
            assert _wire(source) == payload


@pytest.mark.parametrize("encoding", ["utf-7", "euc_jp", "euc_kr", "gb18030"])
@pytest.mark.parametrize("newline", [None, "\n", "\r\n"], ids=["universal", "lf", "crlf"])
def test_opaque_decoded_cache_refusal_keeps_the_callers_text(tmp_path, encoding, newline):
    with _source(tmp_path, "pipe", b"launcher\nstill owned by caller\n", encoding, "strict", newline) as source:
        assert source.read(len("launcher\n")) == "launcher\n"
        with pytest.raises(ValueError, match=r"\[G6_E_STDIN_HANDOFF\]"):
            cache_reader(source)
        assert source.read() == "still owned by caller\n"
