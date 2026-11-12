# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Back-translation with limits (G6 0.3 design, section 15.3 check 4): the statement in words, beside its bytes.

A ``Rendering`` is built from the checked statement and nothing else: no task text, no question, no model's
paraphrase reaches a renderer (each takes the statement alone). The words are ASCII, one labelled line per part, and
are always shown beside the formal text and its digest. A renderer is exhaustive over its fragment's grammar; a
statement outside the fragment, or too large to show whole, is ``Unrenderable`` and stays unconfirmed. It is never
shown in part.

The words say what the statement says. Whether that is what the person meant is theirs to decide: a rendering a
person approves records their scoped acceptance, not established fidelity of the translation.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from g6.core.ledger import canonical
from g6.harness.formalisation.statement import FormalStatement

#: The most a dialog is asked to show: beyond either, the statement is refused whole.
MAX_WORDS_CHARS = 3_000
MAX_FORMAL_CHARS = 2_000


class Unrenderable(ValueError):
    """No supported renderer can show this statement whole; the message says why."""


@dataclass(frozen=True)
class Rendering:
    words: str
    formal_text: str
    statement_sha256: str
    definitions_sha256: str
    renderer: str

    def record(self) -> bytes:
        return canonical({
            "words": self.words, "formal_text": self.formal_text, "statement_sha256": self.statement_sha256,
            "definitions_sha256": self.definitions_sha256, "renderer": self.renderer}).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.record()).hexdigest()

    def to_dict(self) -> dict:
        return {"words": self.words, "formal_text": self.formal_text, "statement_sha256": self.statement_sha256,
                "definitions_sha256": self.definitions_sha256, "renderer": self.renderer, "sha256": self.sha256}


#: Fragment id -> its renderer, which takes the statement alone.
RENDERERS: dict[str, Callable[[FormalStatement], Rendering]] = {}


def finished(statement: FormalStatement, lines: list[tuple[str, str]], renderer: str) -> Rendering:
    """The rendering of labelled lines, refused when a line is not one ASCII line or the whole is too large."""
    for label, content in lines:
        text = f"{label}: {content}"
        if not text.isascii() or not text.isprintable():
            raise Unrenderable(f"the line {label!r} holds a character outside printable ASCII")
    words = "\n".join(f"{label}: {content}" for label, content in lines)
    if len(words) > MAX_WORDS_CHARS or len(statement.text) > MAX_FORMAL_CHARS:
        raise Unrenderable(f"the statement is too large to show whole ({len(words)} characters of words, "
                           f"{len(statement.text)} of formal text); it is never shown in part")
    return Rendering(words=words, formal_text=statement.text, statement_sha256=statement.sha256,
                     definitions_sha256=statement.definitions_sha256, renderer=renderer)


def render(statement: FormalStatement) -> Rendering:
    """The statement's rendering by its fragment's renderer, or ``Unrenderable``."""
    made = RENDERERS.get(statement.fragment)
    if made is None:
        raise Unrenderable(f"no renderer is registered for the fragment {statement.fragment!r}")
    return made(statement)


__all__ = ["MAX_FORMAL_CHARS", "MAX_WORDS_CHARS", "RENDERERS", "Rendering", "Unrenderable", "finished", "render"]