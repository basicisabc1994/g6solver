# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""From intent to a formal property, with the translation checked (G6 0.3 design, section 15).

A person-accepted formalisation, with named checks and limits: a formal statement is a hypothesis until five
translation checks have examined it and the person has accepted its displayed rule and scope. This package holds the
statement record, named engine queries with explicit polarity, one observation adapter per supported fragment and
the checks. It calls the engines G6 already ships and re-implements none.

Support is advertised per tested fragment (``FRAGMENTS``), never by a language name. A language, fragment or adapter
that is not listed is refused with ``G6_E_FORMALISATION_UNSUPPORTED``.

The package sits under ``g6.harness`` because every top-level package of ``g6`` is a registered component
(``g6.core.registry``), and this is not one: it has no block and no ops, and its one surface is an MCP tool.
"""
from __future__ import annotations

from g6.harness.formalisation import smt_adapter
from g6.harness.formalisation.adapters import ADAPTERS, FRAGMENTS, Bound, ExampleObservation, from_expectation, register
from g6.harness.formalisation.checks import CheckOutcome, check_domain, check_examples
from g6.harness.formalisation.query import DECIDED, READINGS, EngineQuery, QueryResult, differ, valid_under, witness
from g6.harness.formalisation.statement import FormalStatement, Observation

UNSUPPORTED = "G6_E_FORMALISATION_UNSUPPORTED"

register(smt_adapter.ADAPTER, language="smt", tests=("tests/formalisation/test_smt_adapter.py",))


class Unsupported(ValueError):
    """The statement's language, fragment or adapter is not one G6 supports (``G6_E_FORMALISATION_UNSUPPORTED``)."""

    code = UNSUPPORTED


def supported(statement: FormalStatement) -> None:
    """``Unsupported`` unless the statement's fragment is listed and its own adapter supports it."""
    entry = FRAGMENTS.get(statement.fragment)
    if entry is None or entry["language"] != statement.language or entry["adapter"] != statement.adapter:
        raise Unsupported(f"[{UNSUPPORTED}] {statement.language}/{statement.fragment} with adapter "
                          f"{statement.adapter!r} is not a supported fragment; supported: "
                          + ", ".join(sorted(FRAGMENTS)))
    why = ADAPTERS[entry["adapter"]].supports(statement)
    if why:
        raise Unsupported(f"[{UNSUPPORTED}] {why}")


__all__ = ["ADAPTERS", "DECIDED", "FRAGMENTS", "READINGS", "UNSUPPORTED", "Bound", "CheckOutcome", "EngineQuery",
           "ExampleObservation", "FormalStatement", "Observation", "QueryResult", "Unsupported", "check_domain",
           "check_examples", "differ", "from_expectation", "register", "supported", "valid_under", "witness"]
