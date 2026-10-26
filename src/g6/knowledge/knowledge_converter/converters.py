# SPDX-License-Identifier: Apache-2.0
"""The convert stage: bytes to a :class:`~g6.knowledge._types.Document` through converters registered by media type.

:func:`convert` hands the bytes given (never a URL: nothing is fetched here) to the converter registered for their
media type (:data:`CONVERTERS`) and returns a :class:`Converted`: ``ok``, or ``partial`` when part of the source
could not be read (each part named in the document's ``unread``), or a refusal: ``nothing_readable`` (no text at
all, such as a scanned PDF), ``unsupported`` (no converter for the type), ``not_installed`` (the converter's extra is
missing) or ``failed`` (the converter could not read the bytes). A document records what converted it.

Shipped: ``text/plain``, ``text/markdown`` (text as it is; bytes that are not valid in the declared charset are
replaced and reported), ``text/html`` and ``application/xhtml+xml`` (the family's standard-library HTML reader:
scripts, styles and hidden text dropped, the hidden text reported) and ``application/pdf`` (pypdf, the ``docs`` extra:
each page's text layer, pages separated by a form feed, a page with no text layer reported as unread). Never PyMuPDF.

pypdf runs in a child process (``_pdf_child.py`` under :mod:`g6.core.component_process`): a clean environment, a
deadline (:data:`PDF_SECONDS`, after which it is stopped and the outcome is ``time_limit``), a memory cap where the
platform has one (:data:`PDF_MEMORY_MB`) and a capped reply. A PDF whose page tree declares more than
:data:`MAX_PAGES` pages is refused before any page is loaded; reading stops at :data:`MAX_PDF_TEXT_BYTES` of text or
after :data:`PDF_READ_SECONDS`, and the pages not read are listed as unread (``partial``).
"""
from __future__ import annotations

import binascii
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from g6.core import component_process
from g6.knowledge import _html, _store
from g6.knowledge._types import Document, Provenance, Source, now, sha256_hex

Outcome = Literal["ok", "partial", "nothing_readable", "unsupported", "not_installed", "failed", "time_limit"]
#: The most pages a PDF may declare; one that declares more is refused before any page is loaded.
MAX_PAGES = 5000
#: The most bytes one conversion takes.
MAX_BYTES = 50 * 1024 * 1024
#: The most text one PDF conversion returns, in bytes (each page's text as JSON in UTF-8); the pages after it are
#: reported as unread.
MAX_PDF_TEXT_BYTES = 12 * 1024 * 1024
#: How long the child reading a PDF may run before it is stopped (``time_limit``), and how long it reads pages before
#: it stops on its own and reports the rest as unread.
PDF_SECONDS = 60.0
PDF_READ_SECONDS = 45.0
#: The memory the child reading a PDF may use, in MiB (a Job Object on Windows, an address-space limit on POSIX).
PDF_MEMORY_MB = 1024
NO_TEXT_LAYER = "no text layer (a scanned image or a blank page); it was not read"
_CHILD = Path(__file__).with_name("_pdf_child.py")
#: Environment variables the child may see (the interpreter needs these on Windows); nothing else is passed.
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")


