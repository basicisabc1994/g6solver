# RC development replay

## Registration (commit before any replay)

This is the development replay in design D, section 4(a). The unit is the bug;
patches from one bug are not independent observations. No result here establishes
benefit on unseen bugs. The lead commits this registration separately, first.
The harness refuses a live replay until this document matches its committed copy.

The frozen G6 commit is the full 40-character commit supplied through
`--registered-commit`, initially `6b8c2567b1b4479e6ad44f8e8f4b4dc0cf8b8e07`.
Every task image must carry exactly that `we.g6.commit` label. Record `--s0 before`
or `--s0 after` explicitly when comparing the S0 change; no exception is inferred
from an outcome. The plan and every saved review retain this commit and image ID.

Predictions and kill criteria below are verbatim from design D 4(a), registered
before any replay. No replay has been run by this builder.

| # | Prediction | Kill criterion |
|---|---|---|
| P1 | Modes 1 and 2 on RC: no integrity alarm on any right patch or on the maintainers' fix, on upstream-fetched repositories | Any alarm: that class is open; repair before any live run |
| P2 | Mode 1 on RC: no "supported" on a wrong patch except through the S0 hole; after S0, none | A "supported" on a wrong patch after S0: the rule has another hole |
| P3 | Mode 2 on RC: `defect_detected` on at most 3 of 130 wrong patches and on 0 of 113 right | More than 2 of 113 right called a defect: R1's default is not safe to ship as it stands |
| P4 | Mode 3: a lead on at least 40% of distinct wrong patches on at least 3 of the 4 neighbouring-case bugs (black-5634, starlette-3718, pydantic-14052, click-3769) | A lead on fewer than 2 of the 4 bugs: tiers 1 and 2 do not reach what they were designed from; stop |
| P5 | Mode 3: a lead on at most 5 of the 113 right patches (4.4%, upper 95% bound just under 10%) | More than 10 of 113: the lifted relations misfire; stop |
| P6 | Mode 3: no lead on black-5521; on click-3678 no lead and at least one question candidate | A lead on most black-5521 patches would be a welcome surprise and is reported as one |
| P7 | Median review time under 120 s for each patch in mode 3 | Over 300 s: the tiers need a tighter exit |

## Inputs and isolation

Pass the source archive as `--source`; set `G6_WE_ARCHIVE` to a new output folder.
Only the source root, `round2`, and `round3` indexes are in scope. Cohort folders
are outside this replay. Paths containing `sealed` or named `final-*.json` are
refused before opening, as are links, junctions and escaping relative paths.
The output root must be new and disjoint from the source root in both directions.
The source archive is never mounted or copied wholesale into a container.

Final patches are deduplicated by bug and SHA-256, ordered by digest. All run
origins (archive round, arm, review round and label source) are retained. Labels
come only from completed, unapplied-error-free `check` records for `final`;
amended `recheck` labels are excluded. Missing or conflicting labels are named
exclusions. No maintainers' check is run or copied. Labels stay on the host.

Agent mode uses each eligible run's latest archived review whose captured patch
matches the final patch, in rounds 1/2 or arms B through H1. Identical patches
retain every such run's arguments, and each is replayed. The distinct patch is
counted once per bug and mode; any successful call's alarm or wrong acceptance
is counted, so another call cannot hide it. Failed calls enter no outcome count
and keep the prediction incomplete. When no review matches, agent mode is named
as unavailable; hook mode still applies. Arguments are preserved except
repository and base, which must refer to the task image.
Model clients are disabled; the original search arguments remain recorded.

Hook mode uses the issue text extracted from the archived prompt, with only
`task`, one `task` requirement with empty checks, the image base and a 600-second
timeout, matching `completion_loop._final_review`. The neighbourhood mode is a
disabled placeholder. Requesting it for execution is refused until implemented.

Only task images are inspected and launched, pinned by image ID. Before applying
the patch, the repository must have the expected upstream parent, matching tree,
one commit, a clean working tree, and no `/oracle` directory. No network or model
is available. Container creation, copy, git, patch, reply and cleanup failures
are saved as failed with result `no review`; they enter no outcome denominator.

## Running and scoring

```text
python validation/worked_examples/replay_rc.py --source <source> --registered-commit <full-commit> --dry
python validation/worked_examples/replay_rc.py --source <source> --registered-commit <full-commit> --s0 before
python validation/worked_examples/replay_rc.py --score <saved-batch-run.json>
python validation/worked_examples/replay_rc.py --score <saved-batch-run.json> --source <source>
```

Dry mode prints cases by bug and mode, image names, and named exclusions; it
does not instantiate Docker, check images, or create the output folder. Live
execution is sequential. The lead checks the 6.5 GB free-memory floor first.
Saved case records, the plan and the registration digest allow plain-code
rescoring without containers or models. A current-rule batch needs no source
archive if its source manifest is well formed, its evaluation matches a fresh
evaluation of its saved cases, exclusions and flags, and its batch start row
matches. The manifest holds relative names and a source identity digest, never
an absolute source root. Scoring reads its start row from the output archive
containing the command-line batch file.

Older, absent or structurally inconsistent rule bindings require an explicit
`--source` to re-derive labels, exclusions and flags. The selected tasks, source
identity, round inventory and every consumed byte must match the saved snapshot;
saved review arguments must also match the freshly derived case. Without that
match, every enabled prediction is incomplete, with no asserted result or kill,
and scoring exits 2. A newer-rule batch is always incomplete with exit 2: older
code never re-derives its evidence. A mode the current rule cannot review also
takes that incomplete route. Registration and image-pin refusals exit 1 before
rule-version handling. Each score prints standard JSON naming the rule version
and whether evidence came from the batch, the source, or was unavailable.
Scoring never opens a source folder or share named inside a batch.

A batch is a local file editable by its owner. These consistency checks do not
protect against deliberate, self-consistent same-user edits to a batch or its
start row. They detect mismatched saved evidence; they do not authenticate its
author. A version/digest test binds the evidence rule's code to its version so
an honest rule change must also raise that version.

JSON text is saved with escapes, preserving even lone surrogate escapes in
captured tool inputs. Dry and live planning use the same evidence enumeration;
such text is serializable in either route. A failed replay-record finalization
is handled by the batch failure path, which saves a failed batch naming the file.

Every count and denominator is retained by bug. Registration thresholds that
mention patch totals are applied to those grouped counts without confidence
claims from treating patches as independent. Failed/missing cases make an
affected prediction incomplete; observed kill criteria remain visible. Before
S0, wrong acceptances are listed as requiring S0 attribution, never assumed to
come through that hole. P1's maintainers'-fix clause stays incomplete because
this final-patch replay does not import or execute the maintainers' fix. Mode 3
predictions remain disabled until mode 3 records exist. The evaluator accepts
saved mode 3 records with neighbourhood case classes `symptom_persists`,
`agreement_lost`, or `kept_behaviour_changed` (their space-separated design
spellings also work) and `question_candidates` in the neighbourhood record.
P7 reports the median of completed review-call durations, with times by bug.
Counts from a partial development archive are displayed with their actual
denominators; the original 243/113/130 design tally is not manufactured.
