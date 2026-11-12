# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The translation checks of section 15.3. This module holds checks 1 and 2 for the SMT fragment.

A check's outcome is one of ``passed``, ``failed``, ``incomplete``, ``unsupported`` or ``unavailable``. Only a
decided engine answer makes ``passed`` or ``failed``: an unknown, a timeout, a refusal, a missing engine, a missing
adapter or an observation the adapter does not bind leaves the check ``incomplete``, ``unavailable`` or
``unsupported``, with the reason kept. A passed check says the statement survived this check on this scope; it does
not say the statement is what the person meant, and it accepts nothing.

1. ``check_examples``: every confirmed example, bound through the statement's adapter, must be permitted by the
   statement, and every outcome the person rejected for the same call must be excluded by it.
2. ``check_domain``: asked apart and reported apart: the domain has a witness; each guard is reachable in it; the
   domain, assumptions and statement hold jointly, overall and where each guard holds; the statement excludes some
   observation (a relation that permits everything constrains nothing); the accepted observations satisfy the
   relation and the rejected ones violate it. Named entailment obligations (``entails_<name>`` definitions) go
   through ``g6.formal.check_contract``; nothing else does, because a specification relation need not hold for
   every unconstrained output.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.adapters import ADAPTERS, ExampleObservation
from g6.harness.formalisation.query import QueryResult, witness
from g6.harness.formalisation.statement import FormalStatement

CHECKS = ("examples", "domain", "comparison", "rendering", "scope")
STATUSES = ("passed", "failed", "incomplete", "unsupported", "unavailable")
_ENTAILS = "entails_"


@dataclass(frozen=True)
class CheckOutcome:
    check: Literal["examples", "domain", "comparison", "rendering", "scope"]
    status: Literal["passed", "failed", "incomplete", "unsupported", "unavailable"]
    reasons: tuple[str, ...]
    queries: tuple[QueryResult, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.check not in CHECKS or self.status not in STATUSES:
            raise ValueError("a check outcome names one of the five checks and one of the five statuses")

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status, "reasons": list(self.reasons),
                "queries": [q.to_dict() for q in self.queries], "detail": dict(self.detail)}


class _Tally:
    """What one check found, kept apart by kind so the status is read from all of it at the end."""

    def __init__(self) -> None:
        self.failed: list[str] = []
        self.unsupported: list[str] = []
        self.undecided: list[str] = []
        self.unavailable: list[str] = []
        self.incomplete: list[str] = []
        self.queries: list[QueryResult] = []

    def ask(self, found: QueryResult, what: str) -> QueryResult:
        self.queries.append(found)
        if not found.decided:
            (self.unavailable if found.reading == "unavailable" else self.undecided).append(
                f"{what}: the engine did not decide ({found.reading}; {found.code or found.native}); that is not an "
                "answer either way")
        return found

    def outcome(self, check: str, detail: Mapping[str, Any] | None = None) -> CheckOutcome:
        status = ("failed" if self.failed else "unsupported" if self.unsupported else
                  "unavailable" if self.unavailable else "incomplete" if self.undecided or self.incomplete else
                  "passed")
        reasons = (*self.failed, *self.unsupported, *self.unavailable, *self.undecided, *self.incomplete)
        return CheckOutcome(check, status, reasons, tuple(self.queries), MappingProxyType(dict(detail or {})))


def _adapter(statement: FormalStatement, check: str) -> tuple[Any, CheckOutcome | None]:
    adapter = ADAPTERS.get(statement.adapter)
    if adapter is None:
        return None, CheckOutcome(check, "unsupported", (f"no adapter {statement.adapter!r} is registered: the "
                                                         "statement stays unconfirmed",))
    why = adapter.supports(statement)
    if why:
        return None, CheckOutcome(check, "unsupported", (why,))
    return adapter, None


def _observations(statement: FormalStatement, adapter: Any, cases: Sequence[ExampleObservation], tally: _Tally,
                  timeout: float) -> dict:
    """Ask, for every case, that the accepted outcome is permitted and each rejected one excluded."""
    sorts = adapter.sorts(statement)
    counts = {"accepted_permitted": 0, "rejected_excluded": 0, "cases": len(cases)}

    def asked(case: ExampleObservation, outcome: Mapping[str, Any], what: str) -> QueryResult | None:
        bound = adapter.bind(statement, case, outcome)
        if bound.unsupported:
            tally.unsupported.append(f"{what} of {case.expectation_id or 'an example'} is not bound: "
                                     f"{bound.unsupported}")
            return None
        relation = [statement.text] if bound.with_relation else []
        return tally.ask(witness(sorts, [*statement.domain, *statement.assumptions, *bound.conjuncts, *relation],
                                 statement_sha256=statement.sha256, timeout=timeout), what)

    for case in cases:
        name = case.expectation_id or "an example"
        found = asked(case, case.accepted, "the confirmed outcome")
        if found is not None and found.decided:
            if found.reading == "witness":
                counts["accepted_permitted"] += 1
            else:
                tally.failed.append(f"the statement, with its domain and assumptions, does not permit the confirmed "
                                    f"outcome of {name}: {dict(case.accepted)!r}")
        if not case.rejected:
            tally.incomplete.append(f"{name} has no rejected outcome: negative control unavailable")
        for rejected in case.rejected:
            found = asked(case, rejected, "a rejected outcome")
            if found is not None and found.decided:
                if found.reading == "witness":
                    tally.failed.append(f"the statement permits an outcome the person rejected for {name}: "
                                        f"{dict(rejected)!r}")
                else:
                    counts["rejected_excluded"] += 1
    return counts


