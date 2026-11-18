# SPDX-License-Identifier: Apache-2.0
"""An advisory reading beside lexical grounding; no status or gate is changed here."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.core import semantic
from g6.grounding.claims import _negated, _numbers, check_claim
from g6.grounding.retrieval import Passage

READINGS = ("supports", "contradicts", "unclear")


def _whitespace(text: str) -> str:
    return " ".join(text.split())


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: list[str] = Field(min_length=1, max_length=2, description=(
        '["supports" | "contradicts" | "unclear", "one offered source sentence verbatim"], '
        'or ["unclear"] when no sentence applies'))

    @field_validator("value")
    @classmethod
    def reading_and_sentence(cls, value):
        if value[0] not in READINGS or len(value) == 1 and value[0] != "unclear":
            raise ValueError("a reading needs one source sentence, or unclear without a quote")
        return [value[0], _whitespace(value[1])] if len(value) == 2 else value


def model_reading(claim: str, sources: dict[str, str]) -> dict:
    """Choose meaning and a whole sentence through the shared bounded, guarded reader.

    A quote is an offered candidate itself. This prevents invented, clipped or
    case/punctuation-rewritten evidence; only whitespace may be normalised.
    """
    sentences: dict[str, str] = {}
    for source, text in sources.items():
        # Normalise line wrapping before splitting so a newline is not a new sentence.
        for sentence in re.split(r"(?<=[.!?])\s+", _whitespace(text)):
            if sentence:
                sentences.setdefault(sentence, source)
    candidates = tuple(semantic.Candidate(value, "reading: " + value) for value in READINGS) + tuple(
        semantic.Candidate(sentence, "source sentence: " + source)
        for sentence, source in sentences.items() if sentence not in READINGS)
    reading = semantic.read(semantic.Question(
        kind="grounding", text=json.dumps({"claim": claim, "sources": sources}, ensure_ascii=True),
        schema=_Reply, floor=lambda: ["unclear"], combine="choose", candidates=candidates))
    value = reading.value
    quote = value[1] if len(value) == 2 else ""
    source = sentences.get(quote)
    found = bool(quote and source is not None and quote in _whitespace(sources[source]))
    lexical = check_claim(claim, [Passage(quote, source)]).to_dict() if found else None
    verdict = value[0] if found else "unclear"
    reason = reading.reason
    if verdict == "supports" and (_negated(claim) != _negated(quote) or not _numbers(claim) <= _numbers(quote)):
        verdict, reason = "unclear", "NUMBER_OR_NEGATION"
    return {"reading": verdict, "quote": quote, "quote_found": found, "lexical_on_quote": lexical,
            "reason": reason, "source": source or ""}
