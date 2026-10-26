# Round 1: the default system prompt (development, not a validation)

Plan part B, Task 3: before method v0 is frozen, does G6 as built (B) lose to one direct call (A) on any meta-train
workflow's dev split, and would another default system prompt fix that? llama3.1:8b, temperature 0, seed 20261019,
the 20 dev cases of each workflow (`default_prompt.json`).

| System prompt | GSM8K | MASSIVE | SciFact | MBPP | ARC | banking77 | Pooled |
|---|---|---|---|---|---|---|---|
| A, one direct call (today's default) | 3 | 17 | 15 | 8 | 19 | 16 | 78/120 |
| B, today's "Do the task. Reply with the answer only." | 3 | 17 | 15 | 8 | 19 | 16 | 78/120 |
| B, "Do the task." | 19 | 18 | 14 | 8 | 15 | 16 | 90/120 |
| B, "Do the task. End with the final answer on its own line." | 18 | 18 | 14 | 8 | 6 | 16 | 80/120 |

**Decision: keep today's default.** B never loses to A (the plan's rule for changing it), and neither alternative is
better on every workflow: each frees the model to reason, which lifts GSM8K from 3 to 18-19 of 20, but costs ARC (19
to 15 or 6) and SciFact (15 to 14). Which instructions suit a task is what the meta-harness tunes per workflow; GSM8K's
3 of 20 is the clearest dev target in this round (recovery proposes a working-out prompt for wrong numbers).

Twenty cases per workflow: a development signal, not a measurement.
