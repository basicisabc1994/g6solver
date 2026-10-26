# SPDX-License-Identifier: Apache-2.0
"""The domain a candidate is checked on, the oracle it is checked against, and the check (audit 027 CG-001, CG-004).

The archive inferred argument types by calling the oracle with probe values and skipped every input on which the
oracle raised, so an oracle that always raised verified any candidate. Here the domain is explicit, an input the
oracle does not answer is counted as skipped, and a check that compared nothing is ``inconclusive``.

The oracle is your own callable and runs in your process (``TableOracle`` holds data only); candidates run only through
a :class:`~g6.cegis.runner.Runner`.

A :class:`Verifier` is an optional second check the loop runs after the sampled one passes (audit 027 §11): it answers
:class:`Counterexample`, :class:`NoCounterexample` or :class:`Inconclusive`, and only the first two are decisions.
"""
from __future__ import annotations

import itertools
import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from g6.cegis._child import same
from g6.cegis.runner import Runner
from g6.cegis.sketch import Control, Sketch

Oracle = Callable[..., Any]


@dataclass(frozen=True)
class TableOracle:
    """An oracle that is a table of input/output examples; an input it does not list is unanswered."""

    cases: tuple[tuple[tuple[tuple[str, Any], ...], Any], ...]

    @classmethod
    def of(cls, cases: Sequence[tuple[Mapping[str, Any], Any]]) -> TableOracle:
        return cls(tuple((tuple(sorted(dict(inp).items())), out) for inp, out in cases))

    def __call__(self, **inputs: Any) -> Any:
        key = tuple(sorted(inputs.items()))
        for known, out in self.cases:
            if known == key:
                return out
        raise KeyError("the table has no case for these inputs")


@dataclass(frozen=True)
class InputDomain:
    """The inputs a candidate is checked on: the product of each argument's values, or ``max_inputs`` of them drawn
    with ``seed`` when the product is larger. ``describe`` says which, so a result names its own boundary."""

    values: tuple[tuple[str, tuple[Any, ...]], ...]
    max_inputs: int = 2000
    seed: int = 0

    @classmethod
    def of(cls, values: Mapping[str, Sequence[Any]], *, max_inputs: int = 2000, seed: int = 0) -> InputDomain:
        if not values or any(len(v) == 0 for v in values.values()):
            raise ValueError("every argument needs at least one value")
        return cls(tuple((name, tuple(v)) for name, v in values.items()), max_inputs, seed)

    @classmethod
    def ints(cls, names: Sequence[str], lo: int = -10, hi: int = 10, **kwargs: Any) -> InputDomain:
        return cls.of({name: range(lo, hi + 1) for name in names}, **kwargs)

    @property
    def size(self) -> int:
        total = 1
        for _, vals in self.values:
            total *= len(vals)
        return total

    @property
    def exhaustive(self) -> bool:
        return self.size <= self.max_inputs

    def inputs(self) -> list[dict[str, Any]]:
        names = [n for n, _ in self.values]
        if self.exhaustive:
            return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*(v for _, v in self.values))]
        rng, seen, out = random.Random(self.seed), set(), []
        while len(out) < self.max_inputs:
            combo = tuple(rng.choice(vals) for _, vals in self.values)
            key = json.dumps(combo, sort_keys=True, default=repr)
            if key not in seen:
                seen.add(key)
                out.append(dict(zip(names, combo, strict=True)))
        return out

    def describe(self) -> dict[str, Any]:
        return {"arguments": {name: len(vals) for name, vals in self.values}, "size": self.size,
                "exhaustive": self.exhaustive, "drawn": None if self.exhaustive else self.max_inputs,
                "seed": None if self.exhaustive else self.seed}


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def answered(oracle: Oracle, inputs: Sequence[Mapping[str, Any]]) -> list[tuple[dict[str, Any], Any]]:
    """The inputs the oracle answers with a JSON value, with its answer; an input it raises on, or answers with
    something that is not JSON, is left out (and counted as skipped by the caller)."""
    out = []
    for inp in inputs:
        try:
            out.append((dict(inp), _json(oracle(**inp))))
        except Exception:  # noqa: BLE001 - an unanswered input is skipped and counted, never compared
            continue
    return out


@dataclass(frozen=True)
class Check:
    """One check of a candidate on the domain: what was found, and how much was actually compared."""

    status: Literal["counterexample", "no_counterexample", "inconclusive"]
    compared: int
    skipped: int
    counterexample: dict[str, Any] | None = None
    expected: Any = None
    got: dict = field(default_factory=dict)
    reason: str = ""


def check_candidate(sketch: Sketch, control: Control, cases: Sequence[tuple[dict[str, Any], Any]], *,
                    skipped: int, runner: Runner) -> Check:
    """Run the candidate on every answered input; the first disagreement (or a candidate error where the oracle
    answered) is a counterexample. Nothing compared is ``inconclusive`` (CG-001)."""
    if not cases:
        return Check("inconclusive", 0, skipped, reason="the oracle answered no input in the domain")
    row = runner.evaluate(sketch, [control], [inp for inp, _ in cases])[0]
    for index, ((inp, expected), got) in enumerate(zip(cases, row, strict=True)):
        if "ok" not in got or not same(got["ok"], expected):
            return Check("counterexample", index + 1, skipped, inp, expected, got)
    return Check("no_counterexample", len(cases), skipped)


@dataclass(frozen=True)
class Counterexample:
    """An input of the domain on which a verifier says the candidate breaks the spec. ``cegis`` uses it only after
    running the candidate there (out of process) and finding that it disagrees with the oracle."""

    inputs: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoCounterexample:
    """A verifier decided that no input of its encoded domain breaks the spec: a decision on the caller's encoding,
    so at most ``qualified-draft``, never ``verified``."""

    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Inconclusive:
    """A verifier reached no decision, with a stable reason such as ``z3_unknown`` or ``z3_timeout``. It is degraded and
    never read as "no counterexample" (audit 027, CG-008: the archive read Z3 unknown as UNSAT, and a Rosette timeout as
    "unavailable" while the sampled verdict stood, with no disclosure)."""

    degradation_reason: str
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return True


Outcome = Counterexample | NoCounterexample | Inconclusive


class Verifier(Protocol):
    def find_counterexample(self, sketch: Sketch, control: Control, domain: InputDomain) -> Outcome: ...


__all__ = ["Check", "Counterexample", "Inconclusive", "InputDomain", "NoCounterexample", "Oracle", "Outcome",
           "TableOracle", "Verifier", "answered", "check_candidate"]
