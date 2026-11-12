---
name: g6-workflow
description: Use when a person asks what G6 recorded about its own review work in their repository, wants to look at, correct or delete that record, or asks what is holding a task up.
---

# The workflow record

G6 can keep a local record of its own review activity in a repository: the runs it made for a task, their rounds,
the questions it asked, its tool calls and their times. From that record G6 reports the person's stated outcome,
an obstacle it has a receipt for (or that observation is insufficient), one next check the person may run, and what
G6 itself cost on the task.

## What it is and is not

- It is the person's data, kept on their computer under `$G6_HOME/workflow/<project-id>/`. It is off until the
  person sets `G6_WORKFLOW_RECORD=on` where the G6 server is registered. You cannot turn it on.
- It records G6's own activity only. Work done outside G6 is unknown to it, and G6 says so.
- An entry holds codes, counts, times and digests. It holds no task text, code or answers.
- G6 reports an obstacle with a receipt, a hypothesis, or "unknown". It never says it found the constraint on the
  person's work, and neither should you.

## Rules for you, the agent

- Never answer a dialog G6 opens. A decision, a deletion or a feedback answer comes from the person, in their
  client's dialog or in a file they write. No argument you pass can stand in for it.
- If a reply says results were withheld, the person has not shared the workflow record with your provider. Tell
  them so in one sentence. Do not ask again, and do not look for the files yourself.
- Do not read `inspection.json` or `corrections.json` back to the person, and do not edit them: they are for the
  person to open.
- The optional block after a review report decides nothing. Show the report first and whole.
- A suggestion is the person's to adopt or dismiss. You may relay it; you may not decide it, and you do not bring a
  dismissed one back. A finding in the report is not a suggestion: it stays whatever was dismissed.

## The tools

- `g6_workflow_export`: call it when the person asks what G6 recorded. It writes `inspection.json` and says where.
- `g6_workflow_correct`: call it after the person says they wrote `corrections.json`. It takes no correction from you.
- `g6_workflow_delete`: call it when the person asks to delete a run, a task or the project's record. They confirm
  in their client's dialog.
- `g6_suggestion_decide`: call it when the person wants to adopt, dismiss or reopen a suggestion G6 showed. They
  choose in their client's dialog. It answers only once suggestions are switched on.
- `g6_feedback`: pass on, in `volunteered`, feedback the person volunteers about G6. With none G6 may ask them one
  question in their client's dialog, at most once a week and only if they opted in; you never ask it or answer it.

## Files the person opens

In the project folder under `$G6_HOME/workflow/`:

- `inspection.json`: what G6 recorded, written when the person asks to look. It expires after 30 days.
- `corrections.json`: written by the person to annotate or supersede an entry. G6 reads it when asked and reports
  problems by line or entry number.

The storage policy (what is kept, for how long, and what deletion does) is
`docs/product_global_specs/workflow_storage_policy.md`.
