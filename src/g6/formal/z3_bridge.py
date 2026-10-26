# SPDX-License-Identifier: Apache-2.0
"""Z3 checks on SMT-LIB terms: satisfiability, validity, entailment, counterexamples (audit 017 §11, FM-014).

Z3 is optional and imported only when a check runs; without it every check returns ``blocked-escalated`` with
``G6_E_FORMAL_SOLVER_UNAVAILABLE``.

Input is closed and explicit:

* every symbol is declared by the caller with a sort: ``Int``, ``Real``, ``Bool`` or ``String`` (any case), a
  bit-vector ``(_ BitVec n)`` or an array ``(Array S T)`` of these; nothing is declared for you. The archive's Python
  dialect typed every free name ``Int``, so ``x > 0, x < 1`` "proved" ``x == 5`` (FM-014);
* each formula, premise or conclusion is one SMT-LIB term (an ``(assert t)`` wrapper is accepted), read before it
  reaches Z3: no quoted symbols, keywords or comments, a string literal only of printable ASCII and a ``#`` only in a
  numeral (``#x0f``, ``#b01``), so a term cannot carry a command such as ``set-option``; and every query Z3 reads is
  checked against the same closed grammar (``declare-const``, ``assert`` and one final ``check-sat``). The archive
  passed caller text to ``parse_smt2_string`` verbatim.

A validity check decides UNSAT(premises ∧ ¬conclusion) and, when that holds, whether the premises are satisfiable at
all: unsatisfiable premises make the result ``vacuous``, never ``valid``, and a conclusion whose every conjunct is a
premise is flagged ``conclusion_assumed`` (FM-014; the check_proof half of FM-003). Unknown, a timeout included, is
never read as a decision. Each check uses a fresh solver, so no state is shared between calls.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from g6.formal.envelope import (
    CONCLUSION_ASSUMED,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    PARSE_ERROR,
    SOLVER_ERROR,
    SOLVER_TIMEOUT,
    SOLVER_UNAVAILABLE,
    SOLVER_UNKNOWN,
    UNKNOWN_SORT,
    VACUOUS_PREMISES,
    Refusal,
    apply_envelope,
)
from g6.formal.schema import FormalResult, ProofArtifact, SolverCheck, ValidityResult

DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 600.0
MAX_TERM_CHARS = 10_000
MAX_QUERY_CHARS = 200_000
MAX_DEPTH = 200
MAX_SORT_CHARS = 200
MAX_BV_WIDTH = 1024

_BASE_SORTS = {"int": "Int", "real": "Real", "bool": "Bool", "string": "String"}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# Outside string literals: SMT-LIB simple-symbol characters, digits, parentheses, whitespace and '#' (only in a numeral,
# checked per token). Left out on purpose: '|' (quoted symbols), ':' (keywords, as in set-option
# :regular-output-channel, which writes a file), ';' (comments), '\'. A '"' only opens a string literal, read as
# SMT-LIB 2.6 reads it (a doubled quote inside is one quote), so a literal ends where Z3's does, never later.
_ALLOWED = re.compile(r"[A-Za-z0-9~!@$%^&*_\-+=<>.?/#()\s]*\Z")
_STRING = re.compile(r'"(?:[^"]|"")*"')
_TOKEN = re.compile(r'"(?:[^"]|"")*"|\(|\)|[^\s()"]+')
_NUMERAL = re.compile(r"#x[0-9A-Fa-f]+|#b[01]+")
_PRINTABLE = re.compile(r"[ -~]*\Z")
_COMMANDS = frozenset({
    "assert", "check-sat", "check-sat-assuming", "declare-const", "declare-datatype", "declare-datatypes",
    "declare-fun", "declare-sort", "define-const", "define-fun", "define-fun-rec", "define-funs-rec", "define-sort",
    "echo", "eval", "exit", "get-assertions", "get-assignment", "get-info", "get-model", "get-option", "get-proof",
    "get-unsat-assumptions", "get-unsat-core", "get-value", "pop", "push", "reset", "reset-assertions", "set-info",
    "set-logic", "set-option", "simplify",
})


class _Term(NamedTuple):
    text: str
    tree: Any


def load_z3() -> Any:
    """The ``z3`` module, imported now; ``Refusal(G6_E_FORMAL_SOLVER_UNAVAILABLE)`` when it cannot be."""
    try:
        import z3
    except Exception as exc:  # noqa: BLE001 - ImportError, or the native library failing to load
        raise Refusal(SOLVER_UNAVAILABLE,
                      f"z3-solver is not importable here ({type(exc).__name__}); install it with "
                      "pip install 'g6[formal]', or use solver='propositional' for Boolean formulas") from None
    return z3


def z3_version() -> str:
    return str(load_z3().get_version_string())


def _parse(text: str, what: str, limit: int) -> list[Any]:
    """Every top-level s-expression of ``text`` as nested tuples, after the character check."""
    if len(text) > limit:
        raise Refusal(INPUT_TOO_LARGE, f"{what} is longer than {limit} characters")
    outside = _STRING.sub(" ", text)
    if not _ALLOWED.match(outside):
        bad = "".join(sorted({ch for ch in outside if not _ALLOWED.match(ch)}))
        raise Refusal(PARSE_ERROR, f"{what} contains characters g6.formal does not read: {bad!r} (no quoted symbols, "
                                   "keywords, comments or unclosed strings)")
    stack: list[list[Any]] = [[]]
    for token in _TOKEN.findall(text):
        if token.startswith('"') and not _PRINTABLE.match(token):
            raise Refusal(PARSE_ERROR, f"{what} has a string literal with a character outside printable ASCII; write "
                                       "it as an escape such as \\u{e9}")
        if "#" in token and not token.startswith('"') and not _NUMERAL.fullmatch(token):
            raise Refusal(PARSE_ERROR, f"{what} has {token[:40]!r}: '#' only starts a numeral such as #x0f or #b0101")
        if token == "(":
            if len(stack) > MAX_DEPTH:
                raise Refusal(INPUT_TOO_LARGE, f"{what} is nested deeper than {MAX_DEPTH}")
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise Refusal(PARSE_ERROR, f"{what} has an unmatched ')'")
            done = stack.pop()
            stack[-1].append(tuple(done))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise Refusal(PARSE_ERROR, f"{what} has an unclosed '('")
    return stack[0]


def _text(node: Any) -> str:
    return node if isinstance(node, str) else "(" + " ".join(_text(n) for n in node) + ")"


def _term(text: Any, what: str) -> _Term:
    """One SMT-LIB term, in canonical spacing. A bare ``(assert t)`` is read as ``t``."""
    if not isinstance(text, str) or not text.strip():
        raise Refusal(PARSE_ERROR, f"{what} must be a non-empty SMT-LIB term, such as (> x 0)")
    nodes = _parse(text, what, MAX_TERM_CHARS)
    if len(nodes) != 1:
        raise Refusal(PARSE_ERROR, f"{what} must be exactly one SMT-LIB term, such as (> x 0); it has {len(nodes)} "
                                   "top-level items")
    node = nodes[0]
    if isinstance(node, tuple) and len(node) == 2 and node[0] == "assert":
        node = node[1]
    head = node[0] if isinstance(node, tuple) and node else node
    if isinstance(head, str) and head in _COMMANDS:
        raise Refusal(PARSE_ERROR, f"{what} is the SMT-LIB command {head!r}, not a term; declare symbols with their "
                                   "sorts instead")
    return _Term(_text(node), node)


def _terms(items: Any, what: str) -> list[_Term]:
    if isinstance(items, str) or not isinstance(items, Sequence):
        raise Refusal(INPUT_INVALID, f"the {what}s must be a list of terms, not {type(items).__name__}")
    return [_term(item, f"{what} {i}") for i, item in enumerate(items, 1)]


def _sort(node: Any) -> str | None:
    """The canonical text of a sort tree g6.formal declares, or ``None``: Int, Real, Bool, String (any case),
    ``(_ BitVec n)`` with 1 <= n <= ``MAX_BV_WIDTH``, and ``(Array S T)`` of these."""
    if isinstance(node, str):
        return _BASE_SORTS.get(node.lower())
    words = [w.lower() if isinstance(w, str) else w for w in node]
    if (len(node) == 3 and words[:2] == ["_", "bitvec"] and isinstance(node[2], str) and node[2].isdigit()
            and 1 <= int(node[2]) <= MAX_BV_WIDTH):
        return f"(_ BitVec {int(node[2])})"
    if len(node) == 3 and words[0] == "array":
        index, element = _sort(node[1]), _sort(node[2])
        return f"(Array {index} {element})" if index and element else None
    return None


def _sorts(sorts: Any) -> dict[str, str]:
    """``{name: canonical sort}``; any other sort, or a name that is not a plain symbol, is refused."""
    if sorts is None:
        return {}
    if not isinstance(sorts, Mapping):
        raise Refusal(INPUT_INVALID, "sorts must map each symbol name to its sort, such as Int or (_ BitVec 8)")
    out: dict[str, str] = {}
    for name, sort in sorts.items():
        if not isinstance(name, str) or not _NAME.match(name) or name in _COMMANDS:
            raise Refusal(INPUT_INVALID, f"{name!r} is not a plain symbol name (letters, digits and _, not starting "
                                         "with a digit)")
        try:
            nodes = _parse(sort, "a sort", MAX_SORT_CHARS) if isinstance(sort, str) else []
        except Refusal:
            nodes = []
        canonical = _sort(nodes[0]) if len(nodes) == 1 else None
        if canonical is None:
            raise Refusal(UNKNOWN_SORT, f"{name!r} has sort {sort!r}; g6.formal takes Int, Real, Bool or String (any "
                                        f"case), (_ BitVec n) with 1 <= n <= {MAX_BV_WIDTH}, or (Array S T) of these")
        out[name] = canonical
    return dict(sorted(out.items()))


def _check_timeout(timeout: Any) -> None:
    if (isinstance(timeout, bool) or not isinstance(timeout, int | float) or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_TIMEOUT_S):
        raise Refusal(INPUT_INVALID, f"timeout must be a number of seconds in (0, {MAX_TIMEOUT_S:g}]")


def _query(sorts: Mapping[str, str], terms: Sequence[str]) -> str:
    lines = [f"(declare-const {name} {sort})" for name, sort in sorts.items()]
    return "\n".join([*lines, *(f"(assert {t})" for t in terms), "(check-sat)"])


def _conj(terms: Sequence[str]) -> str:
    return "true" if not terms else terms[0] if len(terms) == 1 else "(and " + " ".join(terms) + ")"


def _plan(question: str, sorts: Mapping[str, str], assumptions: Sequence[str],
          goal: str) -> tuple[str, tuple[str, ...]]:
    """The statement and the queries that decide it; shared by every check and by ``recheck``."""
    if question == "satisfiable":
        return _conj(assumptions), (_query(sorts, assumptions),)
    return (f"(=> {_conj(assumptions)} {goal})",
            (_query(sorts, [*assumptions, f"(not {goal})"]), _query(sorts, assumptions)))


def _check_query(query: str) -> None:
    """Refuse any query outside the closed grammar: declare-const of a canonical sort, assert, one final check-sat."""
    nodes = _parse(query, "the query", MAX_QUERY_CHARS)
    if not nodes or nodes[-1] != ("check-sat",):
        raise Refusal(PARSE_ERROR, "a query ends with one (check-sat)")
    for node in nodes[:-1]:
        if (isinstance(node, tuple) and len(node) == 3 and node[0] == "declare-const" and isinstance(node[1], str)
                and _NAME.match(node[1]) and _sort(node[2]) == _text(node[2])):
            continue
        if isinstance(node, tuple) and len(node) == 2 and node[0] == "assert":
            continue
        raise Refusal(PARSE_ERROR, f"a query holds only declare-const, assert and one final check-sat; found "
                                   f"{_text(node)[:80]!r}")


def _message(exc: Exception) -> str:
    raw = exc.args[0] if exc.args else exc
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return " ".join(str(raw).split())


def _value(z3: Any, value: Any) -> Any:
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_int_value(value) or z3.is_bv_value(value):
        return value.as_long()  # a bit-vector as its unsigned value
    if z3.is_string_value(value):
        return value.as_string()
    return str(value)  # a rational such as 1/2, an algebraic number or an array, as Z3 prints it


def run_query(query: str, timeout: float) -> tuple[str, dict[str, Any], str]:
    """Decide one query with a fresh Z3 solver: ``(answer, model, reason_unknown)``, answer sat, unsat or unknown.

    The query must be in the closed grammar (``Refusal`` otherwise), so a recorded or caller-built query cannot set
    options or run commands.
    """
    _check_timeout(timeout)
    _check_query(query)
    z3 = load_z3()
    solver = z3.Solver()
    solver.set("timeout", max(1, int(timeout * 1000)))
    try:
        solver.from_string(query)
    except z3.Z3Exception as exc:
        raise Refusal(PARSE_ERROR, f"z3 did not accept the query: {_message(exc)}") from None
    try:
        answer = solver.check()
    except z3.Z3Exception as exc:
        raise Refusal(SOLVER_ERROR, f"z3 failed: {_message(exc)}") from None
    if answer == z3.sat:
        model = solver.model()
        return "sat", {d.name(): _value(z3, model[d]) for d in model.decls() if d.arity() == 0}, ""
    if answer == z3.unsat:
        return "unsat", {}, ""
    return "unknown", {}, str(solver.reason_unknown())


def _undecided(why: str, prefix: str = "") -> dict[str, str]:
    code = SOLVER_TIMEOUT if "timeout" in why or "canceled" in why else SOLVER_UNKNOWN
    return {"code": code,
            "reason": f"{prefix}z3 returned unknown ({why or 'no reason given'}); unknown is not a decision"}


def artifact_queries(artifact: ProofArtifact) -> tuple[str, tuple[str, ...]]:
    """The statement and queries an artifact's fields give, refusing fields that are not canonical."""
    sorts = _sorts(artifact.sorts)
    assumptions = [_term(a, "an assumption").text for a in artifact.assumptions]
    goal = _term(artifact.goal, "the goal").text if artifact.question == "valid" else ""
    if (sorts != artifact.sorts or tuple(assumptions) != artifact.assumptions or goal != artifact.goal
            or (artifact.question == "satisfiable" and not assumptions)):
        raise Refusal(PARSE_ERROR, "the artifact's sorts, assumptions or goal are not in canonical form")
    return _plan(artifact.question, sorts, assumptions, goal)


