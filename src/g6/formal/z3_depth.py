# SPDX-License-Identifier: Apache-2.0
"""Z3 depth: optimise, unsat cores, simplify, quantifier elimination, model counts and equivalence, in process.

The useful operations of the archive's ``z3_mcp`` (owner, 2026-10-20), on the closed SMT-LIB input of
:mod:`g6.formal.z3_bridge`: every symbol declared with its sort (``Int``, ``Real``, ``Bool``, ``String``,
``(_ BitVec n)``, ``(Array S T)``), one term per input, and a timeout on every solver call. Not ported:
``interpolate`` (Z3 dropped interpolation in 4.8, and the archive's op silently fell back to a plain unsat check),
``run_code`` (it ran caller Python with ``exec``, FM-011), the SQLite contexts and their text search.

Z3's answer is checked again on fresh solvers, through the same closed grammar, before it is reported:

* ``optimise``: no feasible point is better than the optimum (a validity question, so the constraints are checked for
  consistency too) and some feasible point reaches it (``attainment``);
* ``unsat_core``: the core alone is unsatisfiable; then each member is dropped in turn and kept only when the rest is
  satisfiable, so the core reported is minimal;
* ``simplify`` and ``eliminate_quantifiers``: ``(= term result)`` is valid, and a quantifier left in the result means
  the elimination did not finish;
* ``count_models``: no model outside those found exists (the count is exact) or one does (the cap was hit, and the
  count is a lower bound; the archive once reported "Found 10 models" for a problem with 55);
* ``check_equivalence``: ``(= a b)`` as a validity check, with its vacuity and circularity flags.

An answer its own check contradicts is ``blocked-escalated`` with ``G6_E_FORMAL_CERTIFICATE_REFUTED``; a confirmed
one is ``qualified-draft``, its certificate (model, core, term, count) in the evidence. Unknown, a timeout included,
is never read as a decision, and nothing here is ``verified``.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from g6.formal.envelope import (
    CERTIFICATE_REFUTED,
    INPUT_INVALID,
    NO_OPTIMUM,
    PARSE_ERROR,
    SOLVER_ERROR,
    SOLVER_TIMEOUT,
    SOLVER_UNKNOWN,
    Refusal,
    apply_envelope,
)
from g6.formal.schema import (
    CoreResult,
    CountResult,
    OptimumResult,
    ProofArtifact,
    SolverCheck,
    TermResult,
    ValidityResult,
)
from g6.formal.z3_bridge import (
    _NAME,
    DEFAULT_TIMEOUT_S,
    _check_query,
    _check_timeout,
    _conj,
    _message,
    _plan,
    _query,
    _sorts,
    _term,
    _terms,
    _undecided,
    _validity,
    _value,
    load_z3,
    run_query,
    z3_version,
)

MAX_OBJECTIVES = 20
MAX_NAMED = 200
MAX_MINIMISE = 50
DEFAULT_CAP = 100
MAX_CAP = 1000

_DIRECTIONS = {"min": "minimise", "minimise": "minimise", "minimize": "minimise",
               "max": "maximise", "maximise": "maximise", "maximize": "maximise"}
_UNBOUNDED = re.compile(r"\boo\b|epsilon")


def _ms(seconds: float) -> int:
    return max(1, int(seconds * 1000))


def _solver(z3: Any, call: Callable[[], Any]) -> Any:
    """``call()``, with a Z3 failure as a refusal: a timeout or cancellation as such, anything else as an error."""
    try:
        return call()
    except z3.Z3Exception as exc:
        why = _message(exc)
        code = SOLVER_TIMEOUT if "timeout" in why or "canceled" in why else SOLVER_ERROR
        raise Refusal(code, f"z3 failed: {why}") from None


def _parsed(z3: Any, sorts: Mapping[str, str], terms: Sequence[str], *, any_sort: bool = False) -> list[Any]:
    """Z3 expressions of canonical ``terms`` over the declared ``sorts``, read through the closed grammar. With
    ``any_sort`` a term need not be Boolean: it is read inside ``(= t t)`` and taken back out."""
    query = _query(sorts, [f"(= {t} {t})" for t in terms] if any_sort else terms)
    _check_query(query)
    try:
        exprs = list(z3.parse_smt2_string(query.removesuffix("(check-sat)")))
    except z3.Z3Exception as exc:
        raise Refusal(PARSE_ERROR, f"z3 did not accept the input: {_message(exc)}") from None
    if len(exprs) != len(terms):
        raise Refusal(SOLVER_ERROR, f"z3 read {len(exprs)} assertions from {len(terms)} terms")
    return [e.arg(0) for e in exprs] if any_sort else exprs


def _variables(z3: Any, sorts: Mapping[str, str]) -> dict[str, Any]:
    return dict(zip(sorts, _parsed(z3, sorts, list(sorts), any_sort=True), strict=True))


def _model(z3: Any, model: Any, variables: Mapping[str, Any]) -> dict[str, Any]:
    """Every declared symbol's value in ``model`` (completed, so an unconstrained one still has a value)."""
    return {name: _value(z3, model.eval(v, model_completion=True)) for name, v in variables.items()}


