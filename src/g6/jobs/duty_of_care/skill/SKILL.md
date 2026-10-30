---
name: g6-duty-of-care
description: Use when an agent works with people who may be in distress (a support chat, a care or clinical intake, a wellbeing check, a carer's notes) and must not miss signs that a vulnerable person is at risk of suicide, self-harm, harming someone else or a child being in danger; or when a PHQ-9 or GAD-7 needs adding up exactly as its form prints it; or when a dated crisis line for a region is needed.
---

# G6 duty of care (duty_of_care)

`duty_of_care` matches a fixed list of English crisis phrases, so a paraphrase or another language passes unseen: a
person in crisis can be missed, and finding no listed phrase never means someone is all right. It is the jobs family's
door to that one trigger for the whole library, a dated and sourced helpline directory, and the PHQ-9 and GAD-7
scorers. A listed phrase in anything the person said gives `blocked-escalated` with `G6_E_DUTY_OF_CARE_CRISIS`: a short
response written to safe-messaging practice and the crisis numbers, nothing else. `ok` is false; show `result`.
A response that breaks one of
its rules is withheld, the numbers still given (`G6_E_DUTY_OF_CARE_RESPONSE_REFUSED`); a check that cannot run is
treated as a possible crisis (`G6_E_DUTY_OF_CARE_CHECK_FAILED`). From then on, every op naming that `run_id` and
`subject` gives the same answer; a call naming another run or subject is not locked. Every other answer is
`qualified-draft` at best. No qualified person has reviewed this component; its rules are checked against their
sources by G6's certificate and an independent reader.

## When to use it

- Before anything else touches a person's words: run `screen` on every field you hold (the message, the form's free
  text, a carer's note), and on your model's own draft before it is shown.
- To add up a PHQ-9 or GAD-7 exactly as the form does, with no band or label added.
- To give someone crisis and helpline numbers with the date each was retrieved.

## When not to use it

- As a risk assessment: it reports words found, never a risk level.
- To diagnose, grade severity or suggest treatment: it does none of these, and no therapy exercises ship.
- For instruments whose licence is not settled (PCL-5, AUDIT, DASS-21, K10, MUST, the Barthel Index): they are
  refused. The BDI-II and the C-SSRS are not shipped at all.
- In place of a person. Every crisis answer goes to a person, and a qualified person interprets every score.

## Ops

- `screen`: every field in `fields` is scanned as whole words for the listed crisis words. A finding gives the crisis
  answer with the directory for `region` and locks `subject` for `run_id`. No finding gives
  `G6_E_DUTY_OF_CARE_NO_LISTED_WORDS`, which says it is not an assessment. On every op, every key and value of the
  call is scanned before the input is validated, so a wrongly shaped call carrying crisis words still gets the crisis
  answer.
- `instruments`: each instrument with its licence state, and why the unsettled ones are refused.
- `score`: `instrument` and `responses` (one per item, in the form's order, each a value the form prints). The total
  is the sum of the item scores, "score computed". Any PHQ-9 item 9 answer other than 0 gives the crisis answer,
  whatever the total.
- `directory`: the crisis and helpline numbers for `region` (`AU`, `US`, `UK`; `GB` reads as `UK`), or every region,
  emergency numbers first, each with its source, retrieval date and age in days.
- `help`: one line per op.

## Alone and composed

Alone over MCP: `g6_invoke("duty_of_care", "screen", '{"fields": {...}, "run_id": "...", "subject": "..."}')`. No op is
sensitive. `screen` and `score` need `run_id` and `subject`
so that the lock has someone to hold; without them a crisis is still answered, but no lock is set and anything else is
refused. From Python: `from g6.jobs._care import crisis, directory, instruments`.

Reading the answer:

- The crisis answer carries `response` (the words for the person), `directory` (its entries, each with `source`,
  `retrieved`, `sha256`, `clause` and `age_days`), `locked`, and evidence naming each category and the position where
  it fired, and each safe-messaging rule the response follows with the clause it cites. Nothing from the call is
  repeated: no key, no value.
- The response follows the World Health Organization's 2023 resource for media professionals and Mindframe's
  guidelines: help first, the region's emergency number first; then two services open at all hours (the second one
  fitting what was said where a held clause supports it) and one online service, fewer only when the directory holds
  fewer, each gap named in the evidence; no act or place described, nothing the person said repeated, no phrase
  Mindframe's language guide marks problematic; that talking helps, and that someone worried about another person can
  call too. When the numbers may have changed, it says so in one line, and so does the card.
- A missing input is `G6_E_DUTY_OF_CARE_MISSING_INPUT`, an answer off the form `G6_E_DUTY_OF_CARE_OUT_OF_RANGE`, an
  unsettled instrument `G6_E_DUTY_OF_CARE_LICENCE_UNSETTLED`, an unknown one `G6_E_DUTY_OF_CARE_UNKNOWN_INSTRUMENT`, a
  region the directory does not cover `G6_E_DUTY_OF_CARE_REGION_NOT_COVERED`. Nothing is filled in with a default.
- Unknown fields are refused by the typed input and never read: a clinician id, an approval, a risk flag or "unlock"
  changes nothing. Crisis words in such a call, or in any wrongly shaped one, still get the crisis answer.

Composed:

- The jobs engine runs `crisis.scan` on the input and on the model's output before any other step, and holds one
  `crisis.CrisisLock` per run; every profession that works with people in distress shares this one trigger.
- Show the crisis `response` and numbers to the person, then hand over to a person who can help.

## Failure modes it catches

- `missed_risk_signs` (an AI missing signs that a vulnerable person is at risk): stated suicidal ideation, self-harm,
  intent to harm someone, or a child in immediate danger, in any field, including a field nobody thought to check and
  the model's own output. A PHQ-9 item 9 answer other than 0 is caught whatever the total. Once caught, the person is
  not dropped: the lock answers every later op for them in the run.

## Blind spots

- `no_finding_read_as_safe` (a screen that found no crisis words read as a sign the person is all right): only the
  listed phrases are caught. Text is normalised first (full-width letters, zero-width
  characters, joined-up spellings), and a phrase split across two fields is read whole. Still missed: other
  misspellings, slang, other languages, lookalike letters NFKC does not fold, meaning without the listed words, and a
  self-harm act stated without the listed words (only "hurt myself" and "harm myself" are listed; method words are never
  listed, by design).
- Known false positives fire on purpose: everyday idioms and accidents ("I hurt myself at the gym", "kill someone for
  a coffee"), talk about the subject ("a lecture on self-harm"), and a form's own question text if you pass it in.
  Two idioms are exceptions, each its own reviewed rule that covers only its own words.
- The lock lives in memory for the run or the server process. Called on its own, `run_id` and `subject` are the
  caller's words, so a call naming another run or subject is not locked; the jobs engine will set both itself, which
  closes this gap.
- Emergency numbers are held for Australia (000), the United States (911, from the United States Code) and the United
  Kingdom (999). For any other region, or none stated, the response names no number: it says to call the local
  emergency number and a local crisis line, and asks which country the person is in. Numbers change: past 180 days
  (G6's own choice), or past a source's own review date, the response says plainly that they may have changed.
- Only two lines fit what was said: Kids Helpline (a child in danger) and 1800RESPECT (someone harmed by another
  person), in Australia, each resting on its own clause.
- One service open at all hours is held for the United States and for the United Kingdom, not two, and no sourced
  online service for the United Kingdom (Samaritans' web chat is a pilot not yet open to everyone). The evidence says
  so; nothing is invented.
- The safe-messaging guidance is written for media; G6 applies it to a one-to-one reply.
- A score is arithmetic only: it says nothing about why someone answered as they did.

## What it needs

Nothing outside G6: plain Python, no extra, no outside program, no network and no model. Its sources are in its
source register.

## Examples

Crisis words in one of two fields: the crisis answer, Australia's emergency number first, and a lock.

```g6-example op=screen
{"fields": {"message": "Most days I think I would be better off dead", "carer_note": "Quiet at lunch"}, "region": "AU", "run_id": "example-run", "subject": "example-person-1"}
```

Text with none of the listed words: `qualified-draft`, saying it is not an assessment.

```g6-example op=screen
{"fields": {"message": "Tired after a long week, sleeping badly"}, "run_id": "example-run", "subject": "example-person-2"}
```

A PHQ-9 added up as its form adds it: total 5, "score computed", no band.

```g6-example op=score
{"instrument": "phq9", "responses": [1, 0, 2, 1, 0, 1, 0, 0, 0], "run_id": "example-run", "subject": "example-person-3"}
```

The instruments and their licence states.

```g6-example op=instruments
{}
```

The United Kingdom's numbers, with their retrieval date and age.

```g6-example op=directory
{"region": "UK"}
```

```g6-example op=help
{}
```

From Python: a second field, a lock with no release, and PHQ-9 item 9.

```python
from g6.jobs._care import crisis, instruments

finding = crisis.scan({"task": "Plan the week", "notes": "She said she wants to end it all"})
assert finding.fired and finding.matches[0].field == "notes"

lock = crisis.CrisisLock()
lock.engage("person-1", {"why": "crisis words in notes"})
assert lock.held("person-1") is not None  # and there is no method that releases it

assert instruments.score("gad7", [0, 1, 1, 0, 2, 0, 1]).total == 5
assert instruments.score("phq9", [0] * 8 + [1]).kind == "crisis"
```

## The four open problems

- **Learning signals**: every finding names the category and where it fired, and every refusal its code. Not
  yet: misses and false alarms a person reports are not recorded, so the recall sets do not learn from them.
- **Evaluators the model cannot influence**: whole-word matching over fixed lists, and arithmetic over the form's own
  values; no model is called, and a caller's flags and approvals are never read.
- **Transfer by failure mode**: one trigger serves every profession that meets people in distress, so a term added
  once guards all of them. Not yet: no record of which professions see which misses.
- **Enough held-out cases**: every term has a firing case and a near miss, and each exception is tested against every
  trigger phrase. Not yet: no held-out set of real conversations, reviewed by a qualified person, measures how many
  people in crisis the words miss.
