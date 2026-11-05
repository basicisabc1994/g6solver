# SPDX-License-Identifier: Apache-2.0
"""The one way the bridge holds what a caller gives it: a deep, immutable snapshot, taken when it is accepted.

Every record type in the package is a :func:`record` (a frozen dataclass whose every field is snapshotted when it is
made, before its own checks run), and every other class stores a caller's value through :func:`snapshot`;
``tests/cognitive_bridge/test_snapshot_inventory.py`` lists every store from the code and fails on any other. A
snapshot is a tuple, a frozenset, a read-only mapping of snapshots (:class:`FrozenMap`), ``bytes`` or an immutable
scalar; code (a callable) and another record are kept as they are. A caller who keeps the list, dict or buffer it
passed in holds a different object from the one the bridge checks and uses, so changing it changes nothing here.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Set
from typing import Any, TypeVar

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


def snapshot(value: Any) -> Any:
    """A deep, immutable copy of ``value``: a mapping becomes a :class:`FrozenMap` of snapshots, a list or tuple a
    tuple, a set a frozenset, a bytes-like buffer ``bytes``, a number or string itself (a subclass is made the plain
    type); a record (:func:`record`) and code (a callable) are kept. Anything else is refused (``TypeError``)."""
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
    if kind.__module__ == "numpy" and getattr(value, "ndim", None) == 0:  # a numpy scalar, as its Python value
        return snapshot(value.item())
    if isinstance(value, Mapping):
        return FrozenMap((snapshot(k), snapshot(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(snapshot(v) for v in value)
    if isinstance(value, Set):
        return frozenset(snapshot(v) for v in value)
    if getattr(kind, "__snapshot_record__", False) or callable(value):
        return value
    raise TypeError(f"a {kind.__name__} cannot be held as a snapshot; pass plain data")


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


__all__ = ["FrozenMap", "record", "snapshot"]