def _refuted(reason: str) -> dict[str, str]:
    return {"code": CERTIFICATE_REFUTED, "reason": reason}


def _artifact(operation: str, question: str, statement: str, sorts: Mapping[str, str], assumptions: Sequence[str],
              goal: str, checks: Sequence[SolverCheck]) -> ProofArtifact:
    return ProofArtifact(operation=operation, question=question, statement=statement, sorts=dict(sorts),
                         assumptions=tuple(assumptions), goal=goal, solver="z3", solver_version=z3_version(),
                         checks=tuple(checks))


def _labelled(result: Any, operation: str, started: float, request_id: str, run_id: str) -> Any:
    result = result.model_copy(update={"elapsed_s": round(time.monotonic() - started, 4)})
    return apply_envelope(result, backend="z3", operation=operation, request_id=request_id, run_id=run_id)


# --- optimise ---------------------------------------------------------------------------------------------------------

def _objectives(objectives: Any) -> list[tuple[str, str]]:
    if isinstance(objectives, str | Mapping) or not isinstance(objectives, Sequence) or not objectives \
            or len(objectives) > MAX_OBJECTIVES:
        raise Refusal(INPUT_INVALID, "objectives is a list of [direction, term] pairs in priority order, such as "
                                     f'[["minimise", "(+ x y)"]], 1 to {MAX_OBJECTIVES} of them')
    out = []
    for i, item in enumerate(objectives, 1):
        if (isinstance(item, str) or not isinstance(item, Sequence) or len(item) != 2
                or _DIRECTIONS.get(str(item[0]).lower()) is None):
            raise Refusal(INPUT_INVALID, f"objective {i} must be [direction, term] with direction minimise or "
                                         f"maximise; got {str(item)[:80]!r}")
        out.append((_DIRECTIONS[str(item[0]).lower()], _term(item[1], f"objective {i}").text))
    return out


def _number(value: Any, sort: str, what: str) -> str | None:
    """An SMT-LIB numeral of ``sort`` for a bound, or ``None`` for an open side."""
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, int | float)
            or (isinstance(value, float) and (not math.isfinite(value) or (sort == "Int" and not value.is_integer())))):
        raise Refusal(INPUT_INVALID, f"{what} holds a lower and an upper number (whole for an Int symbol), or null for "
                                     f"an open side; got {value!r}")
    if sort == "Int":
        text = str(abs(int(value)))
    else:
        text = format(Decimal(repr(abs(float(value)))), "f")
        text = text if "." in text else text + ".0"
    return f"(- {text})" if value < 0 else text


def _bounds(bounds: Any, sorts: Mapping[str, str]) -> list[str]:
    """``{name: [lower, upper]}`` as constraints on declared Int and Real symbols."""
    if bounds is None:
        return []
    if not isinstance(bounds, Mapping):
        raise Refusal(INPUT_INVALID, "bounds map a declared Int or Real symbol to [lower, upper] (null for an open side)")
    out: list[str] = []
    for name, pair in bounds.items():
        if sorts.get(name) not in ("Int", "Real") or isinstance(pair, str) or not isinstance(pair, Sequence) \
                or len(pair) != 2:
            raise Refusal(INPUT_INVALID, f"bounds[{name!r}] must be [lower, upper] for a declared Int or Real symbol "
                                         "(null for an open side)")
        lower, upper = (_number(v, sorts[name], f"bounds[{name!r}]") for v in pair)
        out += [f"(<= {lower} {name})"] if lower is not None else []
        out += [f"(<= {name} {upper})"] if upper is not None else []
    return out


