# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A formal statement and the observation it constrains (G6 0.3 design, section 15.2, layer 3).

A ``FormalStatement`` is a hypothesis until its translation is checked and a person accepts it: nothing here says it
is right. It is identified by the digest of its canonical JSON bytes (``g6.core.ledger.canonical``), so one changed
byte of the text, a definition, the domain, an assumption, a bound, the observation or the adapter id is another
statement. Three digests are kept apart because the scope binding (check 5) names each: the whole statement, its
definitions, and its domain, assumptions and bounds.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, fields
from typing import Literal

from g6.core.ledger import canonical

LANGUAGES = ("smt", "lean4", "tla", "datalog")
OBSERVATION_KINDS = ("returns", "raises", "effect")



def _strings(value: object, what: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, tuple | list) or any(type(v) is not str for v in value):
        raise ValueError(f"{what} is a sequence of strings")
    return tuple(value)


def _pairs(value: object, what: str, second: type) -> tuple[tuple, ...]:
    if isinstance(value, str) or not isinstance(value, tuple | list):
        raise ValueError(f"{what} is a sequence of pairs")
    out = []
    for item in value:
        if (not isinstance(item, tuple | list) or len(item) != 2 or type(item[0]) is not str
                or type(item[1]) is not second):
            raise ValueError(f"{what} holds (name, {second.__name__}) pairs")
        out.append((item[0], item[1]))
    if len({name for name, _ in out}) != len(out):
        raise ValueError(f"{what} names something twice")
    return tuple(out)


@dataclass(frozen=True)
class Observation:
    """What of a call the statement constrains: its return value, the error it raises, or an effect on an argument.
    ``entry_point`` is the dotted function, as example.v1 names it; ``inputs`` are its parameters with their sorts, in
    the function's own order."""

    kind: Literal["returns", "raises", "effect"]
    entry_point: str
    inputs: tuple[tuple[str, str], ...]
    result_sort: str = ""
    error: str = ""
    setup: str = ""

    def __post_init__(self) -> None:
        if self.kind not in OBSERVATION_KINDS:
            raise ValueError(f"an observation is one of {', '.join(OBSERVATION_KINDS)}")
        for name in ("entry_point", "result_sort", "error", "setup"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} is text")
        object.__setattr__(self, "inputs", _pairs(self.inputs, "inputs", str))


@dataclass(frozen=True, kw_only=True)
class FormalStatement:
    """One formal statement, exactly as its engine is given it, with everything its meaning depends on."""

    language: Literal["smt", "lean4", "tla", "datalog"]
    fragment: str
    text: str
    observation: Observation
    definitions: tuple[tuple[str, str], ...] = ()
    domain: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    bounds: tuple[tuple[str, int], ...] = ()
    adapter: str = ""

    def __post_init__(self) -> None:
        for name in ("language", "fragment", "text", "adapter"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} is text")
        if not isinstance(self.observation, Observation):
            raise ValueError("a statement names the observation it constrains")
        object.__setattr__(self, "definitions", _pairs(self.definitions, "definitions", str))
        object.__setattr__(self, "domain", _strings(self.domain, "domain"))
        object.__setattr__(self, "assumptions", _strings(self.assumptions, "assumptions"))
        object.__setattr__(self, "bounds", _pairs(self.bounds, "bounds", int))

    def to_dict(self) -> dict:
        return asdict(self)

    def record(self) -> bytes:
        """The statement's bytes: canonical JSON of every field."""
        return canonical(self.to_dict()).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.record()).hexdigest()

    def definitions_record(self) -> bytes:
        return canonical([list(pair) for pair in self.definitions]).encode("ascii")

    def assumptions_record(self) -> bytes:
        """The domain, the assumptions and the bounds: everything the claim is conditional on."""
        return canonical({"domain": list(self.domain), "assumptions": list(self.assumptions),
                          "bounds": [list(pair) for pair in self.bounds]}).encode("ascii")

    @property
    def definitions_sha256(self) -> str:
        return hashlib.sha256(self.definitions_record()).hexdigest()

    @property
    def assumptions_sha256(self) -> str:
        return hashlib.sha256(self.assumptions_record()).hexdigest()

    def definition(self, name: str) -> str | None:
        return dict(self.definitions).get(name)


#: Every field of a statement, for inventories that must name each part.
STATEMENT_FIELDS = tuple(f.name for f in fields(FormalStatement))

__all__ = ["LANGUAGES", "OBSERVATION_KINDS", "STATEMENT_FIELDS", "FormalStatement", "Observation"]
