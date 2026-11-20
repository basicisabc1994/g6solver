# A6: the fit filter on the 13 M1 acquisition bugs (exploratory)

Run 2026-11-20 with `python -m validation.distill.pilot.m1b.a6`. Inputs: the M1 allocation, the development cohort,
M1's saved run 2 funnel report and the captured host interpreter. Material was extracted read-only in Docker; nothing
was executed. Wall time 12 s.

## Counts from the filter

| Configuration | Repositories |
|---|---|
| K_0 (as built) | 0 |
| K_M (per module, V rules off) | 1 |
| K_1 (helper staging) | 1 |
| K_2, K_3 | 0 |
| K_123 (never selected) | 1 |

Chooser: K_0, tier `stop`, D4 `stop` ("the single-change design did not reach 10"), as expected at 13 bugs. The one
repository counted under K_M, K_1 and K_123 is python-dotenv (bug 700).

## Agreement with the funnel's known outcomes

| Bugs | Funnel (run 2) | Filter | Reading |
|---|---|---|---|
| All 13, as built and fixed tests | 0 labels | K_0 counts 0 | Agree |
| black-5144, black-5521, pygments-3158, pygments-3204, black-5148 | Stopped at extraction | Same extraction reason | Agree (shared extractor) |
| charset-normalizer-715 | Stopped at shape | `invalid_material` | Agree |
| charset-normalizer-772 | Labels in the multi_target arm only | M1, M4 (several fix targets) | Outside the single-change design by registration; no conflict |
| pluggy-799, pluggy-809 | Fixed suites: source loading (pluggy cannot test its own code in this runner) | M1/M4/M5 | Agree |
| pyjwt-1201, pyjwt-1216, python-dotenv-711 | Stopped at shape or fixed suites | M4/M6/V1/V2 | Agree |
| python-dotenv-700 | Fixed suites: `missing_module` | Admitted only with helper staging (K_1) or per module (K_M) | A prediction the funnel never tested, not a conflict |

The filter admitted no bug that the funnel refused under the same configuration. The registration sets no numeric
bar for A6; this table is its result.