def check_sat(formula: str, variables: Mapping[str, str] | None = None, assertions: Sequence[str] = (), *,
              timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "", run_id: str = "") -> FormalResult:
    """Is ``formula`` ∧ every assertion satisfiable over the declared ``variables``? A model when it is."""
    started = time.monotonic()
    try:
        sorts = _sorts(variables)
        terms = [_term(formula, "the formula").text, *(t.text for t in _terms(assertions, "assertion"))]
        statement, (query,) = _plan("satisfiable", sorts, terms, "")
        answer, model, why = run_query(query, timeout)
        artifact = ProofArtifact(operation="check_sat", question="satisfiable", statement=statement, sorts=sorts,
                                 assumptions=tuple(terms), solver="z3", solver_version=z3_version(),
                                 checks=(SolverCheck(query=query, result=answer),))
        result = FormalResult(operation="check_sat", backend="z3",
                              satisfiable={"sat": True, "unsat": False}.get(answer), model=model, artifact=artifact,
                              **(_undecided(why) if answer == "unknown" else {}))
    except Refusal as refusal:
        result = FormalResult(operation="check_sat", backend="z3", code=refusal.code, reason=refusal.reason)
    result = result.model_copy(update={"elapsed_s": round(time.monotonic() - started, 4)})
    return apply_envelope(result, backend="z3", operation="check_sat", request_id=request_id, run_id=run_id)


