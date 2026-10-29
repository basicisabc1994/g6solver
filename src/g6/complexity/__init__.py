# SPDX-License-Identifier: Apache-2.0
"""g6.complexity — maps a Python package's modules, state and effects from its source, without running it, and lists what a change can reach.

A read-only Complexity Intelligence Representation (CIR), v0.1: an advisory structural reading, not an evaluator,
executor or approval service. ``build_atlas`` parses a file map with ``ast`` (it never imports or runs the code) into
the package's modules and functions, the imports and calls between them, the module-level and instance state they
share, and the reads, writes and input or output they may perform; every call, import or effect it cannot resolve is
listed under ``unknowns``, never assumed absent. ``impact_cone`` walks that map backwards from what a change touches,
and keeps every normative obligation of the caller's frozen intent; ``propose_simplifications`` proposes a narrower
owner for state the caller marked avoidable, with the checks a change must pass.

    from g6.complexity import FrozenIntent, Obligation, Requirement, build_atlas, impact_cone
    intent = FrozenIntent((Requirement("r1", "Preserve authorization", "person_confirmed", "person", "preserve"),),
                          (Obligation("o1", "r1", "No cross-account revocation", "person", True),))
    cir = build_atlas(files, package="shop", intent=intent, environment_digest="env-v1")
    impact_cone(cir, ("shop.state",)).impacted          # every component the change can reach

Over MCP, ``g6_invoke("complexity", "atlas" | "impact" | "simplify", ...)`` answers the same, as a ``qualified-draft``
bound to the tree's fingerprint; a requirement's authority is the caller's word and never raises a state.
"""
from g6.complexity.atlas import build_atlas
from g6.complexity.block import ComplexityBlock, ComplexityInput
from g6.complexity.impact import impact_cone
from g6.complexity.ir import (
    CIR,
    ComplexityFinding,
    Dependency,
    Effect,
    FrozenIntent,
    Hypothesis,
    Obligation,
    Requirement,
    Snapshot,
    StateVariable,
)
from g6.complexity.simplify import propose_simplifications

__g6_component__ = True
__g6_block__ = "ComplexityBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("design_governance",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a short cone
# read as reassurance, though it is a static reading of the text sent).
__g6_mitigates__: tuple[str, ...] = ("outside_scope", "unmanaged_complexity")
__g6_introduces__: tuple[str, ...] = ("false_confidence",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = [
    "CIR", "ComplexityBlock", "ComplexityFinding", "ComplexityInput", "Dependency", "Effect", "FrozenIntent",
    "Hypothesis", "Obligation", "Requirement", "Snapshot", "StateVariable", "build_atlas", "impact_cone",
    "propose_simplifications",
]
