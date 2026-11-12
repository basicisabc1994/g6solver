# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The observation adapter ``smt.return.int_bool.v1`` of fragment ``smt.relation.int_bool.v1``.

The statement is one relation (a Bool term of ``smt_ir``'s grammar) over the function's Int and Bool parameters and
``result``, the value it returns. What the adapter binds:

- a confirmed ``returns`` of an ``int`` or a ``bool`` (``True`` is never bound as ``1``, nor ``1`` as ``True``);
- a confirmed ``raises`` only when the statement carries the definition ``raises_when`` (a Bool term over the
  parameters) and names the error it stands for: the raise needs the guard true, a return needs it false;
- nothing else. An ``effect``, a float, a string, a collection, a keyword the function does not have: each gives
  ``Bound.unsupported`` with the reason. An encoding of a return never stands for an effect.

Definitions named ``entails_<name>`` are named entailment obligations (Bool terms over the parameters and
``result``), checked through ``g6.formal.check_contract`` by the domain check and never used to bind an example.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.adapters import Bound, ExampleObservation
from g6.harness.formalisation.query import QueryResult, witness
from g6.harness.formalisation.statement import FormalStatement, Observation

ID = "smt.return.int_bool.v1"
FRAGMENT = "smt.relation.int_bool.v1"
RESULT = "result"
RAISES_WHEN = "raises_when"
ENTAILS = "entails_"
_OTHER = "result__other"
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}\Z")


def _literal(value: Any, sort: str) -> str | None:
    """The SMT literal of a Python value of exactly this sort, or ``None``."""
    if sort == "Int" and type(value) is int:
        return smt_ir.write(smt_ir.IntLit(value))
    if sort == "Bool" and type(value) is bool:
        return "true" if value else "false"
    return None


def _kind(value: Any) -> str:
    name = type(value).__name__
    return "None" if value is None else f"{'an' if name[0] in 'aeiou' else 'a'} {name}"


