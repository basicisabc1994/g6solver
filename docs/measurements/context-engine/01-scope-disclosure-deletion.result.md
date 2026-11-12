# Context Engine, row 01: scope, disclosure and deletion (result)

Protocol: `01-scope-disclosure-deletion.protocol.md`. Run on the tree of the review 1 repair (the commit that adds
this file), 2026-11-11, Python 3.12.11, SQLite 3.49.1, Windows 11. No model was called. One pytest process per file.

| Declared suite | Cases | Passed | Failed or errored | Not reached |
|---|---|---|---|---|
| `tests/knowledge/test_engine_scope.py` | 7 | 7 | 0 | 0 |
| `tests/knowledge/test_engine_walk.py` | 7 | 7 | 0 | 0 |
| `tests/knowledge/test_engine_lifecycle.py` | 10 | 10 | 0 | 0 |
| `tests/knowledge/test_engine_view.py` | 3 | 3 | 0 | 0 |
| `tests/knowledge/test_engine_inventory.py` | 9 | 9 | 0 | 0 |
| `tests/core/test_mcp_knowledge.py` | 25 | 25 | 0 | 0 |
| `tests/knowledge/test_engine_refresh.py` | 11 | 11 | 0 | 0 |
| `tests/knowledge/test_engine_snapshots.py` | 5 | 5 | 0 | 0 |

Against the thresholds: forbidden reads 0, disclosures of ungranted sources 0, tombstone resurrections 0, cases
failed or errored 0, on these suites. The placeholder, link and git cases were reached on this machine.

What this result does not say, and what happened on the way to it:

- This is not the first run of these suites against the first code. An independent review of slices 1 and 2 found
  fourteen defects that these suites did not catch, among them two disclosures of this row's kind (a search reply and
  a refresh reply that varied with an ungranted source's content) and a deletion that reported removal it had not
  done. They were repaired, and suites were added that are not in the frozen list above:
  `tests/core/test_mcp_knowledge_scope.py` (2 cases, passed), `tests/knowledge/test_engine_removal.py` (18 passed,
  1 not reached: a step an undisturbed deletion does not take) and the review's own 80 cases (passed). Under the
  protocol's own rule a changed suite retires the protocol: the next version of row 01 must declare the added suites
  and be run on a cohort of cases that were not used to repair the code. Until then this row is a development
  result, not an admission.
- Retained history and prior delivery, reported separately as the protocol asks: audit copies exist only for sources
  added with retention "audit" and are removed by a complete deletion; text already returned to a client or logged
  as disclosed cannot be recalled, and the disclosure log keeps passage ids and hashes after a deletion.
- A deleted source's copy in the person's own Elasticsearch is not removed (the action gate holds that removal).
- Code running as the same OS user outside G6 is not measured.

Costs: about 80 seconds of wall clock for the eight declared suites; builder time one night; no reviewer time
counted here.
