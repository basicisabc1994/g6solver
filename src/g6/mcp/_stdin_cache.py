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
from functools import lru_cache


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


class _TextBoundary(Exception):
    """Stop before the wrapper feeds any more bytes to its decoder, including a synthetic EOF."""


@lru_cache(maxsize=256)
def _ambiguous_characters(encoding):
    """Find noncanonical byte aliases in character maps and native one/two-byte codecs.

    A Unicode round trip alone cannot distinguish CP932's two spellings of some characters. For longer
    native multibyte codecs there is no bounded inverse here: report uncertainty rather than guess bytes.
    """
    codec = codecs.lookup(encoding)
    multibyte = any(base.__module__ == "_multibytecodec" for base in codec.incrementaldecoder.__mro__)
    signature = "".encode(encoding)
    ambiguous = set()
    longer = False
    candidates = (bytes((byte,)) for byte in range(256))
    if multibyte:
        candidates = (number.to_bytes(size, "big") for size in (1, 2) for number in range(256 ** size))
    for candidate in candidates:
        try:
            char = candidate.decode(encoding)
        except UnicodeError:
            if multibyte and len(candidate) == 2 and not longer:
                decoder = codec.incrementaldecoder("strict")
                try:
                    decoder.decode(candidate, final=False)
                except UnicodeError:
                    continue
                longer = len(decoder.getstate()[0]) == 2
            continue
        if char:
            try:
                canonical = char.encode(encoding)
            except UnicodeError:
                ambiguous.add(char)
                continue
            if signature and canonical.startswith(signature):
                canonical = canonical[len(signature):]
            if canonical != candidate:
                ambiguous.add(char)
    return frozenset(ambiguous), longer


def _check_codec_inverse(encoding, text):
    if codecs.lookup(encoding).name in ("mbcs", "oem"):
        raise ValueError("runtime code page has no fixed byte inverse")
    # Textual escape/shift encodings can spell the same character as plain bytes or escaped ASCII. Their
    # decoder state does not retain the original spelling, even when a re-encoded string decodes identically.
    for sample in ("\u00e9", "\u4e00", "\U0001f600"):
        try:
            encoder = codecs.getincrementalencoder(encoding)("strict")
            encoder.encode("", final=False)
            encoded = encoder.encode(sample, final=False)
        except UnicodeError:
            continue
        if len(encoded) > 1 and all(32 <= byte < 127 for byte in encoded):
            raise ValueError("text codec has ambiguous ASCII escape spellings")
    ambiguous, longer = _ambiguous_characters(encoding)
    if longer or any(spelling in text for spelling in ambiguous):
        raise ValueError("text codec has no unique bounded byte inverse for this cache")


def _restore_text(source, buffer, raw, method, decoder, text, state, cookie):
    """Put refused characters back in the native wrapper without reading more pipe input.

    Seekable wrappers can restore their original cookie. Pipes have no cookie: feed one synthetic chunk
    through the codec's Python adapter and consume only its sentinel. The remaining characters then belong
    to TextIOWrapper's own cache again, including for readline/iteration; no caller method is replaced.
    """
    newline = isinstance(decoder, io.IncrementalNewlineDecoder)
    codec = _decoder(decoder) if newline else decoder
    seen = decoder.newlines if newline else None

    def restore_decoder(saved):
        if newline:
            # Native newline history is separate from getstate(). Rebuild only observations the
            # caller already had; synthetic normalized LFs must not add a new newline kind.
            observed = "\0".join(seen if isinstance(seen, tuple) else (seen,) if seen else ())
            with _method(codec, "decode", lambda *args, **kwargs: observed):
                decoder.reset()
                decoder.decode(b"", final=True)
        decoder.setstate(saved)

    if cookie is not None:
        try:
            source.seek(cookie)
        except (OSError, ValueError):
            pass
        else:
            restore_decoder(decoder.getstate())
            return
    # A newline decoder holds the artificial final CR instead of exposing it. This also lets a real
    # trailing CR through when translation is disabled. Restore the original held CR/bytes afterwards.
    chunk = "\0" + text + ("\r" if newline else "")

    def decode(*args, **kwargs):
        return chunk

    def read(*args):
        return b"\0"

    def readinto(target):
        target[:1] = b"\0"
        return 1

    try:
        decoder.setstate((b"", state[1] & ~1 if newline else state[1]))
        with ExitStack() as stack:
            stack.enter_context(_method(codec, "decode", decode))
            stack.enter_context(_method(raw, method, read if method == "read" else readinto))
            if type(buffer) is io.BufferedReader:
                stack.enter_context(_method(buffer, "read1", read))
            if source.read(1) != "\0":
                raise ValueError("[G6_E_STDIN_HANDOFF] stdin text cache could not be restored")
    finally:
        restore_decoder(state)


