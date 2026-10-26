# SPDX-License-Identifier: Apache-2.0
"""g6.datalog — work out what follows from facts and rules in a strict Datalog, on two engines that must agree, and check every answer before it is reported.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the Datalog one.

Capability label: ``implemented``.

    from g6.datalog import evaluate, explain, query
    family = '''
        parent(alice, bob). parent(bob, carol).
        ancestor(X, Y) :- parent(X, Y).
        ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
    '''
    r = query(family, "ancestor(alice, X)")
    [a["bindings"] for a in r.queries[0]["answers"]]   # [{"X": "bob"}, {"X": "carol"}]
    r.completion_state, r.engines                       # ("qualified-draft", ["pure_python", "z3"])
    explain(family, "ancestor(alice, carol)").explanation["derivation"]["from"]
    # [{"atom": "parent(alice, bob)", "by": "fact"}, {"atom": "ancestor(bob, carol)", "by": "rule", ...}]

What it does, in process:

* reads a strict Datalog with its own parser (:mod:`g6.datalog.language`): facts, rules, stratified negation
  (``not p(X)``), comparisons on bound variables and queries (``ancestor(alice, X)?``), with facts also accepted as
  declarative JSON. A program that does not parse, a rule that is unsafe, negation through recursion, a relation used
  with two arities, and programs over the size and time limits (at most 60 seconds) are each refused with their own
  ``G6_E_DATALOG_*`` code, the line and the column. Nothing a caller sends is evaluated as code;
* evaluates it on two engines: a pure-Python semi-naive engine, stratum by stratum, which records how each tuple was
  derived; and Z3's Datalog engine (``z3.Fixedpoint``, ``pip install 'g6[formal]'``, imported only when an op runs),
  with every constant encoded as a bit-vector code in the comparison order. The two must derive the same relations,
  tuple for tuple, or the answer is refused with ``G6_E_DATALOG_ENGINES_DISAGREE``. Without Z3 the pure-Python engine
  runs alone and the card says so, and how to add the second;
* checks the model in pure Python before any answer is built from it (:mod:`g6.datalog.checks`): closure (applying
  every rule once more adds nothing), support (every tuple derives from the facts, through tuples derived before it)
  and negation (each negated atom is absent from the completed lower strata). A model the check contradicts is refused
  with ``G6_E_DATALOG_CERTIFICATE_REFUTED``. A tuple's support is its certificate: the rule and the facts or earlier
  tuples it used.

Ops: ``evaluate`` (the derived relations), ``query`` (each answer with its derivation), ``explain`` (a tuple's
derivation tree, or that it is not derivable, which the check shows) and ``check`` (the parse, arity, safety and
stratification report, nothing evaluated).

The best state is ``qualified-draft``. The model of your program is what the program says, not a proof a person has
inspected, so nothing here is ``verified``. ``DatalogBlock`` makes every op callable over MCP (``g6_invoke``); none runs
code, so none is sensitive.

Not ported from the archive's ``datalog_mcp``, and why:

* pyDatalog, which it imported but never called (LGPL, unmaintained), and its install hint; Z3's own Datalog engine is
  the second engine instead, and clingo is not part of this package;
* its pure-Python engine: a naive fixpoint capped at 100 rounds (a longer derivation was cut short without a word),
  negation read during the same fixpoint rather than by strata, no safety check, and a regular-expression parser that
  split quoted constants at their commas. The engine here is new, and every answer it gives is checked;
* ``run_code`` and the program store (create, load, save, get, list, update, delete, export, search, compare), which
  need persistence and are not reasoning; the aggregates, which summed any value that looked like a number; and the
  query "optimiser" and grounding helpers, which reported nothing the engine used.
"""
from g6.datalog import checks
from g6.datalog.datalog_block import DatalogBlock, DatalogInput
from g6.datalog.envelope import Refusal, finish
from g6.datalog.language import Program, parse_goal, parse_program
from g6.datalog.ops import check_program, evaluate, explain, query
from g6.datalog.schema import DatalogResult

__g6_component__ = True
__g6_block__ = "DatalogBlock"
__g6_sensitive_ops__: tuple[str, ...] = ("assert_facts", "reset")
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unsupported_conclusion", "missed_implication", "inconsistent_rule_use")
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",)}

__all__ = ["DatalogBlock", "DatalogInput", "DatalogResult", "Program", "Refusal", "check_program", "checks", "evaluate",
           "explain", "finish", "parse_goal", "parse_program", "query"]