class SmtReturnAdapter:
    id = ID
    fragment = FRAGMENT
    #: The relation may permit several results for one input; uniqueness is not asked unless a route needs it.
    requires_function = False

    def sorts(self, statement: FormalStatement) -> dict[str, str]:
        return {**dict(statement.observation.inputs), RESULT: statement.observation.result_sort}

    def _inputs(self, statement: FormalStatement) -> dict[str, str]:
        return dict(statement.observation.inputs)

    def supports(self, statement: FormalStatement) -> str:
        """``""`` when the statement is in the fragment and in canonical form, else why not."""
        seen = statement.observation
        if statement.language != "smt" or statement.fragment != FRAGMENT:
            return f"the statement is {statement.language}/{statement.fragment}, not {FRAGMENT}"
        if statement.adapter != ID:
            return f"the statement names the adapter {statement.adapter!r}, not {ID}"
        if seen.kind != "returns":
            return (f"the statement constrains {seen.kind!r}: this adapter binds a returned int or bool (and a raise "
                    f"only through a {RAISES_WHEN} guard); an effect has no reviewed encoding here")
        if statement.bounds:
            return "this fragment has no bounds; a bound here would carry no meaning"
        try:
            inputs = smt_ir.check_sorts(self._inputs(statement))
            if RESULT in inputs:
                return f"a parameter named {RESULT!r} would be confused with the returned value"
            sorts = smt_ir.check_sorts(self.sorts(statement))
            for what, text, over in ([("the statement", statement.text, sorts)]
                                     + [("a domain term", t, inputs) for t in statement.domain]
                                     + [("an assumption", t, inputs) for t in statement.assumptions]):
                term = smt_ir.read(text, over)
                if smt_ir.sort_of(term) != "Bool":
                    return f"{what} is an Int term where a relation (a Bool term) is needed"
                if smt_ir.write(term) != text:
                    return f"{what} is not in canonical form: {text[:60]!r}"
            for name, text in statement.definitions:
                if name != RAISES_WHEN and not (name.startswith(ENTAILS) and _PLAIN.match(name[len(ENTAILS):])):
                    return (f"the definition {name[:40]!r} (this fragment has {RAISES_WHEN} and {ENTAILS}<name>, the "
                            "name plain letters, digits and _)")
                term = smt_ir.read(text, inputs if name == RAISES_WHEN else sorts)
                if smt_ir.sort_of(term) != "Bool" or smt_ir.write(term) != text:
                    return f"the definition {name!r} is not a canonical Bool term"
        except smt_ir.OutsideFragment as exc:
            return f"outside {FRAGMENT}: {exc}"
        if statement.definition(RAISES_WHEN) is not None and not seen.error:
            return f"a {RAISES_WHEN} guard needs the observation to name the error it stands for"
        if statement.definition(RAISES_WHEN) is None and seen.error:
            return f"the observation names the error {seen.error!r} and the statement has no {RAISES_WHEN} guard"
        return ""

    def values(self, statement: FormalStatement, case: ExampleObservation) -> tuple[dict[str, Any], str]:
        """The example's value for every parameter, by name, or why the call cannot be bound."""
        seen = statement.observation
        if case.function and case.function != seen.entry_point:
            return {}, f"the example calls {case.function}, the statement is about {seen.entry_point}"
        names = [name for name, _ in seen.inputs]
        if len(case.args) > len(names):
            return {}, f"the example passes {len(case.args)} positional arguments to {len(names)} parameters"
        given = dict(zip(names, case.args, strict=False))
        for name, value in case.kwargs:
            if name not in names or name in given:
                return {}, f"the example's keyword {name!r} is not one parameter of the statement, given once"
            given[name] = value
        missing = [name for name in names if name not in given]
        if missing:
            return {}, f"the example gives no value for {', '.join(missing)}"
        for name, sort in seen.inputs:
            if _literal(given[name], sort) is None:
                return {}, f"the value of {name!r} is {_kind(given[name])}, and the statement declares it {sort}"
        return {name: given[name] for name in names}, ""

    def _call(self, statement: FormalStatement, case: ExampleObservation) -> tuple[list[str], str]:
        """The conjuncts fixing every parameter to the example's value, or why the call cannot be bound."""
        given, why = self.values(statement, case)
        if why:
            return [], why
        return [f"(= {name} {_literal(given[name], sort)})" for name, sort in statement.observation.inputs], ""

    def bind(self, statement: FormalStatement, case: ExampleObservation, outcome: Mapping[str, Any]) -> Bound:
        why = self.supports(statement)
        if why:
            return Bound((), why)
        conjuncts, why = self._call(statement, case)
        if why:
            return Bound((), why)
        if len(outcome) != 1 or next(iter(outcome)) not in ("returns", "raises", "effect"):
            return Bound((), "an outcome states exactly one of returns, raises or effect")
        kind, value = next(iter(outcome.items()))
        guard = statement.definition(RAISES_WHEN)
        if kind == "effect":
            return Bound((), f"an effect is outside {ID}: an encoding of a return never stands for an effect")
        if kind == "raises":
            if guard is None:
                return Bound((), f"a raise is bound only through a {RAISES_WHEN} guard, and the statement has none")
            if value != statement.observation.error:
                return Bound((), f"the guard stands for {statement.observation.error}, the example raises {value}")
            return Bound((*conjuncts, guard), with_relation=False)
        literal = _literal(value, statement.observation.result_sort)
        if literal is None:
            return Bound((), f"the returned value is {_kind(value)}, and the statement declares the result "
                             f"{statement.observation.result_sort}")
        return Bound((*conjuncts, f"(= {RESULT} {literal})", *([f"(not {guard})"] if guard is not None else [])))

    def concretise(self, statement: FormalStatement, assignment: Mapping[str, Any]) -> dict | None:
        """The executable call and outcome an engine's assignment stands for, or ``None`` when it is not one: every
        parameter and the result present with a value of its sort."""
        if self.supports(statement):
            return None
        seen = statement.observation
        python = {"Int": int, "Bool": bool}
        args = []
        for name, sort in seen.inputs:
            if name not in assignment or type(assignment[name]) is not python[sort]:
                return None
            args.append(assignment[name])
        guard = statement.definition(RAISES_WHEN)
        if guard is not None and smt_ir.evaluate(smt_ir.read(guard, self._inputs(statement)),
                                                 dict(zip([n for n, _ in seen.inputs], args, strict=True))):
            return {"args": args, "kwargs": {}, "outcome": {"raises": seen.error}}
        if RESULT not in assignment or type(assignment[RESULT]) is not python[seen.result_sort]:
            return None
        return {"args": args, "kwargs": {}, "outcome": {"returns": assignment[RESULT]}}

    def two_results(self, statement: FormalStatement, *, timeout: float = 10.0) -> QueryResult:
        """Is there an input in the domain for which the relation permits two different results? A ``witness`` is such
        an input with both results; ``no_witness_for_this_encoding`` means the relation gives at most one."""
        sorts = self.sorts(statement)
        relation = smt_ir.read(statement.text, sorts)
        other_sorts = {**sorts, _OTHER: statement.observation.result_sort}
        other = smt_ir.substitute(relation, RESULT, smt_ir.Var(_OTHER, statement.observation.result_sort))
        return witness(other_sorts, [*statement.domain, *statement.assumptions, statement.text, smt_ir.write(other),
                                     f"(distinct {RESULT} {_OTHER})"],
                       statement_sha256=statement.sha256, timeout=timeout)


def statement(*, entry_point: str, inputs: tuple[tuple[str, str], ...], result_sort: str, relation: str,
              domain: tuple[str, ...] = (), assumptions: tuple[str, ...] = (), raises_when: str = "", error: str = "",
              entails: Mapping[str, str] | None = None, setup: str = "") -> FormalStatement:
    """A statement of this fragment with every term in canonical form, or ``smt_ir.OutsideFragment``."""
    inputs = tuple((name, sort) for name, sort in inputs)
    over_inputs = dict(inputs)
    sorts = {**over_inputs, RESULT: result_sort}
    definitions = []
    if raises_when:
        definitions.append((RAISES_WHEN, smt_ir.canonical(raises_when, over_inputs)))
    for name, text in sorted((entails or {}).items()):
        definitions.append((ENTAILS + name, smt_ir.canonical(text, sorts)))
    return FormalStatement(
        language="smt", fragment=FRAGMENT, text=smt_ir.canonical(relation, sorts),
        observation=Observation(kind="returns", entry_point=entry_point, inputs=inputs, result_sort=result_sort,
                                error=error, setup=setup),
        definitions=tuple(definitions), domain=tuple(smt_ir.canonical(t, over_inputs) for t in domain),
        assumptions=tuple(smt_ir.canonical(t, over_inputs) for t in assumptions), adapter=ID)


ADAPTER = SmtReturnAdapter()

__all__ = ["ADAPTER", "ENTAILS", "FRAGMENT", "ID", "RAISES_WHEN", "RESULT", "SmtReturnAdapter", "statement"]
