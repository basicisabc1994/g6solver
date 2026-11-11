# SPDX-License-Identifier: Apache-2.0
"""The bridge's boundary: one data model for everything a caller or a collaborator hands it, and the records it honours.

Every value that enters the bridge from a caller or a collaborator (a tool's answer, a model's reply, evidence and
artefact mappings, checker manifests, identities, selections) is converted once, where it enters, by :func:`plain`
into plain JSON data: objects with text keys, arrays, text, finite numbers, booleans and null. The conversion is a
round trip through the bridge's own canonical encoder (:func:`canonical_json`), and it refuses (:class:`NotPlainData`,
under ``codes.INPUT``, with the path) anything that does not survive it unchanged: a key that is not text, a subclass of
any type, a set, any other object, a number outside the finite float range, an integer longer than the encoder writes. Its walk
reads each value's kind by the leaf test the claim rule reads by (``codes._read_as``), so the two never disagree on
what is JSON data. An identity is text (:func:`text`), and a number is an integer or a float (:func:`number`): read
raw by the boundary first, then by its rule, and never converted from another kind
(``tests/cognitive_bridge/test_conversion_inventory.py``). Code after the boundary sees only that data, so the claim rule
(``codes.claims``) walks JSON alone.

How deep a value nests is one count (``codes.DEPTH``), from where it enters: every walk here takes its depth from its
caller and hands it on, so a subtree handed from one walk to another (to :func:`plain`, :func:`text`, :func:`view` or a
record made again) keeps the count, and a record's fields stand one level inside the record, counted from where the
record stands (``tests/cognitive_bridge/test_depth_inventory.py``).

A record (:func:`record`) is the bridge's own frozen dataclass, and each of its fields is taken where the record is
made, by the kind its annotation declares: data through :func:`plain`, claiming no state anywhere (``codes.unclaimed``:
a claim is refused where it enters), held read-only (``codes.FrozenMap`` and tuples); text as text; a number, an
integer or a flag (``float``, ``int``, ``bool``) by its rule, never converted from another kind; bytes only in a field declared ``bytes``, which the record fills itself; another record only of its
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
import contextvars
import dataclasses
import hashlib
import json
import types
import typing
import weakref
from typing import Any, Callable, TypeVar

from returns.result import Failure, Success

from g6.core.result import Result

from .codes import _code, _is_type, _type_mro
from .codes import ARRAYS, DEPTH, OBJECTS, FrozenMap, NotPlainData, _read_as, _unread, unclaimed

T = TypeVar("T")

# Limits apply while projecting, before the JSON serializer allocates its output.
MAX_ITEMS = 100_000
MAX_SIZE = 4_000_000


def _admit_array(value: Any, *, where: str) -> memoryview:
    """Admit native conversion from buffer shape and byte count before tolist."""
    # A small native buffer cannot bound a Python override of tolist, attribute
    # access, or buffer export. Resolve slots from the actual MRO without
    # invoking any of them; only native descriptors have this storage contract.
    pending = {'tolist', '__getattribute__', '__buffer__'}
    for base in _type_mro(type(value)):
        members = type.__dict__['__dict__'].__get__(base)
        if any(type(key) is not str for key in members):
            raise NotPlainData(where, "array size cannot be admitted from a foreign slot namespace")
        for name in tuple(pending):
            if name in members:
                member = members[name]
                native = ((types.MethodDescriptorType,) if name == 'tolist' else
                          (types.MethodDescriptorType, types.WrapperDescriptorType))
                if not _is_type(type(member), native):
                    raise NotPlainData(where, f"array size cannot be admitted before conversion: non-native {name}")
                pending.remove(name)
    try:
        buffer = memoryview(value)
    except (BufferError, TypeError, ValueError):
        raise NotPlainData(where, "array size cannot be admitted before conversion") from None
    try:
        if len(buffer.shape) > DEPTH:
            raise _too_deep(where)
        visits, prefix = 1, 1
        for dimension in buffer.shape:
            prefix *= dimension
            visits += prefix
            if visits > MAX_ITEMS:
                raise NotPlainData(where, "size exceeds the bridge's limit")
        if buffer.nbytes > MAX_SIZE:
            raise NotPlainData(where, "size exceeds the bridge's limit")
    except BaseException:
        buffer.release()
        raise
    return buffer  # keep native storage pinned through conversion


def _admit_bytes(value: Any, cap: int, message: str) -> None:
    """A byte buffer's native length, before its immutable copy."""
    if _is_type(type(value), (bytes, bytearray, memoryview)):
        size = value.nbytes if type(value) is memoryview else len(value)
        if size > cap:
            raise ValueError(message)


