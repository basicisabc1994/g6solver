---
name: g6-workspace-manager
description: Use when an agent's task needs a folder of its own to keep files in, and every read and write must stay inside it, with '..', symbolic links, junctions, hard links and network paths refused as part of the act, and backups, restores and clean-ups that change nothing until asked.
---

# G6 workspace manager

`g6.workspace.workspace_manager` gives each task its own workspace folder, `workspaces/<task_id>` under the G6 home,
and checks every path an agent names as part of the read or write itself. A path is joined onto the folder and
resolved, `..`, symbolic links and junctions followed; one that would leave the folder is refused. It is an extension
in the workspace family; its best state is `qualified-draft`.

## When to use it

- An agent's task needs scratch files, drafts or intermediate results kept apart from every other task's.
- You want a write that cannot land outside the task's folder, whatever path the agent was given or made up.
- You want a backup before a risky change, and a restore that can itself be undone.
- You want to know what a workspace holds: each file's size and SHA-256, whether it holds a `.git` folder.

## When not to use it

- Several agents editing the same files: use `shared_workspace`, whose writes say which version they replace.
- Versioned prompts, specifications or evaluation sets: use `align_prompt_library`.
- Files in a repository the person declared: use `ctx_git`.
- Running code in the folder: a workspace keeps files; it runs nothing. Use the sandbox.

## Ops

- `contained`: whether `path` stays inside the task's workspace once resolved, and where (`task_id`, `path`);
  nothing is read or written.
- `read`: a text file's content (`task_id`, `path`, `max_chars`), cut at `max_chars` and said so, with the SHA-256
  of the whole file.
- `files`: each plain file's path, bytes and SHA-256 (`task_id`), and the links skipped, never followed.
- `status`: whether the workspace exists, its files and bytes, whether it holds `.git` (looked at, never asked), and
  its backups (`task_id`).
- `write` (sensitive: it changes files): write a text file (`task_id`, `path`, `content`, `dry_run`); a dry run
  unless `dry_run` is false.
- `backup` (sensitive): a gzip tar archive of the plain files in the workspace's backups folder (`task_id`,
  `dry_run`).
- `restore` (sensitive): put a backup's files back (`task_id`, `backup`, `dry_run`); every member is checked first,
  and what it replaces is backed up.
- `cleanup` (sensitive): remove the task's workspace folder and its backups (`task_id`, `dry_run`); it cannot be
  undone.

## Alone and composed

Alone over MCP: `g6_invoke("workspace_manager", "read", '{"task_id": "report-7", "path": "notes/plan.md"}')`. The
four ops that change files are refused over MCP (`G6_E_SENSITIVE_COMPONENT_DENIED`) unless the person who runs the
server lists them in `G6_MCP_SENSITIVE` (for example `workspace_manager.write`); `g6_describe("workspace_manager")`
shows which are allowed. Never change that setting yourself: ask the person. Even when allowed, each is a dry run
unless you pass `"dry_run": false`. From Python: `from g6.workspace.workspace_manager import Workspace, ops`;
`Workspace(root)` takes a folder of your own choosing, `Workspace.for_task(task_id)` the task's.

How it is guarded:

- The folder is the task's, never a shared temporary folder and never one the caller names: there is no field for
  one, nor for a confirmation, an approval or whether git is there.
