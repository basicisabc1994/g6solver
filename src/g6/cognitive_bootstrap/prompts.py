"""Provider-neutral meta-prompts adapted from the cognitive-bootstrap proof of concept.

Templates generate candidate *proposals*. None may alter frozen goals, checks,
security policy, evaluator or evidence requirements. Prompt changes are versioned
and separately tested rather than being silently adopted.
"""
from __future__ import annotations
from string import Template
from dataclasses import dataclass
import hashlib

POC_META_PROMPTS: dict[str, str] = {
 "constraints": "Identify specific, measurable and achievable constraints for this objective. Distinguish user-provided requirements from inferred assumptions. Return JSON array. Objective: $objective; context: $context",
 "first_principles": "Decompose the objective into domain first principles, including known limits and falsifiable claims. Return JSON array. Objective: $objective; constraints: $constraints",
 "heuristics": "Identify useful domain-specific problem-solving heuristics and their failure modes; return JSON array. Objective: $objective; constraints: $constraints",
 "self_assessment": "Assess uncertainty, dependencies, computational resources, and likely failure modes before execution. Objective: $objective; constraints: $constraints",
 "decompose": "Construct a bounded goal tree of testable subgoals, prerequisites, and completion criteria. Objective: $objective; constraints: $constraints",
 "search_query": "Generate a focused evidence-retrieval query for this subgoal, indicating information that could refute the plan. Subgoal: $subgoal",
 "execute": "Propose one bounded step, showing assumptions, evidence gaps, and intermediate results. Subgoal: $subgoal; constraints: $constraints",
 "synthesize": "Synthesize candidate results and evidence without upgrading uncertain claims to verified. Objective: $objective; observations: $observations",
 "critique": "Adversarially critique the candidate against the frozen constraints and identify counterexamples. Candidate: $candidate; constraints: $constraints",
 "revise": "Revise the candidate addressing the critique without changing frozen requirements. Candidate: $candidate; critique: $critique",
 "evaluate": "Evaluate the candidate against supplied criteria; distinguish model judgment from independent test evidence. Candidate: $candidate; constraints: $constraints",
 "theory_of_mind": "Infer possible user intent and uncertainty conservatively; do not invent private beliefs or override explicit user requirements. User input: $objective",
 "user_facing": "Communicate results and remaining uncertainty faithfully. Candidate: $candidate; observations: $observations",
 "prompt_reflection": "Propose a versioned revision of a prompt to fix demonstrated failures. Keep immutable constraints and evaluation rubrics unchanged. Return the revised prompt alone. Prompt: $candidate; failures: $critique",
}

def render_prompt(stage: str, **values: object) -> str:
    if stage not in POC_META_PROMPTS:
        raise KeyError(f"unknown bootstrap stage: {stage}")
    return Template(POC_META_PROMPTS[stage]).safe_substitute({k: str(v) for k,v in values.items()})

@dataclass(frozen=True)
class PromptRevision:
    stage: str
    prior_digest: str
    proposed_text: str
    frozen_evaluator_digest: str
    status: str = "proposed"

def propose_prompt_revision(stage: str, prior: str, failures: str,
                            proposer, *, frozen_evaluator_digest: str) -> PromptRevision:
    if stage not in POC_META_PROMPTS or not frozen_evaluator_digest:
        raise ValueError("known stage and frozen evaluator digest required")
    if not prior.strip() or not failures.strip():
        raise ValueError("revision must address recorded failures")
    proposal = proposer(render_prompt("prompt_reflection", candidate=prior, critique=failures))
    if not isinstance(proposal, str) or not proposal.strip() or len(proposal) > 32000:
        raise ValueError("invalid prompt revision")
    return PromptRevision(stage, hashlib.sha256(prior.encode()).hexdigest(),
                          proposal, frozen_evaluator_digest)
