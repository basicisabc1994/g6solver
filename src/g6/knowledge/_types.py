# SPDX-License-Identifier: Apache-2.0
"""What every knowledge extension shares: where text came from, the text read from it, and exact spans of that text.

``_``-prefixed, so it never registers as a component (``g6.core.registry``). The sources and memory families build on
these types too, so they stay small.

* :class:`Source` is where text came from: its address (a URL or a path, ``uri``), when it was read
  (``retrieved_at``), the SHA-256 of the exact bytes read, how the text was captured (``provenance``: ``fetched``,
  ``caller_supplied`` or ``model_transcribed``), and its licence or terms and its own publication date when they are
  known. Unknown stays unknown: ``""``, never a guess.
* :class:`Document` is text read from one source: the text, the source, the media type it was read from, what
  converted it (``converted_by``, ``""`` when the source was text already) and what could not be read (``unread``: a
  page with no text layer is listed there, never left silently empty). Its ``sha256`` is the hash of its text.
* :class:`Passage` is an exact span of a document: ``text`` is ``document.text[start:end]``, and its ``id`` is the
  SHA-256 of the source's address and hash, the document's hash and the two offsets (:func:`passage_id`). So the same
  words read from two addresses are two passages, each citing its own, and a passage whose offsets moved is another
  passage. :func:`passage_problems` checks a passage against the document it says it came from, and
  :meth:`Passage.to_grounding` hands it to ``g6.grounding``, so every grounding check runs on it unchanged.

Retrieved text is untrusted data, never instructions. A document and a passage say so in ``trust``, which is always
``"untrusted"``: it is not a field, so no input, copy or update can set it to anything else. Nothing in the family
reads a retrieved text to decide what to do. :func:`untrusted_note` records the instruction-like phrasings that
``g6.grounding``'s tripwire finds in one, for a person to look at; it changes no state, code or label.
"""
from __future__ import annotations

import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

TRUST = "untrusted"
Provenance = Literal["fetched", "caller_supplied", "model_transcribed"]
#: The most characters one document may hold.
MAX_TEXT = 20_000_000
_SHA256 = r"^[0-9a-f]{64}$"
_INSTANT = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})")
_TAG = b"g6-knowledge-passage-v1\x00"
_CLOCK = threading.Lock()
_LAST: list[datetime] = []


def sha256_hex(data: bytes | str) -> str:
    """The SHA-256 of ``data`` in hex; a ``str`` is hashed as UTF-8."""
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def now() -> str:
    """This moment in UTC, to the microsecond, in ISO 8601 (``2026-10-21T09:30:00.123456Z``). Each call in a process
    is later than the one before it (by a microsecond when the clock has not moved), so a source read after another
    is always dated after it."""
    with _CLOCK:
        moment = datetime.now(UTC)
        if _LAST and moment <= _LAST[0]:
            moment = _LAST[0] + timedelta(microseconds=1)
        _LAST[:] = [moment]
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def instant(value: str) -> datetime | None:
    """``value``, an ISO 8601 date and time with its offset (as a source's ``retrieved_at`` is), as an aware datetime
    to the microsecond; ``None`` when it is not one."""
    found = _INSTANT.fullmatch(value)
    if not found:
        return None
    base, fraction, offset = found.groups()
    try:
        return datetime.fromisoformat(base + (f".{fraction[:6]:0<6}" if fraction else "")
                                      + ("+00:00" if offset == "Z" else offset))
    except ValueError:
        return None


class Source(BaseModel):
    """Where text came from: ``sha256`` is the hash of the exact bytes read from ``uri`` at ``retrieved_at``.
    ``licence`` (its licence or terms) and ``published_at`` (its own date) are ``""`` when not known."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=_SHA256)
    retrieved_at: str
    provenance: Provenance = "caller_supplied"
    licence: str = Field("", max_length=500)
    published_at: str = Field("", max_length=40)

    @field_validator("retrieved_at")
    @classmethod
    def _an_instant(cls, value: str) -> str:
        if not _INSTANT.fullmatch(value):
            raise ValueError("retrieved_at is an ISO 8601 date and time with its offset, such as 2026-10-21T09:30:00Z")
        return value

    @classmethod
    def of(cls, uri: str, data: bytes | str, *, provenance: Provenance = "caller_supplied", licence: str = "",
           published_at: str = "", retrieved_at: str = "") -> Source:
        """The source of ``data`` read from ``uri`` now (or at ``retrieved_at``)."""
        return cls(uri=uri, sha256=sha256_hex(data), retrieved_at=retrieved_at or now(), provenance=provenance,
                   licence=licence, published_at=published_at)


class _Untrusted(BaseModel):
    """A model holding retrieved text: its ``trust`` is always ``"untrusted"``. Given back as ``"untrusted"`` (a dumped
    model loading again) it is accepted; given as anything else it is refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _trust_is_not_an_input(cls, value: Any) -> Any:
        if isinstance(value, dict) and "trust" in value:
            if value["trust"] != TRUST:
                raise ValueError(f"retrieved text is untrusted data; its trust cannot be {value['trust']!r}")
            value = {k: v for k, v in value.items() if k != "trust"}
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trust(self) -> Literal["untrusted"]:
        """Always ``"untrusted"``: retrieved text is data, never instructions."""
        return "untrusted"


