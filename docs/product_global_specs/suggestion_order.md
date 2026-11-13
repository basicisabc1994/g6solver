# The order of suggestions

Rule version: `suggestion-order.v1`. A change to this order is a new version.

After a review's report G6 may offer suggestions. They are optional, and there are never more than three. Zero is a
valid number. A finding in the report is not a suggestion: it is always shown, and no decision about a suggestion can
hide it.

## What may be suggested

Only an action that applies to this task, is supported by what G6 recorded, and is one of these kinds:

| Kind | What it offers |
|---|---|
| `run_reproduction` | run the failing check yourself |
| `answer_pending_question` | answer the pending question in your client's dialog |
| `state_a_check_for_requirement` | state a check for a requirement that has none |
| `rerun_at_base` | run the failing check at the base commit |
| `grant_source` | decide whether to share the source the reviewer needs |
| `fix_setup` | change the one thing a refusal names, then run the review once |
| `reduce_g6_steps` | switch off one of G6's optional steps where the server is registered |
| `observe_more` | let G6 observe what is missing before anything is changed |
| `do_nothing` | do nothing: no action is supported by what G6 observed |

Several of these are things you do without G6. They are ordered by the same rule as G6's own steps: no kind is
preferred.

A suggestion never changes a setting, runs anything, reaches outside the machine or weakens a safeguard. When G6
itself is in the way, the only step it suggests on itself is switching off one of its optional features
(`G6_WORKFLOW_VIEW=off`, `G6_WORKFLOW_RECORD=off`, `G6_QUESTIONS_PER_TASK=0`, `G6_FEEDBACK_QUESTION=off`). You set the
switch; G6 changes nothing. It never suggests switching off consent, the confinement boundary, the review, a stop or
an approval.

## The order

1. **Class 1:** backed by a receipt and addresses the stated outcome. Without a receipt that ties it to the outcome
   you stated, an action is not in this class, however much it might speed something up.
2. **Class 2:** a cheap, reversible check that tells the hypothesis's explanations apart.
3. **Class 3:** nothing above is supported, so this only says what is missing or to leave things as they are.

Within a class:

- first an action whose burden G6 has measured in this project and found lower;
- then one whose burden it has measured;
- then one whose burden is unknown, which the suggestion says in those words.

What remains is ordered by the suggestion's identity, a digest. The first three are shown. Each says which class it
is in and why.

G6 does not guess minutes saved, does not score suggestions, and does not attach a probability to a hypothesis. No
model produces any number used here.

## A suggestion's identity

A suggestion is identified by its kind, its target and its scope (this task, or the project), and by nothing else.
Its wording, the review job it came from, its receipts, the evidence behind it and the version of this rule are not
part of its identity. That is what a decision is kept under.

## Your decisions

You adopt, dismiss or reopen a suggestion in your client's own dialog, or by a line in `corrections.json`. No tool
argument decides for you, and no reason is asked for. Declining the dialog, cancelling it, or a client that cannot
show it, decides nothing.

A decision is written before G6 acknowledges it. A dismissal is for this task's text unless you choose the project.
It holds across calls, retries, restarts, a reworded suggestion, new receipts and a new version of this rule. G6
offers a dismissed suggestion again only when:

- you reopened it;
- an expiry you chose has passed;
- the task's stated outcome was replaced (the dismissal form says so before you dismiss);
- 365 days have passed, the storage policy's period for decisions (the form says that too).

When it does, it says which of these is the reason.
