# SPDX-License-Identifier: Apache-2.0
"""Cut a document into passages, and check a citation against the document it names.

:func:`spans` finds a text's units as ``(start, end)`` offsets into the text exactly as given: characters, words,
sentences, or tokens from a tokenizer the caller passes. Line endings are never normalised, so an offset is the same
for every reader that counts characters. :func:`chunk` packs ``size`` units into each window, ``overlap`` of them
shared with the next, and cuts each window as ``document.passage(start, end)``, so a passage's text is the document's
text at its offsets and its id is bound to its source. Before it returns them, it checks every passage it cut: the
slice, the id, the source, and that each one starts and ends on a unit's edge and holds at most ``size`` units. One
that fails is a refusal, never a passage. :func:`span_problems` checks one citation an agent holds (its offsets, its
text, its id, its source) against a document.
"""
from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from typing import Literal

from g6.knowledge._types import Document, Passage, Source, passage_id, passage_problems

Unit = Literal["chars", "words", "sentences", "tokens"]
Span = tuple[int, int]
Tokenizer = Callable[[str], Sequence[Span]]

NO_TEXT = "G6_E_KNOWLEDGE_NO_TEXT"
CHUNK_INPUT = "G6_E_KNOWLEDGE_CHUNK_INPUT"
TOO_MANY = "G6_E_KNOWLEDGE_CHUNK_TOO_MANY"
TOKENIZER = "G6_E_KNOWLEDGE_TOKENIZER"
SPAN_MISMATCH = "G6_E_KNOWLEDGE_SPAN_MISMATCH"
#: The most passages one call cuts.
MAX_PASSAGES = 100_000
#: The most characters one call's passages may hold in all, as a multiple of the text's length (overlapping windows
#: copy the text they share), and the least that bound is, so a short text can still be cut with a large overlap.
MAX_COPIES = 4
MIN_COPY_CHARS = 1_000_000

_WORD = re.compile(r"\S+")
#: Where a sentence ends: after ".", "!" or "?" (and any closing quotes or brackets) before a space or the end of the
#: text; and at a blank line, in any line ending.
_END = re.compile(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)|(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)")


