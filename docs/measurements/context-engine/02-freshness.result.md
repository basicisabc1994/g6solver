# Context Engine, row 02: freshness (result)

Protocol: `02-freshness.protocol.md`. Run on the tree of the review 1 repair (the commit that adds this file),
2026-11-11, Python 3.12.11, SQLite 3.49.1, Windows 11. No model was called.

| Declared suite | Cases | Passed | Failed or errored |
|---|---|---|---|
| `tests/knowledge/test_engine_lifecycle.py` | 10 | 10 | 0 |
| `tests/core/test_mcp_knowledge.py` | 25 | 25 | 0 |
| `tests/knowledge/test_engine_refresh.py` | 11 | 11 | 0 |

Against the thresholds: uses of an expired version 0, of a revoked version 0, of a changed version 0; changed or
expired passages served by a failed refresh 0; cases failed or errored 0. Refresh of the 49-file synthetic corpus
with one changed file finished inside the 30-second limit in every run (the whole refresh suite, eleven cases with
their set-up, took about 20 seconds on this machine under load); the protocol's limit says "46-file corpus", and the
corpus as built has 49 indexable files.

What this result does not say:

- An independent review found two uses of an expired source that these suites did not catch (a refresh that re-read
  and re-indexed it, and a snapshot that was reconstructed). They were repaired at one root (one predicate, asked
  with the time by every consumer) and are covered by the review's cases and by an inventory over every shared
  module. As for row 01, the protocol's suites changed in effect, so this is a development result; the next version
  of the row declares the added cases and is run on cases not used for the repair.
- Downtime caused by a failed refresh is reported by the refresh reply, not bounded.
