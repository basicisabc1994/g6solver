# G6 for Claude Code

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

Coding agents say a task is finished when the code handles the example they were given. This plugin tests that claim. When
Claude Code finishes a Python task, G6 tests the work with checks the agent never saw, sends failures back until the work
holds or the stop rule fires, and then tells you what was tested and what remains unknown.

## Install

G6 must be installed for the `python` that Claude Code finds on its `PATH`. Start Claude Code with G6's virtual
environment active. If `python` cannot import G6, the hooks fail with a notice and your turn ends as usual.

```
/plugin marketplace add basicisabc1994/g6solver
/plugin install g6@g6solver
```

The plugin registers G6's MCP server. If you already ran `g6 setup --client claude`, keep one of the two: `claude mcp
remove --scope user g6` removes the other. The plugin's server allows `patch_review.review`, so installing it is your
approval for patch reviews.

## Turn it on

The loop spends your Claude Code subscription, so it does nothing until you approve that. Add this line to your key file,
`~/.g6/.env`:

```
G6_LLM_PROVIDER=claude_code
```

An optional line adds a reviewer from another model family, on your Codex sign-in:

```
G6_LOOP_CRITIC=codex
```

Scouts run at each session's first prompt, whatever it asks, in any repository that holds Python. To keep them until the
agent stops, which spends only on work that changed Python but is not the loop the worked examples measured:

```
G6_LOOP_SCOUTS=at_stop
```

Lines set only in an agent's environment do not count: an agent's environment is not your approval.

## What happens

1. At your first prompt, before the agent starts, three scouts read your request and a scratch copy of the repository at the
   commit you are on. None has a shell. Each writes tests of the behaviour the request requires. This holds the agent's
   first turn for as long as they take, at most 40 minutes.
2. G6 runs those tests on that copy, inside an operating-system boundary, with no network. A set of tests that exercised
   nothing is left out.
3. The agent starts with G6's method and one scout's work. The other two sets stay hidden from it.
4. When it finishes and Python files have changed, G6 runs the sets on its code. Failures go back to it in the same session,
   at most three times. The loop ends sooner if nothing fails, or if the agent leaves the code as it was.
5. The turn ends and you see the report, which is also saved beside the session's state.

If the scouts cannot run first (no boundary, no approval, their time ran out), the agent starts anyway. The scouts still to
run work on a copy of the same commit when the agent stops, and the report says what did not happen. If no hook saw your
prompt, the task is read from the session's transcript and the base is the commit you are on then.

Anything you ask before G6 first tests the work adds to the task, but only your first prompt reaches the scouts. Uncommitted
work you had at your first prompt counts as part of the change. The loop runs once per session: start a new session for a
new task.

## What the report says, and what it does not

The report says what was tested, what still fails, and what is not established. "No counterexample found in N probes"
means those probes did not refute the patch. It does not show the patch is right. A model wrote the probes, so none of
them passes a requirement, and they are never given to the review as evidence.

Merging stays your decision.

## Limits

The hooks are a convenience, not a security boundary. Anything the agent can run with your account can turn them off,
change G6's state, or read G6's files.

Hidden probes live outside the repository, in G6's state folder, and the agent is never told where. That protects them from
the agent reading them by accident or habit, not against code running as the same user.

Without an operating-system boundary, G6 refuses to run model-written tests, asks no model, and says so. `g6 doctor` says
what your computer needs.

The loop tests Python changes only. It differs from the worked examples' arm L9 in two ways: G6 runs the scouts' tests for
them, as they have no shell, and the reviewer reads the patch and the changed files as text, with no tools.