def _admit_fields(value: Any, cls: type) -> None:
    """Record-local buffer bounds also precede detachment and reconstruction."""
    for field in dataclasses.fields(cls):
        if 'max_bytes' not in field.metadata and 'max_length' not in field.metadata:
            continue
        if type(value) is dict:
            if field.name not in value:
                continue  # the schema reader supplies defaults/names missing fields
            item = value[field.name]
        else:
            item = getattr(value, field.name)
        if 'max_bytes' in field.metadata:
            _admit_bytes(item, field.metadata['max_bytes'], field.metadata['size_error'])
        if 'max_length' in field.metadata:
            _admit_length(item, field.metadata['max_length'], field.metadata['size_error'])


def _admit_length(value: Any, cap: int | None, error: str, *, minimum: int = 0) -> None:
    """Read native length before detaching; unfamiliar kinds go to the boundary."""
    if _is_type(type(value), (str, list, tuple, dict, FrozenMap)):
        if len(value) < minimum or (cap is not None and len(value) > cap):
            raise ValueError(error)


def _prompt_value(value: Any, *, where: str) -> Any:
    """Detach for size admission without serializing prompt input first.

    Unknown outer kinds go through snapshot now, before a caller can iterate
    them to select context. Typed reads and round trips still follow admission.
    """
    taken = _walk(value, where)
    if not _is_type(type(taken), (str, list, tuple, dict, FrozenMap, int, float, bool, type(None))):
        return snapshot(taken, where=where)
    return taken


def generated(count: int, make: Callable[[int], Any], *, where: str) -> tuple[Any, ...]:
    """Generate only an admitted count, charging the walker before the next item.

    The tuple itself spends one visit. Items are made lazily and share the
    walker's item and size budgets, including any children they contain.
    """
    if type(count) is not int or not 0 <= count < MAX_ITEMS:
        raise NotPlainData(where, "size exceeds the bridge's limit")
    budget = [0, 0]
    _walk(None, where, budget=budget)  # the output container

    def items() -> Any:
        index = 0
        while index < count:
            if budget[0] >= MAX_ITEMS or budget[1] >= MAX_SIZE:
                raise NotPlainData(where, "size exceeds the bridge's limit")
            yield _walk(make(index), f"{where}[{index}]", 1, budget=budget)
            index += 1

    return tuple(items())


