# SPDX-License-Identifier: Apache-2.0
"""The bridge's boundary: one data model for everything a caller or a collaborator hands it, and the records it honours.

Every value that enters the bridge from a caller or a collaborator (a tool's answer, a model's reply, evidence and
artefact mappings, checker manifests, identities, selections) is converted once, where it enters, by :func:`plain`
into plain JSON data: objects with text keys, arrays, text, finite numbers, booleans and null. The conversion is a
round trip through the bridge's own canonical encoder (:func:`canonical_json`), and it refuses (:class:`NotPlainData`,
under ``codes.INPUT``, with the path) anything that does not survive it unchanged: a key that is not text, a subclass of
any type, a set, any other object, a number that is not finite. An identity is text (:func:`text`). Code after the
boundary sees only that data, so the claim rule (``codes.claims``) walks JSON alone.

A record (:func:`record`) is the bridge's own frozen dataclass, and each of its fields is taken where the record is
made, by the kind its annotation declares: data through :func:`plain`, held read-only (``codes.FrozenMap`` and tuples);
text as text; bytes only in a field declared ``bytes``, which the record fills itself; another record only of its
declared type; code (a callable) by reference. :func:`snapshot` takes a caller's value (data, and records in lists and
mappings), making each record again so it shares nothing; :func:`fresh` is the same copy, handed to a collaborator; and
what a collaborator returns is used only through :func:`returned`. ``tests/cognitive_bridge/test_boundary_inventory.py``
lists every entry point, record field, collaborator's answer and tool's answer from the code.

The bridge honours only records it made: each maker records the digest of what it returns (:func:`digest`, over every
field) in the instance that made it (:func:`issue`), and execution and assessment take a compilation, decision, dossier
or report only when the instance that made it issued it (:func:`issued`). The record is held in memory: after a restart
nothing is honoured.
"""
from __future__ import annotations

import collections.abc
import dataclasses
import hashlib
import json
import math
import types
import typing
import weakref
from typing import Any, Callable, TypeVar

from .codes import ARRAYS, DEPTH, INPUT, LEAVES, OBJECTS, FrozenMap, unclaimed

T = TypeVar("T")


