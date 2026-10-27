# AI-for-AI continuation — 27 October 2026

## Accepted objective

An AI discovers components, constructs an application, diagnoses failures, proposes a
composition or code redesign, and tests it against a frozen baseline. Candidate code
cannot modify the evaluator, authorization policy or promotion enforcement. Python
patch review is the first proving ground, not the limit of the product.

## Implementation sequence

1. Recover A1/A2, finish trust enforcement, confinement and digest-bound consent.
2. Strict composition with explicit adapters, evidence contracts and assurance reports.
3. Expose the construction/redesign/evaluation/approval/run loop through MCP, using the
   existing meta-harness. Preserve exploration-only operation below its evidence floor.
4. Exercise successful and rejected redesigns, fresh installation and separate utility
   measurements. Prepare v0.1.1; do not mutate v0.1.0 or publish automatically.
5. Correct V8's model/split/attribution and budget registration before paid execution.

## Recovery ledger

- Public main checked with `git ls-remote`: 419e43fdc6f52d773c6c0e25cb787fda63bcb776.
- Local export adds 3ad72c0 (honesty and installation fixes); retained as the base.
- Workspace: Desktop/g6solver-continuation, branch codex/ai-for-ai-continuation.
- Imported all 13 A1 commits through af7d9875ee and 10 A2 commits through f0e28bc923
  from the private repository, filtering to oss/ and preserving original authors.
- Ruling: work directly in an isolated public-layout clone. Private source history,
  secrets and unrelated worktrees are not imported. Public/private history differs;
  original source commits are recorded above for traceability.
- Resolved CI workflow context divergence and overlapping MCP documentation during
  import. Existing public workflow behaviour and the trust-door description retained.
- Original A2 uncommitted documentation remains in its source worktree for review.
- Baseline before import: 19 focused MCP/pipeline tests passed.
- Publication, paid model studies and empirical improvement claims remain gated on
  their actual evidence. No broadening of supported Python versions in this milestone.

## Verification and limitations

Record commands and results here as each implementation slice completes. Committed
recovery is not itself evidence that the integrated code passes.

### Trust hardening slice

- Recovered baseline: 131 passed, 1 platform skip across door, pipeline and confinement tests.
- New spawn-boundary tests failed for inherited credentials and unrestricted descriptors,
  then passed after a common allowlist and descriptor guard were applied.
- Adjacent confinement run: 55 passed, 1 platform skip. Added Linux real-child secret probe.
- Scoped integrity evidence test failed on missing artifact digest, then passed with
  explicit check version, claim, pattern scope and execution environment: 69 door/integrity tests passed.
- Ruff and git diff --check passed for this slice.
- Ruling: environment policy lives at the common confine.popen boundary; existing runners'
  narrower environments still apply. This avoids relying on every caller to scrub secrets.

### Composition and consent slice

- Same-session elicitation tests failed under supported FastMCP, then passed after consent
  was bound to recorded decision/candidate/proposal digests and recorded as unauthenticated.
- Added strict composition, typed inputs, explicit field mappings, per-operation outputs,
  evidence-kind declarations, stop/precondition checks and append-only assertion provenance.
- Added data-only g6_compose and contracts for grounding, coding-agent gate and action gating.
- 28 composition/MCP/approval tests passed; focused Ruff and whitespace checks passed.
- Created .venv using declared FastMCP <3 (system installation was incompatible).
- Initial broader run: core/integrity 2274 passed, 10 failed, 21 skipped; metaharness
  289 passed, 20 failed, 1 skipped. Failures traced to missing optional dependencies and
  default isolation refusal; rerunning with declared extras and Windows CI's explicit opt-in.
  These opt-in tests are not OS-isolation evidence.
