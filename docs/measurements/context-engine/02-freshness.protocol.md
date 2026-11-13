# Context Engine, row 02: freshness (protocol)

Frozen before the code it measures (design 12.8, row "Freshness"; plan slice 1 for the use side, slice 2 for refresh).
A change to a threshold or a suite below retires this protocol: the next version is a new file with a new cohort.

## What is measured

Whether a passage of an expired, revoked or changed source version is ever used after the point at which it was denied:

- **expiry**: a registration whose `expires_at` is at or before the clock at the moment of use;
- **revocation**: a registration revoked before the reply is built, in this process or another;
- **change**: a file whose bytes differ from the bytes indexed, observed at the re-read every returned passage gets.

A use is a passage appearing in a search reply, a fetch reply or (from slice 2) a snapshot, context or other stored
consumer.

## Fixed inputs

Source versions are the bytes of the synthetic corpus at commit and the edits each case makes. The clock is injected
(`clock=` on the engine functions); no case sleeps to reach an expiry. The refresh commit point (slice 2) is the
publication of the new generation in the registry, one transaction.

## Unit and denominator

One test case of the declared suites; every collected case stays in the denominator, and errors, skips and cases not
run are reported apart.

## Declared suites

- `tests/knowledge/test_engine_lifecycle.py` (use side: expiry, revocation, change, same bytes rewritten, crash between
  stage and publish, two processes)
- `tests/core/test_mcp_knowledge.py` (the cases that revoke or change between calls)
- from slice 2: `tests/knowledge/test_engine_refresh.py`

## Baseline

None: the admission rule is an absolute count. The comparison with direct file search is row 03.

## Versions and environment

As row 01. The result file records the commit, the Python and SQLite versions and the platform.

## Limits

Refresh readiness delay is measured on the 46-file synthetic corpus on the machine that runs the suite, and is a
statement about that corpus and machine only. A failed refresh reports the content as unavailable; the downtime it
causes is reported, not bounded, by this row.

## Costs recorded

Wall-clock seconds per suite and per refresh, builder and reviewer time, zero model calls.

## Thresholds

```json
{
  "row": "02-freshness",
  "frozen": "2026-11-11",
  "unit": "test case of the declared suites",
  "thresholds": {
    "uses_of_an_expired_version": 0,
    "uses_of_a_revoked_version": 0,
    "uses_of_a_changed_version": 0,
    "cases_failed_or_errored": 0,
    "refresh_seconds_46_file_corpus_max": 30,
    "changed_or_expired_passages_served_by_a_failed_refresh": 0
  },
  "admission": "every threshold met on the measured commit; the two refresh thresholds apply from slice 2"
}
```
