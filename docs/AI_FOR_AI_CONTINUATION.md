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

### Application construction and reference workflow

- Added data-only workflow creation and project/global discovery (project wins, explicit Python config overrides).
- Added digest-bound confirmation, generated application code proposals with a hypothesis, bounded evaluation/run
  jobs, terminal consent fallback, and rollback prompts bound to the workflow, profile and current version history.
- Baseline files are frozen including the complete filename set; portable filenames cannot collide by case.
  The declarative evaluator is included in the judging manifest frozen at confirmation.
- Added a Python patch review application: actual selected pytest runs, source grounding, reviewed adapters,
  coding gate and action gate. Receipts identify the working-tree digest, base commit, checks and environment.
  Weakened tests, changed runner configuration, unconfined execution and stale artifacts block recommendation.
- Ruling: source grounding remains lexical and caller-supplied; it cannot establish source freshness. The patch
  application supplies no merge authority/action profile; even passing tests can leave the overall action blocked.
  Its scoped observations are not merge authorization or complete patch correctness.
- Ruling: declarative code workflows do not invent a successful independent safety/injection evaluation. They
  can measure candidate improvements but need a host-configured patch_evaluator before activation, as the existing
  gate requires. This limits turnkey code activation, rather than weakening the protected evaluator boundary.
- Generated-code fixture: five observed paired wins; activation refused with five held-out cases. This tests the
  mechanism, not empirical application benefit. Runtime opt-in used; not OS isolation evidence.
- Setup registers explicitly selected Claude/Codex clients using the full interpreter path, preserves existing
  skills, and stores an explicitly selected host model without exposing other settings. Registration syntax checked
  against both installed CLIs. Tests use temporary configuration and mocked registration, not the user's settings.
- 52 combined composition/MCP/reference tests passed; 35 setup/isolation-choice/skill tests passed; 11 terminal
  workflow/MCP tests passed. Focused lint and whitespace checks passed.
- Broader configured baseline: metaharness 309 passed, 1 skipped; core/integrity 2298 passed, 17 skipped and one
  expected-tools-list failure, since fixed and covered by the focused discovery tests.
- Recovered the nine remaining A2 documentation/site-test edits without changing their source worktree.

### Validation, public claims and study preparation

- Windows OS confinement: 51 passed with no global unconfined opt-in. Linux bubblewrap and secret probes:
  10 passed inside a disposable Docker container with a read-only checkout and no network. These are boundary
  tests, distinct from the Windows CI opt-in used for general regression tests.
- Adjacent grounding/coding/action/bundle suites: 535 passed. Full source/test Ruff scan passed.
- Core/integrity: 2305 passed, 17 skipped; one resolver-policy failure fixed by using outside.find_program.
  Setup and resolver regression: 31 passed.
- Harness/metaharness: 392 passed, 1 skipped; three import-layer guard tests failed on the new terminal entry point.
  Ruling: permit exactly g6.cli -> g6.metaharness.surface, retaining the prohibition for components and core.
  This admits the authorized terminal consent path without relaxing component layering. All 26 guardrail tests pass.
- Added a real FastMCP Client create/confirm/job/run journey and sensitive patch-tool refusal test; it passes.
- Public headline now reports fixed arm D separately from post-hoc best-per-dataset selection. Exploratory winners
  carry selection and unadjusted-p-value caveats. Positioning preserves AI-for-AI as the objective, not a result.
- V8 draft sampler verified the existing V2 parquet hash and read only three metadata columns: 8 Python dev tasks
  (2 reused OpenLibrary pilots) and 48 scored IDs; no scored text inspected, no model call. Protocol and split remain
  draft pending price/cap calibration, evaluator controls and final registration. Budget ceiling remaining $167.85.
- v0.1.0 was not modified. Package development version becomes 0.1.1.dev0; no publication or shared-branch push.

### Independent review fix pass

- Final read-only review identified three material findings, documented in CONTINUATION_REVIEW.md.
- Fixed a reproduced sandbox parent-read escape through a child-created symlink. Output collection now refuses
  linked/special files and validates the bounded opened file. Synthetic sentinel only, no real secrets inspected.
