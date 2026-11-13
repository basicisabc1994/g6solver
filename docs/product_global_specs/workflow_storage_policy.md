# Workflow record: storage policy

Policy version: `workflow-storage.v1`. A change to any period or rule below is a new version.

## What this covers

When the workflow record is on, G6 keeps a record of its own review activity in a repository: which runs it made
for a task, their rounds, the questions it asked, its tool calls and their times, and later your decisions about its
suggestions and any feedback you give. It is your data. It is kept on this computer, under
`$G6_HOME/workflow/<project-id>/`, and nowhere else.

The record is **off until you turn it on**: set `G6_WORKFLOW_RECORD=on` where the G6 server is registered.
`G6_WORKFLOW_RECORD=off`, or any other value, keeps it off. An agent cannot set it. While it is off, G6 adds nothing
to any part of the record, feedback included; you can still look at and delete what is already held, and it still
expires.

An entry holds codes, counts, seconds, ids and digests. It holds no task text, no code, no patch, no test output and
no answer text; it points to the review or question G6 already keeps by id and digest. A note you write in a
correction is kept as you wrote it, at most 200 characters.

A project is one repository folder as G6 was given it. The same folder written with another letter case or a
trailing separator is the same project. A second worktree is a second project: its record, its dismissals and its
feedback cooldown are its own.

## How long each part is kept

A period runs from the newest entry in a file. A chained file is removed whole once its newest entry is past its
period; an older entry in a file still in use is not read once it is past its period.

| What | Kept for | Where, under the project folder |
|---|---|---|
| `events`: what G6 observed in a run | 180 days | `tasks/<task-id>/runs/<run-id>.jsonl` |
| `annotations`: your corrections | 180 days | `tasks/<task-id>/annotations.jsonl` |
| `decisions`: suggestions you adopted, dismissed or reopened | 365 days | `decisions.jsonl`, `tasks/<task-id>/decisions.jsonl` |
| `feedback`: opt-in, invitations and what you answered | 180 days | `feedback.jsonl` |
| `exports`: the inspection file | 30 days | `inspection.json` |
| `receipts`: that something was deleted or expired | 365 days | `receipts.jsonl` |

Beside a file G6 may keep a `.head` (the last entry's hash), a `.lock` and, for a moment while writing, a `.part`.
Each is removed with the file it stands beside.

`corrections.json` is yours: you write it, G6 reads it when asked and never writes it. G6 removes it only when you
delete the whole project record.

A dismissal lapses when its record expires, 365 days after it was made. The dismissal form says so.

## Looking, correcting, deleting

All three are done by local code, with no model and no network.

- **Look.** G6 writes `inspection.json` in the project folder: every task, its runs in order, each entry with where
  it came from (observed by G6, confirmed by you, a label someone attributed, or derived by a rule), and repeated
  rounds marked as rework. Open it with any editor.
- **Correct.** Write `corrections.json` in the same folder and ask G6 to apply it. A correction is added beside the
  entry it speaks of, as an annotation or as a supersession. The entry itself is never changed, and a confirmed
  answer is never changed this way. A file G6 cannot read changes nothing; G6 says which line or entry is wrong and
  never repeats the file's content.
- **Delete.** One run, one task, or the whole project record. Deletion removes those files, every entry about that
  run or task in the record's other files (your corrections of it, decisions and feedback kept for that task), and
  the inspection file made from them. It leaves one receipt. Your `corrections.json` is not changed unless you delete
  the whole project record.

## The receipt

A deletion or an expiry leaves one line in `receipts.jsonl` with these fields and no others: `policy_version`,
`scope` (run, task or project), `scope_id_sha256` (a digest of the id, not the id), `removed` (how many files, and
entries taken out of files that stay),
`at`, and `reason` (`deletion_requested` or `expired`). A receipt cannot name a task's text.

## What deletion does not do

- It does not remove what G6 keeps under other rules: saved reviews (`reviews/`), the questions G6 asked and your
  answers (`questions/`), loop session folders, and the sharing ledger (`cloud_consent/`). Those are kept until you
  remove them or their own tool does. Stores that other parts of G6 add are listed here as they ship.
- It does not recall anything already shared with a provider you granted. Revoking a grant stops later sharing.

## Sharing

Nothing in the record is sent to a model by G6. A reply to your coding agent that would carry anything from the
record is checked first: without your grant for the source `workflow:<project-id>` and that agent's provider, the
reply is withheld and the record stays local. A grant for the repository does not cover the workflow record, and a
grant for the workflow record does not cover feedback (`workflow-feedback:<project-id>`). The inspection file is
always written locally, grant or no grant, so you can look without sharing.
