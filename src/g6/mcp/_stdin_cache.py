# SPDX-License-Identifier: Apache-2.0
"""Move a standard input stream's existing caches ahead of a raw descriptor reader.

The caller has exclusive read ownership during this handoff. Temporarily returning EOF from the raw object's
Python read method lets its buffered readers drain without touching the OS or waiting on outside input. All method
attributes are restored before returning. Only the standard IO stack is supported: an opaque wrapper cannot safely
be bypassed merely because it exposes a descriptor.
"""
from __future__ import annotations

import codecs
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
    if type(buffer) is io.FileIO:
        return b""
    chunks = []
    while chunk := buffer.read1(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def _pending_cr(source):
    """Recover a newline decoder's pending CR even if its UTF-8 decoder also holds an incomplete character.

    A failed decode leaves the decoder's earlier state intact. Supply only the missing continuation bytes to let
    the newline decoder emit that CR; the synthetic completed character is discarded, never handed to the parser.
    The incomplete original bytes are already included in the failed decode's input.
    """
    buffer = source.buffer
    method = "read1" if hasattr(buffer, "read1") else "read"
    with _method(buffer, method, lambda size: b""):
        try:
            char = source.read(1)
        except UnicodeDecodeError as exc:
            partial = exc.object
            lead = partial[0]
            length = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
            suffix = bytearray()
            if len(partial) == 1:
                suffix.append(0xA0 if lead == 0xE0 else 0x90 if lead == 0xF0 else 0x80)
            suffix.extend(b"\x80" * (length - len(partial) - len(suffix)))

            def complete(size):
                nonlocal suffix
                chunk, suffix = bytes(suffix), bytearray()
                return chunk

            with _method(buffer, method, complete):
                char = source.read(1)
                while source.read(1):
                    pass                # consume the synthetic completion even if a pending CR was returned first
    return b"\r" if char in ("\r", "\n") else b""


def cache_reader(source):
    """Validate the whole stack before taking a duplicate; return its finite, reader-thread cache transfer."""
    text = type(source) is io.TextIOWrapper
    buffer = source.buffer if text else source
    if text and (codecs.lookup(source.encoding).name != "utf-8"
                 or source.errors not in ("strict", "surrogateescape", "surrogatepass")):
        raise ValueError("stdin handoff requires lossless UTF-8 text input")
    if type(buffer) is io.FileIO:
        raw = buffer
        method = "read"
    elif type(buffer) is io.BufferedReader and type(buffer.raw) is io.FileIO:
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

    return drain