def _optimum(z3: Any, sorts: Mapping[str, str], constraints: Sequence[str], objectives: Sequence[tuple[str, str]],
             timeout: float) -> tuple[str, list[str], list[tuple[str, str]], dict[str, Any], str]:
    """Z3's ``Optimize``, objectives in priority order (lexicographic): ``(answer, values, (lower, upper) per
    objective, model, why unknown)``, each value the objective in the optimal model, as SMT-LIB text."""
    variables = _variables(z3, sorts)
    exprs = _parsed(z3, sorts, [term for _, term in objectives], any_sort=True)
    for (_, term), expr in zip(objectives, exprs, strict=True):
        if not (z3.is_arith(expr) or z3.is_bv(expr)):
            raise Refusal(INPUT_INVALID, f"the objective {term!r} is not of sort Int, Real or a bit-vector")
    opt = z3.Optimize()
    opt.set("timeout", _ms(timeout))
    for constraint in _parsed(z3, sorts, constraints):
        opt.add(constraint)
    handles = [opt.minimize(e) if d == "minimise" else opt.maximize(e) for (d, _), e in zip(objectives, exprs,
                                                                                               strict=True)]
    answer = str(_solver(z3, opt.check))
    bounds = [(h.lower().sexpr(), h.upper().sexpr()) for h in handles] if answer != "unsat" else []
    if answer != "sat":
        return answer, [], bounds, {}, str(opt.reason_unknown()) if answer == "unknown" else ""
    model = opt.model()
    values = [model.eval(e, model_completion=True).sexpr() for e in exprs]
    return answer, values, bounds, _model(z3, model, variables), ""


def _bound(direction: str, term: str, value: str) -> str:
    bits = value.startswith("#")  # a bit-vector objective: Z3 orders it unsigned
    op = {("minimise", False): ">=", ("maximise", False): "<=", ("minimise", True): "bvuge",
          ("maximise", True): "bvule"}[direction, bits]
    return f"({op} {term} {value})"


def _confirm_optimum(sorts: dict[str, str], constraints: list[str], goals: list[tuple[str, str]], values: list[str],
                     timeout: float) -> dict[str, Any]:
    """The bound (no feasible point is better, lexicographically) and the attainment, each on a fresh solver."""
    parts, fixed = [], []
    for (direction, term), value in zip(goals, values, strict=True):
        compare = _bound(direction, term, value)
        parts.append(f"(=> {_conj(fixed)} {compare})" if fixed else compare)
        fixed.append(f"(= {term} {value})")
    goal = _term(_conj(parts), "the optimum's bound").text
    reached = [_term(f, "the optimum").text for f in fixed]
    statement, (main, premises) = _plan("valid", sorts, constraints, goal)
    answer, better, why = run_query(main, timeout)
    checks = [SolverCheck(query=main, result=answer)]
    consistent = why_consistent = ""
    if answer == "unsat":
        consistent, _, why_consistent = run_query(premises, timeout)
        checks.append(SolverCheck(query=premises, result=consistent))
    _, (attain,) = _plan("satisfiable", sorts, [*constraints, *reached], "")
    attained, _, why_attained = run_query(attain, timeout)
    fields: dict[str, Any] = {"attainment": SolverCheck(query=attain, result=attained),
                              "artifact": _artifact("optimise", "valid", statement, sorts, constraints, goal, checks)}
    if answer == "sat":
        return fields | _refuted(f"a feasible point is better than the reported optimum: {better}")
    if consistent == "unsat":
        return fields | _refuted("the constraints are unsatisfiable on a fresh solver, so no optimum exists")
    if attained == "unsat":
        return fields | _refuted("no feasible point attains the reported optimum, so it is not one")
    for found, reason in ((answer, why), (consistent, why_consistent), (attained, why_attained)):
        if found == "unknown":
            return fields | _undecided(reason, "could not confirm the optimum: ")
    return fields | {"optimal": True}


