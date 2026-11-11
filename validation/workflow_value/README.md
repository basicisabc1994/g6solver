# Measuring the workflow view: the protocol

This folder holds how section 17 of the 0.3 design (the workflow record and what G6 reports from it) is measured.
Nothing here is a result about developers yet. Each row below is `evidence_pending` until its gate has run on real
work.

## The order

Each step is built on the one before, and is switched on only after its gate has been run and recorded:

1. **G2, the narrow view.** The block after the report (stated outcome, one obstacle with a receipt or "observation
   is insufficient", one next check, G6's own cost) against the existing report alone.
2. **G3, diagnosis.** Hypotheses only, against the narrow view.
3. **G4, suggestions.** At most three, with decisions the person makes, against the diagnosis view.
4. **G5, the optional feedback question and the value row.** The whole admitted flow, with invited outside developers.

A gate that fails sends its step back to repair. A gate with too little evidence holds the next step. A threshold is
never changed after results are seen; a repaired step is measured again under a fresh frozen manifest.

## What is in this folder

- `manifests/G2.json` to `G5.json`: each gate's decision manifest. It states the cases, the labels, the unit, every
  eligible start, the baseline and comparison, the agent, information and budget policy, the accepted outcome, the
  preservation checks, the clocks, what happens to failed, abandoned, missing and censored starts, disclosures, costs,
  and every threshold with its value, unit, denominator, sample requirement and uncertainty rule. The values are the
  ones adopted from the measurement research, each named `S17.<gate>.<name>`. Before a gate runs, its manifest is
  frozen again with the start date; its digest is what the results are bound to.
- `run_contracts.py`: runs the local deterministic contracts (the fixtures under
  `tests/workflow/fixtures/contracts/`) and compares each case's output with its frozen expected output. Every case
  must match, with zero unsupported definite claims and zero unauthorised disclosures. Its `simulate` function puts
  synthetic sessions through the view and measures what the view adds (lines, build time, questions).
- `at_mapping.json`: the acceptance tests AT-001 to AT-010 of G6-UX-VALUE-001, each bound to the owner's text by
  digest and to the tests and fixtures that check it. AT-003, 004, 005, 007 and 008 are in 0.3.0's scope. The other
  five are bound for the part 0.3.0 builds and are never reported as accepted.
- `decisions.json`: one entry per gate run: the manifest's digest, the row, the results and the decision. The view's
  admitted level in code must equal what this file admits.

## Rules a run follows

- The task is the unit. G6 assigns the arm. Every start counts: failed, abandoned, skipped and unmarked starts stay
  in every denominator.
- Nobody works without their coding agent or without G6's mandatory report. The arms differ only in the optional
  block.
- No gate has a time-saved bar, and no self-reported saving is recorded as a measurement.
- Each row needs a usefulness minimum above zero, so refusing everything cannot pass.
- Simulated sessions run first. They can fail a gate. They can never pass usefulness: a row decided on simulated
  results alone is `hold`.
- Fixture success is not a developer-value result. It admits only the tested behaviour.
- A developer's data stays on their machine. What they send back is one export they have opened and read.

## What is owed

| Gate | Decided by | Smallest sample | State |
|---|---|---|---|
| G2 | the owner's own real work | 20 marked tasks | `evidence_pending`: simulated pre-flight recorded, `hold` |
| G3 | the owner's own real work, blind review with one Codex reviewer | 40 tasks, 20 hypotheses shown | `evidence_pending` |
| G4 | the owner's own real work | 30 tasks, 15 suggestions shown | `evidence_pending` |
| G5 | 3 to 5 invited outside developers | 40 tasks, 20 in the candidate arm; 10 invitations | `evidence_pending` |

Results from the owner's own work are labelled "builder's own work, one developer". Each row is published with its
result, its costs and its limits, whatever it shows.

## Running the contracts

```
python validation/workflow_value/run_contracts.py
```

It prints the counts and each mismatch, and exits 1 when any case does not match. It works in a scratch state folder
of its own, with no model and no network.
