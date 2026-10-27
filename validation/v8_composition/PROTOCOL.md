# V8: application redesign feasibility — draft, not frozen

No paid run is authorized by this document. Register its final hash, runner/configuration hashes, split hash,
G6 commit and verified current model pricing before any scored execution. V7 design D remains retired, unrun;
its pilot archives remain development evidence only.

## Question and claim

Does a failure-theory harness implemented with G6 recover more initial failures than Claude Code's ordinary
self-check, under matched model, time, turn and spending caps? This is a two-arm feasibility and effect-estimation
study of the whole intervention. It does not isolate the value of G6 composition from prompts, diagnostic tests
or workflow design. A future direct implementation of the same strategy without G6 is needed for attribution.

The engineering evaluation is separate: construction effort, reusable code, integration errors, false verification
claims and runtime overhead. A null performance result does not erase engineering evidence, and engineering
success must not be reported as a task-performance win.

## Fixed candidate design, subject to pre-run calibration

- Model choice: `claude-sonnet-5-5`, high effort, matching the actual V7 runner; not Haiku 4.5.
  Confirm availability and prices and freeze the exact client build before running. If unavailable, amend and
  register a new model choice and budget; never substitute silently.
- Preserve V7's pinned SWE-bench Pro V2 dataset revision/hash and official harness commit/checksums.
  Do not reuse its mixed-language scored population. `sample.py` reads only the three metadata columns after
  verifying the dataset hash, and draws a new Python-only split with seed 20261027.
- Development: eight tasks, including only the two OpenLibrary tasks among V7's four pilots. Exclude other
  previously seen V7 development tasks. Scored: an ordered 48-task ceiling, disjoint by task, not repository.
  The split is a draft until registered; it must not be selected using task outcomes.
- Freeze the harness after development. The builder receives development evidence only; scored task answers,
  reference patches and hidden tests stay with the evaluator. Lessons during scoring are archived in shadow,
  never fed into subsequent scored runs.
- Both second-pass arms start from the same stage-one patch and session, use the same model and equal resource
  caps, and have their order randomized by a registered seed. Include every completed pair and every failure
  to execute; no best-per-task post-selection. Do not retry selectively after observing correctness.
- Budget remaining: at most $167.85 from the $180 ceiling after $12.15 of V7 pilots, subject to actual account
  reconciliation. Reserve budget before dispatch, count failed calls and retries, and stop before exceeding it.
  Forty-eight tasks is a maximum, not a promise that this budget buys them. Register reduced caps/sample size
  after calibration on development tasks, before opening scored outcomes. Do not guess current token prices.

## Outcomes and uncertainty

Primary estimand: paired difference in recovery among stage-one failures, with the failure count and denominator
reported. Also report overall success, discordant wins/losses, exact paired test, uncertainty interval, costs,
wall time, false acceptances and infrastructure failures. Freeze the interval procedure and analysis code before
scoring. With roughly fifteen initial failures a meaningful difference may remain inconclusive; no definitive
improvement headline is justified by the proposed sample alone.

Archive all prompts, outputs, patches, evaluator receipts, image digests and costs under the existing study
archive policy. Publish IDs and summaries without redistributing task content. Run reference-patch and empty-patch
oracle controls and secret/leak checks first. Record every deviation and report unsupported hypotheses too.

## Outstanding release and study gates

Fresh-user and real-client consent trials; independent reference-application utility measurement; working Python
task-image checks; current price calibration; official evaluator controls; final split and runner registration;
budget enforcement and analysis implementation. Until these pass this protocol is a preparation artifact only.
