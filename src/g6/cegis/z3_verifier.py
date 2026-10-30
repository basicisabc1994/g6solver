# SPDX-License-Identifier: Apache-2.0
"""``Z3Verifier``: is there an input of the domain on which the candidate breaks your spec? Z3 decides, through
``g6.formal`` (audit 027 §11, CG-008).

The spec is one SMT-LIB term over the candidate's argument names and ``result``, its return value, in g6.formal's
closed grammar, such as ``(= result (+ (* 3 x) 1))``. The candidate's return expression is read from its source (parsed,
never run) into an SMT-LIB term; the domain becomes constraints (an integer argument whose values are a contiguous range
is a bound, any other is the list of its values; a boolean argument is a ``Bool``). Z3 is asked for an input of the
whole domain, not only the sampled part, on which the spec is false. Z3 runs in this process and is imported only
then; without ``z3-solver`` (``pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'``) every check is ``Inconclusive("z3_unavailable")``.

What it can answer:

* ``Counterexample``: such an input. ``cegis`` does not take Z3's word for it: it runs the candidate there (out of
  process) and asks the oracle, and feeds the input back only if they disagree;
* ``NoCounterexample``: Z3 decided that no input of the encoded domain breaks the spec. That is a solver decision on
  your spec and on this module's reading of the candidate, so a result resting on it is ``qualified-draft`` at most;
* ``Inconclusive`` (degraded): no decision. ``z3_unknown`` and ``z3_timeout`` are Z3 answering unknown (CG-008: the
  archive read unknown, a timeout included, as UNSAT); ``z3_unavailable``, no Z3 here; ``z3_untranslatable``, a
  candidate or domain outside the grammar below; ``z3_refused``, a query g6.formal or Z3 would not read (a spec naming
  an undeclared symbol, for one); ``z3_model_outside_domain``, a model this module cannot use as an input.

The grammar is read exactly. Python's ``int`` is SMT-LIB ``Int`` (unbounded; the archive's 64-bit bit-vectors wrapped
around): one ``def`` whose body is one ``return`` (a docstring may come first); int and bool literals and arguments;
``+``, ``-``, ``*``; ``//`` and ``%`` by a positive literal (where Python and SMT-LIB agree); unary ``-`` and ``+``;
``<``, ``<=``, ``>``, ``>=``, ``==``, ``!=`` (chained too); and ``and``, ``or``, ``not`` and ``a if c else b`` on bools. A
call, a string, ``**``, a bool used as a number or any other construct is ``z3_untranslatable``, never approximated.
Z3 reads the spec as Z3 reads SMT-LIB, leniently: it may coerce a ``Bool`` to an ``Int``; the evidence names the sort of
``result``, the exact query and the solver's version.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from g6.cegis.sketch import Control, Sketch
from g6.cegis.verify import Counterexample, Inconclusive, InputDomain, NoCounterexample, Outcome
from g6.formal import FormalResult, check_sat
from g6.formal.envelope import SOLVER_TIMEOUT, SOLVER_UNAVAILABLE, SOLVER_UNKNOWN

DEFAULT_TIMEOUT_S = 10.0
#: The most values an integer argument that is not a contiguous range may list.
MAX_LISTED = 256
RESULT = "result"
_TOKEN = re.compile(r"\(|\)|[^\s()]+")
_ARITH = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
_DIVIDE = {ast.FloorDiv: "div", ast.Mod: "mod"}
_ORDER = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
#: g6.formal's codes for "Z3 did not decide", as the degradation each one is (audit 027, CG-008).
_UNDECIDED = {SOLVER_UNKNOWN: "z3_unknown", SOLVER_TIMEOUT: "z3_timeout", SOLVER_UNAVAILABLE: "z3_unavailable"}


class Untranslatable(ValueError):
    """The candidate or the domain is outside what this module reads exactly."""


def _int(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


def _shown(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text if len(text) <= 60 else text[:57] + "..."


def _term(node: ast.expr, sorts: Mapping[str, str]) -> tuple[str, str]:
    """``node`` as an SMT-LIB term and its sort (``Int`` or ``Bool``), or :class:`Untranslatable`."""
    if isinstance(node, ast.Constant) and type(node.value) in (bool, int):
        if type(node.value) is bool:
            return ("true" if node.value else "false"), "Bool"
        return _int(node.value), "Int"
    if isinstance(node, ast.Name) and node.id in sorts:
        return node.id, sorts[node.id]
    if isinstance(node, ast.UnaryOp):
        text, sort = _term(node.operand, sorts)
        if isinstance(node.op, ast.Not) and sort == "Bool":
            return f"(not {text})", "Bool"
        if isinstance(node.op, ast.USub) and sort == "Int":
            return f"(- {text})", "Int"
        if isinstance(node.op, ast.UAdd) and sort == "Int":
            return text, "Int"
    elif isinstance(node, ast.BinOp):
        left, lsort = _term(node.left, sorts)
        op = type(node.op)
        if op in _ARITH:
            right, rsort = _term(node.right, sorts)
            if lsort == rsort == "Int":
                return f"({_ARITH[op]} {left} {right})", "Int"
        elif (op in _DIVIDE and lsort == "Int" and isinstance(node.right, ast.Constant)
              and type(node.right.value) is int and node.right.value > 0):
            # Python's floor division and modulo equal SMT-LIB div and mod exactly when the divisor is positive
            return f"({_DIVIDE[op]} {left} {node.right.value})", "Int"
    elif isinstance(node, ast.BoolOp):
        parts = [_term(value, sorts) for value in node.values]
        if all(sort == "Bool" for _, sort in parts):
            word = "and" if isinstance(node.op, ast.And) else "or"
            return f"({word} {' '.join(text for text, _ in parts)})", "Bool"
    elif isinstance(node, ast.Compare):
        terms = [_term(node.left, sorts), *(_term(c, sorts) for c in node.comparators)]
        pairs = []
        for op, (a, asort), (b, bsort) in zip(node.ops, terms, terms[1:], strict=False):
            if isinstance(op, ast.Eq | ast.NotEq) and asort == bsort:
                pairs.append(f"(= {a} {b})" if isinstance(op, ast.Eq) else f"(not (= {a} {b}))")
            elif type(op) in _ORDER and asort == bsort == "Int":
                pairs.append(f"({_ORDER[type(op)]} {a} {b})")
            else:
                raise Untranslatable(f"the comparison {_shown(node)!r} is not of two ints (or two bools for == and !=)")
        return (pairs[0] if len(pairs) == 1 else f"(and {' '.join(pairs)})"), "Bool"
    elif isinstance(node, ast.IfExp):
        test, tsort = _term(node.test, sorts)
        body, bsort = _term(node.body, sorts)
        other, osort = _term(node.orelse, sorts)
        if tsort == "Bool" and bsort == osort:
            return f"(ite {test} {body} {other})", bsort
    raise Untranslatable(f"{_shown(node)!r} is outside the grammar Z3 reads exactly here")


def candidate_term(source: str, fn_name: str, sorts: Mapping[str, str]) -> tuple[str, str]:
    """The return expression of ``fn_name`` in ``source`` as an SMT-LIB term, and its sort. Parsed, never run (audit
    027, CG-002: candidate code runs only in the runner's child process)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Untranslatable(f"the candidate does not parse: {exc.msg}") from None
    fn = tree.body[0] if len(tree.body) == 1 else None
    if not isinstance(fn, ast.FunctionDef) or fn.name != fn_name or fn.decorator_list:
        raise Untranslatable(f"the candidate is not one plain def {fn_name}(...)")
    args = fn.args
    if args.posonlyargs or args.vararg or args.kwonlyargs or args.kwarg or args.defaults:
        raise Untranslatable("the candidate's arguments are not plain names")
    params = sorted(a.arg for a in args.args)
    if params != sorted(sorts):
        raise Untranslatable(f"the candidate takes {params}; the domain gives {sorted(sorts)}")
    body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) \
        and isinstance(fn.body[0].value.value, str) else fn.body
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        raise Untranslatable("the candidate's body is not one return statement")
    return _term(body[0].value, sorts)


