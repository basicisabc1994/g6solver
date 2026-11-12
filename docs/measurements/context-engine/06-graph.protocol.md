# Context Engine, row 06: the passage-linked graph (protocol)

Frozen before the code it measures (design 12.8, row "Relations"; plan slices 8 and 9). A change to a threshold or a
suite retires this protocol.

## What is measured

Whether a relation query returns only what a passage witnesses, to a caller who may read that passage, and whether a
model's proposed relation ever counts as evidence:

- **unwitnessed edges**: an edge whose witness offsets do not lie in its passage, or whose witness no longer reads
  the same when the file is read again;
- **paths through a source the caller may not use**: a returned path with any edge, start, end or intermediate node
  in a revoked, expired, ungranted or deleted source;
- **imports stated as behaviour**: an import edge shown without the words that it is a possible dependency;
- **silent truncation**: a hop or path limit that returns part of the answer without saying so;
- **leads admitted as evidence** (slice 9): a model-proposed edge that adds fusion weight, enters a context pack as
  an evidence line, or supports a behaviour-question candidate;
- **extraction sent without a grant** (slice 9): source text sent to a model the source is not shared with;
- **direction and multi-hop usefulness**: on independently labelled queries, how often the containment and import
  baseline, and then the model relations, lead to the labelled passage at fixed resource limits.

## Two halves

1. **Baseline: containment and static imports (slice 8), and the safety of model leads (slice 9).** The declared
   suites below. No model and no person is needed: slice 9's suites use stand-in callers that record what they were
   sent.
2. **Usefulness on independently labelled queries.** Direction and multi-hop queries written by someone who has not
   seen the extractor, over a corpus the extractor was not developed on; the baseline arm and the model-relations arm
   run at the same limits (`max_hops`, `max_paths`, model calls, seconds). Owed: it needs the labels and, for the
   second arm, a model run. Recorded as `evidence_pending`.

## Unit and denominator

First half: one test case of the declared suites; every collected case stays in the denominator. Second half: one
labelled query; a query with no labelled passage stays in the denominator of the no-answer measure.

## Declared suites (first half)

- `tests/knowledge/test_graph.py`
- `tests/knowledge/test_relations.py`
- the path cases of `tests/core/test_mcp_knowledge.py`

## Versions and environment

As row 01. The extractor's version is recorded on every edge (`g6.knowledge._graph/1`); imports are read from the
syntax tree and nothing is executed, so an import built at run time is counted as unknown and is never a path.

## Costs recorded

Wall-clock seconds per suite; edges built, files not read and dynamic imports counted as unknown per build; zero
model calls in the first half; the second half records model calls, tokens and seconds per arm.

## Thresholds

```json
{
  "row": "06-graph",
  "frozen": "2026-11-12",
  "unit": "test case of the declared suites (first half); labelled query (second half)",
  "thresholds": {
    "unwitnessed_edges": 0,
    "paths_through_a_source_the_caller_may_not_use": 0,
    "imports_stated_as_behaviour": 0,
    "silent_truncations": 0,
    "leads_admitted_as_evidence": 0,
    "extractions_sent_without_a_grant": 0,
    "cases_failed_or_errored": 0
  },
  "second_half": "evidence_pending: independently labelled direction and multi-hop queries; both arms at equal limits",
  "admission": "every threshold met on the first half admits returning witnessed containment and import paths, and keeping model relations as leads; nothing about usefulness is claimed until the second half is run"
}
```
