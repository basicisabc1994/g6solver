# G6 self-maintenance and self-evolution control plane

## Objective

Use G6 on G6 to measure and improve reliability, real task success, first-use experience,
accessibility, fairness and resource efficiency. An autonomous loop may decide what
**to investigate and propose**, but its judge, tests, approval, release and safety
policy remain under independent control. Neither user delight nor throughput can buy
exceptions to safety or authorization. This is dogfooding and a technical acceptance
exercise; unfamiliar-user UAT needs separately recruited users.

## Narrow functional architecture

1. **Observe** CI, reproducible user issues, failed activation, real task completions,
   costs and opted-in privacy-preserving satisfaction feedback. Not model assertions.
2. **Classify constraints** with `self_maintenance.constraints.identify_constraint`
   across actual serial stages: discovery → specification → coding → verification →
   user acceptance. The minimum effective capacity is a **candidate** bottleneck;
   measure end-to-end throughput to validate that it is truly constraining.
3. **Exploit/subordinate/elevate** the current constraint. Prioritize bounded tickets
   with evidence, protected path checks, predicted value per reserved token, and a
   separately controlled risk score.
4. **Reserve** daily/cycle/provider/issue compute with `BudgetBook`, keeping an
   untouchable reserve for independent verification and rollback. Unknown actual
   tokens are charged at the full reservation. Reservations are **not enforceable
   provider token ceilings**: CLI agents can overrun a reservation; host deadlines
   limit time, and subscriptions' usage limits are externally enforced.
5. **Run** Codex or Claude Code with G6's existing `AgentRuntime`, permissions,
   bounded time and a disposable, version-controlled workspace. Keep credentials,
   evaluator fixtures and held-out tests outside the working copy.
6. **Verify** independently using host-owned `workforce.repo_tests`, negative controls,
   cross-module regressions, test-weakened detection, exact diff inspection and
   machine-checked algorithm certificates where available.
7. **Recommend** a draft PR only when objective checks support it; maintain
   independent review and release authority. Accepted changes are remeasured in
   shadow use; immediately demote on credible safety evidence and roll back upon
   loss of constraints. G6's existing metaharness owns profile evaluation and
   promotion—this kernel must not implement a competing mutable judge.
8. **Repeat** after identifying the next bottleneck. Record counterexamples, total
   tokens, provider attribution, cost, false assurances, outcome changes and
   anonymous first-use friction. Stop if no improvement is demonstrated.

## Public API and MCP

Python: `g6.self_maintenance.kernel` supplies `BudgetPolicy`,
`MaintenanceTicket`, `BudgetBook` and `rank_tickets`.
`g6.self_maintenance.constraints` supplies TOC identification, safety envelope and
`focus_plan`. `g6.self_maintenance.controller.run_one_cycle` calls an injected,
host-owned coding agent and checker for **one disposable workspace**, and only after a
person's approval in the operator's queue bound to that ticket, provider and day
(`ActionQueue.request_run`, `cycle_binding`); the report names the approver.
`run_existing_agent`, the live Codex and Claude path, refuses in this release
(`G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF`) before any runtime is built.

MCP: `g6_find("self maintenance")`, `g6_describe("self_maintenance")`,
`g6_invoke("self_maintenance", "identify_constraint", ...)`, or
`g6_invoke("self_maintenance", "plan", ...)`. Both are **read-only**.
No MCP call can opt in to paid coding agents, modify source, override controls,
write the ledger or approve a change.

## Strict limitations and external trust boundary

- No unattended background daemon, Github Actions scheduler, GitHub issue ingestion,
  staging worktree creation, GitHub PR creation, merge, deployment, or release yet.
- Executing a ticket requires an explicitly approved run. Agent copy must be
  isolated with OS and filesystem boundaries. The CLI tools' restrictions on
  writable paths, shell and network are distinct from G6's own test sandbox.
  The current controller requires an external host to supply this isolation.
- A model can select a ticket but cannot validate its own success. The independent
  checker must recompute diffs, validate checked source hashes and test
  provenance, inspect test configuration and supply protected receipts.
- The controller performs a **post-run** changed-path check; the CLI instruction
  listing allowed paths is **not an enforcement boundary** during its run.
  No live write run should be enabled until the host mounts an isolated
  workspace and confines writes independently.
- User signals are voluntary, aggregate and minimal: no identities, raw
  transcripts or hidden persuasion/engagement optimization.
- Small sample rates and observed means cannot prove fairness, zero unsafe
  actions or monotone real-world improvement. The safety envelope is a strict
  necessary filter; held-out statistical tests, product monitoring and independent
  governance remain mandatory.
- The budget ledger is single-writer. No multi-host scheduler or atomic external
  billing meter is provided; a trusted usage receipt is needed to refund reserves.
- PR review and merge authority must be independent of any worker able to
  propose source modifications. Protected judges, tests and policies must not be
  changed in the same candidate branch under self-approval.

## Minimal trial

Create one host-authored, reproducible ticket outside the protected code,
specify real existing regression node IDs, record independent stage rates, and
run `focus_plan` or the MCP read-only plan. Review the ranked decision.
Call `run_one_cycle` with no approval as a dry run (`simulation_only`). Only an external
trusted orchestration service can authorize and provide the disposable workspace,
sealed independent checker and allowed agent. Do not enable write mode by
default. Compare the fix to an ordinary Codex/Claude run on the same tasks
and compute budget; track fixes/attempt, cost/fix, unsupported claims, total
time-to-value and UAT user task success.

Tests: `python -m pytest tests/self_maintenance/test_kernel.py
tests/self_maintenance/test_experience.py`.

Release decision: **not validated** until real host tests, actual token accounting,
installed-wheel MCP discovery, CI and a hands-on independent approval trial pass.
