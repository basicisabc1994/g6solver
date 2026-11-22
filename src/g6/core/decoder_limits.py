# SPDX-License-Identifier: Apache-2.0
"""A JSON record past the decoder's limits, or not in its expected shape: unreadable, never passed over for another.

``json.loads`` raises ``RecursionError`` on a record nested too deeply, ``MemoryError`` on one too large, and a plain
``ValueError`` on an integer longer than the interpreter converts (``sys.get_int_max_str_digits``). Most of G6's readers
turn these into ``json.JSONDecodeError``, so that the module's own refusal of malformed input handles them. A reader
whose malformed-record handler passes over the record and goes on to another (the result a program printed last, the
latest audit in a log, the first object in a model's reply, a stored list a gate reads) must not: the record it could
not read may be the one that decides, and passing over it lets an earlier, older or nested one decide in its place.
Such a reader decodes through :func:`decode`. Its :class:`UndecodableRecord` is a ``ValueError`` but not a
``json.JSONDecodeError``, so the passing-over handler does not catch it, and the reader's caller refuses or abstains.

A record that decodes is not yet readable: :func:`shaped` reads it only when the fields a decision takes are present
with exactly their types (a bool is ``True`` or ``False``, never ``"false"`` or ``0``; an int or a float is never a
bool). A record that is not in its shape is as unknown as one that does not decode.
"""
from __future__ import annotations

import json
import sys
from typing import Any, NamedTuple


class UndecodableRecord(ValueError):
    """A JSON record past the decoder's limits (nested too deeply, too large, or an integer too long to convert): no
    other record stands in for it."""


class _HookRefusal(Exception):
    """A decoder hook's deliberate refusal, retained outside the native decoder's failure translation."""

    def __init__(self, error: ValueError) -> None:
        self.error = error


def _hook(function: Any) -> Any:
    def call(*args: Any) -> Any:
        try:
            return function(*args)
        except ValueError as exc:
            raise _HookRefusal(exc) from None
    return call


def _integer(digits: str) -> int:
    limit = sys.get_int_max_str_digits()
    if limit and len(digits.lstrip("-")) > limit:
        raise UndecodableRecord("a JSON record holds an integer too long to convert")
    return int(digits)


def decode(text: str | bytes | bytearray, *, partial: bool = False, start: int = 0, **options: Any) -> Any:
    """``json.loads(text)``; a record past the decoder's limits raises :class:`UndecodableRecord`, and a malformed one
    ``json.JSONDecodeError`` as before. Only failures inside the decoder are caught here, never a later
    ``ValueError``. Decoder hooks retain their site's policy. With ``partial``, return ``(value, end)`` from the
    object beginning at ``start``, preserving the caller's existing raw-decoder semantics."""
    try:
        options = {name: _hook(value) if name in {"parse_float", "parse_constant", "object_hook", "object_pairs_hook"}
                   and callable(value) else value for name, value in options.items()}
        if partial:
            return json.JSONDecoder(parse_int=_integer, **options).raw_decode(text, start)
        return json.loads(text, parse_int=_integer, **options)
    except json.JSONDecodeError:
        raise
    except _HookRefusal as exc:
        raise exc.error from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise UndecodableRecord(f"a JSON record cannot be decoded ({type(exc).__name__})") from None


def json_value(text: str | bytes | bytearray, **options: Any) -> Any:
    """Decode through :func:`decode` for a site's ordinary malformed-JSON refusal.

    Use ``decode`` directly where passing over an unreadable record could let another record decide. This adapter
    retains the existing ``JSONDecodeError`` handlers at sites that refuse or read the input as unknown.
    """
    try:
        return decode(text, **options)
    except UndecodableRecord:
        raise json.JSONDecodeError("a JSON record cannot be decoded", "", 0) from None


class ListOf(NamedTuple):
    """A list whose every item is of ``item`` (a type or a tuple of types, read as :func:`shaped` reads them)."""

    item: Any


def _is(value: Any, kind: Any) -> bool:
    if isinstance(kind, ListOf):
        return isinstance(value, list) and all(_is(one, kind.item) for one in value)
    if isinstance(kind, tuple):
        return any(_is(value, one) for one in kind)
    if kind is None or kind is type(None):
        return value is None
    if kind is bool:
        return isinstance(value, bool)
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, kind)


def shaped(record: Any, required: dict[str, Any], optional: dict[str, Any] | None = None) -> bool:
    """Whether ``record`` is a dict holding every ``required`` field, each ``required`` and present ``optional`` field
    of exactly its type: ``bool`` only ``True`` or ``False``; ``int`` never a bool; ``float`` an int or a float, never a
    bool; ``None`` only ``None``; a tuple any of its kinds; :class:`ListOf` a list of that kind. Other fields are not
    read."""
    return (isinstance(record, dict)
            and all(name in record and _is(record[name], kind) for name, kind in required.items())
            and all(name not in record or _is(record[name], kind) for name, kind in (optional or {}).items()))


__all__ = ["ListOf", "UndecodableRecord", "decode", "json_value", "shaped"]