def _walk(value: Any, where: str, depth: int = 0, *, budget: list[int] | None = None,
          schema: type | None = None, capability: bool = False,
          models: tuple[type, ...] = (), truncate: bool = False, outer: bool = False,
          preflight: Callable[[str, Any], None] | None = None) -> Any:
    """Detach input before any other traversal, counting every reach before reading it.

    Repeated references spend the budget again. Unknown kinds are opaque here:
    the existing kind readers refuse them, without invoking their protocols.
    Record callbacks are references; only their declared data fields are walked.
    All subsequent validation/projection/copy walks receive this detached tree.
    """
    if budget is None:
        budget = [0, 0]
    if budget[0] >= MAX_ITEMS:
        raise NotPlainData(where, "size exceeds the bridge's limit")
    budget[0] += 1
    if depth > DEPTH:
        if truncate:
            return value  # the reader names this depth before touching the value
        raise _too_deep(where)
    kind = type(value)
    budget[1] += len(value) if kind is str else 1
    if budget[1] > MAX_SIZE:
        raise NotPlainData(where, "size exceeds the bridge's limit")
    if capability:
        return value
    if outer:
        # Only the block's outer mapping uses the registry's mapping protocol.
        # Enumerate it here so neither unpacking nor a second raw walk precedes
        # the visit budget. Protocol implementations themselves are caller code.
        try:
            if not value:
                try:
                    value = {"op": value["op"]}
                except KeyError:
                    value = {}
                return _walk(value, where, depth, budget=budget)
            bases = _type_mro(kind)
            iteration = next((member for base in bases
                              for name, member in type.__dict__["__dict__"].__get__(base).items()
                              if type(name) is str and name == "__iter__"), None)
            # Native unpacking uses dict storage for a dict subtype whose
            # iterator is unchanged; it deliberately bypasses mapping hooks.
            native = any(base is dict for base in bases) and iteration is dict.__iter__
            if native and 2 * dict.__len__(value) > MAX_ITEMS - budget[0]:
                raise NotPlainData(where, "size exceeds the bridge's limit")
            keys = dict.keys(value) if native else value.keys()
            items = {}
            for key in keys:
                key = _walk(key, f"{where} (a key)", depth + 1, budget=budget)
                if type(key) is not str:
                    raise NotPlainData(where, f"a {_named(key)} key is not text")
                if budget[0] >= MAX_ITEMS:
                    raise NotPlainData(where, "size exceeds the bridge's limit")
                item = dict.__getitem__(value, key) if native else value[key]
                if preflight is not None:
                    preflight(key, item)
                items[key] = _walk(item, f"{where}.{key}", depth + 1, budget=budget, models=models)
            return items
        except NotPlainData:
            raise
        except Exception as exc:
            raise NotPlainData(where, f"cannot copy the outer mapping: {exc}") from None
    if _is_type(kind, models):
        fields = {}
        for name in kind.model_fields:
            try:
                item = getattr(value, name)
            except AttributeError:
                raise NotPlainData(where, f"missing input field {name}") from None
            fields[name] = _walk(item, f"{where}.{name}", depth + 1,
                                 budget=budget, models=models, truncate=truncate)
        return fields
    if schema is not None or _is_record(value):
        cls = schema if schema is not None else kind
        _admit_fields(value, cls)
        made = object.__new__(cls)
        kinds = _kinds(cls)
        for field in dataclasses.fields(cls):
            try:
                item = getattr(value, field.name)
            except AttributeError:
                if field.init:
                    raise NotPlainData(where, f"missing record field {field.name}") from None
                continue  # computed by the record's own checks
            item = _walk(
                item, f"{where}.{field.name}", depth + 1, budget=budget,
                capability=kinds[field.name][0] == "code", models=models, truncate=truncate)
            object.__setattr__(made, field.name, item)
        return made
    if _is_type(kind, ARRAYS):
        if len(value) > MAX_ITEMS - budget[0]:
            raise NotPlainData(where, "size exceeds the bridge's limit")
        items = [_walk(item, f"{where}[{index}]", depth + 1, budget=budget, models=models, truncate=truncate)
                 for index, item in enumerate(value)]
        return tuple(items) if kind is tuple else items
    if _is_type(kind, OBJECTS):
        if 2 * len(value) > MAX_ITEMS - budget[0]:
            raise NotPlainData(where, "size exceeds the bridge's limit")
        items = {}
        for key, item in value.items():
            key = _walk(key, f"{where} (a key)", depth + 1, budget=budget)
            if type(key) is not str:
                raise NotPlainData(where, f"a {_named(key)} key is not text (the key, which is not text)")
            items[key] = _walk(item, f"{where}.{key}", depth + 1, budget=budget, models=models, truncate=truncate)
        return FrozenMap(items) if kind is FrozenMap else items
    return value


def _encoded_size(value: Any, cap: int) -> int:
    """Exact ensure_ascii JSON size, stopping at the cap without building text.

    Input is already detached and JSON-checked. Each character costs 1, 2, 6,
    or 12 ASCII bytes; integers/floats are bounded by the numeric reader.
    """
    size = 0

    def charge(count: int) -> None:
        nonlocal size
        size += count
        if size > cap:
            raise NotPlainData("a value", "encoded size exceeds the bridge's limit")

    def string(item: str) -> None:
        charge(2)
        # Even an unescaped string cannot fit: refuse without scanning it.
        if len(item) > cap - size:
            charge(len(item))
        for char in item:
            point = ord(char)
            charge(2 if char in '\"\\\b\f\n\r\t' else
                   6 if point < 32 or 127 <= point <= 65535 else
                   12 if point > 65535 else 1)

    def visit(item: Any) -> None:
        kind = type(item)
        if kind is str:
            string(item)
        elif kind is dict:
            charge(2 + max(0, len(item) - 1) + len(item))
            for key, child in item.items():
                string(key)
                visit(child)
        elif kind is list:
            charge(2 + max(0, len(item) - 1))
            for child in item:
                visit(child)
        elif item is None:
            charge(4)
        elif kind is bool:
            charge(4 if item else 5)
        else:
            charge(len(int.__repr__(item) if kind is int else float.__repr__(item)))

    visit(value)
    return size


