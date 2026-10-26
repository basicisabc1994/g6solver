---
name: g6-epistemic-integrity
description: Use when a conclusion is about to be stated or acted on and its confidence must match its evidence, such as a recommendation someone will act on, a health, legal, security or financial claim, an irreversible step, a percentage without its baseline, causal language, or several sources that may all repeat one origin.
---

# G6 epistemic integrity

Deterministic claim adjudication. It splits a message into atomic claims, types each one, grades the supporting
evidence you declare (E0 to E3) by its sources and lineages, after collapsing sources that share a lineage, caps
confidence by that grade, and applies a risk-by-evidence gate. No model runs, so tone and insistence cannot move it.

## When to use it

- Before stating a conclusion someone will act on: a recommendation to a client, a health, legal, security or
  financial claim, a premise for an irreversible step. Call `adjudicate` with the matching `action_criticality`.
- Several sources say the same thing and you are not sure they are independent: give each record its
  `independence_group`.
- An answer has a percentage with no denominator, a vague word such as "safe" or "better" with no scope (checked at
  R2 and R3), or causal language ("because", "leads to").

## When not to use it

- Finding which source sentence backs a claim, or catching a wrong figure against a document: `grounding`. This
  component reads the evidence records you declare, not the text of sources.
- Checking a coding agent's "done": `coding_agent_gate.verify_answer`.
- Before an agent action that rests on a premise: declare it in `action_gating`'s `factual_preconditions`; the gate
  runs this grading for you.
- Low-stakes or creative text: nothing (`action_criticality: "creative"` gives `ALLOW_WITH_LABEL` at R0; a
  percentage with no baseline is still `ASK`).

## Ops

- `adjudicate`: adjudicate a `message` against `evidence` at an `action_criticality`; each claim's type, risk tier, evidence grade, status, confidence and disposition, and the most restrictive disposition overall.

## Reading the result

`overall_disposition` is `ALLOW`, `ALLOW_WITH_LABEL`, `ASK`, `ESCALATE` or `BLOCK`, the most restrictive across the
claims. `overall_status` is `ASSERTED`, `CONTESTED`, `CLARIFICATION_REQUIRED`, `REFUTED` or `UNVERIFIABLE`. Each entry in
`claims` gives `claim_type`, `risk_level`, `evidence_grade`, `epistemic_status`, `confidence` (the grade's ceiling:
E0 0.2, E1 0.5, E2 0.8, E3 1.0, and at most 0.5 when contested), `disposition` and `triggered` (the checks that
fired).

- Risk: `action_criticality` `creative` or `opinion` is R0; empty, `info` or `low` is R1; `reversible`,
  `decision`, `medium` or `operational` is R2; `irreversible`, `external`, `high`, `safety`, `health`, `legal`,
  `security`, `financial` or `critical` is R3; any other word is R2.
- Grade: only records that support the claim (`supports_or_opposes` `supports` or `mixed`) count; opposing and
  `context_only` records never raise it. E0 is no supporting evidence, or only `model_assertion`; E1 one lineage; E2
  a primary source (`source_type` `primary`, `canonical`, `experiment`, `direct_observation` or `dataset`) or two
  independent lineages; E3 two independent lineages with a primary source among them, or two lineages of
  `authority: "high"`.
- Gate: R0 is `ALLOW_WITH_LABEL`; R1 needs E1, else `ASK`; R2 needs E2 and no conflict, else `ESCALATE`; R3 needs E3,
  `has_human_approval` and `has_rollback`, else `BLOCK`; with a conflict, `ESCALATE`.
- Stance: credible opposition (a non-model `opposes` record) with no support makes the claim `REFUTED` and `BLOCK`
  at every tier, whatever approval and rollback say; support and opposition together make it `CONTESTED`, with
  confidence at most 0.5.
- An evidence record takes `source_uri_or_artifact_id` (or `source` or `uri`), `source_type`, `supports_or_opposes`
  (`supports`, `opposes`, `mixed` or `context_only`; a record that gives none supports the claim), `independence_group`,
  `authority` and the other fields of `g6.core.evidence.EvidenceRecord`. A key a record does not take, or a stance
  outside those four, is refused with `G6_E_EPISTEMIC_INTEGRITY_EVIDENCE_INVALID`, never dropped, so a misspelt
  `stance` cannot turn opposition into support; `EvidenceRecord` refuses such a stance in Python too. A parameter the
  op does not take is refused with `G6_E_INPUT_VALIDATION`, and an empty `message` with
  `G6_E_EPISTEMIC_INTEGRITY_MESSAGE_REQUIRED`.

## Alone and composed

Alone, over MCP: `g6_invoke("epistemic_integrity", "adjudicate", params_json)`. From Python:
`from g6.epistemic_integrity import adjudicate` with `EvidenceRecord` from `g6.core.evidence`.

Composed:

