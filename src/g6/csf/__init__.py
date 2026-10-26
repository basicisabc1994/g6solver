# SPDX-License-Identifier: Apache-2.0
"""g6.csf — compute safety bounds and checks on an agent model you supply: reachability, LTL properties, composition and rollback.

g6.csf — compute safety bounds and checks on an agent model you supply: reachability, LTL, composition, rollback
(audit 012 §11).

Capability label: ``implemented``.

    from g6.csf import BoundedAgent, ResourceTriple, reachability_bound
    agent = BoundedAgent(
        states=["plan", "act", "done", "harm"],
        transitions=[{"from": "plan", "to": "act", "probability": 1.0},
                     {"from": "act", "to": "done", "probability": 0.95},
                     {"from": "act", "to": "harm", "probability": 0.05},
                     {"from": "done", "to": "done", "probability": 1.0},
                     {"from": "harm", "to": "harm", "probability": 1.0}],
        initial_state="plan", unsafe_states=["harm"], resources=ResourceTriple(N=3))
    report = reachability_bound(agent, epsilon=0.2)
    report.vub, report.meets_bound, report.completion_state   # (0.1, True, "qualified-draft")
    # The true probability is 0.05: the union bound counts "harm" at steps 2 and 3.

    from g6.csf.smt import check_z3                              # needs z3-solver (pip install 'g6[formal]')
    check_z3(agent, "G(!harm)").counterexample                   # ("plan", "act", "harm", "harm")

What it is: ``BoundedAgent``, ``TransitionOutcome`` and ``ResourceTriple``, a typed finite Markov model whose
transitions pass through ``validate_transitions``, the one validator (unknown states, unreadable or out-of-range
probabilities, sums other than 1 and states with no outgoing transition are refused); ``reachability_bound``, the
union bound on P(reach an unsafe state within N steps) counting the initial state, with ``p_unsafe == vub``;
``parse_ltl``, ``evaluate_ltl`` and ``ltl_to_str`` for finite-trace LTL; ``compose_bound``, the Fréchet bound
``eps1 + eps2`` unless you assert independence; ``rollback_feasibility`` and ``classify_operation`` over one
operation vocabulary (``OPERATIONS``; an unknown operation is never assumed reversible); ``GitCheckpoint``, a snapshot
of tracked files and an opt-in ``git reset --hard`` inside one repository root; and, optional (it needs z3-solver),
``g6.csf.smt.check_z3``, which checks an LTL property on every path of the model with Z3 or refuses a
property it cannot encode.

A bound is a computed number on the model you supplied, never a measurement and never ``verified``: the best state is
``qualified-draft``, a bound above epsilon, an infeasible rollback or a violated property is ``blocked-escalated``, and
the model and its assumptions travel in the evidence. Nothing is persisted, and nothing reads or writes the home
directory.

Not ported from the archive (audit 012), and why:

* ``SafetyVerifier.worst_case``, which was not a bound (CSF-006), and ``SafetyQuery`` with the ``method`` and
  ``strategy`` fields that could switch a gate to it; the Wilson interval and PAC ``delta`` on an exact value (CSF-013);
* ``check_safety``, ``verify_bounds``, ``get_hazard_info`` and their hazard table: asserted constants summed into a
  "probability" (CSF-003, CSF-019);
* Theorem 1 composition, which overstated safety for disjoint or negatively correlated failures and passed an
  out-of-range epsilon, and its interface and resource checks, which never changed its verdict (CSF-010);
* the NuSMV bridge, called without an ``ltl_spec`` (CSF-009), the SMV generator's most-probable-successor
  abstraction and the QF_LRA re-encoding of the union bound that labelled an unread property (CSF-001). The Lean
  bridge and ``safety_game.py`` are quarantined, not ported (CSF-009, CSF-013);
* ``CSFBlock`` (``framework.py``), both 26-op MCP blocks, the ``skill/`` plugin with its warn-only hooks and MCP
  servers, ``capabilities.py``, ``patterns.py``, ``skills.py`` and ``agentic_runtime.py`` (CSF-012, CSF-014, CSF-015);
* the policy, circuit-breaker, event and incident ledgers, their ``~/.csf`` SQLite files and the LLM incident text
  (CSF-014). ``@csf_guarded`` belongs to ``align_csf``, not to this package (CSF-004, CSF-005).
"""
from g6.core.outside import OutsideProgram
from g6.csf.trace import fit_from_trace
from g6.csf.policy import EpsilonPolicy, tighten
from g6.csf.composition import compose_bound
from g6.csf.ltl import (
    LTLAlways,
    LTLAnd,
    LTLAtom,
    LTLEventually,
    LTLFormula,
    LTLNext,
    LTLNot,
    LTLOr,
    LTLRelease,
    LTLUntil,
    evaluate_ltl,
    ltl_to_str,
    parse_ltl,
)
from g6.csf.rollback import OPERATIONS, GitCheckpoint, RollbackFeasibility, classify_operation, rollback_feasibility
from g6.csf.schema import (
    BoundedAgent,
    CSFError,
    CSFInputError,
    ResourceTriple,
    TransitionOutcome,
    validate_transitions,
)
from g6.csf.verifier import SafetyReport, StepHazard, reachability_bound

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ("formal",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
# Not ``improvised_risky_behaviour`` (audit 2026-10-23): nothing here watches what an agent does in a known risky
# situation; the approved epsilon cap (``tighten``) only lowers a bound.
__g6_mitigates__: tuple[str, ...] = ("unanalysed_hazard", "model_reality_gap")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (OutsideProgram(
    name="git", purpose="GitCheckpoint snapshots a working tree and rolls it back with git",
    install="Install git from git-scm.com and put it on PATH.", licence="GPL-2.0", command="git"),)
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",)}

__all__ = ["OPERATIONS", "BoundedAgent", "CSFError", "CSFInputError", "GitCheckpoint", "LTLAlways", "LTLAnd", "LTLAtom",
           "LTLEventually", "LTLFormula", "LTLNext", "LTLNot", "LTLOr", "LTLRelease", "LTLUntil", "ResourceTriple",
           "RollbackFeasibility", "SafetyReport", "StepHazard", "TransitionOutcome", "classify_operation",
           "compose_bound", "evaluate_ltl", "ltl_to_str", "parse_ltl", "reachability_bound", "rollback_feasibility",
           "validate_transitions", "fit_from_trace", "EpsilonPolicy", "tighten"]
