# Site data

## `component_catalogue.json`

The extensions the Components page lists, made once (2026-10-20) from the extension tracker by `from_tracker.py`,
which is kept here as the record of how it was made: the tracker's DROPs removed (their names kept in `dropped`), the
owner's HOLD and every row the tracker does not have hidden (`hidden` says why), and every other row given its family
(one of the 15, or `core` for a row planned into a core package) and the package its code lands in. Nothing in it says
whether a row is in the library: the page asks the component registry, and describes an entry in the library from its
own package (`pages/extensions.py`). `vetting` is the tracker's `vetting_required` kinds: a row in one of those areas
is shown as built, awaiting its certificate, until its package's certificate is current (`g6.core.vetting`; no
qualified person's review is awaited, DECISIONS #39). The page lists a row that is not in the library (a planned
row) only while `SHOW_PLANNED` in `pages/extensions.py` is on; the data keeps every row either way.

## `archive_benchmarks.json`

G6's earlier benchmark studies (April to June 2026), one row per benchmark, shown on the Research page among every
study run as "Earlier study, audited". None was re-run on the current library.

Each figure is the one the 2026 audits corrected it to. `note` is the one limit its audit left standing, in plain
words, and is what the page shows; `audit` is the audit's full finding:

| `audit_status` | Meaning |
|---|---|
| `sound` | The audit recomputed the result from its raw data and it held. |
| `attribution disputed` | The numbers held, but which part of G6 produced them is unproven. |
| `framing corrected` | The numbers held, but the page described them as more than they measured. |
| `corrected` | The first published figure was wrong or chosen; this row carries the corrected one. |
| `withdrawn` | The gain was withdrawn; only what survived is shown. |
| `null` | No difference between G6 and the baseline. |

The audits are a benchmark-integrity review (2026-06-25), the conformance register's benchmark findings, the HLE
held-out re-run (2026-06-24), and the audits of October 2026 (SOAR-004, DBT-006, LL-001). The raw per-task data is
not published, because benchmark answer keys are never published.

`in_library` says which package of the library covers what G6 did there (none: an extension); `specialised` gives a
result built on the evaluation items themselves, as its record describes it; `rerun: "candidate"` marks the results
to re-run as registered studies (DECISIONS #33).
