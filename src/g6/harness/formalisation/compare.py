# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Diverse candidate comparison (design section 15.3, check 3).

Two families' drafts of one statement, within one scope, are compared by the engine: do the two relations differ
anywhere in the accepted domain? The check passes when equivalence is decided there, or when a disagreement is
settled by something already accepted (a confirmed example one draft breaks). This is diversity, not demonstrated
independence, and agreement says nothing about intent: two copies of a wrong formula agree. A passed comparison
leaves the statement still needing the person.

A disagreement is an engine assignment. It becomes something a person can be asked about only when the adapter
makes it an executable call and outcome: the disputed observation itself, never invented unique results. Otherwise
it stays a model disagreement and the check is incomplete. One draft missing, or both from one family, is
incomplete too.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.adapters import Adapter, ExampleObservation
from g6.harness.formalisation.checks import CheckOutcome, check_examples
from g6.harness.formalisation.draft import Draft
from g6.harness.formalisation.query import differ, witness

#: What happens when only one model family is available (the plan's owner question 1). ``stop_unconfirmed``: the
#: comparison is incomplete and nothing can be accepted. ``accept_with_limit`` is the other answer the owner may
#: give: the comparison then passes with the limit recorded for the form and the receipt. One constant, one branch.
SINGLE_FAMILY = "stop_unconfirmed"
SINGLE_FAMILY_LIMIT = ("one model family drafted this statement: no diverse-candidate comparison was run, so "
                       "nothing but its own checks and your reading stands against a shared misreading")


@dataclass(frozen=True)
class Disputed:
    call: dict
    observation: Mapping[str, Any]
    permitted_by: tuple[str, ...]
    unique: bool


def _outcome(status: str, *reasons: str, queries=(), **detail) -> CheckOutcome:
    return CheckOutcome("comparison", status, tuple(reasons), tuple(queries), MappingProxyType(detail))


def _undecided(found, what: str) -> CheckOutcome:
    return _outcome("unavailable" if found.reading == "unavailable" else "incomplete",
                    f"{what}: the engine did not decide ({found.reading}; {found.code or found.native}); that is "
                    "not an answer either way", queries=(found,))


def compare(a: Draft, b: Draft, *, adapter: Adapter, cases: Sequence[ExampleObservation] = (),
            single_family: str = SINGLE_FAMILY, timeout: float = 10.0) -> tuple[CheckOutcome, Disputed | None]:
    """Check 3 on two drafts. ``(outcome, disputed)``: ``disputed`` only for a disagreement the adapter made an
    executable observation and nothing accepted settles."""
    drafted = [d for d in (a, b) if d.status == "drafted" and d.statement is not None]
    if len(drafted) < 2:
        missing = next((d for d in (a, b) if d not in drafted), a)
        if len(drafted) == 1 and single_family == "accept_with_limit":
            return _outcome("passed", limit=SINGLE_FAMILY_LIMIT, families=[drafted[0].family]), None
        return _outcome("incomplete", f"a second draft is missing ({missing.status}"
                        + (f": {missing.reason}" if missing.reason else "") + "): one family is never treated as "
                        "two, so the comparison was not made"), None
    if a.family == b.family:
        return _outcome("incomplete", f"both drafts are from the family {a.family!r}: that is one voice, not a "
                                      "diverse comparison"), None
    one, two = a.statement, b.statement
    if (one.observation != two.observation or one.domain != two.domain or one.fragment != two.fragment
            or one.adapter != two.adapter or adapter.supports(one) or adapter.supports(two)):
        return _outcome("incomplete", "the two drafts are not within one supported scope (observation, domain, "
                                      "fragment and adapter)"), None
    sorts = adapter.sorts(one)
    premises = [*one.domain, *sorted({*one.assumptions, *two.assumptions})]
    queries = []
    guards = (one.definition("raises_when") or "false", two.definition("raises_when") or "false")
    found = differ(dict(one.observation.inputs), premises, guards[0], guards[1], statement_sha256=one.sha256,
                   timeout=timeout)
    queries.append(found)
    if not found.decided:
        return _undecided(found, "whether the drafts raise on the same inputs"), None
    if found.reading == "equivalent_under_assumptions":
        found = differ(sorts, [*premises, f"(not {guards[0]})"], one.text, two.text, statement_sha256=one.sha256,
                       timeout=timeout)
        queries.append(found)
        if not found.decided:
            return _undecided(found, "whether the drafts differ"), None
        if found.reading == "equivalent_under_assumptions":
            return _outcome("passed", queries=queries, families=[a.family, b.family],
                            note="equivalence was decided in the accepted domain; agreement alone never accepts "
                                 "the intent"), None
    # a disagreement: first, does anything already accepted settle it?
    if cases:
        verdicts = [check_examples(d.statement, cases, timeout=timeout).status for d in (a, b)]
        if sorted(verdicts) == ["failed", "passed"]:
            kept = (a, b)[verdicts.index("passed")]
            return _outcome("passed", queries=queries, families=[a.family, b.family], kept=kept.family,
                            resolved_by="a confirmed example the other draft breaks",
                            note="the disagreement was settled by an accepted example, without a question"), None
    values = dict(found.witness or {})
    call = None
    for statement in (one, two):
        call = call or adapter.concretise(statement, values)
    names = [name for name, _ in one.observation.inputs]
    if call is None or any(name not in values for name in names):
        return _outcome("incomplete", "model disagreement, not concretised: the engine's assignment is not an "
                                      "executable observation, so it cannot be asked about", queries=queries), None
    inputs = {name: values[name] for name in names}
    permitted = tuple(d.family for d in (a, b) if _permits(d.statement, adapter, inputs, call["outcome"]))
    fixed = [f"(= {name} {smt_ir.write(_literal(inputs[name]))})" for name in names]
    unique = all(_one_result(d.statement, adapter, premises, fixed, timeout) for d in (a, b))
    disputed = Disputed(call={"function": one.observation.entry_point, "args": call["args"], "kwargs": call["kwargs"]},
                        observation=dict(call["outcome"]), permitted_by=permitted, unique=unique)
    return _outcome("incomplete", f"the drafts disagree on an observation: {one.observation.entry_point}"
                    f"({', '.join(repr(v) for v in call['args'])}) -> {dict(call['outcome'])!r} is permitted by "
                    f"{', '.join(permitted) or 'neither'}; only the person, or something already accepted, settles it",
                    queries=queries, disputed={"call": disputed.call, "observation": dict(disputed.observation),
                                               "permitted_by": list(permitted), "unique": unique}), disputed


