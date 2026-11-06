# SPDX-License-Identifier: Apache-2.0
"""The one way the bridge holds what a caller gives it: a deep, immutable snapshot, taken when it is accepted.

Every record type in the package is a :func:`record` (a frozen dataclass whose every field is snapshotted when it is
made, before its own checks run), and every other class stores a caller's value through :func:`snapshot`;
``tests/cognitive_bridge/test_snapshot_inventory.py`` lists every store from the code and fails on any other. A
snapshot is a tuple, a frozenset, a read-only mapping of snapshots (:class:`FrozenMap`), ``bytes`` or an immutable
scalar; code (a callable) and another record are kept as they are. A caller who keeps the list, dict or buffer it
passed in holds a different object from the one the bridge checks and uses, so changing it changes nothing here.

Collaborators (code a caller hands in: a model, a selector, an updater, a callback) are the other side of the same
rule. A collaborator is handed only :func:`fresh` copies, which share nothing with what the bridge checked, and what it
returns is used only through :func:`returned`: a fresh copy taken the moment it returns, then checked against what was
checked. ``tests/cognitive_bridge/test_collaborator_inventory.py`` lists every call of a collaborator from the code and
fails on any other use of its answer or any other argument.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Set
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class FrozenMap(dict):
    """A read-only mapping of snapshots: a ``dict`` (so JSON, ``asdict`` and equality read it as one) on which every
    write raises."""
    __slots__ = ()

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("a snapshot cannot be changed; make a new value instead")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _refuse

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(frozenset(self.items()))

    def __copy__(self) -> FrozenMap:
        return self

    def __deepcopy__(self, memo: dict) -> FrozenMap:
        return self

    def __reduce__(self) -> tuple:
        return FrozenMap, (dict(self),)

    def __repr__(self) -> str:
        return f"FrozenMap({dict.__repr__(self)})"


def _freeze(value: Any, renew: bool) -> Any:
    kind = type(value)
    if value is None or kind in (bool, int, float, str, bytes):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if kind.__module__ == "numpy" and hasattr(value, "tolist"):  # a numpy scalar or array, as its Python values
        return _freeze(value.tolist(), renew)
    if isinstance(value, Mapping):
        return FrozenMap((_freeze(k, renew), _freeze(v, renew)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v, renew) for v in value)
    if isinstance(value, Set):
        return frozenset(_freeze(v, renew) for v in value)
    if getattr(kind, "__snapshot_record__", False):
        if not renew:
            return value
        return dataclasses.replace(value, **{f.name: _freeze(getattr(value, f.name), True)
                                             for f in dataclasses.fields(value) if f.init})
    if callable(value):
        return value
    raise TypeError(f"a {kind.__name__} cannot be held as a snapshot; pass plain data")


def snapshot(value: Any) -> Any:
    """A deep, immutable copy of ``value``: a mapping becomes a :class:`FrozenMap` of snapshots, a list or tuple a
    tuple, a set a frozenset, a bytes-like buffer ``bytes``, a number or string itself (a subclass is made the plain
    type, a numpy value its Python value); a record (:func:`record`) and code (a callable) are kept. Anything else is
    refused (``TypeError``)."""
    return _freeze(value, False)


def fresh(value: Any) -> Any:
    """A :func:`snapshot` of ``value`` that shares nothing with it: every record in it is made again, its fields
    snapshotted anew, so no write anyone makes to what they hold (through any door, a read-only view's own ``dict``
    methods included) reaches the copy. What a collaborator is handed, and what the bridge takes from one."""
    return _freeze(value, True)


def returned(value: Any, check: Callable[..., T], *against: Any) -> T:
    """What a collaborator returned, as the bridge uses it: a :func:`fresh` copy taken the moment it returns, then
    ``check(copy, *against)``, which raises, or returns what is used from then on. Nothing else of ``value`` is read."""
    return check(fresh(value), *against)


def text(reply: Any) -> str:
    """A :func:`returned` check for a model's reply: a string, or a ``ValueError``."""
    if type(reply) is not str:
        raise ValueError(f"a model's reply is text, not a {type(reply).__name__}")
    return reply


def record(cls: type[T]) -> type[T]:
    """A frozen dataclass whose every field is a :func:`snapshot`, taken when it is made and before the class's own
    ``__post_init__`` checks it, so what is checked is what is kept."""
    own = cls.__dict__.get("__post_init__")

    def __post_init__(self: Any) -> None:
        for field in dataclasses.fields(self):
            object.__setattr__(self, field.name, snapshot(getattr(self, field.name)))
        if own is not None:
            own(self)

    cls.__post_init__ = __post_init__  # type: ignore[attr-defined]
    cls.__snapshot_record__ = True  # type: ignore[attr-defined]
    return dataclasses.dataclass(frozen=True)(cls)


__all__ = ["FrozenMap", "fresh", "record", "returned", "snapshot", "text"]
