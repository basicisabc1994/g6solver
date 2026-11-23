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
from contextlib import contextmanager


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


def _pending_cr(source):
    """Move the real decoder's pending newline without trying to complete potentially invalid UTF-8.

    CPython's text wrapper visits its decoder for GC. Use the decoder's state API, not native memory offsets:
    the failed decode's error already owns every original byte, including the incremental byte buffer. Clear
    that buffer and finalize just the newline state. The raw line framer now owns both states.
    """
    for decoder in gc.get_referents(source):
        if isinstance(decoder, (io.IncrementalNewlineDecoder, codecs.IncrementalDecoder)):
            _, flags = decoder.getstate()
            decoder.setstate((b"", flags))
            newline = decoder.decode(b"", final=True)
            return b"\r" if newline else b""
    raise ValueError("[G6_E_STDIN_HANDOFF] stdin has no accessible UTF-8 decoder")


def cache_reader(source):
    """Validate before duplication; return the finite cache transfer and optional native-console raw reader."""
    text = type(source) is io.TextIOWrapper
    buffer = source.buffer if text else source
    if text and (codecs.lookup(source.encoding).name != "utf-8"
                 or source.errors not in ("strict", "surrogateescape", "surrogatepass")):
        raise ValueError("stdin handoff requires lossless UTF-8 text input")
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

    def drain():
        boundary = 0

        def eof(*args):
            nonlocal boundary
            boundary += 1
            return b"" if method == "read" else 0

        with _method(raw, method, eof):
            if not text:
                return _binary_cache(buffer)
            pending = bytearray()
            while True:
                before = boundary
                try:
                    char = source.read(1)
                except UnicodeDecodeError as exc:
                    # No character from this failed decoder input was returned. Keep all its original bytes,
                    # including any preceding incremental UTF-8 state, ahead of the remaining binary cache.
                    pending.extend(_pending_cr(source))
                    pending.extend(exc.object)
                    pending.extend(_binary_cache(buffer))
                    return bytes(pending)
                if not char:
                    return bytes(pending)
                if boundary != before and char == "\n":
                    char = "\r"         # EOF flushed a pending CR: the next raw byte may still be its LF
                pending.extend(char.encode("utf-8", errors=source.errors))

    return drain, raw if type(raw) is console_type else None