def optimise(objectives: Sequence[Sequence[str]], constraints: Sequence[str] = (), sorts: Mapping[str, str] | None = None,
             *, bounds: Mapping[str, Sequence[float | None]] | None = None, timeout: float = DEFAULT_TIMEOUT_S,
             request_id: str = "", run_id: str = "") -> OptimumResult:
    """The lexicographic optimum of ``objectives`` (``[[minimise|maximise, term], ...]``, terms of sort Int, Real or
    a bit-vector) under the ``constraints`` and ``bounds`` (``{name: [lower, upper]}``, null for an open side).

    ``optimal`` only when the optimum passed its bound and attainment checks; an unbounded or unattained objective
    (z3's ``oo`` or ``epsilon``) has no optimum and is ``G6_E_FORMAL_NO_OPTIMUM``; infeasible constraints are a
    decision (``satisfiable`` False).
    """
    started = time.monotonic()
    fields: dict[str, Any] = {}
    try:
        declared = _sorts(sorts)
        goals = _objectives(objectives)
        terms = [t.text for t in _terms(constraints, "constraint")] + _bounds(bounds, declared)
        _check_timeout(timeout)
        z3 = load_z3()
        answer, values, found, model, why = _optimum(z3, declared, terms, goals, timeout)
        fields["objectives"] = [{"direction": d, "term": t, "value": v, "lower": lo, "upper": hi} for (d, t), v, (lo, hi)
                                in zip(goals, values or [""] * len(goals), found or [("", "")] * len(goals),
                                       strict=True)]
        if answer == "unknown":
            fields |= _undecided(why)
        elif answer == "unsat":
            statement, (query,) = _plan("satisfiable", declared, terms, "")
            feasible, witness, why = run_query(query, timeout)
            fields |= {"satisfiable": {"sat": True, "unsat": False}.get(feasible),
                       "artifact": _artifact("optimise", "satisfiable", statement, declared, terms, "",
                                             [SolverCheck(query=query, result=feasible)])}
            if feasible == "sat":
                fields |= _refuted(f"z3's optimiser found the constraints infeasible, a fresh solver a model: {witness}")
            elif feasible == "unknown":
                fields |= _undecided(why, "could not confirm the constraints are infeasible: ")
        else:
            fields |= {"satisfiable": True, "model": model}
            if any(_UNBOUNDED.search(f"{lo} {hi}") for lo, hi in found):
                fields |= {"code": NO_OPTIMUM, "reason": "an objective is unbounded or its bound is not attained (z3 "
                                                         f"reports {found}), so there is no optimum to report"}
            else:
                fields |= _confirm_optimum(declared, terms, goals, values, timeout)
    except Refusal as refusal:
        fields |= {"code": refusal.code, "reason": refusal.reason}
    return _labelled(OptimumResult(operation="optimise", backend="z3", **fields), "optimise", started, request_id,
                     run_id)


# --- unsat cores -----------------------------------------------------------------------------------------------------

def _named(named: Any) -> dict[str, str]:
    if not isinstance(named, Mapping) or not named or len(named) > MAX_NAMED:
        raise Refusal(INPUT_INVALID, 'named maps a name to each assertion, such as {"budget": "(<= cost 10)"}, 1 to '
                                     f"{MAX_NAMED} of them")
    out: dict[str, str] = {}
    for name, term in named.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise Refusal(INPUT_INVALID, f"{name!r} is not a plain name (letters, digits and _, not starting with a "
                                         "digit)")
        out[name] = _term(term, f"assertion {name!r}").text
    return out


def _core(z3: Any, sorts: Mapping[str, str], named: Mapping[str, str],
          timeout: float) -> tuple[str, list[str], dict[str, Any], str]:
    """Z3's unsat core of the named assertions: ``(answer, names in the core, model when sat, why unknown)``."""
    variables = _variables(z3, sorts)
    solver = z3.Solver()
    solver.set("timeout", _ms(timeout))
    trackers = [z3.FreshBool("g6_core") for _ in named]
    for expr, tracker in zip(_parsed(z3, sorts, list(named.values())), trackers, strict=True):
        solver.assert_and_track(expr, tracker)
    answer = str(_solver(z3, solver.check))
    if answer == "sat":
        return answer, [], _model(z3, solver.model(), variables), ""
    if answer != "unsat":
        return answer, [], {}, str(solver.reason_unknown())
    core = solver.unsat_core()
    return answer, [n for n, t in zip(named, trackers, strict=True) if any(z3.eq(t, c) for c in core)], {}, ""


