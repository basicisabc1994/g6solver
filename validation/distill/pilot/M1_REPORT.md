# M1 pilot: acquisition funnel report (exploratory)

Status: M1 stops under its stop rule D2 (`D2_STOP_RULE.md`, fixed before run 2). Every number here is exploratory and
comes from the six acquisition repositories only. No admission, monitor or reuse unit was read.

## What ran
- 13 real bug fixes from 6 acquisition repositories (black, charset-normalizer, pluggy, pygments, pyjwt,
  python-dotenv), each through the funnel (`funnel.py`): extraction, shape, isolation, fixed suites, control, mutant
  count, settle, label, headline.
- Three arms, fixed before the runs: `as_built` (the oracle's default policy), `fixed_tests` (minimum of 1 mutant),
  `multi_target` (one case per changed Python target).
- The maintainers' tests run on the host interpreter inside G6's isolation boundary; Docker only reads sources.
- Run 1 (code 86b5f1f1): 0 suites ran; the isolation boundary could not read the interpreter (an access grant was
  missing). Run 2 (code f11fcbc0, after the grant): 32 confined suite requests, 68 s, 0 refused.

## Run 2, per arm (13 bugs each)

| Stage reached last | as_built | fixed_tests | multi_target |
|---|---|---|---|
| extraction (record size limits) | 5 | 5 | 5 |
| shape (case converter) | 5 | 5 | 6 bugs, 15 rows |
| fixed suites did not settle | 3 | 3 | 1 |
| headline label | 0 | 0 | 1 bug, 3 labels |

- Extraction: four fixes whose support files exceed the record's per-file or total limits (black x2, pygments x2),
  one whose changed file does (black-5148).
- Shape: the converter takes one non-test Python target and one test module; most real fixes touch more.
- Fixed suites: pluggy cannot test its own code under this runner, because pytest imports the installed pluggy before
  the staged tree is on the path (an intended limit of the runner). python-dotenv-700 (a missing module) and
  pyjwt-1201 (suite did not pass) are unresolved between an unstaged test-support module and set-up errors.
- The only fix to travel the whole path, charset-normalizer-772 under `multi_target`, gave 3 labels in one
  repository: 1 positive (the reversed maintainer change, a known wrong witness) and 2 negatives (assertion-erasing
  edits with no loss seen on its single mutant).

## Stop rule
D2: if no arm shows at least 3 repositories with headline cases of both classes, no v2 registration package is
built on this cohort and no evaluation unit is read. The best arm shows 1. D2 fires.

## What this report can and cannot say
- Can: the pilot's acquisition path runs end to end under isolation on real fixes; on these 13 fixes the default
  oracle policy labels none, and the case model (record limits, one target, one test module, host runner) is the
  binding constraint on supply.
- Cannot: anything about teacher agreement, admission, or distillation into programs. In the one labelled
  repository every label equals the way its case was built (reversed fix = positive, erasure = negative), so a
  trivial rule scores 3 of 3; such labels support no distillation statement.

## Next (owner decisions pending)
A bounded static inventory of newly mined development repositories under a fit filter fixed in advance (M1b), with
separate counts for the baseline converter and for single changes (helper staging, one case per changed test module,
a prepared interpreter), a rule that requires label variation within a construction kind before any distillation
statement, and its own allocation files. The decisions it needs (network reads, pool rule, the T2 bug-judgement rule,
disjointness from the sealed cohort, minima, configuration chooser, construction-validity rule, host execution) are
the owner's.
