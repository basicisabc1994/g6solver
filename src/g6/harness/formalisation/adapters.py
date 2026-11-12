# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Observation adapters: how a confirmed executable example is bound to a statement's formal terms (15.3 check 1).

An adapter is versioned and belongs to one tested fragment. It says in words why it does not support a statement or
an observation, and an unsupported one stays visible: it is never bound through another observation's encoding, and
never counted as passed or failed. Support is advertised per fragment id (``FRAGMENTS``), never by a language name.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from g6.harness.formalisation.statement import FormalStatement


@dataclass(frozen=True)
class ExampleObservation:
    """One confirmed example as an adapter reads it: the call, the outcome the person confirmed, and the outcome(s)
    of the same question they did not choose (the rejected-outcome witnesses, section 11.2 item 3)."""

    expectation_id: str
    answer_sha256: str
    args: tuple
    kwargs: tuple[tuple[str, Any], ...]
    accepted: Mapping[str, Any]
    rejected: tuple[Mapping[str, Any], ...]
    function: str = ""


def from_expectation(one: Any) -> ExampleObservation:
    """The example a ``ConfirmedExpectation`` holds, read from its question's record and the chosen option."""
    shown = one.question.to_dict()
    chosen = one.option
    return ExampleObservation(
        expectation_id=one.expectation_id, answer_sha256=one.answer_sha256, args=tuple(shown["args"]),
        kwargs=tuple(sorted(shown["kwargs"].items())), accepted=dict(shown["outcomes"][chosen]),
        rejected=tuple(dict(outcome) for label, outcome in sorted(shown["outcomes"].items()) if label != chosen),
        function=shown["function"])


@dataclass(frozen=True)
class Bound:
    """An observation as typed formal terms: the conjuncts fixing the inputs and the observed outcome.
    ``with_relation`` is false when the outcome is decided by a definition alone (a raise, by its guard), so the
    statement's relation over the result is not part of the question. ``unsupported`` says why nothing was bound."""

    conjuncts: tuple[str, ...]
    unsupported: str = ""
    with_relation: bool = True


class Adapter(Protocol):
    id: str
    fragment: str
    requires_function: bool

    def supports(self, statement: FormalStatement) -> str: ...

    def sorts(self, statement: FormalStatement) -> dict[str, str]: ...

    def values(self, statement: FormalStatement, case: ExampleObservation) -> tuple[dict[str, Any], str]: ...

    def bind(self, statement: FormalStatement, case: ExampleObservation, outcome: Mapping[str, Any]) -> Bound: ...

    def concretise(self, statement: FormalStatement, assignment: Mapping[str, Any]) -> dict | None: ...


#: Adapter id -> adapter. Filled by ``g6.harness.formalisation`` as each fragment's module is imported.
ADAPTERS: dict[str, Adapter] = {}
#: Fragment id -> ``{"language", "adapter", "renderer", "tested", "tests"}``. ``tests`` are the test modules that name
#: the adapter; ``tested`` is true only when there is at least one.
FRAGMENTS: dict[str, dict] = {}


def register(adapter: Adapter, *, language: str, tests: tuple[str, ...], renderer: str = "") -> None:
    ADAPTERS[adapter.id] = adapter
    FRAGMENTS[adapter.fragment] = {"language": language, "adapter": adapter.id, "renderer": renderer,
                                   "tested": bool(tests), "tests": tuple(tests)}


__all__ = ["ADAPTERS", "FRAGMENTS", "Adapter", "Bound", "ExampleObservation", "from_expectation", "register"]
