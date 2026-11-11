# Context Engine, row 04: usefulness for the behaviour question (protocol)

Frozen before the code it measures (design 12.8, row "Usefulness for A"; plan slice 3 for the first half). A change to
a threshold or a suite retires this protocol.

## What is measured

Whether a cited passage beside one behaviour-question candidate ever does harm, before any claim that it helps:

- **false suppression**: a candidate marked settled by a passage when no normative grant covers this repository, this
  source version and this scenario, or when a conflict is unresolved;
- **induced wrong patch**: on the one cited candidate, a patch that follows the passage and contradicts what the
  person confirmed;
- **missed ambiguity, unnecessary questions, person time and questions saved**: measured against direct search only
  on A's frozen base-only cohort, which section 7's owner runs later. They are not measured in slice 3.

## Two halves

1. **One cited candidate (slice 3).** The declared suites below. No person and no model is needed: the suites drive
   the support records, the designation and the suppression rule directly.
2. **A's frozen base-only cohort.** Blind labels for false suppression, missed ambiguity, wrong patches, unnecessary
   questions and person time. Owed: it needs the frozen cohort and people. Recorded as `evidence_pending`.

## Unit and denominator

First half: one test case of the declared suites; every collected case stays in the denominator.

## Declared suites (first half)

- `tests/knowledge/test_support.py`
- `tests/harness/test_behaviour_support.py`

## How a source becomes normative

Only through the behaviour form. A passage found for a candidate is offered as the form's rule line; when the person
confirms the rule, the answer the question store holds becomes a normative grant for this repository, this source
version and this scenario. This spends the task's one question. No argument, sharing grant or access dialog makes a
grant. When the question is spent the passage stays evidence without authority.

## Versions and environment

As row 01, plus the behaviour-question slice merged at `claude/question-slice-followup` bb0ebc8.

## Costs recorded

Wall-clock seconds per suite; zero model calls in the first half; the second half records person time and questions.

## Thresholds

```json
{
  "row": "04-usefulness-for-a",
  "frozen": "2026-11-11",
  "unit": "test case of the declared suites (first half); task of A's frozen cohort (second half)",
  "thresholds": {
    "false_suppressions": 0,
    "induced_wrong_patches_on_the_cited_candidate": 0,
    "grants_made_from_an_argument": 0,
    "cases_failed_or_errored": 0
  },
  "second_half": "evidence_pending: A's frozen base-only cohort, blind labels, run by section 7's owner",
  "admission": "every threshold met on the first half admits attaching a passage to a candidate; nothing about saved time or questions is claimed until the second half is run"
}
```
