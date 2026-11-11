# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's versioned search envelope (design 12.3): one request and one response for every route.

``_``-prefixed, so it never registers as a component. The canonical :class:`~g6.knowledge._types.Passage` is unchanged;
a response carries :class:`PassageItem` records, which hold a redacted view of the passage, never its raw text. Each
backend's state (``answered``, ``empty``, ``unavailable``, ``failed``, ``partial``, ``unsupported``, ``refused``) is
kept beside the items: a backend that did not answer says nothing about absence of evidence. ``withheld`` names only a
source registration and a provider; ``denied`` names passages that are known to this destination and may no longer be
used, with why.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.knowledge._scope import Operation
from g6.knowledge._view import View

VERSION = "g6.knowledge.search.v1"
BackendState = Literal["answered", "empty", "unavailable", "failed", "partial", "unsupported", "refused"]
DeniedWhy = Literal["revoked", "expired", "changed", "unknown", "not_published"]
MAX_TOP_K = 20


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Denied(_Record):
    passage_id: str
    registration_id: str
    why: DeniedWhy


class Contribution(_Record):
    backend: str
    route: str
    query: str
    rank: int
    score_scale: str
    reason: str
    passage_id: str
    generation: int


class Checked(_Record):
    """The re-read every returned passage gets in the call that returns it."""

    outcome: Literal["intact"]
    reread_sha256: str
    at: str


class PassageItem(_Record):
    passage_id: str
    source: str                      # the source registration id
    path: str                        # the file's path inside the registration, as a view
    start: int
    end: int
    view: View
    rank: int
    contributions: tuple[Contribution, ...]
    checked: Checked
    trust: Literal["untrusted"] = "untrusted"
    instruction_signals: list[str] = Field(default_factory=list)


class BackendReport(_Record):
    backend: str
    route: str
    state: BackendState
    reason: str = ""
    searched: int | None = None
    rung: str = ""


class SearchRequest(_Record):
    operation: Operation = "search"
    query: str = Field(max_length=4000)
    top_k: int = Field(8, ge=1, le=MAX_TOP_K)
    sources: tuple[str, ...] | None = None
    routes: tuple[str, ...] | None = None


class SearchResponse(_Record):
    envelope: str = VERSION
    request: SearchRequest
    generation: int | None
    authorisation_revisions: dict[str, int] = Field(default_factory=dict)
    backends: tuple[BackendReport, ...] = ()
    payload: dict[str, Any] = Field(default_factory=lambda: {"kind": "passages", "items": []})
    withheld: tuple[dict[str, str], ...] = ()
    denied: tuple[Denied, ...] = ()


__all__ = ["MAX_TOP_K", "VERSION", "BackendReport", "BackendState", "Checked", "Contribution", "Denied",
           "DeniedWhy", "PassageItem", "SearchRequest", "SearchResponse"]
