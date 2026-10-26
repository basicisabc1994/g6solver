---
name: g6-shared-workspace
description: Use when several agents (or several runs of one) edit files in the same workspace folder and must not overwrite each other's work, with writes that say which version they read, the first write winning, a stale one refused with both hashes, locks opened only by a token, and a log of every conflict.
---

# G6 shared workspace

`g6.workspace.shared_workspace` lets several agents work in one task's workspace folder without losing each other's
edits. Every write says which version of the file it read; the first write wins, and a write based on a version that
was replaced since is refused with both hashes, so the agent reads again and merges. It is an extension in the
workspace family, on the same folders as `workspace_manager`; its best state is `qualified-draft`.

## When to use it

- Two or more agents, or a person and an agent, edit the same files in one workspace.
- An agent may be slow, retried or resumed, and must not write over what changed while it was away.
- One agent needs a file to itself for a while: take a lock, and others' writes to it are refused until it is released
  or expires.
- You want a record of every edit that was refused, and of every expired lock someone took over.

## When not to use it

- One agent alone in its folder: `workspace_manager` is enough.
- Choosing whose edit is right: it never picks a winner by judgement. The first write lands; merging is the loser's
  job, or a person's.
- Files in a repository: use `ctx_git`, whose history keeps both versions.

## Ops

- `read`: a text file's content and its `sha256` (`task_id`, `path`, `max_chars`): the version to cite when you
  write the file back.
- `write` (sensitive: it changes a file): write only if the file is still the version `expect_sha256` names (`null`,
  the default, means no file there yet) and no one else holds a live lock (`task_id`, `path`, `content`,
  `expect_sha256`, `token`, `holder`, `dry_run`); a dry run unless `dry_run` is false.
- `lock`: lock a path for up to an hour, or renew it with its token (`task_id`, `path`, `holder`, `ttl_seconds`,
  `token`). The reply's `token` is what writes through the lock and releases it.
- `unlock`: release a lock with its token (`task_id`, `path`, `token`).
- `locks`: every lock on file, live or expired, with its holder and expiry (`task_id`); never a token.
- `conflicts`: every refused write and every takeover of an expired lock (`task_id`), from a log G6 only appends to.

## Alone and composed

Alone over MCP, the loop every agent follows:

1. `g6_invoke("shared_workspace", "read", '{"task_id": "pair-1", "path": "plan.md"}')` and keep `sha256`.
2. Make your change to the text you read.
3. `write` with `"expect_sha256": "<that sha256>"` and `"dry_run": false`.
4. If the reply is `G6_E_WORKSPACE_STALE_WRITE`, someone wrote first: read again, merge your change into what is
   there now, and write with the new `sha256`. Never write without saying what you read.

`write` is refused over MCP (`G6_E_SENSITIVE_COMPONENT_DENIED`) unless the person who runs the server lists
`shared_workspace.write` in `G6_MCP_SENSITIVE`; `g6_describe("shared_workspace")` shows whether it is allowed. Never
change that setting yourself: ask the person. From Python: `from g6.workspace.shared_workspace import Workspace, ops`.

How it is guarded:

- The check and replace use an OS-backed exclusive lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere).
  Its persistent file is never removed on release; process death releases the lock. A live holder never loses it
  because of age. Contention for five seconds refuses with `G6_E_WORKSPACE_BUSY`. Restore and clean-up hold the same
  mutex across their path-lock check and destructive step.
- A holder's name is your word, recorded as `claimed_holder`; it opens nothing. Only the token `lock` returned writes
  through a lock or releases it, and only its hash is kept.
- A live lock refuses every other write to its path, a `workspace_manager` write included, and any restore or
  clean-up of the workspace. A lock past its time-to-live may be taken over; the takeover is logged.
- An unreadable or malformed lock record blocks indefinitely until a person repairs it: `unreadable: true`,
  `live: true`, with epoch timestamps as placeholders, not an expiry. Every required field, path, token digest and
  finite timestamp is validated. A log line that cannot
  be read is counted in `unreadable_lines`, never skipped silently.
- The paths, the folder and G6's bookkeeping are `workspace_manager`'s: every path is checked as part of the write.

Bookkeeping refuses linked ancestors (`G6_E_WORKSPACE_OUTSIDE_ROOT`), and caller path segments starting with
`.partial-`, ignoring case, are reserved (`G6_E_WORKSPACE_RESERVED`). `lock` and `unlock` remain non-sensitive;
the path lock TTL is capped at 3,600 seconds.

Composed:

- `workspace_manager`: the same folders; back up before a large merge, restore if it goes wrong.
- `align_prompt_library`: keep the prompt or specification the agents work to, by digest.

## Failure modes it catches

