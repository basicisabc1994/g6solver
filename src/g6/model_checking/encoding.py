# SPDX-License-Identifier: Apache-2.0
"""A symbolic state model read through g6.formal's SMT-LIB reader, and its encoding for Z3.

The model (a mapping, or :class:`~g6.model_checking.schema.StateModel`):

* ``variables``: each state variable's name and sort, ``Int``, ``Real``, ``Bool`` or ``(_ BitVec n)``; 1 to 64 of them.
  A name is a plain symbol (letters, digits and ``_``) and does not start with ``next_``;
* ``init``: one SMT-LIB term over the state variables, the initial states;
* ``transition``: one term over the state variables and their next-state copies, the steps. SMT-LIB has no prime, so
  the next-state copy of ``x`` is written ``next_x`` (``x'`` is not an SMT-LIB symbol);
* ``invariants``: named terms over the state variables (up to 50), checked together as one conjunction.

Each term is read by :mod:`g6.formal.z3_bridge`'s closed grammar (declared symbols only, no commands, no quoted
symbols, keywords or comments, at most 10,000 characters a term) before Z3 reads it, so a term cannot carry a command.
Nothing a caller sends is evaluated as code.

:class:`Encoding` builds the model in a Z3 context of its own: a copy of every variable for each step (``x@0``,
``x@1``, ...), each term moved onto those copies by substitution, so a quantifier inside a term keeps its own bound
names. The search runs in one context and G6's re-check of an unsatisfiable claim in another, built again from the
canonical text.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from g6.formal import envelope as formal
from g6.formal.z3_bridge import _NAME, _check_query, _message, _query, _sorts, _term, load_z3
from g6.model_checking.envelope import (
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_RECHECKABLE,
    PARSE_ERROR,
    SOLVER_ERROR,
    SOLVER_UNAVAILABLE,
    UNKNOWN_SORT,
    Refusal,
)
from g6.model_checking.schema import StateModel

NEXT = "next_"
MAX_VARIABLES = 64
MAX_INVARIANTS = 50
_KEYS = frozenset(StateModel.model_fields)
_CODES = {formal.PARSE_ERROR: PARSE_ERROR, formal.UNKNOWN_SORT: UNKNOWN_SORT, formal.INPUT_INVALID: INPUT_INVALID,
          formal.INPUT_TOO_LARGE: INPUT_TOO_LARGE, formal.SOLVER_UNAVAILABLE: SOLVER_UNAVAILABLE,
          formal.SOLVER_ERROR: SOLVER_ERROR}


def from_formal(exc: formal.Refusal) -> Refusal:
    """g6.formal's refusal as this package's, keeping its reason."""
    return Refusal(_CODES.get(exc.code, SOLVER_ERROR), exc.reason)


def z3_module() -> Any:
    try:
        return load_z3()
    except formal.Refusal as exc:
        raise Refusal(SOLVER_UNAVAILABLE, exc.reason.split(", or use solver=")[0]) from None


@dataclass(frozen=True)
class Model:
    """A model in canonical form: sorts and terms as g6.formal's reader writes them."""

    variables: dict[str, str]
    init: str
    transition: str
    invariants: dict[str, str] = field(default_factory=dict)
    goal: str = ""

    def next_sorts(self) -> dict[str, str]:
        return {NEXT + name: sort for name, sort in self.variables.items()}

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"variables": dict(self.variables), "init": self.init, "transition": self.transition,
                               "invariants": dict(self.invariants), "next_state": "next_<name>"}
        if self.goal:
            out["goal"] = self.goal
        return out


def _bits(sort: str) -> int | None:
    return int(sort[len("(_ BitVec "):-1]) if sort.startswith("(_ BitVec ") else None


def _text(value: Any, what: str) -> str:
    try:
        return _term(value, what).text
    except formal.Refusal as exc:
        raise from_formal(exc) from None


