# SPDX-License-Identifier: Apache-2.0
"""What the memory family's members share: one record type for what an agent keeps, read back as a note, never an order.

``_``-prefixed, so it never registers as a component (``g6.core.registry``). It builds on the knowledge family's types
(``g6.knowledge._types``): a record may cite the :class:`~g6.knowledge._types.Passage` it was taken from.

* :class:`MemoryRecord` is one kept note or lesson: its text; where it came from (``source``, and the passage it cites
  when there is one); who wrote it (``written_by``: ``user``, ``agent:<run>``, ``model:<name>`` or ``tool:<name>``, as
  the writer says: a claim, recorded as one and never checked); when it was kept (``created_at``, set by the store and
  never by the writer) and when it goes stale (``stale_after``, ``""`` when nobody said); its ``kind`` (a ``note``, or
  a ``lesson`` for the playbook); its tags; and, for a lesson, the approval recorded for it (:class:`Approval`: the
  reviewer's name as the approving caller gave it, a claim G6 never checks) or none, which makes it a proposal.
* A record's ``id`` is the SHA-256 of its kind, text, source, writer and cited passage (:func:`record_id`), so keeping
  the same note twice keeps one record, and a record whose id fields were changed without its id recomputed no
  longer loads. Both that id and the store's row hash are unkeyed: an edit that recomputes them is not caught, and
  anyone who can write the store's file can. The approval, ``stale_after``, ``created_at`` and tags are outside the id,
  so only the row hash covers them.
* A record read back is untrusted data. Its ``trust`` is always ``"untrusted"`` (not a field, so nothing sets it), and
  :meth:`MemoryRecord.to_context_item` hands it to ``g6.context`` only as a ``memory_note`` of the lowest authority,
  which that package's own rules keep from ever being read as an instruction. Who the writer says they are changes
  neither.
* :func:`is_stale` says whether a record is past its ``stale_after`` at a given moment.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from g6.knowledge._types import Passage, instant

TRUST = "untrusted"
Kind = Literal["note", "lesson"]
#: The most characters one record holds.
MAX_TEXT = 4000
#: The most characters the passage a record cites may hold, and the most bytes one record takes as it is kept (its
#: JSON): both refused before anything is written.
MAX_PASSAGE_TEXT = 20_000
MAX_RECORD_BYTES = 256 * 1024
MAX_TAGS = 20
#: Who wrote a record, as the writer says: a person (``user``), an agent's run, a model or a tool.
WRITER = r"^(user|agent:[A-Za-z0-9._-]{1,100}|model:[A-Za-z0-9._:/@-]{1,100}|tool:[A-Za-z0-9._-]{1,100})$"
TAG = r"^[a-z0-9][a-z0-9_.:-]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_TAG_RE = re.compile(TAG)
_ID_TAG = b"g6-memory-record-v1\x00"


def record_id(kind: str, text: str, source: str, written_by: str, passage_id: str = "") -> str:
    """The id of the record with these fields: the SHA-256 of all of them, each ended by a zero byte."""
    digest = hashlib.sha256(_ID_TAG)
    for part in (kind, text, source, written_by, passage_id):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def tag_problems(tags: list[str] | tuple[str, ...]) -> list[str]:
    """What is wrong with ``tags``, in words; ``[]`` when each is a plain lower-case tag and there are few enough."""
    out = [f"{t!r} is not a tag (lower-case letters, digits and _ . : -, at most 64)" for t in tags
           if not _TAG_RE.fullmatch(t)]
    if len(tags) > MAX_TAGS:
        out.append(f"at most {MAX_TAGS} tags, not {len(tags)}")
    return out


class Approval(BaseModel):
    """An approval of a lesson as it was recorded: the reviewer's name as the approving caller gave it, when, and why.
    The name is that caller's claim: G6 cannot tell a person from a model that types a person's name, so an approved
    lesson is served marked ``approval_is: "claim"``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: str = Field(min_length=1, max_length=200)
    approved_at: str
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("approved_at")
    @classmethod
    def _an_instant(cls, value: str) -> str:
        if instant(value) is None:
            raise ValueError("approved_at is an ISO 8601 date and time with its offset")
        return value


