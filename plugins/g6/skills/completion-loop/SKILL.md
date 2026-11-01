---
name: completion-loop
description: Use when G6's completion loop sends a reviewer's finding or failing probes back at the end of a turn in a Python repository, or when someone asks what that loop did or what its report means.
---

# G6's completion loop

When you finish a Python task, G6 may stop the end of your turn and send back a test that fails on your code. Which loop
sent it depends on the person's key file. Only the person sets these lines. Do not set them, or tell them one loop is better,
unless they ask.

## The review-until-clean loop

With `G6_LOOP_CRITIC=codex` in the key file, a reviewer from another model family reads your patch without seeing your
reasoning, and states a rule, reasons and a test. If the test fails on your code, the three come back to you. A reviewer can
be wrong: this is one careful reader's view, so check their expectation against the task and the repository before you act.

- If the task or the repository supports it, repair the cause, rerun your own checks and finish. A fresh review reads the
  new patch. Earlier reviewers' tests keep running.
- If not, leave the code as it was and say why in one line. The loop ends, the finding stays open in the report, and your
  reason goes with it.

It ends after three repairs at most, when a review reproduces nothing, or when a review fails. This loop has been measured on
seven training bugs only.

Without the plugin's hook, call `g6_review_until_clean` when you finish, with the same session name and task for each step,
and poll `metaharness_job`. Pass your one line as `reason` when you leave the code as it was: only that rejects a finding.

## The scouts' loop

`G6_LOOP_MODE=scouts`, or no critic and `G6_LLM_PROVIDER=claude_code`, runs this loop. Scouts wrote probes from the task and
the base commit, without seeing your change. You can read one scout's work, at the folder the message names. The others are
hidden from you on purpose.

By default the scouts run when you stop, and only if the session changed Python. This is a different, cheaper loop from the
one the worked examples measured, because your first attempt comes before the scouts' work.

With `G6_LOOP_SCOUTS=before_fix` in the person's key file, the scouts run before you start: of the scouts' two timings, the
configuration with measurements behind it. You then begin with G6's method and a scout's work, the behaviours and input
shapes a complete fix must handle, with probes. Read them before you edit.

The report says which ran.

### When a message comes back

Take each failure in turn.

- A case that passed before your change and fails now is a regression. Repair it, unless the task asks for that change.
- A case that failed before and still fails is one careful reader's view of what the task requires. Repair the cause if
  the task or the repository supports it. If not, leave that case alone and say why in one line.

A reviewer from another model family may add a test. They can be wrong too, so check their expectation against the task
and the repository before you act on it.

Then rerun your own checks and finish. G6 tests the code again. If you leave the code as it was, the loop ends and the
failures stay in the report.

## What not to do

- Do not look for the hidden probes, in G6's folders or anywhere else. They are worth something only if you have not read
  them.
- Do not run the scouts' probes yourself. A model wrote them, so G6 runs them confined, with no network.
- Do not weaken a test or remove a feature to quiet a failure. The review G6 runs at the end looks for that.

## What the report means

Show the person the report as it is. Merging is their decision.

The review-until-clean report leads with what was found, what was fixed and what stayed open. A review that reproduces
nothing is not evidence the work is right, only that this reviewer found no failure it could show.

"No counterexample found in N probes" says only that those probes did not refute the patch. It does not show the patch is
right.

A model wrote these tests, so none of them passes a requirement, and the review's outcome is at most
`insufficient_evidence` unless the repository's own checks say more.