def domain_constraints(domain: InputDomain) -> tuple[dict[str, str], list[str], dict[str, Any]]:
    """Each argument's sort, the constraints that say it takes only its domain's values, and how each was encoded."""
    sorts: dict[str, str] = {}
    constraints: list[str] = []
    encoded: dict[str, Any] = {}
    for name, values in domain.values:
        if name == RESULT:
            raise Untranslatable(f"an argument named {RESULT!r} would be the spec's name for the return value")
        kinds = {type(v) for v in values}
        if kinds == {bool}:
            present = sorted(set(values))
            sorts[name] = "Bool"
            if len(present) == 1:
                constraints.append(name if present[0] else f"(not {name})")
            encoded[name] = {"sort": "Bool", "values": present}
        elif kinds == {int}:
            distinct = set(values)
            lo, hi = min(distinct), max(distinct)
            sorts[name] = "Int"
            if len(distinct) == hi - lo + 1:
                constraints.append(f"(and (>= {name} {_int(lo)}) (<= {name} {_int(hi)}))")
                encoded[name] = {"sort": "Int", "range": [lo, hi]}
            elif len(distinct) <= MAX_LISTED:
                listed = [f"(= {name} {_int(v)})" for v in sorted(distinct)]
                constraints.append(listed[0] if len(listed) == 1 else f"(or {' '.join(listed)})")
                encoded[name] = {"sort": "Int", "values": sorted(distinct)}
            else:
                raise Untranslatable(f"{name!r} takes {len(distinct)} scattered ints; Z3 is given a range or at most "
                                     f"{MAX_LISTED} listed values")
        else:
            raise Untranslatable(f"{name!r} takes values of type {', '.join(sorted(k.__name__ for k in kinds))}; Z3 "
                                 "reads int and bool arguments only")
    return sorts, constraints, encoded