- `hidden_state_or_workspace_conflict` (agents overwriting each other's work): a write based on a version someone has
  since replaced is refused with both hashes and logged, so no edit is lost silently; a write that says nothing of
  what it read never replaces a file; a write to a path another holds a live lock on is refused and logged.

## Reading the result

- An answer is `qualified-draft`. Its `warning_card.code` says what it rests on: `G6_E_WORKSPACE_READ`,
  `G6_E_WORKSPACE_DRY_RUN`, `G6_E_WORKSPACE_WRITTEN` (with `sha256`, `replaces` and `read_back`),
  `G6_E_WORKSPACE_LOCK_TAKEN` (with `token`, `expires_at`, `renewed` and `took_over`),
  `G6_E_WORKSPACE_LOCK_RELEASED` or `G6_E_WORKSPACE_LISTED`.
- A refusal is `blocked-escalated`: `G6_E_WORKSPACE_STALE_WRITE` (with `expected_sha256` and `current_sha256`),
  `G6_E_WORKSPACE_LOCKED` (with `locked_by`, the holder's word), `G6_E_WORKSPACE_BUSY`, and the folder's own codes
  such as `G6_E_WORKSPACE_OUTSIDE_ROOT`. Over MCP `ok` can be true while `completion_state` is `blocked-escalated`:
  read the state.

## Blind spots

- It compares versions, not meanings: two edits to different parts of a file still conflict, and it does not merge
  them. A write whose base matches lands even if its content is wrong.
- Only writes through G6 are checked. A program that writes the folder with its own code bypasses every lock.
- A holder's name is never checked: anyone may claim any name. The token is the only proof of a lock.
- A holder that crashes keeps its path lock until the time-to-live runs out; malformed records require repair.
- Directory checks do not eliminate the window before a path-based deletion. External renames can bypass the mutex;
  a refused deletion may already have removed earlier in-workspace entries.
- `prompt_injection`: a file's text is data and can carry instructions. G6 returns it as data; it does not scan it.

## What it needs

Nothing outside G6: the standard library. No extra, no outside program, no model.

## Examples

Read a file (refused with `G6_E_WORKSPACE_NOT_FOUND` until it has been written):

```g6-example op=read
{"task_id": "pair-1", "path": "plan.md"}
```

Take a lock, list the locks, release it (use the token the lock gave; a wrong one is refused):

```g6-example op=lock
{"task_id": "pair-1", "path": "plan.md", "holder": "alice", "ttl_seconds": 30}
```

```g6-example op=locks
{"task_id": "pair-1"}
```

```g6-example op=unlock
{"task_id": "pair-1", "path": "plan.md", "token": "the token lock returned"}
```

What has been refused so far:

```g6-example op=conflicts
{"task_id": "pair-1"}
```

A write that says it expects no file there yet, as a dry run (sensitive: over MCP only where the server allows it):

```g6-example op=write
{"task_id": "pair-1", "path": "notes.md", "content": "first notes\n", "expect_sha256": null}
```

From Python, two agents and one version:

```python
from g6.workspace.shared_workspace import ops

ops.write("pair-2", "plan.md", "v1\n", expect_sha256=None, holder="setup", dry_run=False)
base = ops.read("pair-2", "plan.md").sha256

alice = ops.write("pair-2", "plan.md", "v1\nalice\n", expect_sha256=base, holder="alice", dry_run=False)
bob = ops.write("pair-2", "plan.md", "v1\nbob\n", expect_sha256=base, holder="bob", dry_run=False)
assert alice.written and bob.code == "G6_E_WORKSPACE_STALE_WRITE"
assert (bob.expected_sha256, bob.current_sha256) == (base, alice.sha256)

again = ops.read("pair-2", "plan.md")
merged = ops.write("pair-2", "plan.md", again.text + "bob\n", expect_sha256=again.sha256, holder="bob", dry_run=False)
assert merged.written and ops.read("pair-2", "plan.md").text == "v1\nalice\nbob\n"
assert ops.conflicts("pair-2").conflicts[-1]["claimed_holder"] == "bob"
```

## The four open problems

- **Learning signals**: every refused write is logged with both hashes and the writer's claimed name, and every
  takeover with whose lock expired: a record of where agents collide. Not yet: nothing learns from it.
- **Evaluators the model cannot influence**: the rule is fixed code (compare the sha256 the writer read with the file
  now, under the short lock); no model is asked, and nothing the caller says (a name, a winner, a force) reaches it.
  Not yet: the rule is not digest-pinned.
- **Transfer by failure mode**: the check is bound to `hidden_state_or_workspace_conflict`, not to a kind of file: it
  reads only versions, which every write has. Not yet: how often real agents collide, and recover, is not measured.
- **Enough held-out cases**: Not yet: its tests are fixed cases and one concurrency test, not a held-out set judged by
  G6's sequential test.