def _admit_json(value: Any, *, cap: int | None = None, error: str | None = None) -> Any:
    """Project and admit exact encoded size without allocating encoded output."""
    projected = view(value)
    found = _not_json(projected, "a value", 0)
    if found:
        raise NotPlainData(*found)
    try:
        _encoded_size(projected, MAX_SIZE if cap is None else min(cap, MAX_SIZE))
    except NotPlainData:
        if error is not None and cap is not None and cap <= MAX_SIZE:
            raise ValueError(error) from None
        raise
    return projected


def canonical_json(value: Any, *, cap: int | None = None, error: str | None = None) -> bytes:
    """The bounded encoder: admit escaped size before either output allocation."""
    projected = _admit_json(value, cap=cap, error=error)
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return encoded


def record_data(value: Any) -> Any:
    """A record's plain representation, through the bounded canonical encoder."""
    return json.loads(canonical_json(value))


def publish(value: T, *, where: str) -> T:
    """The sole publication step, including records, leaves and G6 result envelopes.

    The encoder checks the entire answer before the claim rule sees JSON alone.
    Return the checked object so record identities, tuples and Result semantics stay.
    """
    answer = value.unwrap() if type(value) is Success else value.failure() if type(value) is Failure else value
    if type(value) is Result:
        answer = value.value if value.is_ok() else value.error
    unclaimed(record_data(answer), where=where)
    return value


def _named(value: Any) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


#: Why a value standing deeper than ``codes.DEPTH`` is refused.
_DEEPER = f"nested deeper than {DEPTH} levels"


def _too_deep(where: str) -> NotPlainData:
    return NotPlainData(where, _DEEPER)


def _not_json(value: Any, path: str, depth: int) -> tuple[str, str] | None:
    """The first place in ``value``, standing at ``depth``, that is not JSON data, and why; None when there is none.
    Each value's kind is read by the claim rule's own leaf test (``codes._read_as``): a leaf, an object with text keys
    or an array, by exact type, and a number only when the encoder writes it."""
    if depth > DEPTH:
        return path, _DEEPER
    how = _read_as(value)
    if how in ("text", "leaf"):
        return None
    if how == "object":
        for key, item in value.items():
            if _read_as(key) != "text":
                return f"{path} (a key)", f"a {_named(key)} key is not text"
            found = _not_json(item, f"{path}.{key}", depth + 1)
            if found:
                return found
        return None
    if how == "array":
        for index, item in enumerate(value):
            found = _not_json(item, f"{path}[{index}]", depth + 1)
            if found:
                return found
        return None
    return path, f"{_unread(value)} is not JSON data"


def _same(value: Any, out: Any, depth: int) -> bool:
    """Whether ``out``, the canonical encoder's round trip of ``value`` (standing at ``depth``), is ``value``
    unchanged, type for type (an object read back as a dict, an array as a list)."""
    if depth > DEPTH:
        return False
    kind = type(value)
    if _is_type(kind, OBJECTS):
        return type(out) is dict and out.keys() == value.keys() and all(
            _same(value[k], out[k], depth + 1) for k in value)
    if _is_type(kind, ARRAYS):
        return type(out) is list and len(out) == len(value) and all(
            _same(a, b, depth + 1) for a, b in zip(value, out))
    return type(out) is kind and out == value


def plain(value: Any, *, where: str, _depth: int = 0) -> Any:
    """``value`` as plain JSON data, the one conversion of what enters the bridge: a round trip through
    :func:`canonical_json`, refused (:class:`NotPlainData`, naming ``where`` and the path) unless ``value`` survives it
    unchanged. A new value, which shares nothing with ``value``. ``_depth`` is where ``value`` stands in what it entered
    with, when a walk hands it a subtree; the bound counts from there."""
    value = _walk(value, where, _depth)
    found = _not_json(value, where, _depth)
    if found:
        raise NotPlainData(*found)
    out = json.loads(canonical_json(value))
    if not _same(value, out, _depth):
        raise NotPlainData(where, "it does not survive the bridge's canonical encoder unchanged")
    return out


