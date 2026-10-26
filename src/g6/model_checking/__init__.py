# SPDX-License-Identifier: Apache-2.0
"""g6.model_checking — check a state machine for broken invariants, deadlocks and reachable goals, with Z3 or TLA+'s TLC, and re-check every answer.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the model-checking one.

Capability label: ``implemented``.

    from g6.model_checking import check_invariant, prove_invariant
    counter = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))",
               "invariants": {"small": "(<= x 3)"}}
    r = check_invariant(counter, depth=10)
    r.verdict, [s["state"]["x"] for s in r.trace]       # ("violated", [0, 1, 2, 3, 4])
    r.completion_state, r.recheck["passed"]             # ("blocked-escalated", True)
    prove_invariant({**counter, "invariants": {"natural": "(>= x 0)"}}).k   # 1: verdict "k_inductive", at k = 1

Symbolic state models, in process with Z3 (``pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'``; imported only when an op runs), callable over
MCP. A model is state variables with their sorts (``Int``, ``Real``, ``Bool``, ``(_ BitVec n)``), an initial condition,
a transition relation and named invariants, all SMT-LIB terms read through g6.formal's closed grammar. The next-state
copy of ``x`` is written ``next_x``: SMT-LIB has no prime. Ops (:mod:`g6.model_checking.symbolic`):

* ``check_invariant``: bounded model checking to a depth of at most 50 steps; a state breaking an invariant with its
  trace, or none on paths of up to that depth, which is all it claims;
* ``prove_invariant``: k-induction, the induction step over paths with no repeated state; the verdict ``k_inductive``
  (reported with its k, ``qualified-draft`` at best), a counterexample from the initial states, or "not inductive at
  k" with the induction-step trace, which need not start in a reachable state and is labelled as not a counterexample;
* ``check_deadlock``: a reachable state with no successor, within the bound;
* ``reachable``: a trace to a state meeting a goal, within the bound.

A model whose init no state satisfies, or whose transition gives no state a successor, is refused as vacuous. Every
trace is re-checked state by state on fresh solvers (state 0 satisfies init, each step the transition, the last state
breaks the invariant, has no successor or meets the goal), and every claim that nothing was found, that every path
ends, or that the invariants are k-inductive is decided again in a second Z3 context (:mod:`.checks`). An answer
the re-check contradicts is ``G6_E_MODEL_CHECK_CERTIFICATE_REFUTED``.

TLA+ through TLC and SANY, out of process (:mod:`g6.model_checking.tlc`): ``tlc_check`` (a spec and a config, with
invariants, properties, constants, deadlock checking and workers) and ``sany_parse`` (syntax, semantics and levels).
You supply ``tla2tools.jar`` and Java 11 or later: ``G6_TLA2TOOLS_JAR`` names the jar and ``G6_TLA2TOOLS_SHA256`` pins
its SHA-256, which is checked on the bytes that run; ``G6_JAVA`` or PATH gives Java. G6 never downloads the jar; it is
tested on v1.7.4, the latest stable release, and on builds from the main line. Each
run has a fresh directory, a time limit (at most 600 seconds), an output cap and its process tree killed when either is
passed; the spec reaches TLC as a file. TLC's "no error" is TLC's own verdict, ``qualified-draft`` at best; a violation
is ``blocked-escalated`` with TLC's trace as structured states. Both ops are sensitive, because TLC runs your spec as
you: callable from Python, and over MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE``.

States are ``qualified-draft`` or ``blocked-escalated``, never ``verified``: a model checker's answer on your model is
not a proof a person has inspected. Codes are ``G6_E_MODEL_CHECK_*``.

Not ported from the archive's ``tla_mcp`` and ``pynusmv_mcp``, and why:

* the jar download (audit 017 FM-012: ``tla2tools.jar`` fetched unpinned from the network on first use); the jar is
  yours, pinned by its digest;
* ``run_code`` and the ``eval`` in both management handlers, the model stores and their text search, the spec "operator
  injection" and the regular expression that guessed the specification's name; and its Windows-only Java install
  hint;
* TLC's simulation mode, which reported random traces as if they were a check;
* the SMV-like language and its pure-Python breadth-first search and "bounded model checking", which split formulas
  at ``&`` and ``|`` without precedence, read any expression it did not know as the first value of the domain, and
  labelled its own answers ``verified`` (FM-011); pynusmv and NuSMV (LGPL, unmaintained) and the Docker bridge. The
  symbolic models here are SMT-LIB, checked by Z3, every answer re-checked;
* CTL, LTL and fairness checking over SMV models, which need an engine G6 does not have; TLC checks temporal
  properties of TLA+ specs instead.
"""
from g6.core.outside import OutsideProgram
from g6.model_checking.envelope import JAVA_ENV, SUPPLY_JAR
from g6.model_checking import checks, symbolic, tlc
from g6.model_checking.checks import recheck_trace
from g6.model_checking.envelope import Refusal, finish
from g6.model_checking.model_checking_block import SENSITIVE_OPS, ModelCheckingBlock, ModelCheckingInput
from g6.model_checking.schema import ModelCheckResult, StateModel
from g6.model_checking.symbolic import check_deadlock, check_invariant, prove_invariant, reachable
from g6.model_checking.tlc import sany_parse, tlc_check
from g6.model_checking.tlc_output import parse_sany_output, parse_tlc_output, parse_value

__g6_component__ = True
__g6_block__ = "ModelCheckingBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they run TLC or SANY on your spec
__g6_requires__: tuple[str, ...] = ("formal",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("rare_interleaving",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (OutsideProgram(
    name="Java", purpose="tlc_check and sany_parse run the TLA+ tools (tla2tools.jar) on Java 11 or later",
    install=f"Install Java 11 or later and put java on PATH, or set {JAVA_ENV} to the java program. {SUPPLY_JAR}",
    licence="OpenJDK is GPL-2.0 with the Classpath Exception; the TLA+ tools are MIT", command="java", env=JAVA_ENV,
    version_args=("-version",)),)
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",)}

__all__ = ["ModelCheckResult", "ModelCheckingBlock", "ModelCheckingInput", "Refusal", "SENSITIVE_OPS", "StateModel",
           "check_deadlock", "check_invariant", "checks", "finish", "parse_sany_output", "parse_tlc_output",
           "parse_value", "prove_invariant", "reachable", "recheck_trace", "sany_parse", "symbolic", "tlc",
           "tlc_check"]
