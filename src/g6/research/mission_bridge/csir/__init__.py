# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The CSIR kernel of the Runtime Validator Constructor (2026-10-14). Research code, default OFF (``G6_MISSION_BRIDGE=1``).

It executes the parts of CSIR (``docs/theory/G6_Computational_Structural_Intermediate_Representation.md``) a mission
needs in practice:

- ``judgement``: the typing judgement ``Gamma; S; Pi; B |- p : A ~> B ! eps |> C_p`` over a ``MissionIR``, with every validator
  finding filed under the obligation A1-A6 it breaks. Serial soundness (C1) is a conditional theorem, true only when all six
  hold, so a mission is ``sound`` only when none is broken.
- ``standard``: refinement ``S' >= S`` (a standard is re-frozen only by adding predicates, never silently weakened).
- ``certificate``: the Belnap evidence state per predicate, the A4 grounding rule, and the three certificates (execution
  receipt, evidence statement, formal certificate) that cannot be laundered into one another.
- ``solvability``: an advisory belief on three separate axes (existence, findability, optimality) that never gates an attempt.

Where the theory leaves a gap, the definition used here is stated in the module that closes it.
"""
