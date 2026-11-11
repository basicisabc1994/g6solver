# Context Engine, row 05: scale and persistent meaning (protocol for the spike)

Frozen before any run (design 12.3 "Persistent `ctx_colbert`", 12.8 row "Scale and persistent meaning", challenge 13;
plan slice 6). The spike is a measurement: it adds no product code, and it decides which route slice 7 builds on. No
candidate may be tuned on the cohort reported here; a change to the envelope, the corpus or a candidate after a run
retires this protocol, and the rerun is on a new cohort.

**State on 2026-11-11: not run.** The harness (`validation/context_engine/colbert_spike/`) and this protocol are
built. The runs need model weights already cached on the machine and several GiB of memory to themselves; the owner
has said such runs happen on this machine, on their own, with no builder, outside run or review beside them. Row 05 is
`evidence_pending` until then, and nothing below is a measured number.

## Workload

- Corpus: synthetic passages of 200 words, generated from a fixed vocabulary with a fixed seed
  (`colbert_spike/candidates.py: corpus`), at three sizes: 2,000, 20,000 and 100,000 passages.
- Queries: 200 per size, each built from a passage's own rare words plus distractors, fixed seed; the eligible
  passage is the one it was built from.
- Operations measured per candidate and size: ingest of the whole corpus; an update of 1 percent of the passages; a
  delete of 1 percent; a restart (process exit, then first query); a kill during ingest followed by a restart.

## Candidates

| Id | Candidate |
|---|---|
| a | PyLate's own index |
| b | a PLAID build |
| c | FTS5 candidates (at most 2,000) reranked by ColBERT, token vectors stored per passage id |
| d | FTS5 with cached sentence embeddings (the non-ColBERT baseline) |

Exhaustive MaxSim over the 2,000-passage set is the recall reference. The archive's rebuild-on-change wrapper is a
negative baseline and is not a candidate.

## Pins

Recorded in each run's result and required to be equal across the runs compared: model name and revision, tokenizer
sha256, config sha256, weights sha256, embedding dimension, maximum tokens (truncation), precision (float16 or
float32), chunker (`words:200:20`), index schema version, library versions (pylate, sentence-transformers, torch),
Python, operating system and CPU model. Weights load local-only: a run that would download anything is refused and
reported `unavailable`, never as zero. The licences of the model weights and the tokenizer are recorded in the result
before any number is published.

## Envelope (the supported configuration)

CPU only. Peak resident memory at most 4 GiB. Disk at most 2 GiB per 100,000 passages. Warm p95 query latency under
2 seconds. Recall at 10 at least 0.9 of exhaustive MaxSim on the 2,000 set. Native Windows, WSL and Linux are run and
reported separately; a WSL result is never reported as a Windows result.

## Measures

Per candidate, size and platform: cold and warm query latency p50 and p95; peak resident memory; index bytes on disk;
recall at 10 against the reference; ingest, update and delete throughput (passages per second); whether a restart
re-encodes anything; whether a kill during ingest leaves only whole passages; wall-clock and CPU seconds.

## Unit and denominator

The unit is one cell: candidate x size x platform x measure. Every cell of the declared grid stays in the denominator.
A cell that did not run, ran partially or failed is reported as such and fails the envelope for its candidate on that
platform; it is never left out and never filled in.

## Decision

`report.py` prints the table and one decision line: the cheapest candidate (by peak memory, then disk, then warm p95)
that is inside the whole envelope on every supported platform, or `none: repair and rerun`. Persistence stays required
for 0.3.0 either way: neither an in-memory fallback nor a spike with no winner completes the layer.

## Thresholds

```json
{
  "row": "05-scale-and-meaning",
  "frozen": "2026-11-11",
  "unit": "cell: candidate x size x platform x measure",
  "sizes": [2000, 20000, 100000],
  "candidates": ["a", "b", "c", "d"],
  "platforms": ["windows", "wsl", "linux"],
  "thresholds": {
    "peak_rss_bytes_max": 4294967296,
    "disk_bytes_per_100000_passages_max": 2147483648,
    "warm_p95_seconds_max": 2.0,
    "recall_at_10_vs_exhaustive_maxsim_min": 0.9,
    "passages_re_encoded_after_restart": 0,
    "partial_passages_after_kill": 0,
    "cells_missing_or_failed": 0
  },
  "state": "evidence_pending: not run",
  "admission": "the decision line of report.py on a complete grid; outside-envelope results are diagnostics"
}
```
