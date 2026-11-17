## Deliver a coding task with G6

For a Python task, work in this order (each step in full: `lifecycle.md`).

1. **Intent.** Write the goals as numbered `requirements`, `{"id": "R1", "text": ..., "checks": [...]}`, each check a
   pytest node id.
2. **Risks.** Map each of this project's risks to a check: a repository test, a new acceptance test, or a template in
   `template_instances`: `example.v1`, `relation.v1`, `access_rule.v1`, `differential.v1`, `forbidden_dependency.v1`
   or `state_invariant.v1` (bindings and one valid instance each: `templates.md`).
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
   When a G6 reply carries repair, follow its step; if by is person, tell the person and stop.
7. **Honesty.** Never call a patch `verified`, and never merge for the person. The result rests on your own tests:
   do not rewrite them to change it. The report's not-established list is the to-do list.

Loop: in Claude Code with G6's plugin, its Stop hook runs the review-until-clean loop when you finish; elsewhere,
call `g6_review_until_clean` (`lifecycle.md`).
