# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Code correspondence on the restricted return fragment (G6 0.3 design, sections 15.1 and 15.3).

A formal result concerns its encoded statement. Whether a function does what an accepted statement says is a
separate obligation, and this is the one supported way to meet it in 0.3.0: a function that is one ``return`` of
int and bool arithmetic, read from its source by ``g6.cegis.z3_verifier.candidate_term`` (parsed, never run), and
then run for real through a caller-supplied runner.

Two things must both hold before anything is said of the code:

- the engine decides the statement's obligation of the function *as read*: valid under the domain and assumptions
  (no input breaks the rule), or a counterexample;
- the function *that runs* shows the same: a counterexample counts only when the real call reproduces it, and a
  result with no counterexample counts only when the real code gives, at every replayed input, exactly what the
  reading gives. A reading the real code does not reproduce is a model result only.

Everything else is ``not_established``, with the reason: a function outside the grammar (a loop, a call, two
returns, a bool used as a number), a renamed function or changed parameters, an undecided or unavailable engine, an
empty domain, no runner. ``pass`` here means "no input of the stated domain breaks the stated rule, for this reading,
and the code that ran agreed with the reading where it was replayed": it is a checked result with those limits.
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from g6.harness.formalisation import smt_adapter, smt_ir
from g6.harness.formalisation.query import QueryResult, valid_under, witness
from g6.harness.formalisation.statement import FormalStatement

#: The most inputs a result with no counterexample is replayed on.
MAX_REPLAYS = 8


@dataclass(frozen=True)
class CodeResult:
    result: Literal["pass", "fail", "not_established"]
    query: QueryResult | None
    replayed: Literal["reproduced", "not_reproduced", "not_replayable", "not_replayed"]
    why: str
    #: Each real call made: ``{"call", "expected", "observed", "agrees"}``.
    replays: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict:
        return {"result": self.result, "query": None if self.query is None else self.query.to_dict(),
                "replayed": self.replayed, "why": self.why, "replays": [dict(one) for one in self.replays]}


def _not(why: str, query: QueryResult | None = None, replayed: str = "not_replayed",
         replays: tuple = ()) -> CodeResult:
    return CodeResult("not_established", query, replayed, why, replays)


