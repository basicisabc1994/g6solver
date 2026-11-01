# G6 for Claude Code

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

Coding agents say a task is finished when the code handles the example they were given. This plugin tests that claim. When
Claude Code finishes a Python task, a reviewer from another model family reads the patch and must show a failure, not just
name one. G6 sends what it shows back to the agent, has the repair read again until a review reproduces nothing or the stop
rule fires, and then tells you what was found, what was fixed and what stayed open.

## Install

G6 must be installed for the `python` that Claude Code finds on its `PATH`. Start Claude Code with G6's virtual
environment active. If `python` cannot import G6, the hooks fail with a notice and your turn ends as usual.

```
/plugin marketplace add basicisabc1994/g6solver
/plugin install g6@g6solver
```

The plugin registers G6's MCP server, which refuses sensitive operations until you allow them: installing the plugin allows
nothing. The completion loop needs no such allowance. To let the agent call `g6_review_patch` itself, start Claude Code with
this in its environment:

```
G6_MCP_SENSITIVE=patch_review.review
```

Or run `g6 setup --client claude --allow patch_review.review`, which registers G6 with that allowance. You then have two G6
servers, and only that one allows the review. To keep one, use `/plugin` to disable this plugin's server, or `claude mcp
remove --scope user g6` to remove the other.

## Turn it on

The loop spends your Codex sign-in, so it does nothing until you approve that. Add this line to your key file, `~/.g6/.env`:

```
G6_LOOP_CRITIC=codex
```

Lines set only in an agent's environment do not count: an agent's environment is not your approval.

With that line, the review-until-clean loop runs. With no such line, and `G6_LLM_PROVIDER=claude_code`, the scouts' loop
below runs instead. With neither, nothing is tested, and the report says which line to add.

## What happens

1. What you ask before G6 first tests the work is the task, and the commit you are on at your first prompt is the base.
   Uncommitted work you had then counts as part of the change.
2. When the agent finishes and Python files have changed, a reviewer from another model family reads the task, the patch and
   the files it touches, as they were at the base. It sees none of the agent's reasoning. It must state a rule, its reasons
   and a test, and it may find nothing.
3. G6 runs that test on the patch, inside an operating-system boundary, with no network. A test that will not run goes back
   to the reviewer once, with the error. If it still will not run, the loop ends with no clean review: a defect was claimed
   and could not be reproduced.
4. If the test fails, the rule, the reasons and the test go back to the agent in the same session. The agent may reject the
   finding: the reviewer can be wrong. A fresh review then reads the new patch, at most three repairs and so four reviews.
   Each review's test stays and keeps running, to catch a repair that undoes it. If the agent changes nothing and ends on a
   question, that rejects nothing: the turn ends so you can answer, and the loop goes on when the agent next stops.
5. The loop ends when a review reproduces nothing, when the agent leaves the code as it was, when the repairs run out, or when
   a review fails or cannot be read. That last one is reported as no clean review, and never counts as clean. Neither does a
   review of a patch that changed after it was read.
6. The turn ends and you see the report, which is also saved beside the session's state.

If no hook saw your prompt, the task is read from the session's transcript and the base is the commit you are on then.

A new prompt after a loop has ended arms a new one, on the commit you are on at that prompt. If that is the same commit, the
earlier task stays in it, as the patch still holds that work, and a patch a loop has already come to a result on is not read
again, so a "thanks" spends nothing. On a new commit it is read again, even if the diff is the same, as its parent is not.
After a review that failed the next stop tries again. If a turn ends with nothing to test and the commit then moves, your
next prompt starts on the new commit. Wherever a loop did not run, you get one line saying what did not run and why.

## What the report says, and what it does not

The report starts with how the run ended, in one word: `clean`, when a review of this patch reproduced nothing and no
reviewer's test failed on it; `fixed`, when each test that failed now passes and a fresh review reproduced nothing;
`contested`, when a test fails and the agent left the code as it was; `open`, when a test that failed has not been shown to
pass; `untested`, when nothing tested this patch to a result. A tool's reply gives the same word as `ending`.

Then come the reproduced findings: what was found, what was fixed, and what stayed open. A finding is fixed only when
the case that failed has passed: a skip or a rename fixes nothing. It is open when the repairs ran out with its test still
failing, or when the agent rejected it, with the agent's reason if it gave one. On the training bugs an open finding did not
always mean a wrong patch, so read it as a question for you.

A review that reproduces nothing is not evidence the work is right, only that this reviewer found no failure it could show. A
model wrote the tests, so none of them passes a requirement, and they are never given to the patch review as evidence.

Merging stays your decision.

## What has been measured

This loop is the worked examples' arm K, and it has been measured on seven training bugs only. It has no measurement on a bug
kept out of training. It also differs from what was measured:

- The reviewer here receives a text pack with no tools: the task, the patch, and the files the patch touches as they were at
  the base, up to 120,000 characters. Only those files may be cut: a task and patch too long to send whole get no review, and
  so no clean review. The measured reviewer read a folder holding the whole repository.
- The Stop hook replays the rounds: each hook is a new process that reads what is done from a file, where the measured runner
  held the session itself. A review repeats if a hook is cut short.
- G6 runs each review's test alone, so one that will not run hides no other. The measured runner ran them together.
- A test that will not run goes back to its reviewer once, and a defect no test could show ends with no clean review. The
  measured runner said nothing of such a test, and counted that review as clean.
- Earlier reviewers' tests run even when the latest review wrote none, and a failing one goes back in its own reviewer's words.
  The measured runner ran them only with a new test, and sent the latest reviewer's words.
- The agent has this plugin and G6's server. The measured agent was plain Claude Code. A patch review of the task's text sits
  below the report, and the loop has a two-hour limit.

## From another client

The Stop hook is Claude Code's. Any other client, or an agent without the plugin, can run the same loop one step at a time
with `g6_review_until_clean`, which needs the same `patch_review.review` allowance as `g6_review_patch`. It takes the
repository, the task, a session name and the base commit, and returns a job to poll with `metaharness_job`. The result says
continue, with the text for the agent, or stop, with the report. The first call fixes the session's repository, task and
base, and a call for another repository or task is refused. A session takes one step at a time: a call that finds another
running does nothing, and says wait. Over this tool only a stated reason rejects a finding, so a client that repeats a call
gets the same finding back, and nothing is spent. A reason that ends on a question rejects nothing, and also says wait.

## The scouts' loop

`G6_LOOP_MODE=scouts` in your key file keeps the older loop, even with a critic set. So does having no critic and this line,
which approves your Claude Code subscription:

```
G6_LLM_PROVIDER=claude_code
```

Three scouts read the task and a scratch copy of the base. None can see the change, and none has a shell. Each writes tests of
the behaviour the task requires, and G6 runs those tests on that copy, inside an operating-system boundary, with no network. A
set of tests that exercised nothing is left out. G6 then runs the sets on the agent's code. Failures go back to it in the same
session, at most three times, the first time with G6's method and one scout's work. The other two sets stay hidden from it. The
loop ends sooner if nothing fails, or if the agent leaves the code as it was.

By default the scouts wait until the agent stops, and run only if the session changed Python. Nothing holds your prompt, and
a session that changes no Python spends nothing. This is a different, cheaper loop from the one the worked examples measured:
the agent makes its first attempt without the scouts' work, so no measurement of ours applies to it.

`G6_LOOP_SCOUTS=before_fix` in your key file runs the scouts before the agent starts. Of the scouts' two timings, this is the
configuration with measurements behind it, and its cost is plain: it holds the agent's first turn for as long as the scouts
take, at most 40 minutes, and it spends at each session's first prompt, whatever the prompt asks, in any repository that holds
Python. Only that prompt reaches the scouts. If they cannot run first (no boundary, no approval, their time ran out), the agent
starts anyway, the scouts still to run work on a copy of the base when it stops, and the report says what did not happen.

```
G6_LOOP_SCOUTS=before_fix
```

The report says which ran: when the scouts did not run first, it says so under "Not the loop the worked examples measured".

"No counterexample found in N probes" means those probes did not refute the patch. It does not show the patch is right. A
model wrote the probes, so none of them passes a requirement, and they are never given to the review as evidence.

Even with `G6_LOOP_SCOUTS=before_fix` this loop differs from the worked examples' arm L9 in two ways: G6 runs the scouts' tests
for them, as they have no shell, and the reviewer reads the patch and the changed files as text, with no tools.

## Limits

The hooks are a convenience, not a security boundary. Anything the agent can run with your account can turn them off,
change G6's state, or read G6's files.

Hidden probes live outside the repository, in G6's state folder, and the agent is never told where. That protects them from
the agent reading them by accident or habit, not against code running as the same user. The reviewers' tests live there too,
and a failing one goes back to the agent.

Without an operating-system boundary, G6 refuses to run model-written tests, asks no model, and says so. `g6 doctor` says
what your computer needs.

The loop tests Python changes only.
