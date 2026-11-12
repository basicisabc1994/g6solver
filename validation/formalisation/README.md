# Measuring section 15: from intent to a formal property

This folder is the measurement protocol of design section 15.7 for the fragment `smt.relation.int_bool.v1`. It
holds the instrument and the frozen manifests. It holds no result: no row has been run.

## What is here

- `manifest.py`: a row's manifest (fragments, cases, label authors, adapter and renderer versions, environment,
  thresholds, pass/stop rule). `freeze` writes one once and refuses to overwrite. `retire` records a cohort that
  informed tuning; `confirmatory` refuses it.
- `mutations.py`: deterministic mutations (boundary, omitted guard, constant, result/input swap, domain). A mutant
  is labelled wrong only with a witness from the case's exhaustive table; an equivalent mutant is never wrong.
- `cards.py`: a row's card. Every count has its denominator; unsupported, unrun, timed-out and unavailable items
  are not detections; a zero denominator is "unavailable", never a pass.
- `run_rows.py`: runs one row on one frozen manifest and writes `card.md`, `card.json` and `receipts.json`.
- `fixtures/smt_dev.json`: the development cohort (4 cases), frozen for the proposal row.
- `fixtures/smt_final.json`, `smt_final_rendering.json`, `smt_final_correspondence.json`,
  `smt_final_archive_defects.json`: the final cohort (8 cases), frozen once per row with that row's bars.

## Who wrote the labels

Each case's requirement, reference formula and table were written by the section's builder, a model of the
Anthropic family; each table was computed by a Python expression written apart from the formula. That is allowed
for these four rows because their candidates are G6's deterministic mutations, not a model's drafts. It is not
allowed for a row whose candidates a model of that family drafts: one model never writes both the candidate and
its labels, and `manifest.problems` refuses such a manifest. Those rows need cases whose labels a person or
another family wrote.

## Running a row

    python validation/formalisation/run_rows.py validation/formalisation/fixtures/smt_dev.json <out folder>
    python validation/formalisation/run_rows.py validation/formalisation/fixtures/smt_final.json <out folder> --confirm

Use the development cohort while changing code. Run a final manifest once, with `--confirm`. If a final cohort
informs any change, retire it and write a fresh one. A failed bar means repair and a fresh final cohort, never a
narrower denominator.

## What is owed

| Row | State | Owed |
|---|---|---|
| 1 Proposal fidelity and check detection | evidence_pending | one confirmatory run of `smt_final.json`; with model drafts, a cohort labelled by a person or another family |
| 2 Rendering and person acceptance | evidence_pending | one run of `smt_final_rendering.json` (deterministic half); then the owner's blind sitting: 40 renderings, about 60 minutes |
| 3 Observation and code correspondence | evidence_pending | one confirmatory run of `smt_final_correspondence.json` |
| 4 Archive defects | evidence_pending | one run of `smt_final_archive_defects.json`; its Lean, TLC and Curry-Howard groups stay unrun until those fragments are built |
| 5 Questions from disagreement | evidence_pending | the owner's blind sitting: 20 questions, about 30 minutes |
| 6 Cost and completion | evidence_pending | model drafts through the consent check, on a frozen manifest |