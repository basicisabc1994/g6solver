# Context Engine, row 07: the search planner (protocol)

Frozen before the code it measures (design 12.8, row "Planner"; plan slice 10). A change to a threshold, an arm or a
suite retires this protocol.

## What is measured

Whether choosing routes by fixed rules does anything for a person's query that the fixed fusion of the ready routes
does not, at the same information and the same budget, and whether it is ever less safe:

- **paired outcome**: for each labelled query, recall of the labelled eligible files at 10 in each arm, and what
  each arm spent (searches, model calls, tokens returned, seconds);
- **safety**: passages returned to a caller who may not read them, coverage reported for a need no returned passage
  holds, limits exceeded, a model's reply used as anything but typed search fields. The declared suites below count
  these; the count must be zero in every arm.

## Arms

Each arm reads the same published generation of the same files through the same access, with the same `top_k`, the
same limits and no model call. The harness refuses the comparison when the arms' information or budgets differ.

| arm | what runs | on the development corpus |
|---|---|---|
| `exact` | the lexical route alone | runs |
| `fixed_fusion` | every ready route, fused by passage id | runs |
| `planner` | `g6.knowledge._planner.run` | runs |
| `dense` | the meaning route alone | not ready there: `evidence_pending`, never counted as empty |
| `graph` | the relations route alone, on relation queries | no labelled relation queries yet: `evidence_pending` |

## Unit and denominator

One labelled query; every query stays in the denominator of every arm that ran. A query with no eligible file counts
in the no-answer measure (items returned anyway), not in recall.

## Labels

`tests/knowledge/labels/retrieval.v1.json` is the development cohort: its labels come from the corpus generator and
are not independently written. A result on it is a development result. The admission decision needs independently
written queries (including relation and multi-hop ones) over a corpus the rules were not developed on: owed.

## Declared suites (safety)

- `tests/knowledge/test_planner.py`
- `tests/validation/test_planner_pairs.py`
- the planned-search case of `tests/core/test_mcp_knowledge.py`

## Versions and environment

As row 01. The planner's rules are fixed code: nothing is learned in this row, and the decision log is not used as
training data.

## Costs recorded

Per arm: searches, model calls (zero), tokens returned and wall-clock seconds, each as a total over the queries.

## Thresholds

```json
{
  "row": "07-planner",
  "frozen": "2026-11-12",
  "unit": "labelled query, paired across arms",
  "thresholds": {
    "arms_with_unequal_information_or_budget": 0,
    "safety_cases_failed_or_errored": 0,
    "planner_recall_at_10_minus_fixed_fusion_recall_at_10_min": 0.0,
    "planner_searches_minus_fixed_fusion_searches_max": 0
  },
  "admission": "the planner is admitted as the default only when, on independently labelled queries, it improves a named outcome over fixed fusion or costs less at equal recall, with every safety threshold met; on the development cohort the result is recorded and admits nothing",
  "pending": "evidence_pending: independent labels; the dense and graph arms; any arm with a model call"
}
```
