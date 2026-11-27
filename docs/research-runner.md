# Research runner: host integration and limits

The wave-3 runner is an inactive, injected-capability implementation. It does not
start a daemon, discover credentials, call a provider or change repository authority
when imported. `self_training/autonomy.json` does not ship: an absent file reads as every authority off,
as a malformed one does, and only the owner writes it. Tags, releases, PyPI and certificate issuance remain owner
actions.

## Host service

Construct the service outside engineer containers. The owner supplies:

- `Runner.refresh(now) -> TickInputs`: a fresh measured self-model, independent
  serial-stage capacities/yields, M experiment forecasts, action bindings,
  reference outcomes, complete protected manifest and pre-spend power estimates.
  `experiment_stages` binds experiments to the measured bottleneck. Unknown stop,
  budget or isolation state blocks a tick. Three identical constraint/strategy
  ticks require a changed method, level or frame.
- `Runner.execute(choice, prediction, snapshot, stop=...) -> RunResult`: a bounded
  distill/rectification/M-policy/field-harvest executor. A recorded simulation must
  precede the measured action-start timestamp. Execution polls `stop`, uses the
  reserved provider only, and returns independently measured outcomes and actual
  allowance use. Synthesis still uses its existing MeteredCaller and BudgetMeter
  limits. A reported provider limit never selects another provider or API credit.
- The existing `BudgetMeter`, with the owner's provider windows, caps and reserve.
  Reservations are made before execution and settled afterwards. Interrupted or
  uncertain execution stays charged conservatively at its reservation.
- A persistent `MetaLedger`, a single-writer `ChainLedger` for runner rows, and an
  intake callback adapting monitor/prediction failures through
  `FailureIntake.discrepancy(row, EventContext(...))`. The cycle ledger also retains
  the typed failures and calibration. Keep all host state outside mission mounts.
- A repository adapter and stop callback. The callback includes a fresh reading
  of the `loop:stop` label; unknown remote state must stop. `core.stop` also checks
  the process kill switch, `G6_STOP` and `$G6_HOME/STOP` on each boundary.

Call `Runner.tick()`/`step()` from the host scheduler. It returns a next-at time
for idle/budget/risk holds, initially 15 minutes and doubling up to a day. The host
scheduler remains responsive to the stop between calls. `daily_digest(now=...)`
summarises the day's experiments, predictions/hit rates, library changes,
monitor discrepancies, budgets/targets, merges/certificates and revert proposals.
Publishing needs separate digest, draft and push grants and an owner pinned issue.
The owner bootstraps `claude/g6-auto/digest` once. Later days fetch its checked tip,
commit detached and push fast-forward, without local branch resets or force pushes.
Ambiguous publication is retained for reconciliation rather than blindly retried.

## Missions

`build_mission` produces a pinned-image plan for either `claude -p` or `codex exec`,
with the `g6-mcp` server required in the image/client configuration. `ContainerMissions`
checks the whole immutable plan before handing it to an injected container port.
The port must configure the client's MCP server, enforce the wall/memory/PID caps,
read-only root, dropped capabilities, no privileges, and network policy. There is
no default Docker process executor in this package.

Mount only a disposable mission worktree read/write at `/workspace` and a separate
fresh mission home at `/mission-home`. The host must check their actual contents:
no held-out cases, keys, owner records, archive or host G6 home. Worktree naming is
not an attestation of contents. Never mount a Docker socket or the host runner
state. Sign-in mounts are empty and network is `none`. Live authenticated provider
missions require the owner's Q3 decision and a separately reviewed egress/sign-in
integration. The current fixed mission plan cannot enable sign-in mounts. Its
authority ceiling is draft PR even if a caller changes its claimed privileges.

## Merge custody

Use `distill.merge_evidence_gate.host_merge_gate(...)` in the trusted bootstrap.
It injects the unchanged default `EvidencePolicy` and the full protected union.
The gate module never imports self-model predictions. The host's evidence checker must
authenticate complete canonical `HostEvidence` from the independent evaluator;
neither engineers nor the runner can select the evidence checker, signer, policy or manifest.
The host signer must use the protected SSH allowed-signers root through a service
outside mission custody, replaying the evidence before signing. No production key
or SSH subprocess implementation is supplied to the loop. Test HMAC keys are fixtures.

