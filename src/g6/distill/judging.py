# SPDX-License-Identifier: Apache-2.0
"""The code library acceptance is judged by: one list per digest, read by every gate that depends on it.

The acceptance gate pins JUDGING_PATHS; registration_gate.oracle_digest pins ORACLE_PINS (digest key to
file). The merge gate's host bridge protects DIGESTED_PATHS, their union, from an autonomous merge. No
gate keeps its own copy, so a digest and the protected set cannot drift apart.
"""

JUDGING_PATHS = ("src/g6/distill/acceptance_gate.py", "src/g6/distill/records.py", "src/g6/distill/ledger.py",
                 "src/g6/distill/library.py", "src/g6/distill/facts.py", "src/g6/distill/detectors.py",
                 "src/g6/safety_engine/weakening.py", "src/g6/distillation/sandbox.py", "src/g6/core/source_check.py",
                 "src/g6/core/sandbox.py", "src/g6/core/component_process.py", "src/g6/core/binomial.py",
                 "src/g6/reasoning/compare.py", "src/g6/metaharness/gate.py", "src/g6/metaharness/schema.py",
                 "src/g6/metaharness/evaluate.py")

#: oracle_digest's pins, keyed as its digest has always been keyed (module name, or a short name).
ORACLE_PINS = {
    "g6.distill.held_out.case_gate": "src/g6/distill/held_out/case_gate.py",
    "g6.distill.held_out.oracle": "src/g6/distill/held_out/oracle.py",
    "g6.workforce.mutation_check": "src/g6/workforce/mutation_check.py",
    "g6.workforce.repo_tests": "src/g6/workforce/repo_tests.py",
    "g6.core.confine": "src/g6/core/confine.py",
    "g6.core.ledger": "src/g6/core/ledger.py",
    "sealed_gate": "src/g6/distill/held_out/sealed_gate.py",
    "registration_gate": "src/g6/distill/held_out/registration_gate.py",
    "operators": "src/g6/distill/operators.py",
}

#: Every file any judging or oracle digest pins, and this list itself: what an autonomous merge may not change.
DIGESTED_PATHS = tuple(sorted({*JUDGING_PATHS, *ORACLE_PINS.values(), "src/g6/distill/judging.py"}))
