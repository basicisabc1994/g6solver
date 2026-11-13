# Algorithm-design knowledge bundle (v0.1)

This PR now **includes** the supplied G6 Algorithm Design Engine v0.1 ontology and Skiena problem index.
They were missing in the initial CIR/algorithm-design proposal, which had accepted only caller-supplied metadata.

## Contents

- `src/g6/algorithm_design/data/ontology.json`: 16 problem models, 22 algorithm metadata records, design patterns, 18 agent operators and workflow specifications.
- `src/g6/algorithm_design/data/skiena_problem_index.json`: 75 catalog section entries; bibliographic metadata, **not** 75 verified implementations.
- `knowledge.py`: bundled loader and conservative proposal-only matching through the existing `ontology_adapter`.
- `tests/algorithm_design/test_knowledge.py`: content counts, provenance, prerequisite enforcement, and fresh mapping reads.

## Use

```python
from g6.algorithm_design.knowledge import bundled_ontology, bundled_problem_index, search_bundled_ontology

print(len(bundled_problem_index()["problems"]))  # 75 indexed sections, each described in G6's own words
candidates = search_bundled_ontology(problem_id="shortest_paths", established_features=())
for candidate in candidates:
    print(candidate.algorithm_id, candidate.status, candidate.missing_features)
```

The ontology captures textbook-derived design practices and LLM-era agentic strategies as **untrusted declarative metadata**. It does not invoke model providers, execute code, prove a bound, or turn a proposal into verified output. The PR #3 agent currently searches its small internal primitive list; it does **not yet consume the 22-entry bundled algorithm catalog automatically**. Future integration must preserve host-owned gating and independent evaluation.

The supplied package's independent selector/CEGIS prototype and full programmatic IR were not transplanted here: this PR intentionally retains existing G6's typed search and evaluation interfaces. Follow-up integration should unify the schemata before enabling any autonomous control or execution.

Run: `python -m pytest tests/algorithm_design/test_knowledge.py tests/complexity/test_cir_fusion.py`.
Validation is pending in the repository CI environment.