def text(value: Any, *, where: str, _depth: int = 0) -> str:
    """An identity, a name or a question as the bridge takes it: text (:func:`plain`, standing at ``_depth``); anything
    else is refused."""
    if type(value) is not str:
        raise NotPlainData(where, f"a {_named(value)} is not text")
    return plain(value, where=where, _depth=_depth)


def number(value: Any, *, where: str, _depth: int = 0) -> int | float:
    """A number as the bridge takes it: an integer or a float, by exact type, as the boundary takes it (:func:`plain`,
    standing at ``_depth``: within the finite float magnitude, and an integer the encoder writes); anything else (a flag, text, null, a set,
    bytes) is refused, never converted. The one number rule: every place the bridge reads a caller's or a
    collaborator's value as a number reads it through this (``tests/cognitive_bridge/test_conversion_inventory.py``)."""
    if not _is_type(type(value), (int, float)):
        raise NotPlainData(where, f"a {_named(value)} is not a number: numbers are integers and finite floats, not "
                                  "flags, text or null")
    return plain(value, where=where, _depth=_depth)


def _frozen(value: Any, depth: int) -> Any:
    """Plain JSON data (what :func:`plain` made of a value standing at ``depth``), read-only: an object a
    :class:`FrozenMap`, an array a tuple."""
    if depth > DEPTH:
        raise _too_deep("a value")
    if type(value) is dict:
        return FrozenMap((key, _frozen(item, depth + 1)) for key, item in value.items())
    if type(value) is list:
        return tuple(_frozen(item, depth + 1) for item in value)
    return value


# --- Records ----------------------------------------------------------------------------------------------------------

#: Every record type :func:`record` made, by identity: a class that only says it is one (sets the mark itself) is not.
_RECORDS: dict[int, type] = {}


def _record_type(kind: Any) -> bool:
    found = _RECORDS.get(id(kind))
    return found is not None and found is kind


def _is_record(value: Any) -> bool:
    """Whether ``value`` is a bridge record: its own class, not a subclass, was made by :func:`record`."""
    return _record_type(type(value))


#: The leaf kinds a record field may declare beside text, by its annotation: a number (``float``: an integer or a
#: float), an integer (``int``) and a flag (``bool``), each read by its rule where the record is made (:func:`_leaf`).
_LEAF_KINDS: tuple[tuple[type, str], ...] = ((float, "number"), (int, "integer"), (bool, "flag"))


def _leaf_kind(hint: Any) -> str:
    return next((name for kind, name in _LEAF_KINDS if hint is kind), "")


def _protocol_kind(hint: Any) -> bool:
    """A declared protocol by its actual class MRO, never its supplied marker."""
    try:
        bases = _type_mro(hint)
    except TypeError:
        return False
    return any(base is typing.Protocol for base in bases)


def _kind(hint: Any) -> tuple[Any, ...]:
    """What a record field annotated ``hint`` holds: text, bytes, a tuple of bytes, a number, an integer or a flag (or
    None, when the annotation allows it), a record of one type (or None), a tuple of records of one type, code, or data
    (everything else)."""
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if hint is str:
        return ("text",)
    if hint is bytes:
        return ("bytes",)
    if origin is tuple and (len(args) == 2 and args[0] is bytes and args[1] is Ellipsis):
        return ("bytes*",)
    if _leaf_kind(hint):
        return (_leaf_kind(hint), False)
    if _record_type(hint):
        return ("record", hint, False)
    if _is_type(origin, (typing.Union, types.UnionType)) and len(args) == 2 and any(arg is type(None) for arg in args):
        other = args[0] if args[1] is type(None) else args[1]
        if _leaf_kind(other):
            return (_leaf_kind(other), True)
        if _record_type(other):
            return ("record", other, True)
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis and _record_type(args[0]):
        return ("records", args[0])
    if origin is collections.abc.Callable or _protocol_kind(hint):
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
        for field in dataclasses.fields(cls):
            if kinds[field.name][0] == 'bytes' and 'max_bytes' in field.metadata:
                kinds[field.name] += (field.metadata['max_bytes'], field.metadata['size_error'])
        cls.__record_kinds__ = kinds
    return kinds


