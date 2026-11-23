# SPDX-License-Identifier: Apache-2.0
"""Move a standard input stream's existing caches ahead of a raw descriptor reader.

The caller has exclusive read ownership during this handoff. Temporarily returning EOF from the raw object's
Python read method lets its buffered readers drain without touching the OS or waiting on outside input. All method
attributes are restored before returning. Only the standard IO stack is supported: an opaque wrapper cannot safely
be bypassed merely because it exposes a descriptor.
"""
from __future__ import annotations

import _io
import codecs
import gc
import io
from contextlib import ExitStack, contextmanager


@contextmanager
def _method(obj, name, replacement):
    missing = object()
    original = vars(obj).get(name, missing)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        if original is missing:
            delattr(obj, name)
        else:
            setattr(obj, name, original)


def _binary_cache(buffer):
    if type(buffer) is not io.BufferedReader:
        return b""
    chunks = []
    while chunk := buffer.read1(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def _decoder(source):
    for decoder in gc.get_referents(source):
        if isinstance(decoder, (io.IncrementalNewlineDecoder, codecs.IncrementalDecoder)):
            return decoder
    raise ValueError("[G6_E_STDIN_HANDOFF] stdin has no accessible text decoder")


def _pending_cr(source):
    """Move the real decoder's pending newline without trying to complete potentially invalid UTF-8.

    CPython's text wrapper visits its decoder for GC. Use the decoder's state API, not native memory offsets:
    the failed decode's error already owns every original byte, including the incremental byte buffer. Clear
    that buffer and finalize just the newline state. The raw line framer now owns both states.
    """
    decoder = _decoder(source)
    _, flags = decoder.getstate()
    decoder.setstate((b"", flags))
    newline = decoder.decode(b"", final=True)
    return b"\r" if newline else b""


def _cache_encoding(source):
    """Recover known reversible text caches; bypass every untouched text decoder.

    Reconfiguration with the SAME settings is CPython's public check for text already read: it refuses once
    decoded characters exist. Check incremental bytes/newlines first so reconfiguration cannot discard them.
    Unknown codecs and lossy error modes are safe only on that untouched path. Binary prefetch is independent
    of this check and is always transferred as bytes.
    """
    encoding = codecs.lookup(source.encoding).name
    if encoding in ("utf-8", "utf-8-sig", "cp1252", "iso8859-1") and source.errors in (
        "strict", "surrogateescape", "surrogatepass",
    ):
        # An earlier utf-8-sig read already handled its one initial signature; never insert another one.
        return "utf-8" if encoding == "utf-8-sig" else encoding
    decoder = _decoder(source)
    initial = codecs.getincrementaldecoder(source.encoding)(source.errors)
    if isinstance(decoder, io.IncrementalNewlineDecoder):
        initial = io.IncrementalNewlineDecoder(initial, translate=True)
    if decoder.getstate() != initial.getstate():
        raise ValueError("[G6_E_STDIN_HANDOFF] stdin has unrecoverable text decoder state")
    io.TextIOWrapper.reconfigure(source, encoding=source.encoding, errors=source.errors)
    return None                         # no text read: neither decode nor consume the binary cache as text


def cache_reader(source):
    """Validate before duplication; return the finite cache transfer and optional native-console raw reader."""
    text = type(source) is io.TextIOWrapper
    buffer = source.buffer if text else source
    console_type = getattr(_io, "_WindowsConsoleIO", None)
    if type(buffer) is io.FileIO or (console_type is not None and type(buffer) is console_type):
        raw = buffer
        method = "read"
    elif type(buffer) is io.BufferedReader and (
        type(buffer.raw) is io.FileIO or (console_type is not None and type(buffer.raw) is console_type)
    ):
        raw = buffer.raw
        method = "readinto"
    else:
        raise TypeError("stdin descriptor has an unsupported buffering stack")
    for stream in (source, buffer, raw):
        if any(name in vars(stream) for name in ("read", "read1", "readinto", "readline")):
            raise TypeError("stdin descriptor has custom read methods")
    encoding = _cache_encoding(source) if text else None

    def drain():
        boundary = 0

        def eof(*args):
            nonlocal boundary
            boundary += 1
            return b"" if method == "read" else 0

        def text_eof(*args):
            nonlocal boundary
            boundary += 1
            return b""

        with _method(raw, method, eof):
            if encoding is None:
                return _binary_cache(buffer)
            pending = bytearray()
            with ExitStack() as stack:
                if type(buffer) is io.BufferedReader:
                    # Drain only text already decoded. Fresh/buffered wire bytes must never pass through the
                    # wrapper's codec (or its BOM stripping / lossy error handler).
                    stack.enter_context(_method(buffer, "read1", text_eof))
                while True:
                    before = boundary
                    try:
                        char = source.read(1)
                    except UnicodeDecodeError as exc:
                        pending.extend(_pending_cr(source))
                        pending.extend(exc.object)
                        break
                    if not char:
                        break
                    if boundary != before and char == "\n":
                        char = "\r"     # EOF flushed a pending CR: the next raw byte may still be its LF
                    pending.extend(char.encode(encoding, errors=source.errors))
            pending.extend(_binary_cache(buffer))
            return bytes(pending)

    return drain, raw if type(raw) is console_type else None