- Fixed overlapping evaluations spending the same held-out use: complete API operations acquire reentrant thread
  and OS workspace locks, reload after locking, and MCP metadata writes share the transaction boundary. A busy
  surface returns a refusal rather than blocking its async client. One-handle and separate-handle regressions pass.
- Fixed stripped scratch HOME/USERPROFILE; explicit runtime paths survive only within the plan's writable mounts
  (homes/caches) or readable mounts (Lean/Sage dependency roots). Secrets and import hooks remain excluded.
- Updated boundary results: Windows 52 passed; Linux 12 passed. Adjacent runners 690 passed, 6 optional skips.
  Full harness/metaharness after locking: 398 passed, 1 skipped. Import contracts: 3 kept, none broken.
- Fresh built-wheel smoke passed with isolated settings, Codex skill, a composition and a separate stdio MCP process.
  The final wheel was rebuilt with the review fixes and normalized LF source; its smoke also passed.
- Documentation/tools broad run: 2767 passed, 2 skipped, 8 failures from stale generated catalogues and old homepage
  assertions. Regenerated catalogues and updated expectations; rerun: 370 passed, 1 skipped, three
  certificate-status failures. These exposed five stale independent passes, correctly refused by the registry.
  Updated the FAQ and exact-library expectations to the unavailable status; final focused result below.
- Ruling on review exclusions: actual client consent and usability/utility remain release gates; same-user shell and
  malicious host-process code remain outside the stated boundary; arbitrary Python check external state is not
  frozen by a source hash; real toolchain compatibility and job cancellation are unproven. No claim extends to them.

### Final validation record

- Workforce regression: 1498 passed, 18 skipped. This run began before the final review fixes; affected
  confinement/runner and harness paths were separately rerun after those fixes, as recorded above.
- Skills/component/composition regression: 967 passed. Adjacent gates/bundles: 535 passed.
- Full source/test/validation Ruff and whitespace checks pass. Tracked-source public scan: zero findings
  with --public-ci (no private terms list), using the exact 509e047 archive.
- Certificate check refuses all five pre-existing certificates because independent review hashes differ
  from current code. No signatures were fabricated or stale reviews reused. Available extensions: 55;
  the five affected extensions remain awaiting review. This is an open release gate, not a passing check.
- Development wheel: g6-0.1.1.dev0-py3-none-any.whl in the sibling g6-continuation-artifacts/dist folder.
  Final installed-wheel smoke passes: isolated settings, skill install, composition, separate MCP process;
  no model calls. No client configuration, public tag, release, or shared branch was changed.
- Final FAQ and certificate-status regressions: 9 passed. The earlier three remaining site failures are resolved.

### Release continuation trials

- Actual Codex 0.164.0 and Claude Code 2.1.311 CLI setup succeeded in isolated temporary homes;
  both registrations retained the exact interpreter and installed the client skill. No user settings changed.
- Python 3.13.5 installed-wheel smoke passed; 39 selected composition/setup/workflow/MCP tests passed.
- Codex direct app-server tool calls discover G6 and create workflows but deliver no consent prompt. G6
  refuses confirmation and supplies the terminal fallback. Interactive human consent remains unverified.
- Frozen six-case patch trial executed under Linux bubblewrap. The selected-test baseline accepts two
  behaviorally wrong patches and one runner-policy violation; G6 blocks all six, including the correct patch.
  Ruling: report these as synthetic engineering observations, not application utility or improved correctness.
  Blocking everything and missing an unselected regression cannot substantiate the performance proposition.
- Located original held source snapshots and private quote files outside the public checkout. A fresh
  independent certificate reader verified every bound source hash and all 98 unchanged rule ASTs, reviewed
  changed shared enforcement code, and passed 109 selected tests (1 skip). Scoped delta attestations preserve
  the original source reader and findings; current bindings match all five components. Commit 185bd73.
- Certificate numerical/seeded-defect prechecks are in progress with process-local Windows unconfined opt-in.
  Initial runs lacked optional numerical packages and OS grants; they produced unknowns, not passes. These
  mathematical checks are separate from actual OS boundary evidence. No certificate is renewed yet.
- Detailed observations and remaining trials: docs/releases/RELEASE_TRIALS.md.