def passage_id(source: Source, document_sha256: str, start: int, end: int) -> str:
    """The id of the span ``start:end`` of the text whose hash is ``document_sha256``, read from ``source``."""
    digest = hashlib.sha256(_TAG)
    for part in (source.uri, source.sha256, document_sha256, str(start), str(end)):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class Passage(_Untrusted):
    """An exact span of a document's text, bound to its source, to that text's hash and to its offsets."""

    id: str = Field(pattern=_SHA256)
    source: Source
    document_sha256: str = Field(pattern=_SHA256)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def _bound_to_its_span(self) -> Passage:
        if self.end <= self.start:
            raise ValueError(f"a passage ends after it starts; got {self.start}:{self.end}")
        if len(self.text) != self.end - self.start:
            raise ValueError(f"a passage's text is exactly its span: {self.end - self.start} characters for "
                             f"{self.start}:{self.end}, not {len(self.text)}")
        if self.id != passage_id(self.source, self.document_sha256, self.start, self.end):
            raise ValueError("the passage id is not the hash of its source, its text's hash and its offsets")
        return self

    def to_grounding(self, *, score: float = 0.0, score_scale: str = "none") -> Any:
        """This passage as a ``g6.grounding`` passage (its source's address as the source)."""
        from g6.grounding.retrieval import Passage as GroundingPassage

        return GroundingPassage(text=self.text, source=self.source.uri, score=score, score_scale=score_scale,
                                provenance=self.source.provenance, published_at=self.source.published_at)


class Document(_Untrusted):
    """Text read from one source: what converted it and what it could not read are part of it. ``sha256`` (the hash
    of ``text``) is in its dump, and a dump whose text no longer has that hash does not load."""

    text: str = Field(max_length=MAX_TEXT)
    source: Source
    media_type: str = Field("text/plain", min_length=1, max_length=200)
    converted_by: str = Field("", max_length=200)
    unread: tuple[str, ...] = ()
    title: str = Field("", max_length=2000)
    locator: str = Field("", max_length=4096)  # where inside the source the text sits, or the record's own address

    @model_validator(mode="before")
    @classmethod
    def _its_hash_is_its_texts(cls, value: Any) -> Any:
        if isinstance(value, dict) and "sha256" in value:
            if value["sha256"] != sha256_hex(str(value.get("text", ""))):
                raise ValueError("the document's sha256 is not the hash of its text: the text was changed")
            value = {k: v for k, v in value.items() if k != "sha256"}
        return value

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def sha256(self) -> str:
        """The SHA-256 of the text, in hex."""
        return sha256_hex(self.text)

    def passage(self, start: int, end: int) -> Passage:
        """The span ``start:end`` of the text as a passage."""
        if not 0 <= start < end <= len(self.text):
            raise ValueError(f"{start}:{end} is not a span of a {len(self.text)}-character text")
        return Passage(id=passage_id(self.source, self.sha256, start, end), source=self.source,
                       document_sha256=self.sha256, start=start, end=end, text=self.text[start:end])


def passage_problems(passage: Passage, document: Document) -> list[str]:
    """What is wrong with ``passage`` as a span of ``document``, in words; ``[]`` when it is exactly that span."""
    out = []
    if (passage.source.uri, passage.source.sha256) != (document.source.uri, document.source.sha256):
        out.append(f"it cites another source ({passage.source.uri}, not {document.source.uri}, or other bytes)")
    if passage.document_sha256 != document.sha256:
        out.append("it was cut from another version of the text")
    if document.text[passage.start:passage.end] != passage.text:
        at = document.text.find(passage.text)
        where = f" (it is at {at}:{at + len(passage.text)})" if at >= 0 else " (it is nowhere in the text)"
        out.append(f"its text is not the document's text at {passage.start}:{passage.end}{where}")
    return out


def untrusted_note(text: str) -> dict[str, Any]:
    """The evidence entry for one retrieved text: it is untrusted, and the instruction-like phrasings the tripwire
    found in it (a hit means a person should look, no hit establishes nothing)."""
    from g6.grounding.tripwire import instruction_signals

    return {"kind": "retrieved_text", "trust": TRUST, "characters": len(text),
            "instruction_signals": instruction_signals(text)}


__all__ = ["MAX_TEXT", "TRUST", "Document", "Passage", "Provenance", "Source", "instant", "now", "passage_id",
           "passage_problems", "sha256_hex", "untrusted_note"]