def _validity(operation: str, premises: Any, conclusion: Any, sorts: Any, timeout: Any, request_id: str,
              run_id: str) -> ValidityResult:
    started = time.monotonic()
    try:
        declared = _sorts(sorts)
        given = _terms(premises, "premise")
        goal = _term(conclusion, "the conclusion")
        # audit 017 FM-014 / FM-003: a conclusion assumed among the premises is circular. Syntactic: every conjunct
        # of the conclusion is a premise or a conjunct of one.
        assumed = set(_conjuncts(goal.tree)) <= {c for p in given for c in _conjuncts(p.tree)}
        assumptions = [p.text for p in given]
        statement, (main_query, premises_query) = _plan("valid", declared, assumptions, goal.text)
        answer, model, why = run_query(main_query, timeout)
        checks = [SolverCheck(query=main_query, result=answer)]
        fields: dict[str, Any] = {"conclusion_assumed": assumed}
        if answer == "sat":
            fields["counterexample"] = model  # the premises hold in this model, so they are consistent
        elif answer == "unknown":
            fields |= _undecided(why)
        else:
            # UNSAT(premises ∧ ¬conclusion) is only valid when the premises can hold at all (FM-014).
            premises_answer, _, why = run_query(premises_query, timeout)
            checks.append(SolverCheck(query=premises_query, result=premises_answer))
            if premises_answer == "unsat":
                fields |= {"vacuous": True, "code": VACUOUS_PREMISES,
                           "reason": "the premises are unsatisfiable, so they entail every conclusion: the result is "
                                     "vacuous, not valid"}
            elif premises_answer == "unknown":
                fields |= _undecided(why, "could not establish that the premises are consistent: ")
            else:
                fields["valid"] = True
                if assumed:
                    fields |= {"code": CONCLUSION_ASSUMED,
                               "reason": "every conjunct of the conclusion is literally among the premises, so the "
                                         "check is circular and supports nothing"}
        fields["artifact"] = ProofArtifact(operation=operation, question="valid", statement=statement, sorts=declared,
                                           assumptions=tuple(assumptions), goal=goal.text, solver="z3",
                                           solver_version=z3_version(), checks=tuple(checks))
    except Refusal as refusal:
        fields = {"code": refusal.code, "reason": refusal.reason}
    result = ValidityResult(operation=operation, backend="z3", elapsed_s=round(time.monotonic() - started, 4), **fields)
    return apply_envelope(result, backend="z3", operation=operation, request_id=request_id, run_id=run_id)


