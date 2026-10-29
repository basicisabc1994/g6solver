"""Bounded heuristic proposal and independent evaluation contracts."""
from dataclasses import dataclass, replace
import hashlib

OPERATORS=("regression","boundary","counterexample","metamorphic","decompose","prompt_revision")
@dataclass(frozen=True)
class Heuristic:
    id:str
    operator:str
    parent_id:str=""
    generation:int=0
    status:str="proposed"

@dataclass(frozen=True)
class Trial:
    heuristic_id:str
    case_id:str
    baseline_success:bool
    candidate_success:bool
    baseline_cost:int
    candidate_cost:int
    evaluator_digest:str

def mutate(parent, max_children=4, max_generation=3):
    if parent.operator not in OPERATORS or not 0<=max_children<=len(OPERATORS):
        raise ValueError("invalid mutation")
    if parent.generation>=max_generation:
        return ()
    return tuple(Heuristic(hashlib.sha256(f"{parent.id}:{op}".encode()).hexdigest()[:16],op,parent.id,parent.generation+1)
                 for op in OPERATORS if op!=parent.operator)[:max_children]

def assess(candidate, cases, evaluator, evaluator_digest):
    if not cases or len(set(cases))!=len(cases) or not evaluator_digest:
        raise ValueError("fixed cases and evaluator identity required")
    trials=tuple(evaluator(candidate,c) for c in cases)
    if any(t.heuristic_id!=candidate.id or t.case_id!=c or t.evaluator_digest!=evaluator_digest or min(t.baseline_cost,t.candidate_cost)<0 for t,c in zip(trials,cases)):
        raise ValueError("invalid receipt")
    regression=any(t.baseline_success and not t.candidate_success for t in trials)
    benefit=any((not t.baseline_success and t.candidate_success) or (t.candidate_success and t.candidate_cost<t.baseline_cost) for t in trials)
    return {"candidate":candidate,"trials":trials,"recommended":benefit and not regression,"status":"qualified-draft"}

def promote(assessment, *, approved=False, shadow_digest=""):
    if not approved or not shadow_digest or not assessment["recommended"]:
        raise PermissionError("external shadow verification and approval required")
    return replace(assessment["candidate"],status="promoted")
