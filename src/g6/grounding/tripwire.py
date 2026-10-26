# SPDX-License-Identifier: Apache-2.0
"""A tripwire for source text that reads like instructions to an AI model (audit 014 GRD-016).

It is a short list of phrasings, matched with word boundaries on text that is NFKC-normalised (full-width letters
become plain ones), stripped of invisible format characters and lower-cased. It catches the common phrasings and
those simple disguises. It misses rewordings, other languages and anything subtler, so a hit means "a person should
look at this source", not "this source is hostile", and no hit establishes nothing.
"""
from __future__ import annotations

import re
import unicodedata

_PHRASINGS = {
    "tells the model to ignore its instructions":
        r"\b(?:ignore|disregard|forget|override)\b(?:\W+\w+){0,4}?\W+(?:instructions?|prompts?)\b",
    "gives instructions to the model": r"\binstructions?\s+(?:for|to)\s+(?:the\s+|any\s+)?(?:ai|assistant|model|llm|chatbot)\b",
    "tells the model to take a new role": r"\b(?:from\s+now\s+on,?\s+you|pretend\s+(?:to\s+be|you\s+are))\b",
    "names the model's hidden prompt": r"\b(?:system|developer|hidden)\s+prompt\b",
    "tells the model to keep something from the user":
        r"\b(?:do\s+not|don't|never)\s+(?:tell|inform|show|reveal\s+to|mention\s+(?:this\s+)?to)\s+(?:the\s+)?user\b",
}
_COMPILED = {name: re.compile(pattern) for name, pattern in _PHRASINGS.items()}


def _plain(text: str) -> str:
    text = "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")
    return " ".join(text.lower().split())


def instruction_signals(text: str) -> list[str]:
    """The tripwire's names for the instruction-like phrasings in ``text``; empty when none matched."""
    plain = _plain(text)
    return [name for name, pattern in _COMPILED.items() if pattern.search(plain)]
