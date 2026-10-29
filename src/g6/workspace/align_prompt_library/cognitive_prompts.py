# SPDX-License-Identifier: Apache-2.0
"""Pull request #4's cognitive-bootstrap prompts, as entries of the one prompt catalogue (``catalogue``).

The fourteen meta-prompts of the cognitive-bootstrap proof of concept, with their ``$x`` placeholders written as the
library's ``{{x}}`` so they render through its strict one-pass fill. Each is tied to a work mode and a pipeline step;
none has the step ``gate``, because a model's output never decides a gate. A prompt here proposes; it never alters a
frozen goal, a check, a security rule, an evaluator or what counts as evidence.
"""
from __future__ import annotations

from g6.workspace.align_prompt_library.catalogue import CatalogueEntry

SOURCE = "cognitive bootstrap pull request #4"

#: (stage, mode, step, purpose, template), in pull request #4's order.
_PROMPTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("constraints", "premortem", "propose", "Identify measurable constraints and separate stated from inferred ones",
     "Identify specific, measurable and achievable constraints for this objective. Distinguish user-provided "
     "requirements from inferred assumptions. Return JSON array. Objective: {{objective}}; context: {{context}}"),
    ("first_principles", "premortem", "propose", "Break the objective into first principles and falsifiable claims",
     "Decompose the objective into domain first principles, including known limits and falsifiable claims. Return "
     "JSON array. Objective: {{objective}}; constraints: {{constraints}}"),
    ("heuristics", "premortem", "propose", "Name problem-solving heuristics and how each can fail",
     "Identify useful domain-specific problem-solving heuristics and their failure modes; return JSON array. "
     "Objective: {{objective}}; constraints: {{constraints}}"),
    ("self_assessment", "premortem", "propose", "Assess uncertainty, dependencies, resources and failure modes first",
     "Assess uncertainty, dependencies, computational resources, and likely failure modes before execution. "
     "Objective: {{objective}}; constraints: {{constraints}}"),
    ("decompose", "premortem", "propose", "Build a bounded tree of testable subgoals",
     "Construct a bounded goal tree of testable subgoals, prerequisites, and completion criteria. Objective: "
     "{{objective}}; constraints: {{constraints}}"),
    ("search_query", "premortem", "test", "Write an evidence query that could refute the plan",
     "Generate a focused evidence-retrieval query for this subgoal, indicating information that could refute the "
     "plan. Subgoal: {{subgoal}}"),
    ("execute", "mission", "propose", "Propose one bounded step with its assumptions and gaps",
     "Propose one bounded step, showing assumptions, evidence gaps, and intermediate results. Subgoal: {{subgoal}}; "
     "constraints: {{constraints}}"),
    ("synthesize", "mission", "propose", "Combine results without upgrading uncertain claims",
     "Synthesize candidate results and evidence without upgrading uncertain claims to verified. Objective: "
     "{{objective}}; observations: {{observations}}"),
    ("critique", "mission", "test", "Critique a candidate against the frozen constraints",
     "Adversarially critique the candidate against the frozen constraints and identify counterexamples. Candidate: "
     "{{candidate}}; constraints: {{constraints}}"),
    ("revise", "mission", "propose", "Revise a candidate for a critique, keeping frozen requirements",
     "Revise the candidate addressing the critique without changing frozen requirements. Candidate: {{candidate}}; "
     "critique: {{critique}}"),
    ("evaluate", "mission", "test", "Evaluate a candidate, keeping model judgement apart from test evidence",
     "Evaluate the candidate against supplied criteria; distinguish model judgment from independent test evidence. "
     "Candidate: {{candidate}}; constraints: {{constraints}}"),
    ("theory_of_mind", "premortem", "propose", "Infer what the person wants, conservatively",
     "Infer possible user intent and uncertainty conservatively; do not invent private beliefs or override explicit "
     "user requirements. User input: {{objective}}"),
    ("user_facing", "mission", "approve", "Tell the person the result and what is still uncertain",
     "Communicate results and remaining uncertainty faithfully. Candidate: {{candidate}}; observations: "
     "{{observations}}"),
    ("prompt_reflection", "mission", "propose", "Propose a revision of a prompt for demonstrated failures",
     "Propose a versioned revision of a prompt to fix demonstrated failures. Keep immutable constraints and "
     "evaluation rubrics unchanged. Return the revised prompt alone. Prompt: {{candidate}}; failures: {{critique}}"),
)

ENTRIES: tuple[CatalogueEntry, ...] = tuple(
    CatalogueEntry("cb-" + stage.replace("_", "-"), stage, mode, step, purpose, SOURCE, template)
    for stage, mode, step, purpose, template in _PROMPTS)

__all__ = ["ENTRIES", "SOURCE"]