def read_model(model: Any, *, need_invariants: bool, goal: Any = None) -> Model:
    """``model`` in canonical form, every limit and name checked; ``Refusal`` otherwise. ``goal`` is read when given."""
    if isinstance(model, StateModel):
        model = model.model_dump()
    if not isinstance(model, Mapping):
        raise Refusal(INPUT_INVALID, "the model maps variables, init, transition and invariants to their values")
    unknown = sorted(str(k) for k in model if k not in _KEYS)
    if unknown:
        raise Refusal(INPUT_INVALID, f"the model has no field {', '.join(unknown)}; it takes variables, init, "
                                     "transition and invariants")
    variables = model.get("variables") or {}
    if not isinstance(variables, Mapping) or not variables:
        raise Refusal(INPUT_INVALID, "the model needs at least one state variable in variables, such as {\"x\": \"Int\"}")
    if len(variables) > MAX_VARIABLES:
        raise Refusal(INPUT_TOO_LARGE, f"the model has {len(variables)} state variables; the limit is {MAX_VARIABLES}")
    invariants = model.get("invariants") or {}
    if not isinstance(invariants, Mapping):
        raise Refusal(INPUT_INVALID, "invariants maps a plain name to each term, such as {\"small\": \"(<= x 3)\"}")
    if len(invariants) > MAX_INVARIANTS:
        raise Refusal(INPUT_TOO_LARGE, f"the model has {len(invariants)} invariants; the limit is {MAX_INVARIANTS}")
    if need_invariants and not invariants:
        raise Refusal(INPUT_INVALID, "this op checks invariants: give at least one in invariants, such as "
                                     "{\"small\": \"(<= x 3)\"}")
    try:
        sorts = _sorts(variables)
    except formal.Refusal as exc:
        raise from_formal(exc) from None
    for name, sort in sorts.items():
        if name.startswith(NEXT):
            raise Refusal(INPUT_INVALID, f"{name!r} starts with {NEXT!r}, which names next-state copies; rename the "
                                         "state variable")
        if sort not in ("Int", "Real", "Bool") and _bits(sort) is None:
            raise Refusal(UNKNOWN_SORT, f"{name!r} has sort {sort}; model checking takes Int, Real, Bool or "
                                        "(_ BitVec n)")
    named = {}
    for name, term in invariants.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise Refusal(INPUT_INVALID, f"invariant {name!r} needs a plain name (letters, digits and _, not starting "
                                         "with a digit)")
        named[name] = _text(term, f"invariant {name}")
    return Model(variables=sorts, init=_text(model.get("init"), "init"),
                 transition=_text(model.get("transition"), "the transition"), invariants=named,
                 goal=_text(goal, "the goal") if goal is not None else "")


class Encoding:
    """``model`` in a Z3 context: the terms parsed through the closed grammar, and their copies at each step."""

    def __init__(self, z3: Any, model: Model, ctx: Any = None) -> None:
        self.z3, self.model = z3, model
        self.ctx = ctx if ctx is not None else z3.Context()
        self.names = list(model.variables)
        self.sorts = {n: self._sort(s) for n, s in model.variables.items()}
        self.current = {n: z3.Const(n, self.sorts[n]) for n in self.names}
        self.following = {n: z3.Const(NEXT + n, self.sorts[n]) for n in self.names}
        declared, both = model.variables, model.variables | model.next_sorts()
        self.init = self._parse(declared, model.init, "init")
        self.transition = self._parse(both, model.transition, "the transition")
        self.invariants = {n: self._parse(declared, t, f"invariant {n}") for n, t in model.invariants.items()}
        self.goal = self._parse(declared, model.goal, "the goal") if model.goal else None
        self._copies: dict[int, dict[str, Any]] = {}

    def _sort(self, sort: str) -> Any:
        z3, bits = self.z3, _bits(sort)
        if bits is not None:
            return z3.BitVecSort(bits, self.ctx)
        return {"Int": z3.IntSort, "Real": z3.RealSort, "Bool": z3.BoolSort}[sort](self.ctx)

    def _parse(self, declared: Mapping[str, str], term: str, what: str) -> Any:
        z3 = self.z3
        try:
            query = _query(declared, [term])
            _check_query(query)
        except formal.Refusal as exc:
            raise from_formal(exc) from None
        try:
            (expr,) = z3.parse_smt2_string(query.removesuffix("(check-sat)"), ctx=self.ctx)
        except z3.Z3Exception as exc:
            hint = ""
            if what != "the transition" and NEXT in term:
                hint = f"; only the transition may use next-state names such as {NEXT}{self.names[0]}"
            raise Refusal(PARSE_ERROR, f"{what}: z3 did not accept it: {_message(exc)}{hint}") from None
        return expr

    # --- the model at each step ---------------------------------------------------------------------------------------

    def copy(self, i: int) -> dict[str, Any]:
        if i not in self._copies:
            self._copies[i] = {n: self.z3.Const(f"{n}@{i}", self.sorts[n]) for n in self.names}
        return self._copies[i]

    def at(self, expr: Any, i: int) -> Any:
        """``expr``, over the state variables, at step ``i``."""
        return self.z3.substitute(expr, *[(self.current[n], self.copy(i)[n]) for n in self.names])

    def step(self, i: int) -> Any:
        """The transition from step ``i`` to step ``i + 1``."""
        pairs = [(self.current[n], self.copy(i)[n]) for n in self.names]
        pairs += [(self.following[n], self.copy(i + 1)[n]) for n in self.names]
        return self.z3.substitute(self.transition, *pairs)

    def invariant(self, i: int) -> Any:
        return self.z3.And([self.at(e, i) for e in self.invariants.values()] or [self.z3.BoolVal(True, self.ctx)])

    def named(self, name: str, i: int) -> Any:
        return self.at(self.invariants[name], i)

    def target(self, kind: str, i: int) -> Any:
        """What a search looks for at step ``i``: an invariant broken, no successor, or the goal met."""
        z3 = self.z3
        if kind == "invariant":
            return z3.Not(self.invariant(i))
        if kind == "goal":
            return self.at(self.goal, i)
        bound = [z3.FreshConst(self.sorts[n], f"{NEXT}{n}") for n in self.names]
        pairs = [(self.current[n], self.copy(i)[n]) for n in self.names]
        pairs += [(self.following[n], b) for n, b in zip(self.names, bound, strict=True)]
        return z3.ForAll(bound, z3.Not(z3.substitute(self.transition, *pairs)))

    def different(self, i: int, j: int) -> Any:
        return self.z3.Or([self.copy(i)[n] != self.copy(j)[n] for n in self.names])

    def none_within(self, kind: str, depth: int) -> Any:
        """Some path of at most ``depth`` steps from step 0 meets the target: ``t0 ∨ (T0 ∧ (t1 ∨ (T1 ∧ ...)))``."""
        z3 = self.z3
        formula = self.target(kind, depth)
        for i in range(depth - 1, -1, -1):
            formula = z3.Or(self.target(kind, i), z3.And(self.step(i), formula))
        return formula

    def path(self, steps: int) -> list[Any]:
        return [self.step(i) for i in range(steps)]

    def state(self, z3_model: Any, i: int) -> dict[str, Any]:
        """Step ``i``'s values in a Z3 model: Int and bit-vectors as integers (bit-vectors unsigned), Bool as booleans,
        Real as exact rationals in text (``"1/2"``)."""
        return {n: _value(self.z3, z3_model.evaluate(c, model_completion=True), n) for n, c in self.copy(i).items()}