def _bytes(value: Any, where: str, cap: int | None = None, error: str = "buffer exceeds size cap") -> bytes:
    if not _is_type(type(value), (bytes, bytearray, memoryview)):
        raise NotPlainData(where, f"a {_named(value)} is not bytes")
    buffer = memoryview(value)
    try:
        if cap is not None:
            _admit_bytes(buffer, cap, error)
        return bytes(value)
    finally:
        buffer.release()


def _leaf(kind: str, value: Any, where: str, depth: int) -> Any:
    """``value`` taken into a field that declares a leaf kind (:data:`_LEAF_KINDS`), standing at ``depth``: a number by
    the number rule (:func:`number`), an integer that is an ``int``, a flag that is ``True`` or ``False``. Anything else
    is refused, never converted."""
    if kind == "flag":
        if type(value) is not bool:
            raise NotPlainData(where, f"a {_named(value)} is not a flag (true or false)")
        return plain(value, where=where, _depth=depth)
    taken = number(value, where=where, _depth=depth)
    if kind == "integer" and type(taken) is not int:
        raise NotPlainData(where, f"a {_named(value)} is not an integer")
    return taken


def _field(kind: tuple[Any, ...], value: Any, where: str, depth: int) -> Any:
    """``value`` taken into a record field that holds ``kind``, standing at ``depth`` (one level inside its record), or
    refused. Data is what the boundary made of it (:func:`plain`), and claims no state anywhere (``codes.unclaimed``,
    counted from the same depth): a claim is refused where it enters, in whatever the record later publishes."""
    name = kind[0]
    if name == "data":
        return _frozen(unclaimed(plain(value, where=where, _depth=depth), where=where, _depth=depth), depth)
    if name == "text":
        return text(value, where=where, _depth=depth)
    if name in ("number", "integer", "flag"):
        return None if value is None and kind[1] else _leaf(name, value, where, depth)
    if name in ("bytes", "bytes*"):
        if name == "bytes":
            taken: Any = _bytes(value, where, *kind[1:])
        elif not _is_type(type(value), (list, tuple)):
            raise NotPlainData(where, f"a {_named(value)} is not a list of bytes")
        else:
            taken = tuple(_bytes(item, f"{where}[{index}]") for index, item in enumerate(value))
        plain(view(taken, _depth=depth), where=where, _depth=depth)  # where it stands, as the claim rule reads it
        return taken
    if name == "record":
        if type(value) is kind[1] or (value is None and kind[2]):
            return value
        raise NotPlainData(where, f"a {_named(value)} is not a {kind[1].__name__}")
    if name == "records":
        if not _is_type(type(value), (list, tuple)) or any(type(item) is not kind[1] for item in value):
            raise NotPlainData(where, f"a {_named(value)} is not a list of {kind[1].__name__}")
        return tuple(value)
    if not _code(value):
        raise NotPlainData(where, f"a {_named(value)} is not code")
    return value


#: Where the record being made stands in the value it entered with: 0 when it is made on its own, else the depth
#: :func:`_renewed` makes it again at, so its fields (one level inside it) keep the count from where the value entered.
_AT: contextvars.ContextVar[int] = contextvars.ContextVar("g6_cognitive_bridge_record_depth", default=0)


def record(cls: type[T]) -> type[T]:
    """A frozen dataclass whose every field is taken where it is made, by the kind its annotation declares
    (:func:`_field`), before the class's own ``__post_init__`` checks it, so what is checked is what is kept. Its
    fields stand one level inside it, counted from where it stands (:data:`_AT`)."""
    own = cls.__dict__.get("__post_init__")

    def __post_init__(self: Any) -> None:
        at = _AT.get()
        token = _AT.set(0)  # a record this one's own checks make stands on its own
        try:
            # The schema belongs to the decorated class, never to a caller's
            # subclass or metaclass. Construction does not register a subclass:
            # every boundary still admits records by their exact registered type.
            # Composing records need not publish their whole graph. Stop at the
            # depth frontier; field readers still reject deep raw data, and a
            # consumer must bound the composed record before traversing it.
            bounded = _walk(self, cls.__name__, at, schema=cls, truncate=True)
            kinds = _kinds(cls)
            for field in dataclasses.fields(cls):
                object.__setattr__(self, field.name, _field(kinds[field.name], getattr(bounded, field.name),
                                                            f"{cls.__name__}.{field.name}", at + 1))
            if own is not None:
                own(self)
        finally:
            _AT.reset(token)

    cls.__post_init__ = __post_init__  # type: ignore[attr-defined]
    cls.__snapshot_record__ = True  # type: ignore[attr-defined]
    made = dataclasses.dataclass(frozen=True)(cls)
    _RECORDS[id(made)] = made
    return made