def check_examples(statement: FormalStatement, cases: Sequence[ExampleObservation], *,
                   timeout: float = 10.0) -> CheckOutcome:
    """Check 1: the confirmed examples through the same observation."""
    adapter, refused = _adapter(statement, "examples")
    if refused is not None:
        return refused
    tally = _Tally()
    if not cases:
        tally.incomplete.append("no confirmed example was given: nothing binds the statement to an observation")
    counts = _observations(statement, adapter, cases, tally, timeout)
    return tally.outcome("examples", counts)


def _entailments(statement: FormalStatement, sorts: Mapping[str, str], tally: _Tally, timeout: float) -> list[dict]:
    from g6.formal import check_contract

    out = []
    premises = [*statement.domain, *statement.assumptions, statement.text]
    for name, text in statement.definitions:
        if not name.startswith(_ENTAILS):
            continue
        found = check_contract([text], premises=premises, sorts=dict(sorts), timeout=timeout)
        out.append({"name": name, "value": found.value, "code": found.code})
        if found.value == "T":
            continue
        if found.value == "F":
            tally.failed.append(f"the named obligation {name} does not follow from the domain, assumptions and "
                                f"statement ({found.code})")
        else:
            tally.undecided.append(f"the named obligation {name} was not decided ({found.value}; {found.code}); "
                                   "that is not an answer either way")
    return out


def check_domain(statement: FormalStatement, cases: Sequence[ExampleObservation] = (), *,
                 timeout: float = 10.0) -> CheckOutcome:
    """Check 2: domain, reachability and consistency, each asked and reported apart."""
    adapter, refused = _adapter(statement, "domain")
    if refused is not None:
        return refused
    sorts = adapter.sorts(statement)
    sha = statement.sha256
    tally = _Tally()
    detail: dict[str, Any] = {}
    premises = [*statement.domain, *statement.assumptions]

    domain = tally.ask(witness(sorts, premises, statement_sha256=sha, timeout=timeout), "the domain")
    detail["domain_has_witness"] = domain.reading
    if domain.decided and domain.reading != "witness":
        tally.failed.append("the domain is empty: no input satisfies it, so nothing can be said under it")
        for name in ("guards_reachable", "jointly_satisfiable", "constrains_behaviour", "observations"):
            detail[name] = "not asked: the domain is empty"
        return tally.outcome("domain", detail)

    guards = []
    for guard in dict.fromkeys(smt_ir.write(g) for g in smt_ir.guards(smt_ir.read(statement.text, sorts))):
        reach = tally.ask(witness(sorts, [*premises, guard], statement_sha256=sha, timeout=timeout),
                          f"the guard {guard}")
        entry = {"guard": guard, "reachable": reach.reading, "holds_with_statement": "not asked"}
        if reach.decided and reach.reading != "witness":
            tally.failed.append(f"the guard {guard} is unreachable in the domain: what it guards says nothing there")
        elif reach.decided:
            joint = tally.ask(witness(sorts, [*premises, guard, statement.text], statement_sha256=sha,
                                      timeout=timeout), f"the statement where {guard} holds")
            entry["holds_with_statement"] = joint.reading
            if joint.decided and joint.reading != "witness":
                tally.failed.append(f"a conflict inside the domain: where {guard} holds, the domain, the assumptions "
                                    "and the statement have no joint witness")
        guards.append(entry)
    detail["guards_reachable"] = guards

    joint = tally.ask(witness(sorts, [*premises, statement.text], statement_sha256=sha, timeout=timeout),
                      "the domain, assumptions and statement together")
    detail["jointly_satisfiable"] = joint.reading
    if joint.decided and joint.reading != "witness":
        tally.failed.append("the domain, the assumptions and the statement cannot hold together")

    excludes = tally.ask(witness(sorts, [*premises, f"(not {statement.text})"], statement_sha256=sha,
                                 timeout=timeout), "whether the statement excludes anything")
    detail["constrains_behaviour"] = excludes.reading
    if excludes.decided and excludes.reading != "witness":
        tally.failed.append("the statement constrains nothing: every observation in the domain satisfies it")

    if getattr(adapter, "requires_function", False):
        two = tally.ask(adapter.two_results(statement, timeout=timeout), "whether the result is unique")
        detail["result_unique"] = two.reading
        if two.decided and two.reading == "witness":
            tally.failed.append("the relation permits two results for one input, and this adapter needs a function")

    obligations = _entailments(statement, sorts, tally, timeout)
    if obligations:
        detail["named_obligations"] = obligations
    detail["observations"] = (_observations(statement, adapter, cases, tally, timeout) if cases else
                              "not asked: no example was given to this check")
    return tally.outcome("domain", detail)