def _value(z3: Any, v: Any, name: str) -> Any:
    if z3.is_true(v):
        return True
    if z3.is_false(v):
        return False
    if z3.is_int_value(v) or z3.is_bv_value(v):
        return v.as_long()
    if z3.is_rational_value(v):
        return str(v.as_fraction())
    raise Refusal(NOT_RECHECKABLE, f"{name} has the value {v.sexpr()[:80]}, which is not a rational number, so G6 "
                                   "cannot state it exactly to re-check the trace")


def literal(sort: str, value: Any, name: str) -> str:
    """``value`` of ``sort`` as an SMT-LIB literal; ``ValueError`` saying why when it is not one."""
    bits = _bits(sort)
    if sort == "Bool":
        if not isinstance(value, bool):
            raise ValueError(f"{name} is a Bool, not {value!r}")
        return "true" if value else "false"
    if bits is not None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 1 << bits:
            raise ValueError(f"{name} is a {bits}-bit vector: a whole number from 0 to {(1 << bits) - 1}, not {value!r}")
        return "#b" + format(value, f"0{bits}b")
    if sort == "Int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} is an Int, not {value!r}")
        return str(value) if value >= 0 else f"(- {-value})"
    try:
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError
        number = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"{name} is a Real: a whole number or a rational in text such as \"1/2\", not {value!r}") \
            from None
    text = f"{abs(number.numerator)}.0" if number.denominator == 1 else \
        f"(/ {abs(number.numerator)}.0 {number.denominator}.0)"
    return f"(- {text})" if number < 0 else text


def equalities(model: Model, state: Mapping[str, Any], prefix: str = "") -> list[str]:
    """``(= x v)`` for every state variable; ``ValueError`` when the state is not one of the model's."""
    extra = sorted(str(k) for k in state if k not in model.variables)
    if extra:
        raise ValueError(f"{', '.join(extra)} is not a state variable of the model")
    missing = [n for n in model.variables if n not in state]
    if missing:
        raise ValueError(f"the state has no value for {', '.join(missing)}")
    return [f"(= {prefix}{n} {literal(s, state[n], n)})" for n, s in model.variables.items()]


def queries(model: Model, terms: Sequence[str], *, with_next: bool = False) -> str:
    """A closed-grammar query over the state variables (and their next-state copies)."""
    declared = model.variables | model.next_sorts() if with_next else model.variables
    return _query(declared, list(terms))


__all__ = ["Encoding", "MAX_INVARIANTS", "MAX_VARIABLES", "Model", "NEXT", "equalities", "from_formal", "literal",
           "queries", "read_model", "z3_module"]
