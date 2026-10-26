# SPDX-License-Identifier: Apache-2.0
"""First-party seed hints, with no transforms, assigned confidence or imported texts."""
import json

from g6.learning.schema import Theory
from g6.learning.theory_store import TheoryStore

# First-party actions from the archive's Python seed list, expressed as advice only.
_SEEDS = (
    ("decompose_problem", (), "Split the problem into smaller independent sub-problems."),
    ("exploit_symmetry", ("symmetric", "symmetric_structure"), "Check whether symmetry can reduce the search space."),
    ("analogical_transfer", ("domain", "analogy"), "Compare a solved analogous problem before adapting its approach."),
    ("greedy_improve", ("solution", "objective"), "Try the best single-step improvement to the current solution."),
    ("constraint_propagation", ("constraints",), "Propagate stated constraints before searching."),
    ("domain_detector", (), "Identify the problem domain from the goal."),
    ("math_structure_parser", (), "Separate mathematical givens, unknowns and constraints."),
    ("reasoning_type_identifier", (), "Identify whether the proposed reasoning is deductive, inductive, analogical or causal."),
    ("code_structure_parser", (), "Identify the language, function signature and test cases before coding."),
    ("code_strategy_identifier", (), "Identify whether the task asks for implementation, debugging, refactoring or optimisation."),
    ("tool_context_parser", (), "Identify target systems, operations and preconditions before using tools."),
    ("tool_chain_identifier", (), "Order dependent tool operations before starting them."),
    ("math_approach_suggester", (), "Choose a mathematical approach appropriate to the reasoning required."),
    ("math_verifier", (), "Check the mathematical result against the givens and constraints."),
    ("code_approach_suggester", (), "Choose an implementation approach appropriate to the stated code task."),
    ("code_test_suggester", (), "Choose tests that could fail if the implementation were wrong."),
)


def load_seed_heuristics(store: TheoryStore) -> list[Theory]:
    """Explicitly add 16 unmeasured candidates to the supplied existing store.

    Condition keys match presence of any named metadata key, not the truth of its
    value. Empty keys apply universally. Ids are ``seed:<scope>:<name>`` (theory ids
    are unique across a database), so each scope of one database loads its own set.
    Loading again into the same scope refuses duplicate ids; it never resets a
    learned, active or retired theory. Credit and activation remain entirely with
    TheoryStore and promote.
    """
    theories = [Theory(theory_id=f"seed:{store.scope}:{name}", failure_code="unranked_heuristics", instruction=text,
                       trigger="condition_keys:" + json.dumps(keys),
                       rationale="First-party seed; unmeasured until paired evaluation.")
                for name, keys, text in _SEEDS]
    return store.add_candidates(theories)