def canonical_json(value: Any) -> bytes:
    """Reject NaNs, objects, and noncanonical numerics instead of stringifying them."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


class NotPlainData(TypeError, ValueError):
    """A value refused where it enters the bridge: not plain JSON data, or not of the kind its place declares. It
    carries a registered code (``codes.INPUT``) and the path of what was refused."""

    code = INPUT

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"[{INPUT}] {path}: {why}", path)

    @property
    def path(self) -> str:
        return self.args[1]

    def __str__(self) -> str:
        return self.args[0]


def _named(value: Any) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _not_json(value: Any, path: str, depth: int) -> tuple[str, str] | None:
    """The first place in ``value`` that is not JSON data (``codes.LEAVES``, ``codes.OBJECTS`` with text keys,
    ``codes.ARRAYS``, by exact type: the kinds the claim rule reads), and why; None when there is none."""
    if depth > DEPTH:
        return path, f"nested deeper than {DEPTH} levels"
    kind = type(value)
    if kind in LEAVES:
        return None if kind is not float or math.isfinite(value) else (path, "a number that is not finite")
    if kind in OBJECTS:
        for key, item in value.items():
            if type(key) is not str:
                return f"{path} (a key)", f"a {_named(key)} key is not text"
            found = _not_json(item, f"{path}.{key}", depth + 1)
            if found:
                return found
        return None
    if kind in ARRAYS:
        for index, item in enumerate(value):
            found = _not_json(item, f"{path}[{index}]", depth + 1)
            if found:
                return found
        return None
    return path, f"a {_named(value)} is not JSON data"


def _same(value: Any, out: Any) -> bool:
    """Whether ``out``, the canonical encoder's round trip of ``value``, is ``value`` unchanged, type for type (an
    object read back as a dict, an array as a list)."""
    kind = type(value)
    if kind in OBJECTS:
        return type(out) is dict and out.keys() == value.keys() and all(_same(value[k], out[k]) for k in value)
    if kind in ARRAYS:
        return type(out) is list and len(out) == len(value) and all(_same(a, b) for a, b in zip(value, out))
    return type(out) is kind and out == value


def plain(value: Any, *, where: str) -> Any:
    """``value`` as plain JSON data, the one conversion of what enters the bridge: a round trip through
    :func:`canonical_json`, refused (:class:`NotPlainData`, naming ``where`` and the path) unless ``value`` survives it
    unchanged. A new value, which shares nothing with ``value``."""
    found = _not_json(value, where, 0)
    if found:
        raise NotPlainData(*found)
    out = json.loads(canonical_json(value))
    if not _same(value, out):
        raise NotPlainData(where, "it does not survive the bridge's canonical encoder unchanged")
    return out


def text(value: Any, *, where: str) -> str:
    """An identity, a name or a question as the bridge takes it: text (:func:`plain`); anything else is refused."""
    if type(value) is not str:
        raise NotPlainData(where, f"a {_named(value)} is not text")
    return plain(value, where=where)


def _frozen(value: Any) -> Any:
    """Plain JSON data, read-only: an object a :class:`FrozenMap`, an array a tuple."""
    if type(value) is dict:
        return FrozenMap((key, _frozen(item)) for key, item in value.items())
    if type(value) is list:
        return tuple(_frozen(item) for item in value)
    return value


# --- Records ----------------------------------------------------------------------------------------------------------

#: Every record type :func:`record` made, by identity: a class that only says it is one (sets the mark itself) is not.
_RECORDS: dict[int, type] = {}


def _record_type(kind: Any) -> bool:
    return _RECORDS.get(id(kind)) is kind


def _is_record(value: Any) -> bool:
    """Whether ``value`` is a bridge record: its own class, not a subclass, was made by :func:`record`."""
    return _record_type(type(value))


def _kind(hint: Any) -> tuple[Any, ...]:
    """What a record field annotated ``hint`` holds: text, bytes, a tuple of bytes, a record of one type (or None), a
    tuple of records of one type, code, or data (everything else)."""
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if hint is str:
        return ("text",)
    if hint is bytes:
        return ("bytes",)
    if origin is tuple and args == (bytes, Ellipsis):
        return ("bytes*",)
    if _record_type(hint):
        return ("record", hint, False)
    if origin in (typing.Union, types.UnionType) and len(args) == 2 and type(None) in args:
        other = args[0] if args[1] is type(None) else args[1]
        if _record_type(other):
            return ("record", other, True)
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis and _record_type(args[0]):
        return ("records", args[0])
    if origin is collections.abc.Callable or getattr(hint, "_is_protocol", False):
        return ("code",)
    return ("data",)


def _kinds(cls: type) -> dict[str, tuple[Any, ...]]:
    """Each field of the record type ``cls`` with what it holds (:func:`_kind`), read from its annotations once."""
    kinds = cls.__dict__.get("__record_kinds__")
    if kinds is None:
        try:
            hints = typing.get_type_hints(cls)
        except Exception:  # annotations that cannot be resolved where the type was made: its fields are data
            hints = {}
        kinds = {field.name: _kind(hints.get(field.name, Any)) for field in dataclasses.fields(cls)}
        cls.__record_kinds__ = kinds
    return kinds


def _bytes(value: Any, where: str) -> bytes:
    if type(value) not in (bytes, bytearray, memoryview):
        raise NotPlainData(where, f"a {_named(value)} is not bytes")
    return bytes(value)


def _field(kind: tuple[Any, ...], value: Any, where: str) -> Any:
    """``value`` taken into a record field that holds ``kind``, or refused."""
    name = kind[0]
    if name == "data":
        return _frozen(plain(value, where=where))
    if name == "text":
        return text(value, where=where)
    if name == "bytes":
        return _bytes(value, where)
    if name == "bytes*":
        if type(value) not in (list, tuple):
            raise NotPlainData(where, f"a {_named(value)} is not a list of bytes")
        return tuple(_bytes(item, f"{where}[{index}]") for index, item in enumerate(value))
    if name == "record":
        if type(value) is kind[1] or (value is None and kind[2]):
            return value
        raise NotPlainData(where, f"a {_named(value)} is not a {kind[1].__name__}")
    if name == "records":
        if type(value) not in (list, tuple) or any(type(item) is not kind[1] for item in value):
            raise NotPlainData(where, f"a {_named(value)} is not a list of {kind[1].__name__}")
        return tuple(value)
    if not callable(value):
        raise NotPlainData(where, f"a {_named(value)} is not code")
    return value


def record(cls: type[T]) -> type[T]:
    """A frozen dataclass whose every field is taken where it is made, by the kind its annotation declares
    (:func:`_field`), before the class's own ``__post_init__`` checks it, so what is checked is what is kept."""
    own = cls.__dict__.get("__post_init__")

    def __post_init__(self: Any) -> None:
        kinds = _kinds(type(self))
        for field in dataclasses.fields(self):
            object.__setattr__(self, field.name, _field(kinds[field.name], getattr(self, field.name),
                                                        f"{type(self).__name__}.{field.name}"))
        if own is not None:
            own(self)

    cls.__post_init__ = __post_init__  # type: ignore[attr-defined]
    cls.__snapshot_record__ = True  # type: ignore[attr-defined]
    made = dataclasses.dataclass(frozen=True)(cls)
    _RECORDS[id(made)] = made
    return made


