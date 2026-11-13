# Context Engine, row 03: retrieval, lexical part (development-cohort result)

Protocol: `03-retrieval.protocol.md`. This is a result on the generator-derived labels of
`tests/knowledge/labels/retrieval.v1.json` (sha256 `bcb779007a7d8855eda291f82055d930fd9b8003d99455c3528aed02c9cbae51`).
The protocol says such a result admits nothing: the admission run needs independently written labels, which are owed.

Run: `python validation/context_engine/retrieval_baseline.py --labels tests/knowledge/labels/retrieval.v1.json`, on the
commit after `4986a13`, Python 3.12.11, SQLite 3.49.1, Windows 11, 2026-11-11. Wall clock for both arms: 3.8 seconds.
No model was called.

| Arm | Queries | With an eligible file | Recall at 5 | Recall at 10 | Unsupported citations | Returned for the 3 queries with no eligible file | Files seen |
|---|---|---|---|---|---|---|---|
| `substring` (direct file search) | 53 | 50 | 1.000 | 1.000 | 0 | 0 | 49 |
| `fts5` (the engine's lexical search) | 53 | 50 | 1.000 | 1.000 | 0 | 0 | 49 |

What this does and does not show:

- Both arms saw the same 49 files, and the engine returned no citation that lacks the query's words or was not
  intact at its re-read.
- The lexical thresholds of the protocol are met on these labels (the engine's recall at 10 is not below the
  baseline's; zero unsupported citations).
- The two arms tie at the ceiling. These labels are easy for any lexical search, because each query holds a word that
  occurs in one file only: the result does not show that the engine finds anything a text search does not, and it is
  not evidence for funding a later layer. A label set written independently of the corpus generator, with paraphrased
  and multi-passage queries, is needed before row 03 can be decided.
- Fused and Elasticsearch arms are not in this result (slices 4 and 5).
