# SPDX-License-Identifier: Apache-2.0
"""Bounded JSON decoding for text G6 did not write: a model's reply, a client's ``params_json``, a child's line.

Before anything is decoded the text must be a string of at most ``max_chars`` characters, and the JSON value decoded
must nest at most ``max_depth`` deep (default :data:`MAX_DEPTH`; brackets inside JSON strings do not count): for
:func:`loads` the whole text, for :func:`extract` exactly the span that is decoded, found first. A number that is not
finite (``NaN``, ``Infinity``, ``1e999``) is refused. Every failure, the decoder's own included (``RecursionError``,
``ValueError``, ``TypeError``, ``MemoryError``), is one :class:`Undecodable`, so a caller handles one refusal and never
an exception that escapes its reply.
"""
from __future__ import annotations

from g6.core.decoder_limits import UndecodableRecord, decode as _decode_json

import json
import math
import re
from typing import Any

MAX_DEPTH = 64
#: The most string and bracket tokens :func:`extract` reads while it looks for the span to decode.
MAX_WORK = 200_000
#: A JSON string, a lone quote (a string that never closes), or a bracket.
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|["\[\]{}]', re.DOTALL)


class Undecodable(ValueError):
    """The text was not decoded: too long, nested too deep, not JSON, not finite, or the decoder failed."""


def _constant(name: str) -> Any:
    raise Undecodable(f"{name} is not a finite number")


def _float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise Undecodable("a number is too large to be a finite float")
    return value


def depth(text: str) -> int:
    """How deep arrays and objects nest in ``text``, outside JSON strings (exact for one JSON value)."""
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


def _text(text: Any, max_chars: int) -> str:
    if not isinstance(text, str):
        raise Undecodable(f"expected text, got {type(text).__name__}")
    if len(text) > max_chars:
        raise Undecodable(f"the text holds {len(text)} characters, more than the {max_chars} allowed")
    return text


def _decode(text: str) -> tuple[Any, int]:
    try:
        return _decode_json(text, partial=True, parse_constant=_constant, parse_float=_float)
    except Undecodable:
        raise
    except UndecodableRecord as exc:
        raise Undecodable(str(exc)) from None
    except json.JSONDecodeError as exc:
        raise Undecodable(f"not JSON: {exc}") from None
    except (RecursionError, ValueError, TypeError, MemoryError) as exc:
        raise Undecodable(f"not decoded: {type(exc).__name__}") from None


def loads(text: Any, *, max_chars: int, max_depth: int = MAX_DEPTH) -> Any:
    """``text``, which must be one JSON value and nothing else, decoded within the bounds; :class:`Undecodable`
    otherwise."""
    checked = _text(text, max_chars)
    if depth(checked) > max_depth:
        raise Undecodable(f"the text nests deeper than {max_depth} levels")
    try:
        return _decode_json(checked, parse_constant=_constant, parse_float=_float)
    except Undecodable:
        raise
    except UndecodableRecord as exc:
        raise Undecodable(str(exc)) from None
    except json.JSONDecodeError as exc:
        raise Undecodable(f"not JSON: {exc}") from None
    except (RecursionError, ValueError, TypeError, MemoryError) as exc:
        raise Undecodable(f"not decoded: {type(exc).__name__}") from None


def _span(text: str, start: int, max_depth: int, work: list[int]) -> int:
    """Where the array or object that opens at ``text[start]`` closes, or -1 when it never does; refused when it nests
    deeper than ``max_depth`` or the search reads more than :data:`MAX_WORK` tokens."""
    level = 0
    for token in _TOKEN.finditer(text, start):
        work[0] -= 1
        if work[0] < 0:
            raise Undecodable(f"no JSON value found within {MAX_WORK} tokens of the text")
        found = token.group()
        if found == '"':
            return -1                       # a string that never closes
        if found[0] == '"':
            continue
        if found in "[{":
            level += 1
            if level > max_depth:
                raise Undecodable(f"the JSON value nests deeper than {max_depth} levels")
        else:
            level -= 1
            if level == 0:
                return token.end()
    return -1


def extract(text: Any, *, max_chars: int, max_depth: int = MAX_DEPTH) -> Any:
    """The first JSON object or array in a model's reply, as ``g6.core.llm.extract_json`` finds it (code fences and
    prose are tolerated): its span is found first and its depth checked before that span alone is decoded;
    :class:`Undecodable` otherwise."""
    checked = _text(text, max_chars)
    work = [MAX_WORK]
    for opening in re.finditer(r"[\[{]", checked):
        end = _span(checked, opening.start(), max_depth, work)
        if end < 0:
            continue
        try:
            value, used = _decode(checked[opening.start():end])
        except Undecodable as exc:
            if str(exc).startswith("not JSON"):
                continue                    # not JSON here: look further, as extract_json does
            raise
        if used == end - opening.start():
            return value
    raise Undecodable("no JSON value: no JSON object or array was found in the reply")


__all__ = ["MAX_DEPTH", "MAX_WORK", "Undecodable", "depth", "extract", "loads"]
