# SPDX-License-Identifier: Apache-2.0
"""Redacted views: the only form in which source text and source metadata reach a model-bound reply (design 12.6).

``_``-prefixed, so it never registers as a component. :func:`view_of` runs ``g6.context.redact``'s masks over a text
(secrets, e-mail addresses, home and temporary folder roots, hosts) and keeps a span map from the view back to the
canonical text: each span ``(view_start, view_end, canonical_start, canonical_end)`` is a run the masks left untouched,
so ``view.text[view_start:view_end]`` is exactly the canonical text at ``canonical_start:canonical_end``. What lies
between two spans was masked. The raw bytes stay behind an authorised fetch. :func:`label_view` is the same masking for
a short string (a path, a title, a reason) with no map.

The masks are pattern-based: they hide what they recognise. A view is not a confidentiality guarantee.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from pydantic import BaseModel, ConfigDict

from g6.context.redact import mask_emails, mask_hosts, mask_paths, scrub


class View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    masked: int
    spans: tuple[tuple[int, int, int, int], ...]


def label_view(text: str) -> str:
    """``text`` with every mask applied; for titles, paths, reasons and every other model-bound string."""
    return mask_hosts(mask_emails(mask_paths(scrub(text)))) if text else text


def view_of(text: str, *, canonical_start: int = 0) -> View:
    """The redacted view of ``text`` and its map back to the canonical text, whose first character is at
    ``canonical_start`` of its document."""
    shown = label_view(text)
    if shown == text:
        spans = ((0, len(text), canonical_start, canonical_start + len(text)),) if text else ()
        return View(text=shown, masked=0, spans=spans)
    blocks = SequenceMatcher(None, text, shown, autojunk=False).get_matching_blocks()
    spans = tuple((b.b, b.b + b.size, canonical_start + b.a, canonical_start + b.a + b.size)
                  for b in blocks if b.size)
    covered = sum(end - start for start, end, _, _ in spans)
    gaps = 0
    cursor = 0
    for start, end, _, _ in spans:
        gaps += start > cursor
        cursor = end
    gaps += cursor < len(shown)
    return View(text=shown, masked=max(gaps, 1 if covered < len(text) else 0), spans=spans)


__all__ = ["View", "label_view", "view_of"]
