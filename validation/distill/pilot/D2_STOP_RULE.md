# M1 acquisition funnel: stop rule D2, fixed before run 2

Fixed 2026-11-20T08:12:01Z, before funnel run 2 starts, by the owner's word ("continue, proceed" on the pending D2 question; N left
at the proposed default).

- N = 3.
- Run 2 uses the arms and caps of run 1 (as_built, fixed_tests, multi_target; 600 runs, 10,800 s), at the code of
  commit f11fcbc0 (diagnostics and accounting only; nothing extracted, converted, labelled or admitted changed).
- Run 2 is the last funnel run on the 13 acquisition bugs.
- Each arm is assessed alone. If no arm shows at least 3 repositories with headline cases of both classes, the v2
  registration package is not built on this cohort, no evaluation unit is read, and M1 reports the acquisition
  funnel and the gap.
- All of run 2 is exploratory. Observations are reported apart from predictions.