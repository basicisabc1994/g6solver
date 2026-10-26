# SPDX-License-Identifier: Apache-2.0
"""g6.formal — decide logic questions exactly, optimisation included, with Z3 or pure Python, and label every answer.

g6.formal — decide logic questions with Z3 or pure Python, and label every answer for what it is (audit 017 §11).
The owner, 2026-10-20: formal methods ported into the library.

Capability label: ``implemented``.

    from g6.formal import check_sat, check_validity, optimise
    r = check_sat("(and (> x 2) (< x 4))", {"x": "Int"})
    r.satisfiable, r.model, r.completion_state    # (True, {"x": 3}, "qualified-draft")
    v = check_validity(["(assert false)"], "(> 1 2)")
    v.valid, v.vacuous, v.completion_state        # (False, True, "blocked-escalated")
    o = optimise([["minimise", "(+ x y)"]], ["(>= x 2)", "(>= y 3)"], {"x": "Int", "y": "Int"})
    o.optimal, o.model                            # (True, {"x": 2, "y": 3})

What it is, all in process:

* Z3 on SMT-LIB terms over symbols you declare (``Int``, ``Real``, ``Bool``, ``String``, ``(_ BitVec n)``,
  ``(Array S T)``; nothing is declared for you), when z3-solver is installed (``pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'``; imported
  only when a check runs): ``check_sat``, ``check_validity``, ``check_entailment``, ``find_counterexample``, and from
  the archive's ``z3_mcp``, ``optimise`` (lexicographic, with bounds), ``unsat_core`` (minimal), ``simplify``,
  ``eliminate_quantifiers``, ``count_models`` (exact, or a lower bound when the cap is hit) and
  ``check_equivalence``. Each of these checks Z3's answer again on fresh solvers before reporting it, and an answer
  its own check contradicts is refused;
* ``dpll_solve`` and ``check_propositional``, a pure-Python DPLL for Boolean formulas; ``solve``, one typed
  ``LogicalQuery`` to either;
* ``four_valued``, Belnap's logic of true, false, both and neither (the archive's ``qa_logic``), with its connectives,
  truth tables and modal frames whose conditions are checked.

A validity check also asks whether the premises can hold: if they cannot, the result is ``vacuous``, not ``valid``,
and a conclusion found among the premises is flagged. Every result passes through ``apply_envelope`` and carries
``completion_state``, ``warning_card``, ``evidence`` (the exact queries, the solver and its version, the certificate),
``request_id`` and ``run_id``. ``recheck`` runs a result's recorded queries again; ``solver_support`` probes what works
here. ``FormalBlock`` makes every op callable over MCP (``g6_invoke``); none runs code, so none is sensitive.

The best state is ``qualified-draft``. A Z3 UNSAT is a solver decision on your encoding, reported with its evidence;
it is not a proof explained to and inspected by a person, so nothing here is ``verified``. The encoding is yours: if
it does not say what you mean, the decision does not either.

Not ported from the archive's ``formal_methods`` (audit 017), and why:

* every code-executing backend (SWI-Prolog, Lean, TLA+/TLC, Catlab, SageMath, ``run_code``). No backend name reaches
  one; a request for one is refused (FM-002, FM-010, FM-011, FM-012). Lean belongs to ``lean_prover`` (FM-001);
* ``z3_mcp``'s ``interpolate`` (Z3 removed interpolation in 4.8; the op silently fell back to a plain unsat check),
  its SQLite contexts and its search;
* triangulation, which gave ``verified`` from one LLM-translated Z3 leg (FM-003); its missing vacuity and
  self-assumption checks are fixed here;
* the ProB "crown-jewel" models, which never read the code they cite (FM-004);
* intent fidelity (FM-005..007, deferred), autoformalization and its unmetered LLM calls (FM-008, FM-019);
* the native router and its caller-declared confidence gate (FM-009); the other ``*_mcp`` sub-packages, engine router,
  pattern catalogue, proof index and library, skill plugin and its MCP tools (FM-011, FM-013, FM-015, FM-016, FM-017);
* the Python-syntax formula dialect, which typed every free name ``Int`` (FM-014), and the ``verify`` syntax check.
"""
from g6.formal import four_valued
from g6.formal.contract import ContractResult, check_contract
from g6.formal.envelope import BACKENDS, apply_envelope
from g6.formal.facade import solve, solver_support
from g6.formal.formal_block import FormalBlock, FormalInput
from g6.formal.proof_artifacts import recheck
from g6.formal.propositional import FormulaTooLarge, check_propositional, dpll_solve
from g6.formal.schema import (
    CoreResult,
    CountResult,
    FormalResult,
    FourValuedResult,
    LogicalQuery,
    OptimumResult,
    ProofArtifact,
    SolverCheck,
    TermResult,
    ValidityResult,
)
from g6.formal.z3_bridge import check_entailment, check_sat, check_validity, find_counterexample
from g6.formal.z3_depth import (
    check_equivalence,
    count_models,
    eliminate_quantifiers,
    optimise,
    simplify,
    unsat_core,
)

__g6_component__ = True
__g6_block__ = "FormalBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # no op runs code, writes a file or reaches the network
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("confident_reasoning_error", "vacuous_proof")
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",)}

__all__ = ["ContractResult", "check_contract", "BACKENDS", "CoreResult", "CountResult", "FormalBlock", "FormalInput", "FormalResult", "FormulaTooLarge",
           "FourValuedResult", "LogicalQuery", "OptimumResult", "ProofArtifact", "SolverCheck", "TermResult",
           "ValidityResult", "apply_envelope", "check_entailment", "check_equivalence", "check_propositional",
           "check_sat", "check_validity", "count_models", "dpll_solve", "eliminate_quantifiers", "find_counterexample",
           "four_valued", "optimise", "recheck", "simplify", "solve", "solver_support", "unsat_core"]