class ChunkError(ValueError):
    """A refusal raised while chunking, carrying its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


def _trimmed(text: str, start: int, end: int) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _sentences(text: str) -> list[Span]:
    out: list[Span] = []
    begin = 0
    for found in _END.finditer(text):
        cut = found.end() if text[found.start()] in ".!?" else found.start()
        span = _trimmed(text, begin, cut)
        if span:
            out.append(span)
        begin = found.end()
    span = _trimmed(text, begin, len(text))
    if span:
        out.append(span)
    return out


def _tokens(text: str, tokenizer: Tokenizer | None) -> list[Span]:
    if tokenizer is None:
        raise ChunkError(TOKENIZER, "token windows need a tokenizer, which only a Python caller can pass "
                                    "(KnowledgeChunkerBlock(tokenizer=...) or chunk(..., tokenizer=...)); over MCP "
                                    "use words, sentences or chars")
    try:
        found = [(int(s), int(e)) for s, e in tokenizer(text)]
    except Exception as exc:  # noqa: BLE001 - a tokenizer's error is a refusal, never a crash or a guess
        raise ChunkError(TOKENIZER, f"the tokenizer failed: {type(exc).__name__}: {exc}"[:300]) from None
    last = 0
    for start, end in found:
        if not last <= start < end <= len(text):
            raise ChunkError(TOKENIZER, f"the tokenizer's span {start}:{end} is outside the text, or its spans "
                                        "overlap or are out of order")
        last = end
    return found


def spans(text: str, unit: Unit, tokenizer: Tokenizer | None = None) -> list[Span]:
    """The units of ``text`` as ``(start, end)`` offsets, in order; ``chars`` gives every character that is not
    white space."""
    if unit == "chars":
        return [(i, i + 1) for i, ch in enumerate(text) if not ch.isspace()]
    if unit == "words":
        return [m.span() for m in _WORD.finditer(text)]
    if unit == "sentences":
        return _sentences(text)
    if unit == "tokens":
        return _tokens(text, tokenizer)
    raise ChunkError(CHUNK_INPUT, f"{unit!r} is not a unit (chars, words, sentences or tokens)")


def _count(units: int, size: int, overlap: int, most: int) -> None:
    step = size - overlap
    windows = 1 if units <= size else -(-(units - size) // step) + 1
    if windows > most:
        raise ChunkError(TOO_MANY, f"{units} units in windows of {size} with {overlap} shared would be {windows} "
                                   f"passages, more than {most}; choose a larger size")


def _total(windows: Sequence[Span], length: int) -> None:
    total, most = sum(end - start for start, end in windows), max(MAX_COPIES * length, MIN_COPY_CHARS)
    if total > most:
        raise ChunkError(TOO_MANY, f"{len(windows)} passages would hold {total} characters in all, more than {most} "
                                   f"({MAX_COPIES} times the text's length, or {MIN_COPY_CHARS} characters for a "
                                   "short text); choose a smaller overlap or a smaller size")


def _windows(units: Sequence[Span], size: int, overlap: int) -> list[Span]:
    """The span of each window of ``size`` units, each sharing ``overlap`` units with the next."""
    out: list[Span] = []
    i = 0
    while i < len(units):
        j = min(i + size, len(units))
        out.append((units[i][0], units[j - 1][1]))
        if j == len(units):
            break
        i += size - overlap
    return out


def _char_windows(text: str, size: int, overlap: int) -> list[Span]:
    out = []
    for i in range(0, len(text), size - overlap):
        span = _trimmed(text, i, min(i + size, len(text)))
        if span:
            out.append(span)
        if i + size >= len(text):
            break
    return out


def span_problems(document: Document, *, start: int, end: int, text: str = "", id: str = "",
                  source: Source | None = None, document_sha256: str = "") -> list[str]:
    """What is wrong with a citation of ``start:end`` in ``document``, in words; ``[]`` when its text, id, source and
    document hash (each one that is given) are exactly what the document holds there."""
    size = len(document.text)
    if not text and not id:
        return ["nothing to check: the citation gives neither its text nor its id"]
    if not 0 <= start < end <= size:
        return [f"{start}:{end} is outside the {size}-character text"]
    out = []
    held = document.text[start:end]
    if text and held != text:
        out.append(f"the text at {start}:{end} is {held[:80]!r}, not the cited {text[:80]!r}")
    if id and id != passage_id(document.source, document.sha256, start, end):
        out.append(f"the id is not this document's id for {start}:{end}")
    if source is not None and (source.uri, source.sha256) != (document.source.uri, document.source.sha256):
        out.append(f"it cites another source ({source.uri}, or other bytes)")
    if document_sha256 and document_sha256 != document.sha256:
        out.append("it was cut from another version of the text")
    return out


def found_at(document: Document, text: str) -> list[int] | None:
    """Where ``text`` first occurs in the document, as ``[start, end]``, or ``None``."""
    at = document.text.find(text) if text else -1
    return [at, at + len(text)] if at >= 0 else None


def _own_problems(document: Document, passages: list[Passage], units: Sequence[Span] | None, size: int) -> list[str]:
    starts = [u[0] for u in units] if units is not None else []
    ends = [u[1] for u in units] if units is not None else []
    out = []
    for p in passages:
        found = passage_problems(p, document)
        if units is not None:
            first, last = bisect.bisect_left(starts, p.start), bisect.bisect_right(ends, p.end)
            if first == len(starts) or starts[first] != p.start or last == 0 or ends[last - 1] != p.end:
                found.append("it does not start and end on a unit's edge")
            elif last - first > size:
                found.append(f"it holds {last - first} units, more than {size}")
        out += [f"{p.start}:{p.end}: {problem}" for problem in found]
    return out


def chunk(document: Document, *, unit: Unit = "words", size: int = 200, overlap: int | None = None,
          tokenizer: Tokenizer | None = None, max_passages: int = MAX_PASSAGES) -> list[Passage]:
    """``document`` cut into passages of at most ``size`` units, ``overlap`` (by default a tenth of ``size``) shared
    between neighbours; raises :class:`ChunkError` with its code for anything it cannot cut honestly."""
    text = document.text
    overlap = size // 10 if overlap is None else overlap
    if not text.strip():
        raise ChunkError(NO_TEXT, "there is no text to cut into passages")
    if size < 1 or not 0 <= overlap < size:
        raise ChunkError(CHUNK_INPUT, f"the overlap ({overlap}) must be at least 0 and smaller than the size "
                                      f"({size}), or no window moves on")
    if unit == "chars":
        _count(len(text), size, overlap, max_passages)
        units, windows = None, _char_windows(text, size, overlap)
    else:
        units = spans(text, unit, tokenizer)
        if not units:
            raise ChunkError(NO_TEXT, f"the text has no {unit} to cut into passages")
        _count(len(units), size, overlap, max_passages)
        windows = _windows(units, size, overlap)
    _total(windows, len(text))  # before any window is copied out of the text
    passages = []
    for start, end in windows:
        if not 0 <= start < end <= len(text):
            raise ChunkError(SPAN_MISMATCH, f"the chunker cut {start}:{end}, which is not a span of the "
                                            f"{len(text)}-character text")
        passages.append(document.passage(start, end))
    problems = _own_problems(document, passages, units, size)
    if problems:
        raise ChunkError(SPAN_MISMATCH, "the chunker cut passages that are not exact spans of the text, so none is "
                                        "returned: " + "; ".join(problems[:5]))
    return passages


__all__ = ["CHUNK_INPUT", "MAX_COPIES", "MAX_PASSAGES", "MIN_COPY_CHARS", "NO_TEXT", "SPAN_MISMATCH", "TOKENIZER",
           "TOO_MANY", "ChunkError", "Span", "Tokenizer", "Unit", "chunk", "found_at", "span_problems", "spans"]