def _renewed(value: Any, where: str, depth: int) -> Any:
    """A record standing at ``depth``, made again through its boundary there: every field taken anew one level inside
    it, and every record in it made again where it stands."""
    if depth > DEPTH:
        raise _too_deep(where)
    kinds, changes = _kinds(type(value)), {}
    for field in dataclasses.fields(value):
        if not field.init:
            continue
        item, kind = getattr(value, field.name), kinds[field.name][0]
        at = f"{where}.{field.name}"
        if kind == "record" and _is_record(item):
            item = _renewed(item, at, depth + 1)
        elif kind == "records" and _is_type(type(item), (list, tuple)):
            item = tuple(_renewed(x, f"{at}[{index}]", depth + 2) if _is_record(x) else x
                         for index, x in enumerate(item))
        changes[field.name] = item
    token = _AT.set(depth)
    try:
        return dataclasses.replace(value, **changes)
    finally:
        _AT.reset(token)


def _has_record(value: Any, depth: int) -> bool:
    if _is_record(value):
        return True
    if depth > DEPTH:
        return False
    kind = type(value)
    if _is_type(kind, ARRAYS):
        return any(_has_record(item, depth + 1) for item in value)
    if _is_type(kind, OBJECTS):
        return any(_has_record(item, depth + 1) for item in value.values())
    return False


def _taken(value: Any, where: str, depth: int) -> Any:
    """``value``, standing at ``depth``, as the bridge holds it: a record made again where it stands; a list or a
    mapping that holds a record, item by item (a mapping's keys text); anything else plain JSON data (:func:`plain`),
    read-only. Each is handed the depth it stands at."""
    if depth > DEPTH:
        raise _too_deep(where)
    if _is_record(value):
        return _renewed(value, where, depth)
    kind = type(value)
    if _is_type(kind, ARRAYS) and _has_record(value, depth):
        return tuple(_taken(item, f"{where}[{index}]", depth + 1) for index, item in enumerate(value))
    if _is_type(kind, OBJECTS) and _has_record(value, depth):
        return FrozenMap((text(key, where=f"{where} (a key)", _depth=depth + 1),
                          _taken(item, f"{where}.{key}", depth + 1)) for key, item in value.items())
    return _frozen(plain(value, where=where, _depth=depth), depth)


def snapshot(value: Any, *, where: str = "a value") -> Any:
    """A caller's value as the bridge takes it, where it enters (``where`` names it in a refusal): plain JSON data,
    read-only, and bridge records (in lists and mappings too), each made again so it shares nothing with the caller's.
    Anything else is refused (:class:`NotPlainData`)."""
    return _taken(_walk(value, where), where, 0)


def fresh(value: Any, *, where: str = "a copy") -> Any:
    """What a collaborator is handed: a :func:`snapshot` that shares nothing with ``value``. A bytes value on its own
    (the content of a field a record declares as bytes, handed to a checker) is handed on whole."""
    return value if type(value) is bytes else _taken(_walk(value, where), where, 0)


_VIEW_BOUND = contextvars.ContextVar("g6_bridge_view_bounded", default=False)


def _bounded_view(value: Any, depth: int) -> Any:
    taken = _walk(value, "a value", depth, truncate=True)
    token = _VIEW_BOUND.set(True)
    try:
        return view(taken, _depth=depth)
    finally:
        _VIEW_BOUND.reset(token)