def _function(source: str, fn_name: str, names: list[str]) -> str:
    """``""`` when ``source`` defines ``fn_name`` once, at the top level, undecorated, with exactly the statement's
    parameters; else why not."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError) as exc:
        return f"the source does not parse ({type(exc).__name__})"
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
             and node.name == fn_name]
    if not found:
        return f"the source has no function {fn_name} at its top level: the entry point is missing"
    if len(found) > 1:
        return f"{fn_name} is defined {len(found)} times"
    node = found[0]
    if isinstance(node, ast.AsyncFunctionDef) or node.decorator_list:
        return f"{fn_name} is decorated or asynchronous, so the code that runs is not its body alone"
    args = node.args
    plain = [a.arg for a in args.args]
    if (args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or args.kw_defaults
            or plain != names):
        return (f"the parameters of {fn_name} are ({', '.join(plain)}) and the statement's are "
                f"({', '.join(names)}), plain and without defaults")
    return ""


def _violates(statement: FormalStatement, inputs: Mapping[str, Any], observed: Mapping[str, Any]) -> bool | None:
    """Whether a real observation at ``inputs`` breaks the statement; ``None`` when it is not an observation this
    fragment reads (so nothing is concluded from it)."""
    seen = statement.observation
    guard = statement.definition(smt_adapter.RAISES_WHEN)
    raising = bool(guard is not None and smt_ir.evaluate(smt_ir.read(guard, dict(seen.inputs)), inputs))
    if set(observed) == {"raises"}:
        return not raising or observed["raises"] != seen.error
    if set(observed) != {"returns"}:
        return None
    value = observed["returns"]
    if type(value) is not {"Int": int, "Bool": bool}[seen.result_sort]:
        return None   # another type than the declared result: not an observation this fragment reads
    if raising:
        return True
    relation = smt_ir.read(statement.text, smt_adapter.ADAPTER.sorts(statement))
    return not smt_ir.evaluate(relation, {**inputs, smt_adapter.RESULT: value})


def _ran(run: Callable[[dict], dict], call: dict) -> Mapping[str, Any] | None:
    try:
        observed = run({"args": list(call["args"]), "kwargs": dict(call["kwargs"])})
    except Exception:   # noqa: BLE001 - a runner that cannot run leaves the replay not made, whatever it raised
        return None
    return observed if isinstance(observed, Mapping) else None


def read_function(statement: FormalStatement, source: str, fn_name: str) -> tuple[smt_ir.Term | None, str]:
    """The function's return expression as a term over the statement's inputs, read from its source and never run;
    or ``(None, why)`` when the statement, the entry point or the function is outside the supported check."""
    from g6.cegis.z3_verifier import Untranslatable, candidate_term

    why = smt_adapter.ADAPTER.supports(statement)
    if why:
        return None, f"the statement is outside the supported correspondence check: {why}"
    seen = statement.observation
    if seen.entry_point.rsplit(".", 1)[-1] != fn_name:
        return None, f"the statement is about {seen.entry_point}, not a function named {fn_name}"
    why = _function(source, fn_name, [name for name, _ in seen.inputs])
    if why:
        return None, why
    inputs = dict(seen.inputs)
    try:
        text, sort = candidate_term(source, fn_name, inputs)
        term = smt_ir.read(text, inputs)
    except (Untranslatable, smt_ir.OutsideFragment) as exc:
        return None, f"{fn_name} is outside the restricted return fragment, so nothing is established of it: {exc}"
    if sort != seen.result_sort or smt_ir.sort_of(term) != seen.result_sort:
        return None, (f"{fn_name} returns {'an Int' if sort == 'Int' else 'a Bool'} and the statement's result is "
                      f"declared {seen.result_sort}")
    return term, ""


def check_code(statement: FormalStatement, source: str, fn_name: str, *,
               run: Callable[[dict], dict] | None, timeout: float = 10.0) -> CodeResult:
    """Does the function ``fn_name`` of ``source`` do what ``statement`` says, on the statement's domain? ``run``
    makes one real call (``{"args", "kwargs"}``) and returns what was observed (``{"returns": v}`` or
    ``{"raises": name}``); with no runner the answer is a model result only."""
    term, why = read_function(statement, source, fn_name)
    if term is None:
        return _not(why)
    return decide(statement, term, fn_name, run=run, timeout=timeout)


def decide(statement: FormalStatement, term: smt_ir.Term, fn_name: str, *, run: Callable[[dict], dict] | None,
           timeout: float = 10.0, at: tuple[str, ...] = ()) -> CodeResult:
    """The correspondence check on a function already read as ``term``; ``at`` are further conjuncts over the inputs
    that narrow where it is asked (the negative control asks at one confirmed example's input)."""
    adapter = smt_adapter.ADAPTER
    seen = statement.observation
    names = [name for name, _ in seen.inputs]
    inputs = dict(seen.inputs)
    sorts = adapter.sorts(statement)
    sha = statement.sha256
    premises = [*statement.domain, *statement.assumptions, *at]
    inside = witness(inputs, premises, statement_sha256=sha, timeout=timeout)
    if inside.reading != "witness":
        return _not("the statement's domain has no witness here (" + inside.reading + "), so nothing follows under "
                    "it", inside)
    relation = smt_ir.read(statement.text, sorts)
    obligation = smt_ir.write(smt_ir.substitute(relation, smt_adapter.RESULT, term))
    guard = statement.definition(smt_adapter.RAISES_WHEN)
    if guard is not None:   # a function of this fragment never raises: the guard must be false wherever it is called
        obligation = f"(and (not {guard}) {obligation})"
    asked = valid_under(inputs, premises, obligation, statement_sha256=sha, timeout=timeout)
    if not asked.decided:
        return _not(f"the engine did not decide ({asked.reading}; {asked.code or asked.native}); that is not an "
                    "answer either way", asked)
    if run is None:
        return _not("model result only: the engine decided the function as read, and no real call was made", asked)

    def replay(values: Mapping[str, Any]) -> tuple[dict, Mapping[str, Any] | None]:
        call = {"args": [values[name] for name in names], "kwargs": {}}
        return call, _ran(run, call)

    if asked.reading == "counterexample":
        values = {name: asked.witness.get(name) for name in names}
        if any(type(values[name]) is not {"Int": int, "Bool": bool}[sort] for name, sort in seen.inputs):
            return _not("the engine's counterexample is not an executable input", asked, "not_replayable")
        call, observed = replay(values)
        if observed is None:
            return _not("the counterexample was not replayed: the runner made no call", asked, "not_replayed")
        broken = _violates(statement, values, observed)
        entry = ({"call": call, "expected": "an observation that breaks the rule", "observed": dict(observed),
                  "agrees": broken is True},)
        if broken is None:
            return _not("the real call gave nothing this fragment observes", asked, "not_replayable", entry)
        if not broken:
            return _not("the counterexample did not reproduce on the code that ran, so it is dropped: the function "
                        "as read differs from the code that ran", asked, "not_reproduced", entry)
        return CodeResult("fail", asked, "reproduced",
                          f"the code breaks the statement at {call['args']}: observed {dict(observed)!r}", entry)

    # no counterexample: the reading must be the code that runs, at every replayed input
    points: list[dict] = [{name: inside.witness[name] for name in names}]
    for text_guard in dict.fromkeys(smt_ir.write(g) for g in smt_ir.guards(relation)):
        if smt_ir.symbols(smt_ir.read(text_guard, sorts)) - set(names):
            continue
        for side in (text_guard, f"(not {text_guard})"):
            at = witness(inputs, [*premises, side], statement_sha256=sha, timeout=timeout)
            if at.reading == "witness":
                points.append({name: at.witness[name] for name in names})
    replays = []
    for values in list({tuple(sorted(p.items())): p for p in points}.values())[:MAX_REPLAYS]:
        call, observed = replay(values)
        if observed is None:
            return _not("the result was not replayed: the runner made no call", asked, "not_replayed", tuple(replays))
        if not set(observed) <= {"returns", "raises"} or len(observed) != 1:
            return _not("the real call gave nothing this fragment observes", asked, "not_replayable", tuple(replays))
        expected = {"returns": smt_ir.evaluate(term, values)}
        agrees = (set(observed) == {"returns"} and type(observed["returns"]) is type(expected["returns"])
                  and observed["returns"] == expected["returns"])
        replays.append({"call": call, "expected": expected, "observed": dict(observed), "agrees": agrees})
        if not agrees:
            return _not(f"at {call['args']} the function as read gives {expected['returns']!r} and the code that ran "
                        f"gave {dict(observed)!r}: the function as read differs from the code that ran, so the result "
                        "is about the reading only", asked, "not_reproduced", tuple(replays))
    return CodeResult("pass", asked, "reproduced",
                      f"no input of the stated domain breaks the statement for {fn_name} as read, and the code that ran "
                      f"agreed with the reading at {len(replays)} replayed input(s)", tuple(replays))


__all__ = ["MAX_REPLAYS", "CodeResult", "check_code", "decide", "read_function"]