def _renewed(value: Any) -> Any:
    """A record made again through its boundary: every field taken anew, and every record in it made again."""
    kinds, changes = _kinds(type(value)), {}
    for field in dataclasses.fields(value):
        if not field.init:
            continue
        item, kind = getattr(value, field.name), kinds[field.name][0]
        if kind == "record" and _is_record(item):
            item = _renewed(item)
        elif kind == "records" and type(item) in (list, tuple):
            item = tuple(_renewed(x) if _is_record(x) else x for x in item)
        changes[field.name] = item
    return dataclasses.replace(value, **changes)


def _has_record(value: Any, depth: int) -> bool:
    if _is_record(value):
        return True
    if depth > DEPTH:
        return False
    kind = type(value)
    if kind in ARRAYS:
        return any(_has_record(item, depth + 1) for item in value)
    if kind in OBJECTS:
        return any(_has_record(item, depth + 1) for item in value.values())
    return False


def _taken(value: Any, where: str, depth: int) -> Any:
    """``value`` as the bridge holds it: a record made again; a list or a mapping that holds a record, item by item (a
    mapping's keys text); anything else plain JSON data (:func:`plain`), read-only."""
    if _is_record(value):
        return _renewed(value)
    kind = type(value)
    if kind in ARRAYS and _has_record(value, depth):
        return tuple(_taken(item, f"{where}[{index}]", depth + 1) for index, item in enumerate(value))
    if kind in OBJECTS and _has_record(value, depth):
        return FrozenMap((text(key, where=f"{where} (a key)"), _taken(item, f"{where}.{key}", depth + 1))
                         for key, item in value.items())
    return _frozen(plain(value, where=where))


def snapshot(value: Any, *, where: str = "a value") -> Any:
    """A caller's value as the bridge takes it, where it enters (``where`` names it in a refusal): plain JSON data,
    read-only, and bridge records (in lists and mappings too), each made again so it shares nothing with the caller's.
    Anything else is refused (:class:`NotPlainData`)."""
    return _taken(value, where, 0)