def view(value: Any, *, _depth: int = 0, _budget: list[int] | None = None) -> Any:
    """``value`` as JSON, for the claim rule and for a digest: a record as an object of its fields, bytes as their
    digest and length, a list or a mapping item by item; anything else as it is, for the rule to name. ``_depth`` is
    where ``value`` stands; past ``codes.DEPTH`` it is handed back as it is, for the rule (or the boundary, counting
    from the same depth) to refuse as too deep."""
    if not _VIEW_BOUND.get():
        return _bounded_view(value, _depth)
    if _budget is None:
        _budget = [0, 0]
    _budget[0] += 1
    _budget[1] += len(value) if type(value) is str else 1
    if _budget[0] > MAX_ITEMS or _budget[1] > MAX_SIZE:
        raise NotPlainData("a value", "size exceeds the bridge's limit")
    if _depth > DEPTH:
        return value
    if _is_record(value):
        return {field.name: view(getattr(value, field.name), _depth=_depth + 1, _budget=_budget)
                for field in dataclasses.fields(value)}
    kind = type(value)
    if kind is bytes:
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if _is_type(kind, ARRAYS):
        return [view(item, _depth=_depth + 1, _budget=_budget) for item in value]
    if _is_type(kind, OBJECTS):
        for key in value:
            if _read_as(key) != "text":
                raise NotPlainData("a value (a key)", f"a {_named(key)} key is not text")
            _budget[1] += len(key)
            if _budget[1] > MAX_SIZE:
                raise NotPlainData("a value", "size exceeds the bridge's limit")
        return {key: view(item, _depth=_depth + 1, _budget=_budget) for key, item in value.items()}
    return value


def returned(value: Any, check: Callable[..., T], *against: Any) -> T:
    """What a collaborator returned, as the bridge uses it: a :func:`snapshot` taken the moment it returns (plain data
    and bridge records, made again, else refused), then ``check(copy, *against)``, which raises, or returns what is
    used from then on. Nothing else of ``value`` is read. The copy, and what the check makes of it, claim no state at
    any depth (``codes.unclaimed``), else ``ValueError``."""
    if check is json_reply:
        _admit_reply(value, against[0])
    copy = snapshot(value, where=f"an answer taken by {check.__name__}")
    unclaimed(view(copy), where=f"an answer taken by {check.__name__}")
    out = check(copy, *against)
    unclaimed(view(out), where=f"what {check.__name__} made of an answer")
    return out


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("a model's reply repeats a key")
    return dict(pairs)


def _admit_reply(reply: Any, cap: int) -> None:
    """The local reply cap, also used before returned detaches raw text."""
    if type(reply) is not str:
        raise ValueError(f"a model's reply is text, not a {type(reply).__name__}")
    if len(reply) > cap:
        raise ValueError(f"a model's reply is longer than its {cap}-character cap")


def json_reply(reply: Any, cap: int, preflight: Callable[[Any], None] | None = None) -> Any:
    """A :func:`returned` check for a model's reply: text of at most ``cap`` characters, read as JSON with no repeated
    key, and taken as plain data (:func:`plain`); the value it holds (so :func:`returned` reads what is used), or a
    ``ValueError``. A reply nested past what the JSON parser reads is far deeper than ``codes.DEPTH``, and refused as
    too deep."""
    _admit_reply(reply, cap)
    reply = text(reply, where="a model's reply")
    try:
        parsed = json.loads(reply, object_pairs_hook=_unique)
    except RecursionError:
        raise _too_deep("a model's reply") from None
    if preflight is not None:
        preflight(parsed)
    return plain(parsed, where="a model's reply")


# --- The records the bridge issued ------------------------------------------------------------------------------------

def digest(value: Any) -> str:
    """The identity of a record (or of a report): the SHA-256 of every field, as JSON (:func:`view`), with its type."""
    kind = _named(value) if _is_record(value) else ""
    # The fixed metadata frame adds no caller-data depth. Encode both values before
    # framing them, keeping the existing digest bytes at the exact depth limit.
    framed = b'{"record":' + canonical_json(kind) + b',"value":' + canonical_json(value) + b'}'
    return hashlib.sha256(framed).hexdigest()


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


__all__ = ["FrozenMap", "NotPlainData", "canonical_json", "digest", "fresh", "issue", "issued", "json_reply", "number",
           "plain", "record", "returned", "snapshot", "text", "view"]