def _conjuncts(node: Any) -> list[Any]:
    if isinstance(node, tuple) and node and node[0] == "and":
        return [c for child in node[1:] for c in _conjuncts(child)]
    return [node]


def check_validity(premises: Sequence[str], conclusion: str, *, sorts: Mapping[str, str] | None = None,
                   timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "", run_id: str = "") -> ValidityResult:
    """Do the ``premises`` entail the ``conclusion`` over the declared ``sorts``? ``vacuous`` when the premises are
    unsatisfiable; ``conclusion_assumed`` when the conclusion is among them; ``counterexample`` when it does not follow.
    """
    return _validity("check_validity", premises, conclusion, sorts, timeout, request_id, run_id)


def check_entailment(a: str, b: str, *, sorts: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT_S,
                     request_id: str = "", run_id: str = "") -> ValidityResult:
    """Does ``a`` entail ``b``? ``check_validity([a], b)``: vacuous when ``a`` is unsatisfiable."""
    return _validity("check_entailment", [a], b, sorts, timeout, request_id, run_id)


def find_counterexample(claim: str, constraints: Sequence[str] = (), *, sorts: Mapping[str, str] | None = None,
                        timeout: float = DEFAULT_TIMEOUT_S, request_id: str = "", run_id: str = "") -> ValidityResult:
    """A model of the ``constraints`` in which ``claim`` is false, in ``counterexample``; ``None`` with ``valid=True``
    when the claim holds under them. Vacuous when the constraints are unsatisfiable."""
    return _validity("find_counterexample", constraints, claim, sorts, timeout, request_id, run_id)


__all__ = ["DEFAULT_TIMEOUT_S", "MAX_TIMEOUT_S", "artifact_queries", "check_entailment", "check_sat", "check_validity",
           "find_counterexample", "load_z3", "run_query", "z3_version"]