def fresh(value: Any, *, where: str = "a copy") -> Any:
    """What a collaborator is handed: a :func:`snapshot` that shares nothing with ``value``. A bytes value on its own
    (the content of a field a record declares as bytes, handed to a checker) is handed on whole."""
    return value if type(value) is bytes else _taken(value, where, 0)


def view(value: Any) -> Any:
    """``value`` as JSON, for the claim rule and for a digest: a record as an object of its fields, bytes as their
    digest and length, a list or a mapping item by item; anything else as it is, for the rule to name."""
    if _is_record(value):
        return {field.name: view(getattr(value, field.name)) for field in dataclasses.fields(value)}
    kind = type(value)
    if kind is bytes:
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if kind in ARRAYS:
        return [view(item) for item in value]
    if kind in OBJECTS:
        return {key: view(item) for key, item in value.items()}
    return value


def returned(value: Any, check: Callable[..., T], *against: Any) -> T:
    """What a collaborator returned, as the bridge uses it: a :func:`snapshot` taken the moment it returns (plain data
    and bridge records, made again, else refused), then ``check(copy, *against)``, which raises, or returns what is
    used from then on. Nothing else of ``value`` is read. The copy, and what the check makes of it, claim no state at
    any depth (``codes.unclaimed``), else ``ValueError``."""
    copy = snapshot(value, where=f"an answer taken by {check.__name__}")
    unclaimed(view(copy), where=f"an answer taken by {check.__name__}")
    out = check(copy, *against)
    unclaimed(view(out), where=f"what {check.__name__} made of an answer")
    return out


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("a model's reply repeats a key")
    return dict(pairs)


def json_reply(reply: Any, cap: int) -> Any:
    """A :func:`returned` check for a model's reply: text of at most ``cap`` characters, read as JSON with no repeated
    key, and taken as plain data (:func:`plain`); the value it holds (so :func:`returned` reads what is used), or a
    ``ValueError``."""
    if type(reply) is not str:
        raise ValueError(f"a model's reply is text, not a {type(reply).__name__}")
    if len(reply) > cap:
        raise ValueError(f"a model's reply is longer than its {cap}-character cap")
    return plain(json.loads(reply, object_pairs_hook=_unique), where="a model's reply")


# --- The records the bridge issued ------------------------------------------------------------------------------------

def digest(value: Any) -> str:
    """The identity of a record (or of a report): the SHA-256 of every field, as JSON (:func:`view`), with its type."""
    kind = _named(value) if _is_record(value) else ""
    return hashlib.sha256(canonical_json({"record": kind, "value": plain(view(value), where="a record")})).hexdigest()


#: What each maker issued: the maker's id -> (a weak reference to it, the digests of what it issued). Held in memory,
#: keyed by the maker's identity (never by its own ``__hash__`` or ``__eq__``).
_ISSUED: dict[int, tuple[weakref.ref, set[str]]] = {}


def issue(maker: Any, value: T) -> T:
    """``value``, which ``maker`` made and returns, recorded as one it issued (:func:`digest`)."""
    key = id(maker)
    entry = _ISSUED.get(key)
    if entry is None or entry[0]() is not maker:
        entry = _ISSUED[key] = (weakref.ref(maker, lambda _, key=key: _ISSUED.pop(key, None)), set())
    entry[1].add(digest(value))
    return value


def issued(maker: Any, value: Any) -> bool:
    """Whether ``maker`` issued ``value``: the same digest, every field. A value it did not make (built by a caller,
    edited through ``dataclasses.replace``, made by another instance) is not one; nor is one that cannot be read."""
    entry = _ISSUED.get(id(maker))
    if entry is None or entry[0]() is not maker:
        return False
    try:
        return digest(value) in entry[1]
    except (TypeError, ValueError):
        return False


__all__ = ["FrozenMap", "NotPlainData", "canonical_json", "digest", "fresh", "issue", "issued", "json_reply", "plain",
           "record", "returned", "snapshot", "text", "view"]
