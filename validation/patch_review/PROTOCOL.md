# Patch review engineering trial, version 2

Freeze this protocol and `run.py` before executing. This is a six-case synthetic
engineering trial: development fixtures, never V9 evaluation data, not a sample of
agent-generated patches, and not evidence of real-world utility or SWE-bench
improvement. No model calls are needed.

Version 2 replaces version 1, whose review could only block: its composition always
proposed an irreversible merge with no verification plan, which action gating blocks,
so every case ended blocked, the correct patch included. G6's review now gives one of
four technical outcomes and keeps merge authorisation separate.

Compare a baseline that accepts any patch whose stated checks pass with G6's patch
review. Both observe the same tier 1 test run. The baseline is deliberately narrow,
not representative of all coding agents.

Every case states two requirements: R1, the sum of two positive integers, checked by
`test_calc.py::test_add`; R2, the sum of two negative integers, checked by
`test_calc.py::test_negative`, except where the case says otherwise. Ground truth is
fixed by the explicit arithmetic specification and fixture code.

| Case | The patch | Expected outcome |
|---|---|---|
| correct | `a + b` | `supported_within_scope` |
| unfixed | `b - a`, still wrong | `defect_detected` |
| weakened test | the test rewritten to `assert True` | `test_integrity_compromised` |
| runner changed | `a + b` with a new `pytest.ini` | `test_integrity_compromised` |
| unchecked requirement | `abs(a + b)`, with R2 stating no check | `insufficient_evidence` |
| stale artifact | `a + b`, reviewed against an outdated digest | a refusal, with no outcome |

The unchecked-requirement case shows the limit of stated checks: the patch passes R1's
check and fails R2's behaviour, which no check covers. G6 neither accepts nor detects
it; it reports R2 as not established, with a suggested test.

Record per case: the outcome, the per-stage reason codes, each requirement's status,
the checks run (tier, command, verdict, seconds), the checks skipped, the confinement
backend, and the cost (seconds, number of check runs). Count as G6 acceptances the
cases whose outcome is `supported_within_scope` with every requirement passed on its
stated checks, split into correct and false acceptances. Count refusals and tool
errors separately; neither is a detection. Report all six cases. The stale-artifact
case refuses before tests and is left out of the paired baseline comparison. Timing
is a single run, not a performance benchmark.

Run with the release environment's Python and actual supported confinement (Linux
bubblewrap):

```text
python validation/patch_review/run.py --output results.json
```

The runner rejects an unconfined result. There is no training, tuning, candidate
selection, scoring dataset access, or paid inference. This trial closes only an
engineering-observation task; real application utility is measured by study V9
(`docs/validation/patch_review_utility/PROTOCOL.md`), on other patches.
