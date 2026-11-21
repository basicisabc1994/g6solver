# SPDX-License-Identifier: Apache-2.0
"""A JSON record past the decoder's limits: unreadable, and never passed over for another record.

``json.loads`` raises ``RecursionError`` on a record nested too deeply and ``MemoryError`` on one too large. Most of
G6's readers turn these into ``json.JSONDecodeError``, so that the module's own refusal of malformed input handles them.
A reader whose malformed-record handler passes over the record and goes on to another (the result a program printed
last, the latest audit in a log, the first object in a model's reply, a stored list a gate reads) must not: the record
it could not read may be the one that decides, and passing over it lets an earlier, older or nested one decide in its
place. Such a reader decodes through :func:`decode`. Its :class:`UndecodableRecord` is a ``ValueError`` but not a
``json.JSONDecodeError``, so the passing-over handler does not catch it, and the reader's caller refuses or abstains.
"""
from __future__ import annotations

import json
from typing import Any


class UndecodableRecord(ValueError):
    """A JSON record past the decoder's limits (nested too deeply, or too large): no other record stands in for it."""


def decode(text: str | bytes | bytearray) -> Any:
    """``json.loads(text)``; a record past the decoder's limits raises :class:`UndecodableRecord`, and a malformed one
    ``json.JSONDecodeError`` as before."""
    try:
        return json.loads(text)
    except (RecursionError, MemoryError):
        raise UndecodableRecord("a JSON record exceeds decoder limits") from None


__all__ = ["UndecodableRecord", "decode"]