def check_rendering(statement: FormalStatement) -> tuple[CheckOutcome, Any]:
    """Check 4: the statement itself has a rendering by its fragment's exhaustive renderer, the same every time.
    ``(outcome, rendering)``; no rendering when it is unsupported."""
    from g6.harness.formalisation.render import Unrenderable, render

    try:
        shown = render(statement)
        again = render(statement)
    except Unrenderable as exc:
        return CheckOutcome("rendering", "unsupported", (str(exc),)), None
    if shown != again or shown.formal_text != statement.text or shown.statement_sha256 != statement.sha256:
        return CheckOutcome("rendering", "failed", ("the renderer did not give one rendering of this statement",)), None
    return CheckOutcome("rendering", "passed", (), detail=MappingProxyType(
        {"renderer": shown.renderer, "rendering_sha256": shown.sha256})), shown


@dataclass(frozen=True)
class Assessment:
    """What the five checks found for one statement. ``ready_for_person`` only when all five passed: there is no
    accepting state here, because only the person's recorded answer accepts a formalisation."""

    status: Literal["unconfirmed", "ready_for_person"]
    outcomes: tuple[CheckOutcome, ...]
    reasons: tuple[str, ...]
    statement: FormalStatement
    rendering: Any
    scope: Any
    cases: tuple[ExampleObservation, ...] = ()

    @property
    def sha256(self) -> str:
        import hashlib

        from g6.core.ledger import canonical

        return hashlib.sha256(canonical({
            "status": self.status, "statement_sha256": self.statement.sha256,
            "rendering_sha256": self.rendering.sha256 if self.rendering is not None else "",
            "scope_sha256": self.scope.sha256 if self.scope is not None else "",
            "outcomes": [{"check": one.check, "status": one.status, "reasons": list(one.reasons),
                          "queries": [[q.query.native_query, q.native, q.reading] for q in one.queries]}
                         for one in self.outcomes]}).encode("ascii")).hexdigest()

    def outcome(self, check: str) -> CheckOutcome:
        return next(one for one in self.outcomes if one.check == check)


def assess(statement: FormalStatement, standard: Any, cases: Sequence[ExampleObservation], *,
           comparison: CheckOutcome | None = None, timeout: float = 10.0) -> Assessment:
    """All five checks, in order (examples, domain, comparison, rendering, scope), on one statement against one
    standard. A comparison that was not run is ``incomplete``. Whatever the outcome, the statement, its diagnostics
    and the confirmed examples are kept as they were."""
    from g6.harness.formalisation.scope import bind_scope, check_scope

    if comparison is None:
        comparison = CheckOutcome("comparison", "incomplete", ("no diverse-candidate comparison was run",))
    if comparison.check != "comparison":
        raise ValueError("the comparison outcome is the comparison check's")
    cases = tuple(cases)
    shown_outcome, shown = check_rendering(statement)
    if shown is None:
        scope, bound = None, CheckOutcome("scope", "incomplete", ("there is no rendering to bind a scope to",))
    else:
        try:
            scope = bind_scope(statement, shown, standard, cases)
            bound = check_scope(scope, statement, shown, standard)
        except ValueError as exc:
            scope, bound = None, CheckOutcome("scope", "failed", (str(exc),))
    outcomes = (check_examples(statement, cases, timeout=timeout), check_domain(statement, cases, timeout=timeout),
                comparison, shown_outcome, bound)
    ready = all(one.status == "passed" for one in outcomes)
    reasons = tuple(f"{one.check}: {why}" for one in outcomes for why in one.reasons)
    return Assessment("ready_for_person" if ready else "unconfirmed", outcomes, reasons, statement, shown, scope,
                      cases)


__all__ = ["CHECKS", "STATUSES", "Assessment", "CheckOutcome", "assess", "check_domain", "check_examples",
           "check_rendering"]
