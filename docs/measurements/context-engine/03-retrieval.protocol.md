# Context Engine, row 03: retrieval and fusion (protocol)

Frozen before the code it measures (design 12.8, row "Retrieval and fusion"). Slice 3 measures the lexical mechanism
against direct file search; slice 4 adds the fused row; slice 5 adds the real-cluster run. A change to a threshold, a
label file or a query set retires this protocol: the next version is a new file with a new cohort.

## What is measured

For a frozen set of queries over the synthetic corpus, per mechanism and fused:

- **recall at 5 and at 10** of the labelled eligible files;
- **unsupported citations**: returned items that hold none of the query's words, or (engine arms) are not marked
  intact by the re-read of the call that returned them;
- **returns for queries with no eligible file** (reported, not thresholded: lexical search returns what matches);
- **availability and partial failures**: each backend's state per query, kept through fusion;
- **byte-match accuracy**, reported separately: of the returned engine items, how many are byte-equal to the file's
  text at their offsets when read again by the measurement itself;
- **provenance**: every fused item keeps every contribution (backend, route, rank, scale, generation) and every
  conflict caution; a lost contribution or caution is a failure.

## Arms

1. `substring`: the baseline. A plain scan of the same files for the query's words, ranked by distinct words matched,
   then occurrences, then path. This stands for direct file search by a person or an agent with a text search tool.
2. `fts5`: `g6.knowledge._engine.search` on the local SQLite FTS5 index.
3. From slice 4: `elastic_managed` (a fake loopback server until the real-cluster run of slice 5) and `fused`.

Every arm sees exactly the same files: the walker's list for the corpus. A run in which the arms' file lists differ
is refused.

## Unit and denominator

The unit is one query. The denominator is every query of the label file; a query an arm fails on counts as zero
recall for that arm and is listed. Queries with no eligible file are excluded from recall and reported on their own.

## Labels

`tests/knowledge/labels/retrieval.v1.json`: 53 queries with their eligible files. These labels come from the corpus
generator's own ground truth (each subject word is written into exactly one file); they are not independently written.
The design asks for independently labelled eligible passages and normative-support labels: that label set is owed, and
until it exists a result on this file is a development-cohort result, not the admission result. The slice 3 brief was
to supply the labels; none was supplied in the night build of 2026-11-11.

## Versions and environment

Python 3.12; SQLite FTS5 from the standard library (`unicode61 remove_diacritics 2`); chunker `words:200:20`;
`tests/knowledge/corpus` as committed. The result file records the commit, the Python and SQLite versions, the
platform and the label file's sha256.

Elasticsearch (slices 4 and 5): the features the managed reader and writer use (`_bulk`, `_mget`, index aliases,
`term` and `terms` filters, `multi_match`, `value_count` and `terms` aggregations, API keys) are to be checked on each
supported Elasticsearch version before the real-cluster run, and the versions and the result of that check are
recorded here in the same change that runs it. That check needs the vendor's current documentation and a running
server; neither was available to the night build (no network, no JVM), so the supported versions are **not yet
recorded** and the real-cluster run is owed.

## Limits and costs

The corpus is synthetic and small (49 indexable files); the numbers say nothing about scale (row 05). Costs recorded:
wall-clock seconds per arm, zero model calls, builder and reviewer time.

## Thresholds

```json
{
  "row": "03-retrieval",
  "frozen": "2026-11-11",
  "unit": "query of tests/knowledge/labels/retrieval.v1.json",
  "thresholds": {
    "fts5_recall_at_10_minus_substring_recall_at_10_min": 0.0,
    "fts5_unsupported_citations": 0,
    "arms_see_different_files": 0,
    "fused_recall_at_10_minus_best_single_mechanism_min": 0.0,
    "fused_lost_contributions": 0,
    "fused_lost_conflict_cautions": 0,
    "fused_unsupported_citations": 0
  },
  "admission": "every threshold met on independently written labels; a result on generator-derived labels is a development-cohort result and admits nothing"
}
```