The owner must provision the bot with contents/PR permissions and no workflows
permission, a main ruleset requiring PRs, all CI jobs and `g6-merge-gate`, no bot
bypass, and CODEOWNERS requiring owner review over the complete protected set.
The host attests these actual settings; caller-supplied booleans are insufficient
without its signature. Only the host status service can post `g6-merge-gate`.

Required CI includes all 24 OS/Python/shard jobs and all three bundle builds,
successful on the exact resulting commit. Bundle CI currently needs host dispatch;
missing/skipped jobs block. Review must be clean on that commit, by the other
family, through both K-loop and patch search (at most four reviews/three repairs).
Fixed findings require reproduction receipts and a passing test retained in the
exact diff; closed findings require a reviewer-accepted reason.

A measured claim needs frozen-policy independent receipts, split/registration
digests, sufficient paired cases, valid intervals, sign-test gain and conservative
non-regression margins. A no-behaviour claim needs the full suite and a nonnegative
lower bound on paired monitor success change (a conservative zero success-loss
margin). Prediction references can appear in certificates but clear no gate.
Bound/protected changes, test edits, indirect paths and incomplete unions stay draft.
Only new regular non-conftest Python test files get the narrow test exception.

`GhRepository` takes an explicit argv command port. It creates an isolated worktree,
branch, staged patch, commit with two supplied session trailers, push and draft PR.
Every command checks the stop and authority again. The command port must reject
nonzero exits and transport errors, and must not use a shell. Multiline text uses
stdin. No command runs merely by constructing the adapter.

Plain `gh pr merge --squash --match-head-commit` cannot promise the resulting SHA.
Therefore `merge_exact` refuses unless the host supplies an exact-merger capability
which atomically compares main, PR head and final commit, publishes the gate status
only for the checked artifact, and obeys GitHub rules. It must not bypass a ruleset.
This deployment dependency is deliberate; a head-only check is insufficient.

## Generations, rate state and recovery

The host keeps `certificates/generations.jsonl` and `distill/merge_state.jsonl` outside
mission mounts. On each process restart, provide independently retained
`certificate_head` and `state_head` (empty strings only for owner-established genesis).
Missing anchors refuse persistent-host merge. Retain and advance those heads in
the independent host service after each operation. Hash chains without that custody
cannot detect replacement/deletion of the entire history. In-memory stores are
for logic tests, not evidence of restart durability.

A signed prospective certificate and rate reservation precede merge. The generation
is appended only when the exact-merger confirms the expected commit. An uncertain
effect keeps its reservation and blocks another attempt. Stale lock files are never
stolen automatically. A crash between merge, certificate append and checkpoint
advancement requires the owner to reconcile actual main against the signed prepared
record. Never reset a ledger to get past a hold.

The limits are one merge per rolling 24 hours, four per rolling seven days and a
72-hour cooldown after rollback (also conservatively after a pending revert).
Two rollbacks in 14 days, five merge attempts in an hour, a measured discrepancy
in merge inputs or an invalid certificate chain latch effective autonomy at draft.
Only owner reconciliation/restoration can clear that state; the runner does not
rewrite `autonomy.json` upward.

An executor returns host-signed monitor regressions in `RunResult.regressions`.
The runner opens an idempotent revert PR through the real gate when a measured
lower bound exceeds the frozen margin. A subsequent exact inverse, its fresh CI,
and the signed trigger use `merge_rollback`, with no new held-out gain requirement.
The revert is a signed generation with its own parent and triggering receipt;
its scope explicitly makes no new improvement claim. Rollback beyond the certified
tip, uncertain revert publication and invalid triggers require reconciliation.

Certificates retain parent/change/tree/manifest digests, CI and review receipts,
policy/split/registration/reuse/counts, before/after intervals, prediction references,
claim scope and what was not shown. The host must retain git objects, evaluation
receipts and served/published certificate links for independent replay. This build
does not claim live empirical improvement, production custody, consciousness,
or results outside the named measured cases.