def _shrink(sorts: dict[str, str], named: dict[str, str], core: list[str], first: SolverCheck,
            timeout: float) -> dict[str, Any]:
    """Drop each member in turn, keeping it only when the rest is satisfiable: a minimal core and its checks."""
    if len(core) > MAX_MINIMISE:
        statement = _plan("satisfiable", sorts, [named[n] for n in core], "")[0]
        return {"core": core, "minimal": None, "artifact": _artifact("unsat_core", "satisfiable", statement, sorts,
                                                                     [named[n] for n in core], "", [first])}
    kept, checks, final, minimal = list(core), [], first, True
    for name in core:
        trial = [n for n in kept if n != name]
        _, (query,) = _plan("satisfiable", sorts, [named[n] for n in trial], "")
        answer = run_query(query, timeout)[0]
        checks.append(SolverCheck(query=query, result=answer))
        if answer == "unsat":
            kept, final = trial, checks[-1]
        elif answer == "unknown":
            minimal = None
    statement = _plan("satisfiable", sorts, [named[n] for n in kept], "")[0]
    return {"core": kept, "minimal": minimal, "minimality": checks,
            "artifact": _artifact("unsat_core", "satisfiable", statement, sorts, [named[n] for n in kept], "",
                                  [final])}


def unsat_core(named: Mapping[str, str], sorts: Mapping[str, str] | None = None, *,
               timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "", run_id: str = "") -> CoreResult:
    """Which of the ``named`` assertions (``{name: term}``) cannot hold together? A minimal core, each member
    necessary, confirmed on fresh solvers; a model when they can all hold."""
    started = time.monotonic()
    fields: dict[str, Any] = {}
    try:
        declared = _sorts(sorts)
        terms = _named(named)
        _check_timeout(timeout)
        z3 = load_z3()
        answer, core, model, why = _core(z3, declared, terms, timeout)
        if answer == "unknown":
            fields |= _undecided(why)
        else:
            core = [n for n in terms if n in core]  # in the caller's order
            given = list(terms) if answer == "sat" else core
            statement, (query,) = _plan("satisfiable", declared, [terms[n] for n in given], "")
            fresh, witness, why = run_query(query, timeout)
            first = SolverCheck(query=query, result=fresh)
            fields |= {"core": core,
                       "artifact": _artifact("unsat_core", "satisfiable", statement, declared,
                                             [terms[n] for n in given], "", [first])}
            if fresh == "unknown":
                fields |= _undecided(why, "could not confirm z3's answer on a fresh solver: ")
            elif answer == "sat":
                fields |= {"satisfiable": True, "model": model} if fresh == "sat" else _refuted(
                    "z3 found a model of the assertions, but a fresh solver finds them unsatisfiable")
            elif fresh == "sat":
                fields |= _refuted(f"the reported core {core} is satisfiable on a fresh solver ({witness}), so it is "
                                   "not a core")
            else:
                fields |= {"satisfiable": False, **_shrink(declared, terms, core, first, timeout)}
    except Refusal as refusal:
        fields |= {"code": refusal.code, "reason": refusal.reason}
    return _labelled(CoreResult(operation="unsat_core", backend="z3", **fields), "unsat_core", started, request_id,
                     run_id)


# --- simplify and quantifier elimination -----------------------------------------------------------------------------

def _simplified(z3: Any, sorts: Mapping[str, str], term: str, timeout: float) -> str:
    """Z3's ``simplify`` of one term of any sort, as SMT-LIB text."""
    (expr,) = _parsed(z3, sorts, [term], any_sort=True)
    return _solver(z3, lambda: z3.simplify(expr)).sexpr()


