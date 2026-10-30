# Owner pilot: Claude Code and Codex (the 0.2.0 launch)

The owner ran the minimal launch's first journey in both clients on 30 October 2026, on Windows 11 with Python 3.12,
from the launch branch at `f08bc96` (the code of `0c44e6d`, whose continuous-integration run passed all nine jobs).
This records the owner's own use and the client-mediated consent. It does not show that unfamiliar people succeed,
that a typed name is the person's, or that G6 improves anyone's results.

## What the owner did

1. Cloned the launch branch, made G6's own environment with `python -m venv --copies .venv`, installed `".[all]"` and
   ran `g6 doctor`.
2. Removed Codex's `g6` entry from the earlier pilot (Claude Code had none), then ran
   `g6 setup --client claude --client codex --allow patch_review.review`.
3. Ran the Windows access grants G6 printed: for G6's own `.venv` before the first run, then, at the first review, for
   the repository and its `.venv`.
4. In a small practice repository (one function, two pytest tests, its own `.venv` with pytest), asked each client for
   a change and for a review with G6 before it said the task was done.
5. Asked each client to call `metaharness_start` with the `labels` example of
   [the workflow guide](../AI_FOR_AI_WORKFLOW.md) and then `metaharness_confirm`, and answered the client's form: no,
   then a cancel (Esc), then yes.

## What happened

| Observation | Claude Code | Codex |
|---|---|---|
| Setup | Registered; skill installed | Registered; setup kept the earlier pilot's skill until the owner replaced it with `g6 skills install --user --client codex --force` (finding 4) |
| Access grants | G6's `.venv` before the first run; the repository and its `.venv` at the first review. The agent offered the grants as `!` commands, which Claude Code runs in Git Bash, where `/grant` becomes a path and fails (finding 7) | None more: the same machine and repository |
| The review | Ran confined (`appcontainer`), 12.2 seconds: supported within scope | Ran confined (`appcontainer`), 6.6 seconds: supported within scope |
| The agent's first call | Refused: a template instance's bindings in the wrong shape; the agent fixed its call | Refused twice: an `intent` in the wrong shape; the agent fixed its call after `g6_help` (finding 6) |
| The report | Shown as G6 wrote it. The agent's two template instances ran and were not refuted, but the report did not name them (finding 2) | Shown as G6 wrote it. It said three tests of kept behaviour "fail at the base commit", but their test file could not load there, so they never ran there (finding 1) |
| The form | The client returned `declined` for no, `cancelled` for Esc | No Decline control; Esc cancels. The owner accepts this as how Codex says no |
| No or cancel | Nothing recorded; the workflow stayed unconfirmed | Nothing recorded |
| Yes | Confirmed the workflow digest the form showed; the typed name is recorded as given, not authenticated | The same |
| After a no | The reply offered the terminal confirm command as the next step (finding 3) | The same reply after a cancel |
| Minutes, confusing wording | Not timed. The grants and the refused first calls were the friction | Not timed. The refused first calls were the friction |

The saved assessments agree with the reports above: in Codex's review, the run against the base commit's code
stopped at `ImportError: cannot import name 'line_count'`, reproduced afterwards with plain pytest.

## Findings, and what happened to each

| # | Finding | What happened |
|---|---|---|
| 1 | A check whose test file could not load at the base commit was reported as failing there, "so they exercise the change" | Fixed in `24917ea`: it is now "could not run at the base commit", with an unknown for its file |
| 2 | The report did not name the agent's own proposed checks and template instances, and only counted the unknowns | Fixed in `24917ea`: the Tested step names each with its result, "which decide nothing", and the unknowns are listed |
| 3 | After a no or a cancel in the form, the reply offered the terminal confirm command | Fixed in `24917ea`: the reply tells the agent to ask the person and never offer another way; the command stays only for a client with no form |
| 4 | Setup kept an earlier install's copy of its own skill, so Codex followed old instructions | Fixed in `24917ea`: setup replaces it and says so |
| 5 | The meta-harness keeps a workflow's files in the project's `.g6/` folder, and a later patch review counted them as part of the change | The README and the workflow guide now say to start the meta-harness from a folder outside any repository you review. Git-ignoring `.g6/` is not advised: G6 would then judge its own `main.py` as an ignored file that could replace a tracked module of that name (the fresh review of these fixes). Leaving `.g6/` out of a review is for after launch, with its own security review |
| 6 | Both agents' first review calls were refused for optional inputs in the wrong shape | The skill now starts a review from the folder, the task and the requirements (`24917ea`). Not re-tested with agents |
| 7 | G6 prints each access grant as two commands on one line, and does not say to run them in PowerShell or cmd | After launch: the text is in a file the certificates bind, so it waits for the reissue |
| 8 | `g6 doctor` does not say whether patch review is allowed | After launch |
| 9 | Codex's form has no Decline | The client's; the owner accepts it |
| 10 | An agent can run `g6 workflow inspect` itself, and nothing technical stops it running the terminal confirm under a typed name | A stated limit: local settings assume a trusted host, and a recorded name is not authenticated |

The fixes are verified by tests written first; the pilot was not run a second time on them.

Share observations without credentials or personal information. This pilot is the owner's own; it does not meet the
broader trial with unfamiliar people, which the minimal launch set aside.
