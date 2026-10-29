# SPDX-License-Identifier: Apache-2.0
"""g6.algorithm_design — suggests catalogued algorithms for a stated problem, estimates their cost, and runs a fixed set of algorithm kernels that check each answer.

Bottom-up, typed, bounded algorithm-design search, and top-down fusion with the change-impact map (``g6.complexity``).
A problem is stated by its representations, the output it wants and the properties the caller has established;
``search_algorithms`` composes catalogue patterns whose input and output representations line up, and refuses any
pattern whose preconditions those properties do not cover. ``fuse`` binds one candidate to a tree's frozen intent and
cone, so one check plan names the algorithm's proof obligations and the caller's normative obligations. The bundled
ontology and problem index are textbook-derived metadata, cited by book and section, never run.

    from g6.algorithm_design import DesignProblem, search_algorithms
    found = search_algorithms(DesignProblem(("sequence",), "membership"))
    found.candidates[0].steps            # ("linear_scan",): a proposal, never a proof

``resources.estimate`` and ``rank_options`` cost a candidate's steps for stated input sizes on a stated device, from
hard-coded models (an algorithm with none is refused, never guessed). ``invocation.invoke`` is the one path to G6's 22
fixed kernels: plain data in, an answer out with its witness and how that one answer was checked. ``autonomous`` holds
the design mission's Python API (a bounded propose, test and repair loop over one file).

Over MCP, ``g6_invoke("algorithm_design", "primitives" | "ontology" | "search" | "estimate" | "fuse" |
"list_kernels" | "run_kernel", ...)`` answers the same; nothing it returns approves or runs a change.
"""
from g6.algorithm_design.block import AlgorithmDesignBlock, AlgorithmDesignInput
from g6.algorithm_design.catalog import primitives
from g6.algorithm_design.fusion import FusionResult, fuse
from g6.algorithm_design.ir import AlgorithmPrimitive, DesignCandidate, DesignProblem, DesignSearch
from g6.algorithm_design.search import search_algorithms

__g6_component__ = True
__g6_block__ = "AlgorithmDesignBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("coding_agent_gate", "complexity", "harness", "operator", "workforce")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (composition by
# representation names alone leaves the meaning of each step's output an unchecked assumption).
__g6_mitigates__: tuple[str, ...] = ("reinvented_method", "unproven_optimum")
__g6_introduces__: tuple[str, ...] = ("hidden_assumption",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["AlgorithmDesignBlock", "AlgorithmDesignInput", "AlgorithmPrimitive", "DesignCandidate", "DesignProblem",
           "DesignSearch", "FusionResult", "fuse", "primitives", "search_algorithms"]
