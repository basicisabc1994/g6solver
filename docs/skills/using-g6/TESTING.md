# How the "using G6" skill was tested

The skill was written test-first, following the superpowers `writing-skills` method:

1. watch agents work without the skill;
2. write the skill against what they got wrong;
3. watch them work with it;
4. close what they still got wrong.

Test date: 2026-10-20. Library at `8eb82ac470`.

## Setup

- **Agents:** Sonnet subagents, each starting fresh.
- **What every run saw:** the `g6` MCP server's tools, listed exactly as an MCP client shows them, and one user request.
- **Their task:** write out every step, every G6 call with its arguments, and the final message to the user.
- **Nothing was run.** The test is of what the agent plans to do with G6.
- **Two scenarios:**
  - **A, a coding task:** "The refund test in tests/test_refunds.py has been failing since yesterday's change to the
    payments module. Fix it and get it ready to merge today."
  - **B, an AI system that keeps failing:** "Our support-ticket triage agent … keeps getting the urgency wrong on
    billing tickets and sometimes promises refunds it can't give. I have about 300 past tickets with the correct
    labels. Make it reliable."

## Results

| Behaviour | Baseline: no skill (A ×3, B ×3) | With skill v1 (A ×3, B ×3) | With skill v2 (A ×2, B ×3) |
|---|---|---|---|
| Calls `g6_help` and `g6_describe` before invoking | 6/6 | 6/6 | 5/5 |
| Uses placeholder or guessed op names ("the review op") | **6/6** | 0/6 | 0/5 |
| `coding_agent_gate.preflight` before the risky payments change (A) | **0/3** | 3/3 | 2/2 |
| `coding_agent_gate.verify_answer` before reporting done (A) | **0/3** | 3/3 | 2/2 |
| Premortem through `g6.reliability_pipeline.relevant` | **0/6** | 6/6 | 5/5 |
| Refund promises gated with `action_gating.evaluate`, not a home-made filter (B) | **0/3** | 3/3 | 3/3 |
| Declares the triage as a `g6.metaharness` workflow with dev and held-out suites (B) | **0/3** (all three tuned only "if a declared workflow exists") | 3/3 | 3/3 |
| Knows the meta-gate's minimum (30 held-out, 5 discordant, sign test) (B) | 0/3 (guessed "the gain could be noise") | 3/3 | 3/3 |
| Calls `metaharness_approve` from the session that proposed the change (refused by the server) (B) | 0/3 (they did not get that far) | **2/3** | 0/3 |
| `evidence` and `verifier` in their real shapes | — | 0/6 | 5/5 |
| Reports the label as given, never upgrading to "verified" | 6/6 | 6/6 | 5/5 |
| Uses the superpowers lifecycle skills | 0/6 | 6/6 | 5/5 |

## What changed between v1 and v2 (the refactor)

1. **Approval.** v1 said "a person approves through `metaharness_approve`", and two of three B runs then planned to
   call it from their own session. G6 refuses that call with `G6_E_META_APPROVER_IS_PROPOSER`
   (`oss/src/g6/metaharness/surface.py:149-152`). v2 says: hand the person the decision digest and stop; they approve
   from their own session, from Python (`activate(decision, accepted_by=...)`) or from the terminal. The common-
   mistakes table names the refusal code.
2. **Parameter shapes.** v1 named the fields of `preflight` and `verify_answer` but not their shapes. No run used them:
   each sent `verifier` as a string, or `evidence` as free text. v2 gives the two shapes exactly.

## Re-run after the premortem correction (v3, 2026-10-20)

v2 said `relevant(objective)` returns the failure-mode questions. It returns the curated failure-mode skills that match
the task (one exists today), each with the decision to ask the person; the premortem's questions, one per failure mode,
come from `g6.reliability_pipeline.premortem.premortem_failure_mode_questions()`. v3 names both, in `SKILL.md` and in
`references/lifecycle.md`. Same setup and scenarios, library at `79d3a14f92` plus that change; fresh Sonnet subagents.

| Behaviour | Baseline: no skill (A ×3, B ×3) | With skill v3 (A ×3, B ×3) |
|---|---|---|
| Calls `g6_help` and `g6_describe` before invoking | 6/6 | 6/6 |
| Uses placeholder or guessed op names ("the verify op") | **6/6** | 0/6 |
| `coding_agent_gate.preflight` before the risky payments change (A) | **0/3** | 3/3 |
| `coding_agent_gate.verify_answer` before reporting done (A) | **0/3** | 3/3 |
| Premortem: answers `premortem_failure_mode_questions()` and asks `relevant`'s decisions | **0/6** | 6/6 (one A run called the questions without importing them from `premortem`) |
| Refund promises gated with `action_gating.evaluate`, not a home-made filter (B) | **0/3** | 3/3 |
| Declares the triage as a `g6.metaharness` workflow with dev and held-out suites (B) | **0/3** (all three tuned only "if a declared workflow exists") | 3/3 |
| Knows the meta-gate's minimum (30 held-out, 5 discordant, sign test) (B) | 0/3 | 3/3 |
| Calls `metaharness_approve` from the session that proposed the change (refused by the server) (B) | **2/3** (they read the tool as "it asks the person") | 0/3 |
| `evidence` and `verifier` in their real shapes | — | 6/6 |
| Reports the label as given, never upgrading to "verified" | 6/6 | 6/6 |
| Uses the superpowers lifecycle skills | 0/6 | 6/6 |

v3 keeps every behaviour v2 changed. The baseline re-run differs from the first baseline in one row: two of three B
runs now reached the approval step and planned to call `metaharness_approve` themselves, which the skill's rule
prevents.

## Limits of this test

- It tests what an agent plans, not what it does against a live server. Re-run it as a live test once the package
  ships: a real repository, a real `g6-mcp`, and a scripted person for approvals.
- One model (Sonnet), two scenarios, small samples. Add scenarios for an exact-computation task, a high-stakes
  claim, and a deploy.
- At about 900 words, `SKILL.md` is longer than the ~500 the `writing-skills` guidance suggests for skills that are
  not loaded every turn. The step table is the part that changed behaviour, so it stays; trim the prose if a later
  test shows no loss.