- `action_gating` grades each declared factual precondition with this floor's lineage collapse, grading, conflict
  and refutation checks, against the minimum grade `g6.core.risk_policy` sets for the action's tier. An unsupported
  premise blocks an irreversible, external, real-world or high-risk action, sends an unknown-risk one to a person,
  and adds only a notice to a low-risk one; a contested or refuted premise goes to a person at any lower tier.
- `grounding` finds the passages; declare each source that supports the claim as an evidence record, with one
  `independence_group` per real lineage (`grounding.corroboration` groups sources by web host or corpus).

## Failure modes it catches

- `false_confidence`: `adjudicate` flags unnegated certainty words on E0/E1 claims with
  `G6_E_CERTAINTY_WEAK_EVIDENCE`, tightening disposition to at least `ASK`. A marker-check failure blocks with
  `G6_E_MARKER_CHECK_FAILED`. Python's `markers(text)` returns exact spans per category with local negation flags,
  never an alignment score. Each adjudicated claim exposes these spans relative to that claim's text.

- `epistemic_indifference` (a claim made with no regard for whether the evidence supports it): confidence is capped
  by the evidence grade, with no override; a claim must meet its tier's minimum grade or it is `ASK`, `ESCALATE` or
  `BLOCK`; evidence against a claim never counts for it, and a claim only opposed is `REFUTED`; a percentage without
  a baseline, and at R2 and R3 a vague evaluative without scope, go to `ASK`; causal language without E2 evidence is
  labelled; model output on its own is E0.
- `repetition_as_corroboration` (the same claim repeated, counted as more evidence): records that share an
  `independence_group` are one lineage, and records that declare no group are pooled into one, so ten copies of one
  press release grade like one source.

## Blind spots

- Marker matching covers a small English lexicon. Three-word local negation can miss quotations, irony and distant
  qualifiers. Agreement and confident wording earn no score; refusal is never penalised. No model judge runs.

- The evidence records are declared, including `source_type`, `authority`, `independence_group` and
  `supports_or_opposes`: two copies of one source labelled as independent grade E2, and a record labelled
  `supports` counts as support whatever it says.
- `has_human_approval` and `has_rollback` are the caller's word.
- Claim typing and the numeric and scope checks are word lists ("because", "%", "per"): a rephrased claim slips past.
- The message's evidence is bound to every atomic claim; it cannot tell which source backs which sentence.
- It declares no failure mode of its own.

## What it needs

Nothing outside G6: pure Python, no model.

## Examples

```g6-example op=adjudicate
{"message": "The migration is safe to run on the customer database.", "action_criticality": "irreversible", "evidence": [{"source_uri_or_artifact_id": "blog-a", "source_type": "secondary"}, {"source_uri_or_artifact_id": "blog-b", "source_type": "secondary"}]}
```

The reply is `BLOCK`: an irreversible claim needs E3, the two blog posts declare no lineage so they count as one
(E1), and "safe" has no stated scope (`CLARIFICATION_REQUIRED`).

```python
from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicate

claim = "The new filter removes lead from tap water."
copies = (EvidenceRecord("press-release.html", independence_group="vendor"),
          EvidenceRecord("news-site-a.html", independence_group="vendor"),
          EvidenceRecord("news-site-b.html", independence_group="vendor"))
result = adjudicate(claim, action_criticality="health", evidence_records=copies)
first = result.claims[0].claim
assert first.evidence_grade == "E1" and first.confidence == 0.5  # three copies of one origin count once
assert result.overall_disposition == "BLOCK"  # a health claim needs E3, approval and a rollback

independent = (EvidenceRecord("lab-report.pdf", source_type="primary", independence_group="lab", authority="high"),
               EvidenceRecord("regulator-test.pdf", source_type="primary", independence_group="regulator",
                              authority="high"))
graded = adjudicate(claim, action_criticality="health", evidence_records=independent,
                    has_human_approval=True, has_rollback=True)
assert graded.claims[0].claim.evidence_grade == "E3" and graded.overall_disposition == "ALLOW"
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing. Each claim carries its disposition and the `triggered` checks, a
  labelled record a caller can keep; inside `action_gating`, when the gate audits the action (not low risk), an
  unsupported premise shows in its audit record as the reason code `G6_E_FACTUAL_PRECONDITION_UNSUPPORTED`.
- **Evaluators the model cannot influence**: the floor is fixed code with no model call, the confidence ceiling has
  no override, and the gate matrix is code, read from no file. The caller declares the evidence it grades. Not yet:
  the code is not digest-pinned.
- **Transfer by failure mode**: it is bound to `epistemic_indifference` and `repetition_as_corroboration`, and it
  reads the shape of the evidence, not the subject, so the same grading applies to clinical, legal and engineering
  claims. Not yet: its catch rate on unseen task families is not measured.
- **Enough held-out cases**: Not yet: its catch and false-alarm rates are not judged by G6's sequential test; the
  tests are fixed boundary cases.
