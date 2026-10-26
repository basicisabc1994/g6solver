# SPDX-License-Identifier: Apache-2.0
"""g6.prolog — query a Prolog program in a restricted syntax with SWI-Prolog, out of process, bounded by limits, every answer re-checked.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the Prolog one, with
the earlier version's audit finding fixed (audit 017, FM-010: caller code ran, through ``shell/1`` among others, with
no time limit and one database shared between calls).

Capability label: ``implemented``.

    from g6.prolog import query, prove
    family = ("parent(tom, bob). parent(bob, ann). ancestor(X, Y) :- parent(X, Y). "
              "ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).")
    r = query(family, "ancestor(tom, Who)")
    [a["bindings"]["Who"] for a in r.answers], r.exhausted    # (["bob", "ann"], True)
    r.completion_state, r.recheck["passed"]                     # ("qualified-draft", True)
    prove(family, "ancestor(ann, tom)").succeeded             # False: not derivable from this program

SWI-Prolog is yours, out of process, never installed by G6: install it from swi-prolog.org, which puts ``swipl`` on
PATH, or set ``G6_SWIPL`` to the ``swipl`` program. SWI-Prolog is BSD-2-Clause. No pip extra: caller code never runs in
G6's process, so there is no in-process bridge to install.

Every op runs a fresh ``swipl`` (:mod:`g6.prolog.runner`) in a fresh directory:
``swipl -f none -F none --no-packs --no-tty --no-pce -q --stack-limit=<n>m driver.pl``, so no user or site start-up
file and no packs, with a minimal environment, a time limit (60 seconds by default, at most 600, plus a start-up
allowance), an output cap and its process tree killed when a limit is passed; the database is the new process's own.
SWI-Prolog's stacks are capped on every system by ``--stack-limit``, half of ``memory_mb`` (2048 by default) and at
least 64 MB; the whole process is capped at ``memory_mb`` only on Windows, through the Job Object. The program and the
goal reach SWI-Prolog as files, never on a command line, and G6's fixed driver (``driver.pl``) reads them, never
consults them, and accepts only a restricted syntax (owner, 2026-10-21):

* no directive (``:- ...``, ``?- ...``) runs: each is refused (``G6_E_PROLOG_DIRECTIVE_REFUSED``);
* a program defines only its own predicates: a clause whose head is a built-in, a library predicate (``append/3``,
  ``member/2``) or a hook SWI-Prolog calls (``portray/1``, ``message_hook/3``), names another module or is not callable
  is ``G6_E_PROLOG_CLAUSE_REFUSED``;
* every goal, in a clause body and in the query, calls a predicate the program defines or one on the driver's
  allow-list (``allowed/1`` in ``driver.pl``: control, unification and comparison, arithmetic, type checks, term
  construction, plain atom and number conversions, plain list predicates), or is ``findall/3``, ``bagof/3``,
  ``setof/3``, ``forall/2``, ``aggregate_all/3`` or ``maplist/2`` to ``maplist/4`` with its goal arguments checked the
  same way; never a variable and never ``Module:Goal``. Anything else is ``G6_E_PROLOG_GOAL_NOT_ALLOWED`` and a call to
  a predicate nothing defines is ``G6_E_PROLOG_UNDEFINED_PREDICATE``, each naming the predicate and where it appears; a
  syntax error is ``G6_E_PROLOG_SYNTAX_ERROR``, with its line and column. A program that keeps to it is asserted into a
  fresh module and made static;
* SWI-Prolog's sandbox library (``safe_goal/1``) checks the goal as well, an extra layer
  (``G6_E_PROLOG_UNSAFE_GOAL``);
* the goal runs under an inference limit, a time limit and the stack limit, collecting at most ``max_solutions``
  answers; answers and errors are written as quoted terms, never through SWI-Prolog's message translation or a portray
  hook.

The restricted syntax is not a security boundary, and neither is SWI-Prolog's sandbox library: SWI-Prolog runs as you.
Run programs you do not trust inside your own sandbox, such as a container or a virtual machine.

Ops (:mod:`g6.prolog.ops`): ``query`` (the answers as variable bindings, and ``exhausted``, true only when the search
finished below the cap), ``prove`` (whether the goal succeeds), ``check_program`` (the predicates the program defines,
and its errors with line and column; nothing is queried) and ``swipl_version``. Every answer is re-checked in a second,
fresh SWI-Prolog process under the same restricted syntax and limits: each answer, read back and put into the goal,
must hold again with its free variables left free; and when the search finished, the search run again with a cap one
higher must give the same answers in the same order and no more (an empty answer set must fail again). A re-check that
disagrees is ``G6_E_PROLOG_CERTIFICATE_REFUTED``.

``qualified-draft`` is the best any op reaches, never ``verified``: the answers follow from your program as SWI-Prolog
runs it, and whether the program says what you mean is for a person to inspect. A goal that fails is not derivable
from this program, which, under Prolog's closed world, is not the same as false. All four ops run SWI-Prolog, so they
are sensitive: callable from Python, and over MCP only when the person who runs the server lists them in
``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP. Codes are ``G6_E_PROLOG_*``.

Not ported from the archive's ``prolog_mcp``, and why:

* consulting the program, which ran its directives, and every path to ``shell``, files or the network (FM-010); the
  restricted syntax and the read-only driver replace them;
* the in-process bridge (``janus``), so caller code never runs in G6's process, and the pure-Python mini-Prolog that
  answered when SWI-Prolog was absent without saying it had stood in; a missing SWI-Prolog is a refusal;
* the database shared between calls and the ops that changed it (``assert_clause``, ``retract_clause``), and the
  program store (``save_program``, ``get_program``, ``list_programs``, ``update_program``, ``delete_program``);
* ``findall``, ``functor``, ``unify`` and ``cut_fail``, which are goals ``query`` already runs; ``trace_query`` and
  ``meta_interpret``, whose tracer and meta-interpreter ran caller text unchecked; the analysis ops
  (``validate_program``, ``compare_programs``, ``inspect_predicate``, ``explain_query``, ``module_info``), which
  guessed from text; and the hard-coded Windows install paths and the standalone server.
"""
from g6.prolog import driver, ops, runner
from g6.prolog.envelope import CLOSED_WORLD, SWIPL, SWIPL_ENV, Refusal, finish
from g6.prolog.ops import check_program, prove, query, swipl_version
from g6.prolog.prolog_block import SENSITIVE_OPS, PrologBlock, PrologInput
from g6.prolog.schema import PrologResult

__g6_component__ = True
__g6_block__ = "PrologBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they run SWI-Prolog on your program
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unsupported_conclusion", "missed_implication")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (SWIPL,)

__all__ = ["CLOSED_WORLD", "PrologBlock", "PrologInput", "PrologResult", "Refusal", "SENSITIVE_OPS", "SWIPL",
           "SWIPL_ENV", "check_program", "driver", "finish", "ops", "prove", "query", "runner", "swipl_version"]
