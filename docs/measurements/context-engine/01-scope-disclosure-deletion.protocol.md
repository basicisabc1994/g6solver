# Context Engine, row 01: scope, disclosure and deletion (protocol)

Frozen before the code it measures (design 12.8, row "Scope, disclosure and deletion"; plan slice 1, completed by slice 2).
A change to a threshold or a suite below retires this protocol: the next version is a new file with a new cohort.

## What is measured

Whether any interface of the Context Engine reads, returns or discloses something it may not:

- a **forbidden read**: a file opened that the walker then excludes, a file outside a permitted root, a link followed out
  of a root, or a project store read through another project's binding or through the generic component door;
- a **disclosure of an ungranted source**: passage text, a path, a title, a passage id or a derived answer of a source
  reaching a client whose provider holds no grant for that source, in any reply field (result, `denied`, `withheld`,
  `evidence`, the warning card, an error message);
- a **tombstone resurrection**: a passage of a revoked or deleted registration, or of a file observed changed, returned
  after the tombstone's commit, in the same process or another.

## Unit and denominator

The unit is one test case of the declared suites. The denominator is every case collected from those suites on the
measured commit; a case that errors, is skipped or does not run stays in the denominator and is reported apart. The
placeholder-file case runs on Windows only; elsewhere it is reported as not reached.

## Declared suites

- `tests/knowledge/test_engine_scope.py`
- `tests/knowledge/test_engine_walk.py`
- `tests/knowledge/test_engine_lifecycle.py`
- `tests/knowledge/test_engine_view.py`
- `tests/knowledge/test_engine_inventory.py`
- `tests/core/test_mcp_knowledge.py`
- from slice 2: `tests/knowledge/test_engine_refresh.py`, `tests/knowledge/test_engine_snapshots.py`

## Interfaces, principals and events enumerated

Interfaces: the engine functions (`preview`, `commit`, `search`, `fetch`, `revoke`, `sources`; from slice 2 `refresh`,
`delete`, `reconstruct`) and the MCP tools over them. Principals: one OS account, two projects. Destinations: `local`,
a granted provider, an ungranted provider, `cloud:unknown`. Events: grant, revoke of a cloud grant, revoke of a
registration, expiry by the injected clock, a file changed after commit, a crash between stage and publish, two server
processes on one project.

## Labels and baseline

The labels are the planted markers of the synthetic corpus built by `tests/knowledge/corpus/__init__.py` (a planted
secret, a planted instruction, credential files, an ignored file, an oversized file, a binary file). No private text is
used. There is no baseline arm: the admission rule is an absolute count.

## Versions and environment

Python 3.12; SQLite with FTS5 from the standard library; FastMCP as installed with `g6[mcp]`; the consent check of
`g6.core.cloud_consent`. Supported environment for this row: Windows 11 and Linux, one developer account. The result
file records the commit, the Python and SQLite versions and the platform.

## Limits and what the row does not claim

This is a host and API boundary. Code running as the same OS user outside G6 can read the store's files; the row does
not measure that and nothing here describes separate stores or hashes as protection from it. Text already delivered to a
client or model cannot be recalled; the result file reports retained history and prior delivery separately from the
counts below. Secret masking is pattern-based and is not a confidentiality guarantee.

## Costs recorded

Wall-clock seconds per suite, builder and reviewer time, and zero model calls (none of these suites calls a model).

## Thresholds

```json
{
  "row": "01-scope-disclosure-deletion",
  "frozen": "2026-11-11",
  "unit": "test case of the declared suites",
  "thresholds": {
    "forbidden_reads": 0,
    "disclosures_of_ungranted_sources": 0,
    "tombstone_resurrections": 0,
    "cases_failed_or_errored": 0
  },
  "admission": "every threshold met on the measured commit; cases not reached are listed and are not counted as passes"
}
```