def _decide(decided: FormalResult, domain: InputDomain, evidence: dict[str, Any]) -> Outcome:
    """Z3's answer as an outcome. Only sat and unsat are decisions; anything else is :class:`Inconclusive`."""
    # audit 027, CG-008: the archive's ``if solver.check() != sat: return None`` read unknown, a timeout included, as
    # "nothing found". Here no code means a decision was recorded, and every code is a named degradation.
    if decided.code:
        return Inconclusive(_UNDECIDED.get(decided.code, "z3_refused"), decided.reason, evidence)
    if decided.satisfiable is False:
        return NoCounterexample(evidence)
    if decided.satisfiable is not True:
        return Inconclusive("z3_refused", "the solver recorded no decision", evidence)
    inputs: dict[str, Any] = {}
    for name, values in domain.values:
        # an argument missing from the model is one the formula does not depend on: any value of it will do
        value = decided.model.get(name, values[0])
        if type(value) is not type(values[0]) or value not in values:
            return Inconclusive("z3_model_outside_domain", f"z3 gave {name}={value!r}, which the domain does not hold",
                                evidence)
        inputs[name] = value
    return Counterexample(inputs, evidence)


@dataclass(frozen=True)
class Z3Verifier:
    """Checks a candidate against ``spec`` on every input of the domain; Z3 gets ``timeout_s`` per check."""

    spec: str
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        # audit 027, CG-001 (the same lesson): a check that cannot tell one candidate from another is refused, not run
        if not isinstance(self.spec, str) or RESULT not in _TOKEN.findall(self.spec):
            raise ValueError(f"the spec must be one SMT-LIB term that names {RESULT}, the candidate's return value, "
                             f"such as (= {RESULT} (+ x 1))")

    def find_counterexample(self, sketch: Sketch, control: Control, domain: InputDomain) -> Outcome:
        evidence: dict[str, Any] = {"verifier": "z3", "spec": self.spec, "timeout_s": self.timeout_s}
        try:
            sorts, constraints, encoded = domain_constraints(domain)
            term, sort = candidate_term(sketch.instantiate(control), sketch.fn_name, sorts)
        except Untranslatable as exc:
            return Inconclusive("z3_untranslatable", str(exc), evidence)
        evidence |= {"encoded_domain": encoded, "covers": "every input of the domain, not only the sampled ones",
                     "candidate_term": term, "result_sort": sort}
        decided = check_sat(f"(not (let (({RESULT} {term})) {self.spec}))", sorts, constraints, timeout=self.timeout_s)
        evidence["solver"] = list(decided.evidence)
        return _decide(decided, domain, evidence)


__all__ = ["DEFAULT_TIMEOUT_S", "MAX_LISTED", "RESULT", "Untranslatable", "Z3Verifier", "candidate_term",
           "domain_constraints"]