def _eliminated(z3: Any, sorts: Mapping[str, str], formula: str, timeout: float) -> str:
    """Z3's ``qe`` tactic on one Boolean formula: its subgoals' formulas, joined, as SMT-LIB text."""
    (expr,) = _parsed(z3, sorts, [formula])
    goal = z3.Goal()
    goal.add(expr)
    subgoals = _solver(z3, lambda: z3.TryFor(z3.Tactic("qe"), _ms(timeout))(goal))
    parts = [_conj([f.sexpr() for f in subgoal]) for subgoal in subgoals]
    return parts[0] if len(parts) == 1 else "(or " + " ".join(parts) + ")" if parts else "false"


def _symbols(node: Any) -> set[str]:
    return {node} if isinstance(node, str) else {s for child in node for s in _symbols(child)}


def _rewrite(operation: str, term: Any, sorts: Any, timeout: Any, request_id: str, run_id: str) -> TermResult:
    started = time.monotonic()
    fields: dict[str, Any] = {}
    try:
        declared = _sorts(sorts)
        given = _term(term, "the term" if operation == "simplify" else "the formula")
        fields["term"] = given.text
        _check_timeout(timeout)
        z3 = load_z3()
        engine = _simplified if operation == "simplify" else _eliminated
        out = _term(engine(z3, declared, given.text, timeout), f"z3's {operation} result")
        fields["result_term"] = out.text
        if operation == "eliminate_quantifiers":
            fields["quantifier_free"] = not {"forall", "exists"} & _symbols(out.tree)
            if not fields["quantifier_free"]:
                raise Refusal(SOLVER_UNKNOWN, "z3's qe tactic left a quantifier in the result, so the elimination did "
                                              "not finish (non-linear arithmetic is the usual cause)")
        goal = f"(= {given.text} {out.text})"
        statement, (main, premises) = _plan("valid", declared, [], goal)
        answer, counter, why = run_query(main, timeout)
        checks = [SolverCheck(query=main, result=answer)]
        if answer == "unsat":  # with no premises their consistency query is trivially sat; it is recorded all the same
            checks.append(SolverCheck(query=premises, result=run_query(premises, timeout)[0]))
        fields["artifact"] = _artifact(operation, "valid", statement, declared, [], goal, checks)
        if answer == "sat":
            fields |= {"counterexample": counter} | _refuted(
                f"z3's {operation} result is not equivalent to the input: they differ at {counter}")
        elif answer == "unknown" or checks[-1].result != "sat":
            fields |= _undecided(why, "could not confirm the result is equivalent to the input: ")
        else:
            fields["equivalent"] = True
    except Refusal as refusal:
        fields |= {"code": refusal.code, "reason": refusal.reason}
    return _labelled(TermResult(operation=operation, backend="z3", **fields), operation, started, request_id, run_id)


