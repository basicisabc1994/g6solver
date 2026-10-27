# Independent continuation review and fixes

An independent read-only reviewer inspected public base `419e43fd` through `e4200d9`, including
the recovered trust/confinement work and the new application loop. It found three issues.

| Finding | Correction | Evidence |
|---|---|---|
| Confined child output symlink caused a parent read outside the boundary | Reject links/reparse points and special files; check containment and opened file identity; bound actual reads | Real bubblewrap sentinel reproduction failed before the fix and passes afterward |
| Concurrent evaluations could both use the final permitted held-out evaluation | Reentrant in-process and OS workspace locks cover complete API operations and MCP metadata writes; reload ledgers after acquiring the lock | Overlapping evaluations fail before the fix; one shared handle and two separate handles now each record exactly one evaluation |
| Sanitization removed the runners' writable scratch home | Retain explicit runtime paths only within the confinement plan's permitted mounts | Real confined repository test failed against `/root`, then passed with the scratch home; secret/import-hook tests still pass |

After the fixes: 12 Linux boundary tests, 52 Windows confinement tests, 398 harness/meta-harness tests
(one skip), and 690 adjacent sandbox/Lean/algebra/CEGIS tests (six optional skips) passed. These are different
evidence sets: general Windows runner tests use an explicit process-local unconfined opt-in; the OS boundary
tests assert the actual backend and do not count a skip as confinement evidence.

The review found no further merge-relevant minor issues. It deliberately did not establish real Claude/Codex
elicitation behavior, fresh-user usability, empirical application advantage, current paid-study feasibility,
real Lean/Sage installation compatibility or job-shutdown cancellation. Those remain limitations or release gates.

Rulings: preserve the independent safety-evaluator requirement for code activation; do not claim same-user local
settings are human-only; retain the documented source-only limits of arbitrary Python checks; treat pending
real-client and utility trials as uncompleted gates, not inferred passes. No second reviewer was commissioned;
the fixes are supported by reproductions and regression tests.