class MemoryRecord(BaseModel):
    """One kept note or lesson; see the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_SHA256)
    kind: Kind = "note"
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: str = Field("", max_length=4096)
    passage: Passage | None = None
    written_by: str = Field(pattern=WRITER)
    created_at: str
    stale_after: str = ""
    tags: tuple[str, ...] = ()
    approval: Approval | None = None

    @model_validator(mode="before")
    @classmethod
    def _trust_is_not_an_input(cls, value: Any) -> Any:
        if isinstance(value, dict) and "trust" in value:
            if value["trust"] != TRUST:
                raise ValueError(f"a memory record is untrusted data; its trust cannot be {value['trust']!r}")
            value = {k: v for k, v in value.items() if k != "trust"}
        return value

    @model_validator(mode="after")
    def _consistent(self) -> MemoryRecord:
        if self.passage is not None and len(self.passage.text) > MAX_PASSAGE_TEXT:
            raise ValueError(f"the cited passage holds {len(self.passage.text)} characters; a record cites a passage "
                             f"of at most {MAX_PASSAGE_TEXT}: cite a shorter span")
        if instant(self.created_at) is None:
            raise ValueError("created_at is an ISO 8601 date and time with its offset")
        if self.stale_after and instant(self.stale_after) is None:
            raise ValueError("stale_after is an ISO 8601 date and time with its offset, such as 2026-12-31T00:00:00Z")
        if problems := tag_problems(self.tags):
            raise ValueError("; ".join(problems))
        if self.passage is not None and self.source != self.passage.source.uri:
            raise ValueError("a record that cites a passage has the passage's source as its own")
        if self.approval is not None and self.kind != "lesson":
            raise ValueError("only a lesson is approved; a note is kept as it is")
        expected = record_id(self.kind, self.text, self.source, self.written_by,
                             self.passage.id if self.passage else "")
        if self.id != expected:
            raise ValueError("the record's id is not the hash of its kind, text, source, writer and passage")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trust(self) -> Literal["untrusted"]:
        """Always ``"untrusted"``: a kept note is data, never instructions."""
        return "untrusted"

    @property
    def status(self) -> str:
        """``note``; or, for a lesson, ``approved`` once an approval is recorded (its reviewer a claim) and ``proposed``
        until then."""
        if self.kind == "note":
            return "note"
        return "approved" if self.approval is not None else "proposed"

    @property
    def approval_is(self) -> str:
        """``claim`` for an approved lesson (who approved it is the approving caller's word), ``none`` otherwise."""
        return "claim" if self.approval is not None else "none"

    def to_context_item(self, *, at: datetime | None = None) -> Any:
        """This record as a ``g6.context.ContextItem``: a ``memory_note``, untrusted and of the lowest authority,
        marked stale when it is past its ``stale_after`` at ``at`` (now when not given)."""
        from g6.context.packet import ContextItem

        stale = is_stale(self, at)
        return ContextItem(key=f"memory:{self.id[:16]}", value=self.text, section="memory",
                           authority="model_generated", kind="memory_note", trust="untrusted", source=self.source,
                           created_at=self.created_at, freshness_class="stale" if stale else "unknown",
                           stale_after=self.stale_after, retrieved_by="g6.memory",
                           included_because="a kept note, quoted as data",
                           metadata={"record_id": self.id, "kind": self.kind, "status": self.status,
                                     "approval_is": self.approval_is,
                                     "written_by": self.written_by, "written_by_is": "the writer's own claim",
                                     "source_is": "the writer's own claim",
                                     "instruction_signals": self._signals()})

    def _signals(self) -> list[str]:
        """The instruction-like phrasings ``g6.grounding``'s tripwire finds in the text or the cited passage."""
        from g6.grounding.tripwire import instruction_signals

        texts = [self.text, *([self.passage.text] if self.passage else [])]
        return list(dict.fromkeys(s for text in texts for s in instruction_signals(text)))


def is_stale(record: MemoryRecord, at: datetime | None = None) -> bool:
    """Whether ``record`` is past its ``stale_after`` at ``at`` (now, in UTC, when not given). A record with no
    ``stale_after`` is never reported stale, though nothing says it is current either."""
    if not record.stale_after:
        return False
    limit = instant(record.stale_after)
    return limit is None or limit <= (at or datetime.now(UTC))


__all__ = ["MAX_PASSAGE_TEXT", "MAX_RECORD_BYTES", "MAX_TAGS", "MAX_TEXT", "TAG", "TRUST", "WRITER", "Approval", "Kind",
           "MemoryRecord", "is_stale", "record_id", "tag_problems"]
