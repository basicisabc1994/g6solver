# M1b registration (fixed before any mined source is read)

Owner decisions of 2026-11-20 (evening), taken on the converged D3 debate. Everything here is fixed before the pool
is mined; M1b is exploratory.

## Pool
- Source list: the PyPI download ranking `validation/worked_examples/selection.py` reads, in rank order (the popularity
  bar of rule P1 relaxed to rank order).
- Kept: a public GitHub repository; a permissive licence; not a SWE-bench repository; no source in another language;
  not in the development or selection cohorts. Disjointness from the sealed final cohort is checked on the owner's
  side (A2), counts only.
- Pull requests merged in the window 2026-06-01 to 2026-10-29 (the worked-example window, adopted as the default;
  the owner may change it before mining starts); the mechanical parts of T1, T3 and T4 as coded.
- Bound: the first 150 kept repositories or two working days, whichever comes first. Reads only (GitHub API and
  source at the merge commit's parent and the merge commit); no candidate code is executed during the inventory.

## T2
The person's judgement "a bug with a clear expected behaviour" is replaced by the funnel's control: the base
implementation fails the fixed test module. That is a differential test outcome only; the pool may hold changes that
are not bug fixes. "Real witness" for M1b means a maintainers' change whose new tests fail on the previous code.

## Exposure
A static filter fixed in advance (`fit.py`, `counts.py`, `chooser.py`) reads every candidate's diff and fixed tree
before roles exist. It publishes rule codes and counts, never bodies. Evaluation units are then "read by a fixed
filter, by no learner and no person".

## Fit rules, counts, chooser, D4
As implemented in this package: mandatory rules M1-M6, converter refusal V1, conservative exclusions V2/V3
(execution-aware; guarded imports are flags), flags; counts K_M, K_0, K_1, K_2, K_3, K_123 by repository, each
configuration's complete staged record checked against the CaseRecord limits. Chooser: keep the baseline unless one
single change reaches a higher tier; highest tier; ties go to helper staging, then one case per changed test module,
then a prepared interpreter (which must exist and be recounted); K_123 is never selected.

D4: applied to the count under the selected configuration, before roles are drawn.

| Tier | Selected K | acquisition | admission (minimum) | monitor (minimum) | reuse |
|---|---|---|---|---|---|
| Full | 15 or more | 5 | 5 (3) | 3 (2) | 2 |
| Reduced | 10 to 14 | 4 | 4 (3) | 2 (2) | not measured |
| Stop | under 10 | the single-change design did not reach 10 | | | |

The attrition rule for the minima is label-independent: a role unit is lost only for a reason recorded before any
label is read (extraction, shape, isolation or a fixed suite that does not settle).

## Construction validity
- CV1: report construction kind by label by repository, and the accuracy of the best rule that reads only the kind.
- CV2: a kind is informative when at least 3 repositories each hold both headline labels within it.
- CV3: M1b's acquisition stop rule (N = 3) counts repositories by CV2.
- CV4: with no informative kind, the result is construction recognition; no distillation statement.
- CV5: with an informative kind, teacher, program and the majority-class rate are reported within that kind only.
- Added construction (W5 extension): indexed erasure, one variant per `assert` directly inside a top-level test
  function the fix added or changed, at most 8 per bug; `operators.py` unchanged. Its yield is unmeasured.

## Order
1. A6: the fit filter on the 13 M1 acquisition bugs, against their known funnel outcomes.
2. Mine the pool; the owner runs A2; the inventory counts; the chooser and D4.
3. At 10 or more: allocation and access files for M1b (the existing `allocation.py` and `allocation.json` are
   untouched), then the funnel on mined acquisition repositories only (A3: host execution under G6's isolation).

Not granted by these decisions: use of the development evaluation units (step 8), the in-image route, W1, W2, W6,
W7, a combined design.
