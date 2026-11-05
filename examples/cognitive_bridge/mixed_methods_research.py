"""Demonstrate graph + causal + formal planning against the real installed G6.

All facts/models are synthetic. Planning makes NO backend calls; a caller
may inspect the plan and explicitly approve local execution.
"""
import json

from g6.cognitive_bridge import ClaimIR, MethodRouter, MixedMethodExecutor

claim = ClaimIR(
    id="checking-mechanism",
    statement="Independent checking is related to fewer failures (illustrative only)",
    evidence_needs=("graph", "causal", "formal"),
    models={
        "graph": {
            "program": "depends_on(agent, checks). evidence(checks).",
            "atom": "depends_on(agent, checks)",
        },
        "causal": {
            "graph": "digraph { Complexity -> Checking; Complexity -> Failures; Checking -> Failures }",
            "treatment": "Checking", "outcome": "Failures", "engine": "python",
        },
        "formal": {
            "premises": ["(=> A B)", "A"], "formula": "B",
            "sorts": {"A": "Bool", "B": "Bool"},
        },
    },
)

plan = MethodRouter().plan([claim])
print("Candidate plan (not a result):")
print(json.dumps(plan.to_dict(), indent=2))

# The operator must explicitly decide whether the approved, read-only G6
# backend operations should run; no proposal calls any external tools.
APPROVE_EXECUTION = False
if APPROVE_EXECUTION:
    from g6.core.registry import get_registry
    report = MixedMethodExecutor(get_registry()).execute(plan, approved=True)
    print("Evidence-backed results (still at most qualified-draft):")
    print(json.dumps(report, indent=2))