def _literal(value: Any) -> smt_ir.Term:
    return smt_ir.BoolLit(value) if type(value) is bool else smt_ir.IntLit(value)


def _permits(statement, adapter: Adapter, inputs: Mapping[str, Any], outcome: Mapping[str, Any]) -> bool:
    guard = statement.definition("raises_when")
    raising = bool(guard and smt_ir.evaluate(smt_ir.read(guard, dict(statement.observation.inputs)), inputs))
    if "raises" in outcome:
        return raising
    if raising:
        return False
    return bool(smt_ir.evaluate(smt_ir.read(statement.text, adapter.sorts(statement)),
                                {**inputs, "result": outcome["returns"]}))


def _one_result(statement, adapter: Adapter, premises: list[str], fixed: list[str], timeout: float) -> bool:
    """Whether the statement gives exactly one result at this input: decided, and no second result exists."""
    sorts = adapter.sorts(statement)
    other = smt_ir.write(smt_ir.substitute(smt_ir.read(statement.text, sorts), "result",
                                           smt_ir.Var("result__other", sorts["result"])))
    found = witness({**sorts, "result__other": sorts["result"]},
                    [*premises, *fixed, statement.text, other, "(distinct result result__other)"],
                    statement_sha256=statement.sha256, timeout=timeout)
    return found.reading == "no_witness_for_this_encoding"


def both_permitted(a: Draft, b: Draft, adapter: Adapter, disputed: Disputed, *, timeout: float = 10.0) -> dict | None:
    """For an overlapping dispute: an outcome both drafts permit on the disputed input (``{"returns": v}``), found
    with a witness query, or ``None`` when there is none or the engine did not decide."""
    one, two = a.statement, b.statement
    names = [name for name, _ in one.observation.inputs]
    fixed = [f"(= {name} {smt_ir.write(_literal(value))})" for name, value in zip(names, disputed.call["args"])]
    found = witness(adapter.sorts(one), [*one.domain, *fixed, one.text, two.text], statement_sha256=one.sha256,
                    timeout=timeout)
    return {"returns": found.witness["result"]} if found.reading == "witness" else None


__all__ = ["SINGLE_FAMILY", "SINGLE_FAMILY_LIMIT", "Disputed", "both_permitted", "compare"]