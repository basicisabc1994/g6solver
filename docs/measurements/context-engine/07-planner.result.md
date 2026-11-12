# Context Engine, row 07: the search planner (development-cohort result)

Protocol: `07-planner.protocol.md`. This is a result on the generator-derived labels of
`tests/knowledge/labels/retrieval.v1.json` (sha256 `bcb779007a7d8855eda291f82055d930fd9b8003d99455c3528aed02c9cbae51`).
The protocol says such a result admits nothing: the admission run needs independently written labels, which are owed.

Run: `python validation/context_engine/planner_pairs.py --labels tests/knowledge/labels/retrieval.v1.json`, on the
commit that adds this file, Python 3.12.11, SQLite 3.49.1, Windows 11, 2026-11-12. No model was called.

| Arm | Queries | Recall at 10 | Returned for the 3 queries with no eligible file | Not intact | Searches | Model calls | Tokens returned | Seconds |
|---|---|---|---|---|---|---|---|---|
| `exact` (the lexical route alone) | 53 | 1.000 | 0 | 0 | 53 | 0 | 178116 | 2.8 |
| `fixed_fusion` (every ready route) | 53 | 1.000 | 0 | 0 | 53 | 0 | 178116 | 2.9 |
| `planner` | 53 | 1.000 | 0 | 0 | 53 | 0 | 178116 | 2.9 |
| `dense` (the meaning route alone) | | evidence_pending: the route is not ready on this corpus | | | | | | |
| `graph` (the relations route alone) | | evidence_pending: the labels hold no relation queries | | | | | | |

Every arm that ran read generation 1 of the same 49 files of one source, as the local caller, with the same budget
(10 results, 6 searches, 0 model calls, 8000 tokens per query); the harness refuses the comparison otherwise.

What this does and does not show:

- On this corpus only the lexical route is ready, so the three arms ask the same single route and return the same
  passages. The planner chose a different rule for different queries, and the rule changed nothing it could ask.
- The protocol's two numeric thresholds are met trivially (the planner's recall at 10 is not below fixed fusion's and
  it spent no more searches). No named outcome improved and no cost fell: the admission rule is not met, and would not
  be on a development cohort in any case.
- Nothing here says the planner helps. That needs at least two ready routes, relation and multi-hop queries, and
  labels written independently of the corpus generator.