- Every path goes through `g6.core.storage.contained`: a relative path is joined onto the folder, never onto the
  folder G6 runs in; network and device paths (`\\host\share`, `\\?\`), `:` (a drive or a stream) and device names
  (`CON`, `nul.txt`) are refused before anything is resolved; G6's own `.g6-workspace` folder is never a caller's path.
- A write makes its file beside the target, resolves that file's folder again once it is open, and only then puts it
  in place in one step: a folder swapped for a link after the check is caught, and a hard link to a file elsewhere is
  replaced, never written through. A read refuses a hard link and checks that the file it opened is the one it checked.
- A restore takes only a backup kept in the workspace. Every member is checked with `tarfile`'s `data` filter before
  anything changes (no absolute name, no `..` out, no link out, no device), extraction goes to a folder of its own, and
  the files it replaces are backed up first, so a restore can be undone.
- Clean-up removes only a folder G6 made a workspace (its marker is there) and removes a link itself, never what it
  points at.
- Bookkeeping is resolved through the shared containment check with linked ancestors refused
  (`G6_E_WORKSPACE_OUTSIDE_ROOT`). Caller paths cannot use any segment starting with `.partial-`, ignoring case
  (`G6_E_WORKSPACE_RESERVED`); accepted files count towards the quota.
- A backup compares the opened file's device and inode with its enumerated identity before reading its bytes;
  a mismatch is `G6_E_WORKSPACE_NOT_A_FILE` and no archive is published.
- Restore normalises archive destinations and refuses bookkeeping or reserved temporary paths before changing files
  (`G6_E_WORKSPACE_ARCHIVE_REFUSED`). Restore and clean-up hold the OS-backed mutex from the lock check through deletion.
- Deletion checks each opened directory's device and inode and refuses symbolic links, junctions or mismatches
  before using its child names (`G6_E_WORKSPACE_OUTSIDE_ROOT`). Link entries themselves are removed without entering them.
- Malformed path locks block writes, restore and clean-up indefinitely (`G6_E_WORKSPACE_LOCKED`) until a person repairs
  them. Their listing has `unreadable: true`, `live: true` and epoch timestamps as placeholders, not an expiry.
- Limits: 500 MB a workspace, 20 MB a file, 20,000 members an archive.

Composed:

- `shared_workspace`: the same folders, with writes that must say which version they replace, and locks.
- `align_prompt_library`: keep the prompt or specification a task used, by digest, beside its files.
- `guards`: scan text read from a file for instructions hidden in it before acting on anything it says.

## Failure modes it catches

- `write_outside_workspace` (an agent writing files outside its workspace): a path that leaves the folder through
  `..`, an absolute path, a symbolic link, a junction or a folder swapped for a link after the check is refused, and
  nothing outside changes; a hard link is never read or written through; an archive member that would land outside is
  refused before anything is extracted.

## Reading the result

- An answer is `qualified-draft`. Its `warning_card.code` says what it rests on: `G6_E_WORKSPACE_RESOLVED`,
  `G6_E_WORKSPACE_READ`, `G6_E_WORKSPACE_LISTED`, `G6_E_WORKSPACE_DRY_RUN`, `G6_E_WORKSPACE_WRITTEN` (with
  `sha256`, `replaces` and `read_back`), `G6_E_WORKSPACE_BACKED_UP`, `G6_E_WORKSPACE_RESTORED` (with
  `previous_backup`) or `G6_E_WORKSPACE_REMOVED`.
- A refusal is `blocked-escalated` with a code such as `G6_E_WORKSPACE_OUTSIDE_ROOT`, `G6_E_WORKSPACE_RESERVED`,
  `G6_E_WORKSPACE_NOT_A_FILE`, `G6_E_WORKSPACE_NOT_FOUND`, `G6_E_WORKSPACE_ARCHIVE_REFUSED`,
  `G6_E_WORKSPACE_NOT_A_WORKSPACE` or `G6_E_RESOURCE_LIMIT`. Over MCP `ok` can be true while `completion_state` is
  `blocked-escalated`: read the state.
- No reply names a folder on the machine: paths are relative to the workspace.

## Blind spots

- It guards only the reads and writes that go through it. A program run in the folder, or another component writing
  with its own code, is not checked.
- Between the second check and the final rename there is still a moment in which a folder could be swapped; a process
  that can do that already writes in the G6 home.
- Deletion still has a window between the final directory/ancestor identity check and each path-based child deletion
  or removal. An external process can rename directories in that window; the mutex coordinates G6 callers only.
  Deletion is not a transaction: earlier in-workspace removals are not undone after a refusal.
- A backup holds plain files only: links are skipped (and named), and empty folders are not kept.
- It does not run `git init`; a `.git` folder is reported if one is there.
- `prompt_injection`: a file's text is data and can carry instructions. G6 returns it as data; it does not scan it.

## What it needs

Nothing outside G6: the standard library (`tarfile`, `hashlib`). No extra, no outside program, no model.

## Examples

Whether a path stays inside (nothing is read or written):

```g6-example op=contained
{"task_id": "report-7", "path": "notes/../plan.md"}
```

What the workspace holds:

```g6-example op=status
{"task_id": "report-7"}
```

```g6-example op=files
{"task_id": "report-7"}
```

A read of a file not written yet is refused with `G6_E_WORKSPACE_NOT_FOUND`:

```g6-example op=read
{"task_id": "report-7", "path": "notes/plan.md"}
```

A write, as a dry run (sensitive: over MCP only where the server allows it):

```g6-example op=write
{"task_id": "report-7", "path": "notes/plan.md", "content": "# Plan\n"}
```

From Python, a write, a refused escape, a backup and a restore:

```python
from g6.workspace.workspace_manager import Workspace, WorkspaceRefused, ops

ws = Workspace.for_task("report-7")
done = ws.write("notes/plan.md", "# Plan\n", dry_run=False)
assert done["written"] and done["read_back"] == done["sha256"]

try:
    ws.write("../another-task/plan.md", "overwritten", dry_run=False)
except WorkspaceRefused as refused:
    assert refused.code == "G6_E_WORKSPACE_OUTSIDE_ROOT"

saved = ops.backup("report-7", dry_run=False).backup
ws.write("notes/plan.md", "# A worse plan\n", dry_run=False)
back = ops.restore("report-7", saved, dry_run=False)
assert back.restored and ws.read("notes/plan.md") == b"# Plan\n" and back.previous_backup
assert ops.cleanup("report-7").removed is False                    # a dry run: nothing removed
```

## The four open problems

- **Learning signals**: every refusal names its code and the path asked for, and every write its SHA-256, so a run's
  record shows which escapes were tried. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the checks are fixed code with no model call, and nothing the caller
  says (a folder, a confirmation, whether git is there) reaches them. Not yet: the checks are not digest-pinned.
- **Transfer by failure mode**: the check is bound to `write_outside_workspace`, not to a task: it reads only where a
  path really goes, which every write has. Not yet: its catch rate on paths written by real agents is not measured.
- **Enough held-out cases**: Not yet: its tests are fixed adversarial cases, not a held-out set judged by G6's
  sequential test.
