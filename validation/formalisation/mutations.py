# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Deterministic mutations of a reference formula, and their labels (design 15.7).

A mutant is labelled ``wrong`` only with a witness: an observation of the case's exhaustive table on which the
reference and the mutant disagree. A mutant the solver decides equivalent in the domain is ``equivalent`` and is
never counted as wrong; anything else is ``unlabelled`` and is counted apart. A wrong mutant that agrees with the
reference on the confirmed example and differs elsewhere is its own stratum: checks 1 and 2 cannot see it.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.render import Rendering, render
from g6.harness.formalisation.statement import FormalStatement

MUTATIONS = ("boundary", "omitted_guard", "constant", "swap_result_input", "domain")
_BOUNDARY = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}


def _sites(term: smt_ir.Term, change) -> list[smt_ir.Term]:
    """Every term made by applying ``change`` (a term -> its replacements) at exactly one position of ``term``."""
    out = list(change(term))
    parts = smt_ir.children(term)
    for i, child in enumerate(parts):
        for made in _sites(child, change):
            out.append(smt_ir.rebuilt(term, (*parts[:i], made, *parts[i + 1:])))
    return out


def _boundary(term):
    return [smt_ir.Compare(_BOUNDARY[term.op], term.left, term.right)] if (
        isinstance(term, smt_ir.Compare) and term.op in _BOUNDARY) else []


def _omitted_guard(term):
    return [term.right] if isinstance(term, smt_ir.Implies) else [term.then] if isinstance(term, smt_ir.Ite) else []


def _constant(term):
    return [smt_ir.IntLit(term.value + 1)] if isinstance(term, smt_ir.IntLit) else []


def _swap(term: smt_ir.Term, a: str, b: str) -> smt_ir.Term:
    if isinstance(term, smt_ir.Var):
        return smt_ir.Var(b if term.name == a else a if term.name == b else term.name, term.sort)
    return smt_ir.rebuilt(term, tuple(_swap(child, a, b) for child in smt_ir.children(term)))


def mutants(term: smt_ir.Term, sorts: Mapping[str, str]) -> list[tuple[str, smt_ir.Term]]:
    """Every single-site mutant of a relation, by kind, each still a term of the grammar and not the original."""
    out: list[tuple[str, smt_ir.Term]] = []
    for kind, change in (("boundary", _boundary), ("omitted_guard", _omitted_guard), ("constant", _constant)):
        out += [(kind, made) for made in _sites(term, change)]
    for name, sort in sorts.items():
        if name != "result" and sort == sorts.get("result"):
            out.append(("swap_result_input", _swap(term, "result", name)))
    seen, kept = {smt_ir.write(term)}, []
    for kind, made in out:
        try:
            text = smt_ir.write(smt_ir.read(smt_ir.write(made), sorts))   # still in the grammar
        except smt_ir.OutsideFragment:
            continue
        if text not in seen:
            seen.add(text)
            kept.append((kind, made))
    return kept


def domain_mutants(domain: Sequence[str], inputs: Mapping[str, str]) -> list[tuple[str, tuple[str, ...]]]:
    """Every single-site boundary or constant change of one domain term, as kind ``domain``."""
    out = []
    for i, text in enumerate(domain):
        term = smt_ir.read(text, inputs)
        for change in (_boundary, _constant):
            for made in _sites(term, change):
                out.append(("domain", (*domain[:i], smt_ir.write(made), *domain[i + 1:])))
    return out


def _results(table: Sequence[tuple[Mapping[str, Any], Any]]) -> list[Any]:
    found: dict[tuple[str, Any], Any] = {}   # keyed by type too: True is not 1
    for _, expected in table:
        for value in ([True, False] if type(expected) is bool else [expected - 1, expected, expected + 1]):
            found.setdefault((type(value).__name__, value), value)
    return list(found.values())


def labelled(reference: smt_ir.Term, mutant: smt_ir.Term, domain: Sequence[str], *, sorts: Mapping[str, str],
             table: Sequence[tuple[Mapping[str, Any], Any]], example: Mapping[str, Any] | None = None) -> dict:
    """``{"label", "witness", "stratum"}``. ``wrong`` only with a witness from the exhaustive table; ``equivalent``
    only when the solver decides it in the domain; else ``unlabelled``."""
    candidates = _results(table)
    witness, agrees_on_example = None, True
    for inputs, _ in table:
        for result in candidates:
            if (type(result) is bool) != (sorts["result"] == "Bool"):
                continue
            values = {**inputs, "result": result}
            ref, mut = smt_ir.evaluate(reference, values), smt_ir.evaluate(mutant, values)
            if ref != mut:
                witness = witness or {"inputs": dict(inputs), "result": result, "reference": ref, "mutant": mut}
                if example is not None and dict(inputs) == dict(example):
                    agrees_on_example = False
    if witness is not None:
        stratum = "agrees_on_example" if example is not None and agrees_on_example else "differs_on_example"
        return {"label": "wrong", "witness": witness, "stratum": stratum}
    from g6.harness.formalisation.query import differ

    found = differ(dict(sorts), list(domain), smt_ir.write(reference), smt_ir.write(mutant), statement_sha256="0" * 64)
    if found.reading == "equivalent_under_assumptions":
        return {"label": "equivalent", "witness": None, "stratum": "equivalent"}
    return {"label": "unlabelled", "witness": None, "stratum": "unlabelled"}


def wrong_renderings(statement: FormalStatement) -> list[tuple[str, Rendering]]:
    """Row 2's other axis, built by the harness and never by a renderer: the words of each mutant, paired with the
    original statement's formal text and digests. A right formula shown with wrong words."""
    from g6.harness.formalisation import smt_adapter

    sorts = smt_adapter.ADAPTER.sorts(statement)
    right = render(statement)
    out = []
    for kind, mutant in mutants(smt_ir.read(statement.text, sorts), sorts):
        try:
            other = render(dataclasses.replace(statement, text=smt_ir.write(mutant)))
        except ValueError:
            continue
        if other.words != right.words:
            out.append((kind, dataclasses.replace(right, words=other.words)))
    return out


__all__ = ["MUTATIONS", "domain_mutants", "labelled", "mutants", "wrong_renderings"]