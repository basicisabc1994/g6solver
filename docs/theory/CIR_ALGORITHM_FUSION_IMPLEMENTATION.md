# CIR × Algorithm Design: additive implementation

Top-down `g6.complexity` builds a frozen, read-only analysis of a supplied Python
file map. Bottom-up `g6.algorithm_design` searches a small technique catalog,
and `ontology_adapter` reads the author's supplied ontology metadata.
`fuse` joins candidate proof obligations with **all** normative preservation
checks, rather than guessing a relationship from keyword similarity.
`g6.harness.verification_controller` selects bounded experiments but delegates
all execution and evaluation to existing G6 custodians.

## Example

```python
from g6.complexity import FrozenIntent, Requirement, Obligation, build_atlas, impact_cone
from g6.algorithm_design import DesignProblem, search_algorithms, fuse

intent = FrozenIntent(
    (Requirement("R", "Preserve existing outputs", "stated", "person", "preserve"),),
    (Obligation("O", "R", "Regression suite", "person", True),),
)
cir = build_atlas({"app/__init__.py": "", "app/logic.py": "def find(xs, x): return x in xs"},
                  package="app", intent=intent, environment_digest="locked-env")
candidate = search_algorithms(DesignProblem(("sequence",), "membership")).candidates[0]
plan = fuse(cir, candidate, required_target="membership", impact=impact_cone(cir, ("app.logic",)))
print(plan.completion_state, plan.check_plan)
```

This is a **design/proposal mechanism**. It neither executes target code nor
claims an independent verifier ran. A user-supplied environment digest is not
proof of sandbox isolation. Only a host-observed checked receipt from the
existing executor can influence the existing decision gate. No automatic
merge/release/deploy authorization exists.

The uploaded Skiena index consists of 75 bibliographic section names, not 75
algorithm implementations. The uploaded ontology is metadata-only: recognize
applicability symbolically using `retrieve_ontology(...)`, then independently
check assumptions on the real instance. Structured catalog claims, including
`O(nW)`, retain their cost model and limitations; they do not establish a
polynomial algorithm in binary input length.

Limitations: AST calls and external effects are conservative/incomplete; no
full Python semantics, control dependence, sound may-alias analysis, or
semantic type-checker. Reverse dependency closure is relative to represented
edges only; unknown edges are explicitly surfaced. The scanner marks mutable
module globals as potential state, not as user-essential. Classification is
unknown until requirements and evidence justify it. The technique catalog is
small and typed only by representation names. A proposed composition still
requires pre/postcondition and cost proof. The controller schedules but does
not execute tests, mutate hypotheses from model claims, or alter G6 gates.

Run: `python -m pytest tests/complexity/test_cir_fusion.py`.