class Converted(BaseModel):
    """What a conversion gave: the document (``ok`` or ``partial``), else why not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Outcome
    backend: str = ""
    document: Document | None = None
    reason: str = ""
    unread: tuple[str, ...] = ()


@runtime_checkable
class Converter(Protocol):
    """A converter: its name, the media types it reads, the extra it needs (``""`` for none), whether it is ready
    (:meth:`available` is ``""`` when it is, else why not and how to install it), and :meth:`convert`."""

    name: str
    media_types: tuple[str, ...]
    extra: str

    def available(self) -> str: ...

    def convert(self, data: bytes, source: Source, media_type: str, charset: str) -> Converted: ...


class ConverterRegistry:
    """The converters registered by media type; a type is taken once, unless :meth:`register` ``replace``\\ s it."""

    def __init__(self) -> None:
        self._by_type: dict[str, Converter] = {}

    def register(self, converter: Converter, *, replace: bool = False) -> None:
        if not isinstance(converter, Converter):
            raise TypeError(f"{converter!r} is not a converter (name, media_types, extra, available, convert)")
        taken = [t for t in converter.media_types if t in self._by_type and not replace]
        if taken:
            raise ValueError(f"a converter is already registered for {taken[0]}")
        for kind in converter.media_types:
            self._by_type[kind.lower()] = converter

    def unregister(self, media_type: str) -> None:
        self._by_type.pop(media_type.lower(), None)

    def get(self, media_type: str) -> Converter | None:
        return self._by_type.get(media_type.lower())

    def types(self) -> list[str]:
        """The media types registered, without asking any converter whether it is ready."""
        return sorted(self._by_type)

    def describe(self) -> list[dict[str, str]]:
        return [{"media_type": kind, "converter": c.name, "extra": c.extra, "available": c.available()}
                for kind, c in sorted(self._by_type.items())]


CONVERTERS = ConverterRegistry()


def split_media_type(raw: str) -> tuple[str, str]:
    """``(media type, charset)`` from a value such as ``text/html; charset=utf-8``."""
    kind, _, rest = raw.partition(";")
    charset = ""
    for part in rest.split(";"):
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"')
    return kind.strip().lower(), charset


def decode(data: bytes, charset: str) -> tuple[str, tuple[str, ...]]:
    """``data`` as text in ``charset`` (UTF-8 when none or an unknown one is named), and what could not be read."""
    try:
        return data.decode(charset or "utf-8"), ()
    except LookupError:
        charset = ""
        try:
            return data.decode("utf-8"), ()
        except UnicodeDecodeError:
            pass
    except UnicodeDecodeError:
        pass
    text = data.decode(charset or "utf-8", errors="replace")
    return text, (f"{text.count(chr(0xFFFD))} character(s) were not valid {charset or 'UTF-8'} and were replaced "
                  "with U+FFFD",)


def _done(backend: str, text: str, source: Source, media_type: str, unread: tuple[str, ...], *, converted_by: str = "",
          title: str = "") -> Converted:
    if not text.strip():
        return Converted(outcome="nothing_readable", backend=backend, unread=unread,
                         reason=f"the {media_type} holds no readable text" + (f" ({'; '.join(unread[:3])})"
                                                                               if unread else ""))
    document = Document(text=text, source=source, media_type=media_type, converted_by=converted_by, unread=unread,
                        title=title)
    return Converted(outcome="partial" if unread else "ok", backend=backend, document=document, unread=unread)


class TextConverter:
    name, media_types, extra = "text", ("text/plain", "text/markdown", "text/x-markdown"), ""

    def available(self) -> str:
        return ""

    def convert(self, data: bytes, source: Source, media_type: str, charset: str) -> Converted:
        text, unread = decode(data, charset)
        return _done(self.name, text, source, media_type, unread)


class HtmlConverter:
    name, media_types, extra = "html", tuple(sorted(_html.HTML_TYPES)), ""

    def available(self) -> str:
        return ""

    def convert(self, data: bytes, source: Source, media_type: str, charset: str) -> Converted:
        markup, unread = decode(data, charset)
        title, text, dropped = _html.read_html(markup)
        return _done(self.name, text, source, media_type, unread + dropped, title=title,
                     converted_by=_html.CONVERTED_BY)


def _pypdf():
    try:
        import pypdf
    except ImportError:
        return None
    return pypdf


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _read_pdf(data: bytes) -> dict | str:
    """The child's reply for ``data``, or why there is none (``"time_limit"`` when its deadline passed)."""
    argv = [sys.executable, "-I", str(_CHILD), str(MAX_PAGES), str(MAX_PDF_TEXT_BYTES), f"{PDF_READ_SECONDS:g}",
            str(PDF_MEMORY_MB)]
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    with tempfile.TemporaryDirectory(prefix="g6_pdf_") as cwd:
        try:
            with component_process.window(PDF_SECONDS):
                done = component_process.run_contained(argv, env=env, cwd=cwd, input_bytes=data,
                                                       memory_bytes=PDF_MEMORY_MB * 1024 * 1024)
        except component_process.ExecutionInterrupted:
            return "time_limit"
        except (OSError, RuntimeError) as exc:  # the child could not be run, or its tree not confirmed gone
            return f"the PDF reader could not be run to the end: {type(exc).__name__}: {exc}"[:500]
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip()[-300:]
        return f"the PDF reader stopped with exit code {done.returncode} (out of memory, or a crash): {tail}"[:500]
    try:
        reply = json.loads(done.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "the PDF reader's reply could not be read"
    if not (isinstance(reply, dict) and isinstance(reply.get("version"), str)
            and (reply.get("ok") is False and isinstance(reply.get("reason"), str)
                 or reply.get("ok") is True and _strings(reply.get("texts")) and _strings(reply.get("unread")))):
        return "the PDF reader's reply is not in its format"
    return reply


class PdfConverter:
    name, media_types, extra = "pypdf", ("application/pdf",), "docs"

    def available(self) -> str:
        return "" if _pypdf() is not None else "pypdf is not installed: pip install 'g6[docs]'"

    def convert(self, data: bytes, source: Source, media_type: str, charset: str) -> Converted:
        if _pypdf() is None:
            return Converted(outcome="not_installed", backend=self.name, reason=self.available())
        if not data.lstrip()[:5] == b"%PDF-":
            return Converted(outcome="failed", backend=self.name, reason="the bytes are not a PDF (no %PDF- header)")
        reply = _read_pdf(data)
        if reply == "time_limit":
            return Converted(outcome="time_limit", backend=self.name,
                             reason=f"pypdf did not finish within {PDF_SECONDS:g} seconds, so it was stopped; "
                                    "nothing it read is used")
        if isinstance(reply, str):
            return Converted(outcome="failed", backend=self.name, reason=reply)
        if not reply["ok"]:
            return Converted(outcome="failed", backend=self.name, reason=reply["reason"])
        return _done(self.name, "\f".join(reply["texts"]), source, media_type, tuple(reply["unread"]),
                     converted_by=f"pypdf {reply['version']}")


for _converter in (TextConverter(), HtmlConverter(), PdfConverter()):
    CONVERTERS.register(_converter, replace=True)


def convert(data: bytes, media_type: str, *, uri: str = "", provenance: Provenance = "caller_supplied",
            retrieved_at: str = "") -> Converted:
    """``data`` (of ``media_type``) as a document; see the module docstring."""
    kind, charset = split_media_type(media_type)
    if not kind and data.lstrip()[:5] == b"%PDF-":
        kind = "application/pdf"
    converter = CONVERTERS.get(kind)
    if converter is None:
        listed = ", ".join(CONVERTERS.types())
        return Converted(outcome="unsupported", reason=f"no converter is registered for {kind or 'an unnamed type'} "
                                                       f"(registered: {listed})")
    if len(data) > MAX_BYTES:
        return Converted(outcome="failed", backend=converter.name, reason=f"{len(data)} bytes is over the "
                                                                          f"{MAX_BYTES}-byte cap")
    source = Source(uri=uri or f"bytes:{sha256_hex(data)}", sha256=sha256_hex(data), retrieved_at=retrieved_at or now(),
                    provenance=provenance)
    try:
        found = converter.convert(data, source, kind, charset)
    except Exception as exc:  # noqa: BLE001 - a converter's bug is a refusal, never an empty document
        return Converted(outcome="failed", backend=converter.name,
                         reason=f"the {converter.name} converter failed: {type(exc).__name__}: {exc}"[:500])
    if not isinstance(found, Converted):
        return Converted(outcome="failed", backend=converter.name, reason="the converter did not return a conversion")
    return found


def convert_kept(sha256: str, media_type: str, *, uri: str = "") -> Converted | None:
    """The bytes the fetcher kept as ``sha256``, converted; ``None`` when none are kept under that name. Their source
    is their fetch as the store recorded it (the address after redirects, and when): ``uri`` may name that fetch (by
    that address or the one asked for) and never relabels it. Bytes kept with no fetch on record are the caller's
    (``caller_supplied``, at ``uri``). Raises ``g6.knowledge._store.KeptOriginError`` for a ``uri`` the bytes were
    not fetched from, and ``g6.knowledge._store.KeptError`` for a malformed name, bytes that changed or a record that
    cannot be read."""
    data = _store.kept(sha256)
    if data is None:
        return None
    fetches = _store.origins(sha256)
    if not fetches:
        return convert(data, media_type, uri=uri, provenance="caller_supplied")
    named = [f for f in fetches if uri in (f["url"], f["requested"])] if uri else fetches
    if not named:
        where = ", ".join(sorted({f["url"] for f in fetches}))
        raise _store.KeptOriginError(f"the bytes kept as {sha256} were fetched from {where}, not from {uri}; convert "
                                     "them with that address, or with none")
    fetch = named[-1]
    return convert(data, media_type, uri=fetch["url"], provenance="fetched", retrieved_at=fetch["retrieved_at"])


def from_base64(text: str) -> bytes:
    """Base64 text as bytes; ``ValueError`` when it is not base64."""
    try:
        return binascii.a2b_base64(text.encode("ascii"), strict_mode=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError(f"data_base64 is not base64: {exc}") from None


__all__ = ["CONVERTERS", "MAX_BYTES", "MAX_PAGES", "MAX_PDF_TEXT_BYTES", "NO_TEXT_LAYER", "PDF_MEMORY_MB",
           "PDF_READ_SECONDS", "PDF_SECONDS", "Converted", "Converter", "ConverterRegistry", "HtmlConverter", "Outcome",
           "PdfConverter", "TextConverter", "convert", "convert_kept", "decode", "from_base64", "split_media_type"]