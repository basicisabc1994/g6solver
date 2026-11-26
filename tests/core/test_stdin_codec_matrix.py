# SPDX-License-Identifier: Apache-2.0
"""All installed standard-library text codecs, through both native stdin stacks."""
import codecs
import encodings
import encodings.aliases
import io
import os
import pkgutil

import pytest

from g6.mcp._stdin_cache import cache_reader


# These decoders lack a byte inverse or a safely inspectable pending cursor on non-seekable wrappers.
# The same refusal policy applies to both transports; all other consumed-line cases must hand off.
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
            continue  # platform-only codecs are enumerated on their own platform
        if codec._is_text_encoding and codec.incrementaldecoder is not None:
            result.add(codec.name)
    return sorted(result)


def _source(tmp_path, transport, payload, encoding, errors="strict"):
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
        return io.TextIOWrapper(io.BufferedReader(raw), encoding=encoding, errors=errors)
    except BaseException:
        raw.close()
        raise


def _handoff(source):
    cached, _ = cache_reader(source)
    chunks = [cached()]
    while chunk := os.read(source.fileno(), 65536):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize("encoding", _text_codecs())
@pytest.mark.parametrize("state", ["untouched", "consumed_line", "partly_consumed"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_every_text_codec_preserves_remaining_bytes_or_refuses_with_the_code(tmp_path, encoding, state, transport):
    if state == "untouched":
        payload = b'bad\xff\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'
        expected = payload  # even a codec that cannot encode a launcher line must support untouched input
    else:
        text = "launcher\n" + ("remaining\n" if state == "partly_consumed" else "")
        try:
            payload = text.encode(encoding)
            prefix = "launcher\n".encode(encoding)
        except (UnicodeError, LookupError) as exc:
            pytest.skip(f"codec cannot represent a launcher line: {exc}")
        expected = payload[len(prefix):] if payload.startswith(prefix) else None
    with _source(tmp_path, transport, payload, encoding) as source:
        if state != "untouched":
            assert source.readline() == "launcher\n"
        try:
            got = _handoff(source)
        except (ValueError, OSError) as exc:
            assert "[G6_E_STDIN_HANDOFF]" in str(exc)
            assert state != "untouched", f"untouched text cache refused for {encoding}: {exc}"
            if state == "consumed_line":
                assert encoding.replace("_", "-") in _OPAQUE_INVERSES, f"empty cache refused for {encoding}: {exc}"
        else:
            assert expected is not None, "non-prefix-preserving codec needs an explicit refusal"
            assert got == expected, f"lost, duplicated or reordered cached bytes for {encoding}"


@pytest.mark.parametrize("encoding", ["cp1250", "cp1251", "cp932", "cp1252", "iso8859-2", "koi8-r"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_pending_reversible_text_in_other_code_pages_is_returned(tmp_path, encoding, transport):
    # ASCII is representable in all these encodings; the launcher leaves part of its decoded chunk pending.
    payload = b"launcher\nremaining\n"
    with _source(tmp_path, transport, payload, encoding) as source:
        assert source.readline() == "launcher\n"
        assert _handoff(source) == b"remaining\n"


@pytest.mark.parametrize("encoding,char", [("cp1250", "\u010d"), ("cp1251", "\u0416"), ("cp932", "\u3042"),
                                          ("utf-16", "\u00e9"), ("utf-32", "\u00e9"), ("utf-8-sig", "\u00e9")])
@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_consumed_non_ascii_text_does_not_encode_fresh_wire_bytes(tmp_path, encoding, char, transport):
    payload = ("launcher " + char + "\n").encode(encoding)
    with _source(tmp_path, transport, payload, encoding) as source:
        assert source.readline() == "launcher " + char + "\n"
        assert _handoff(source) == b""


@pytest.mark.parametrize("encoding", ["cp1250", "cp1251", "cp932"])
@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_unread_binary_prefetch_is_independent_of_the_text_codec(tmp_path, encoding, transport):
    payload = b'bad\xff\n{"jsonrpc":"2.0","id":7,"method":"p\xc3\xa9ng"}\n'
    with _source(tmp_path, transport, payload, encoding) as source:
        source.buffer.peek(1)
        assert _handoff(source) == payload


@pytest.mark.parametrize("transport", ["pipe", "file"])
@pytest.mark.parametrize("encoding,payload", [("cp932", b"\x87\x90"), ("utf-7", b"+AGE-")])
def test_noncanonical_byte_spellings_are_refused_instead_of_normalized(tmp_path, transport, encoding, payload):
    with _source(tmp_path, transport, b"launcher\n" + payload, encoding) as source:
        assert source.readline() == "launcher\n"
        with pytest.raises((ValueError, OSError), match=r"\[G6_E_STDIN_HANDOFF\]"):
            _handoff(source)


@pytest.mark.parametrize("transport", ["pipe", "file"])
@pytest.mark.parametrize("encoding,text", [("cp1250", "\u010d"), ("cp1251", "\u0416"), ("cp932", "\u3042")])
def test_pending_non_ascii_code_page_text_keeps_its_original_bytes(tmp_path, transport, encoding, text):
    tail = ("remaining " + text + "\n").encode(encoding)
    with _source(tmp_path, transport, b"launcher\n" + tail, encoding) as source:
        assert source.readline() == "launcher\n"
        assert _handoff(source) == tail


@pytest.mark.parametrize("transport", ["pipe", "file"])
def test_multibyte_decoder_buffer_stays_between_decoded_text_and_fresh_input(tmp_path, transport):
    prefix, tail = b"launcher\nremaining\x82", b"\xa0\n"
    write_fd = None
    if transport == "pipe":
        read_fd, write_fd = os.pipe()
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(read_fd, "rb")), encoding="cp932")
        assert os.write(write_fd, prefix) == len(prefix)
    else:
        path = tmp_path / "split-input"
        path.write_bytes(prefix)
        source = io.TextIOWrapper(io.BufferedReader(io.FileIO(path, "rb")), encoding="cp932")
    try:
        assert source.readline() == "launcher\n"
        if transport == "pipe":
            assert os.write(write_fd, tail) == len(tail)
            os.close(write_fd)
            write_fd = None
        else:
            with path.open("ab") as writer:
                assert writer.write(tail) == len(tail)
        assert _handoff(source) == b"remaining\x82\xa0\n"
    finally:
        source.close()
        if write_fd is not None:
            os.close(write_fd)
