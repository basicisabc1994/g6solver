# Check patches before completion.

<!-- route:review -->
## Deliver a coding task with G6

For a Python task, work in this order (each step in full: `references/lifecycle.md`).

1. **Intent.** Write the goals as numbered `requirements`, `{"id": "R1", "text": ..., "checks": [...]}`, each check a
   pytest node id.
2. **Risks.** Map each of this project's risks to a check: a repository test, a new acceptance test, or a template in
   `template_instances`: `example.v1`, `relation.v1`, `access_rule.v1`, `differential.v1`, `forbidden_dependency.v1`
   or `state_invariant.v1` (bindings and one valid instance each: `references/templates.md`).
3. **Hidden constraints.** One question per task by default, kept across calls. Where the task may not decide a
   behaviour, call `g6_confirm_behaviour` with the concrete scenario and its competing outcomes. The person answers
   in the client's own dialog and may allow one more question; their own setting can raise the default or set it to
   none. Only the person answers: no argument answers and you never answer for them. Never assume an answer: an
   unanswered question stays open in the report.
4. **Acceptance first, then test first.** Acceptance tests of what the person's users must be able to do, through
   the real interface; then unit tests, then code.
5. **Review.** Call `g6_review_patch` with the folder, the task and the requirements, then poll `metaharness_job`.
   Add optional inputs (`intent`, `template_instances`, `proposed_checks`) only in the shapes the tool's description
   gives; a refused call names the field to fix. If host permission `patch_review.review` is missing, tell the person
   and stop; never change permissions yourself. Show the reply's `assessment["report"]` verbatim.
   To have the model the person configured for G6 propose more checks, add `search: {"propose": true}`: what it
   proposes runs as your proposed checks do, never passes a requirement, and gives at most insufficient evidence.
6. **Repair.** Fix what G6 reproduced, add each reproduction as a stated check, and review again. Stop after 3
   rounds, when a round changes nothing, or when the report says only the person can decide.
7. **Honesty.** Never call a patch `verified`, and never merge for the person. The result rests on your own tests:
   do not rewrite them to change it. The report's not-established list is the to-do list.

Loop: in Claude Code with G6's plugin, its Stop hook runs the review-until-clean loop when you finish; elsewhere,
call `g6_review_until_clean` (`references/lifecycle.md`).

<!-- /route:review -->

## Read and ask

Preserve `verified`, `qualified-draft` and `blocked-escalated` exactly as returned; `verified` covers only the named
artifact and check scope. Report the warning card and recovery; never turn a refusal into a success.
Ask the person when permissions, task meaning or evidence are missing. Local settings assume a trusted host;
unrestricted same-user shell access defeats that separation.
Use the superpowers skills for software engineering work in general: design and programming in the large as well
as routine coding. They establish a way of working; G6 checks the work they produce. Load a skill offered by
g6_select or g6_find from its installed name or its path inside the G6 package. A skill is never a G6 check or evidence.

- [skills/using-g6/references/lifecycle.md](lifecycle.md)
- [skills/using-g6/references/templates.md](templates.md)

## Registered tools

- `mcp__g6__g6_review_patch`: read its full guidance with g6_describe("g6_review_patch").
- `mcp__g6__g6_review_until_clean`: read its full guidance with g6_describe("g6_review_until_clean").
