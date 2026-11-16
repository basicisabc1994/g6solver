# SPDX-License-Identifier: Apache-2.0
"""Bounded JSON decoding for text G6 did not write: a model's reply, a client's ``params_json``, a child's line.

Before anything is decoded the text must be a string of at most ``max_chars`` characters whose arrays and objects
nest at most ``max_depth`` deep (default :data:`MAX_DEPTH`; brackets inside JSON strings do not count). Every failure,
the decoder's own included (``RecursionError``, ``ValueError``, ``TypeError``, ``MemoryError``), is one
:class:`Undecodable`, so a caller handles one refusal and never an exception that escapes its reply.
"""
from __future__ import annotations

import json
from typing import Any

MAX_DEPTH = 64


class Undecodable(ValueError):
    """The text was not decoded: too long, nested too deep, not JSON, or the decoder failed."""


def depth(text: str) -> int:
    """How deep arrays and objects nest in ``text``, outside JSON strings (an upper bound for prose around JSON)."""
    deepest = level = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            level += 1
            deepest = max(deepest, level)
        elif ch in "]}":
            level = max(level - 1, 0)
    return deepest


def _checked(text: Any, max_chars: int, max_depth: int) -> str:
    if not isinstance(text, str):
        raise Undecodable(f"expected text, got {type(text).__name__}")
    if len(text) > max_chars:
        raise Undecodable(f"the text holds {len(text)} characters, more than the {max_chars} allowed")
    if depth(text) > max_depth:
        raise Undecodable(f"the text nests deeper than {max_depth} levels")
    return text


def loads(text: Any, *, max_chars: int, max_depth: int = MAX_DEPTH) -> Any:
    """``text``, which must be one JSON value and nothing else, decoded within the bounds; :class:`Undecodable`
    otherwise."""
    checked = _checked(text, max_chars, max_depth)
    try:
        return json.loads(checked)
    except json.JSONDecodeError as exc:
        raise Undecodable(f"not JSON: {exc}") from None
    except (RecursionError, ValueError, TypeError, MemoryError) as exc:
        raise Undecodable(f"not decoded: {type(exc).__name__}") from None


def extract(text: Any, *, max_chars: int, max_depth: int = MAX_DEPTH) -> Any:
    """The first JSON object or array in a model's reply (``g6.core.llm.extract_json``: code fences and prose are
    tolerated), decoded within the bounds; :class:`Undecodable` otherwise."""
    from g6.core.llm import LLMError, extract_json

    checked = _checked(text, max_chars, max_depth)
    try:
        return extract_json(checked)
    except LLMError as exc:
        raise Undecodable(f"no JSON value: {exc}") from None
    except (RecursionError, ValueError, TypeError, MemoryError) as exc:
        raise Undecodable(f"not decoded: {type(exc).__name__}") from None


__all__ = ["MAX_DEPTH", "Undecodable", "depth", "extract", "loads"]
