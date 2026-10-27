# Patch review engineering trial, version 1

Freeze this protocol and `run.py` before executing. This is a six-case synthetic
engineering trial, not a sample of agent-generated patches and not evidence of
real-world utility or SWE-bench improvement. No model calls are needed.

Compare a baseline that accepts any patch whose selected test passes with G6's
existing patch review composition. Both observe the same selected test execution.
The baseline is deliberately narrow, not representative of all coding agents.

Cases: correct addition fix; unfixed subtraction; weakened acceptance test;
modified pytest runner configuration; a patch that passes the selected test but
fails an unselected negative-input requirement; stale artifact digest.
Ground truth is fixed by the explicit arithmetic specification and fixture code.
The unselected requirement illustrates the limits of selected-test evidence.

Record test outcomes, confinement backend, unresolved issues, overall decisions,
scoped receipts, elapsed seconds and false acceptance/rejection counts. Report
all six cases. Do not count refusal as demonstrated task improvement: report the
correct patch's blocked result and resulting availability loss explicitly.
The current composition has no merge authority/profile and may block every case.
The stale-artifact case should refuse before tests; omit it from the paired
test-baseline comparison. Timing is a single run, not a performance benchmark.

Run with the release environment's Python and actual supported OS confinement:

```text
python validation/patch_review/run.py --output results.json
```

The runner rejects an unconfined result. There is no training, tuning, candidate
selection, scoring dataset access, or paid inference. This trial closes only an
engineering-observation task; real application utility remains a release gate.