def simplify(term: str, sorts: Mapping[str, str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S,
             request_id: str = "", run_id: str = "") -> TermResult:
    """Z3's simplified form of ``term`` (any sort), reported only once ``(= term result)`` is decided valid."""
    return _rewrite("simplify", term, sorts, timeout, request_id, run_id)


def eliminate_quantifiers(formula: str, sorts: Mapping[str, str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S,
                          request_id: str = "", run_id: str = "") -> TermResult:
    """A quantifier-free formula equivalent to ``formula`` (Z3's ``qe`` tactic), confirmed equivalent; a quantifier
    left in the result is reported as an unfinished elimination, not as an answer."""
    return _rewrite("eliminate_quantifiers", formula, sorts, timeout, request_id, run_id)


# --- model counting --------------------------------------------------------------------------------------------------

def _enumerate(z3: Any, sorts: Mapping[str, str], terms: Sequence[str], cap: int,
               timeout: float) -> tuple[list[dict[str, Any]], list[str], str, str]:
    """Up to ``cap`` models of the terms over the declared symbols, each blocked once found: ``(models, blocking
    terms, answer, why unknown)``, the answer ``unsat`` when no other model exists, ``cap`` at the cap, else
    ``unknown``. One deadline, ``timeout``, covers the whole enumeration."""
    variables = _variables(z3, sorts)
    solver = z3.Solver()
    for expr in _parsed(z3, sorts, terms):
        solver.add(expr)
    deadline = time.monotonic() + timeout
    models: list[dict[str, Any]] = []
    blocks: list[str] = []
    while len(models) < cap:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return models, blocks, "unknown", "timeout"
        solver.set("timeout", _ms(remaining))
        answer = str(_solver(z3, solver.check))
        if answer != "sat":
            return models, blocks, answer, str(solver.reason_unknown()) if answer == "unknown" else ""
        model = solver.model()
        values = {name: model.eval(v, model_completion=True) for name, v in variables.items()}
        models.append({name: _value(z3, value) for name, value in values.items()})
        same = _conj([f"(= {name} {value.sexpr()})" for name, value in values.items()])
        blocks.append(_term(f"(not {same})", "a blocking term").text)
        solver.add(z3.Or([v != values[n] for n, v in variables.items()]) if variables else z3.BoolVal(False))
    return models, blocks, "cap", ""


def count_models(formula: str, sorts: Mapping[str, str] | None = None, assertions: Sequence[str] = (), *,
                 cap: int = DEFAULT_CAP, timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "",
                 run_id: str = "") -> CountResult:
    """How many assignments of the declared symbols satisfy ``formula`` ∧ every assertion? Models are enumerated up
    to ``cap``; ``exact`` when a fresh solver decides no other exists, ``cap_hit`` (a lower bound) when one does.
    Arrays are refused: their values cannot be enumerated this way."""
    started = time.monotonic()
    fields: dict[str, Any] = {}
    try:
        declared = _sorts(sorts)
        arrays = sorted(n for n, s in declared.items() if s.startswith("(Array"))
        if arrays:
            raise Refusal(INPUT_INVALID, f"count_models enumerates Int, Real, Bool, String and bit-vector symbols; "
                                         f"{arrays} are arrays, whose values it cannot enumerate")
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= MAX_CAP:
            raise Refusal(INPUT_INVALID, f"cap must be a whole number from 1 to {MAX_CAP}")
        terms = [_term(formula, "the formula").text, *(t.text for t in _terms(assertions, "assertion"))]
        _check_timeout(timeout)
        z3 = load_z3()
        models, blocks, answer, why = _enumerate(z3, declared, terms, cap, timeout)
        fields |= {"models": models, "count": len(models), "cap": cap, "satisfiable": True if models else None}
        if answer == "unknown":
            fields |= _undecided(why, f"the enumeration stopped after {len(models)} models: ")
        else:
            statement, (query,) = _plan("satisfiable", declared, [*terms, *blocks], "")
            more, extra, why = run_query(query, timeout)
            fields["artifact"] = _artifact("count_models", "satisfiable", statement, declared, [*terms, *blocks], "",
                                           [SolverCheck(query=query, result=more)])
            if more == "unknown":
                fields |= _undecided(why, "could not decide whether another model exists: ")
            elif answer == "unsat" and more == "sat":
                fields |= _refuted(f"the enumeration ended as if complete, but a fresh solver finds another model: "
                                   f"{extra}")
            else:
                fields |= {"exact": more == "unsat", "cap_hit": more == "sat", "satisfiable": bool(models)}
    except Refusal as refusal:
        fields |= {"code": refusal.code, "reason": refusal.reason}
    return _labelled(CountResult(operation="count_models", backend="z3", **fields), "count_models", started,
                     request_id, run_id)


# --- equivalence -----------------------------------------------------------------------------------------------------

def check_equivalence(a: str, b: str, *, premises: Sequence[str] = (), sorts: Mapping[str, str] | None = None,
                      timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "", run_id: str = "") -> ValidityResult:
    """Are ``a`` and ``b`` equivalent (two formulas) or equal (two terms of one sort) wherever the ``premises`` hold?
    ``check_validity(premises, (= a b))``: ``counterexample`` distinguishes them, vacuous when the premises cannot
    hold."""
    try:
        goal = f"(= {_term(a, 'the first formula').text} {_term(b, 'the second formula').text})"
    except Refusal as refusal:
        result = ValidityResult(operation="check_equivalence", backend="z3", code=refusal.code, reason=refusal.reason)
        return apply_envelope(result, backend="z3", operation="check_equivalence", request_id=request_id,
                              run_id=run_id)
    return _validity("check_equivalence", premises, goal, sorts, timeout, request_id, run_id)


__all__ = ["DEFAULT_CAP", "MAX_CAP", "check_equivalence", "count_models", "eliminate_quantifiers", "optimise",
           "simplify", "unsat_core"]