def _text_cache(source, buffer, raw, method):
    """Prepare a transactional transfer of decoded characters and original decoder bytes.

    read(1) consumes only an existing character; the first attempt to refill raises at the binary boundary.
    Unlike EOF, this neither finalizes a codec nor flushes its partial byte/newline state. It also distinguishes
    an exhausted last chunk from pending characters, without changing the wrapper's configuration.
    """
    # Reconfiguration with the same encoding/errors leaves newline configuration alone. CPython refuses it
    # after a decoded read, even if the last chunk is exhausted. This is a history guard, not a pending cursor:
    # ordinary lossless codecs still need the boundary probe below. Never infer text from GC string referents,
    # which also include the wrapper's fixed read-newline setting.
    saved_state = _decoder(source).getstate()
    decoded_history = False
    try:
        io.TextIOWrapper.reconfigure(source, encoding=source.encoding, errors=source.errors)
    except io.UnsupportedOperation:
        decoded_history = True
    # A successful reconfiguration can replace the decoder, so inspect its state afterwards.
    decoder = _decoder(source)
    if not decoded_history:
        # A read through EOF can leave no decoded chunk yet retain part of a utf-8-sig signature.
        # The history probe must not reset those original bytes or its initial-signature flag.
        decoder.setstate(saved_state)
    buffered, flags = decoder.getstate()
    newline = isinstance(decoder, io.IncrementalNewlineDecoder)
    pending_cr = newline and flags & 1
    clean_flags = flags & ~1 if newline else flags

    if source.errors not in ("strict", "surrogateescape", "surrogatepass"):
        # A used lossy decoder has no recoverable byte provenance. Keep the standing refusal before
        # consuming caller text, including when the wrapper retains an exhausted decoded chunk.
        if buffered or pending_cr or decoded_history:
            raise ValueError("[G6_E_STDIN_HANDOFF] stdin has unrecoverable lossy text")
    if decoded_history:
        # With an opaque inverse, a pipe's consumed cursor cannot be inspected without consuming caller text.
        # Refuse before that destructive probe, even if its last decoded chunk might already be exhausted.
        try:
            _check_codec_inverse(source.encoding, "")
        except (UnicodeError, LookupError, TypeError, ValueError) as exc:
            raise ValueError("[G6_E_STDIN_HANDOFF] stdin has no inspectable lossless text cache") from exc

    def take():
        def boundary(*args):
            raise _TextBoundary

        cookie = None
        try:
            cookie = source.tell()
        except (OSError, ValueError):
            pass                        # pipes and wrappers read by iteration have no usable cookie
        chars = []
        with ExitStack() as stack:
            stack.enter_context(_method(raw, method, boundary))
            if type(buffer) is io.BufferedReader:
                stack.enter_context(_method(buffer, "read1", boundary))
            try:
                while char := source.read(1):
                    chars.append(char)
            except _TextBoundary:
                pass
        text = "".join(chars)
        pending = b""
        if text or pending_cr:
            try:
                # Match the standing whole-cache decision on a copy of decoder state. Per-character
                # checks can mistake partial surrogateescape bytes for new refusals. An empty cache
                # needs no inverse, and unrelated GC string referents say nothing about its cursor.
                _check_codec_inverse(source.encoding, text + ("\r" if pending_cr else ""))
                encoder = codecs.getincrementalencoder(source.encoding)(source.errors)
                encoder.encode("", final=False)
                initial_encoder_state = encoder.getstate()
                pending = encoder.encode(text, final=False)
                check = codecs.getincrementaldecoder(source.encoding)(source.errors)
                if newline:
                    check = io.IncrementalNewlineDecoder(check, translate=False)
                check.setstate((b"", clean_flags))
                if (check.decode(pending, final=False) != text or check.getstate() != (b"", clean_flags)
                        or encoder.getstate() != initial_encoder_state):
                    raise ValueError("text codec does not round-trip in its current state")
                if pending_cr:
                    pending += encoder.encode("\r", final=False)
            except (UnicodeError, LookupError, TypeError, ValueError) as exc:
                if text:
                    _restore_text(source, buffer, raw, method, decoder, text, (buffered, flags), cookie)
                raise ValueError("[G6_E_STDIN_HANDOFF] stdin has unrecoverable text decoder state") from exc
        # Only a successful transfer retires the original decoder's bytes and held CR.
        decoder.setstate((b"", clean_flags))
        return pending + buffered

    return take


def cache_reader(source, *, defer=False):
    """Check standing refusals; optionally defer the transactional probe past descriptor setup."""
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
    take = _text_cache(source, buffer, raw, method) if text else lambda: b""
    prefix = None if defer else take()

    def drain():
        nonlocal prefix
        if prefix is None:
            prefix = take()
        def eof(*args):
            return b"" if method == "read" else 0

        with _method(raw, method, eof):
            return prefix + _binary_cache(buffer)

    return drain, raw if type(raw) is console_type else None
