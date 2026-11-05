# Required G6 integration and release gates

This is a **release-blocker checklist**, not a claim of passed certification. All items below are open until independently demonstrated in the G6 repository and recorded with resolvable artifact references. A checkmark requires a frozen evaluator to witness the exact committed bytes, not a model or this document to say that it passed.

## Gate A — Intent fidelity and professional scope

- [ ] Define at least one concrete initial professional domain, risk envelope, jurisdiction and named owner of the acceptance standard.
- [ ] Resolve authoritative source bytes, edition, licence and canonical reference; pin and revalidate standards over time.
- [ ] Bind every material natural-language requirement to typed claims, explicit assumptions and a reviewer-approved model.
- [ ] Test on adversarial ambiguity where a valid SMT result about the wrong model must **not** certify the original request.
- [ ] Supply qualified independent domain reviewers and verify their credentials for R2/R3 cases.

## Gate B — True evaluator independence

- [ ] Build an evaluator process/container not writable by the candidate, including test source, reference answers, secrets, standard profile, controls and freeze manifests.
- [ ] Use external signing/key custody and authenticated immutable artifact storage, not unkeyed JSON digests as authenticity.
- [ ] Ensure candidate execution cannot inspect hidden holdouts, examiner keys, negative controls or mutate their source.
- [ ] Register exact trusted executable verification oracles; replay them against the exact final candidate bytes.
- [ ] Red-team candidate attempts to pass forged `completion_state=verified`, fabricated reviewer IDs, dummy tests and false evidence refs.

## Gate C — Reliability envelope and certificate issuance

- [ ] Connect candidate dossiers to `g6.core.component_certificate` and G6's independent reviewer/certificate process, respecting its signed scoped source requirements.
- [ ] Add a **genuinely trusted** G6 door rechecker for this new scoped artifact type (or keep all outputs at qualified draft). At the inspected repository state, G6's door recheckers only include the integrity component; a new package declaration alone cannot promote results.
- [ ] Make the door recompute the independent test against the pinned artifact and caller-held criterion, and detect a planted wrong answer.
- [ ] Verify source authenticity, duplicate evidence dependence, reviewer identity, scope and timestamp at the authority boundary.
- [ ] Confirm weakly evidenced/failed/advisory/missing cases cannot reach `verified` via MCP, CLI, Python, bundles, or the new component.

## Gate D — Meta-selector scientific validity

- [ ] Freeze training/evaluation cohorts, versions and cost accounting before any method ranking experiment.
- [ ] Authenticate `EvaluatedTrial` from a real evaluator (do not use a lambda accepting all records).
- [ ] Assess selection bias with paired comparisons on identical cases or a preregistered randomised allocation policy; report propensity and uncertainty.
- [ ] Compare against fixed-policy G6, LLM-only and no-meta baselines under equal budget and frozen acceptance checks.
- [ ] Show held-out improvement in **independently validated** task value, with uncertainty intervals; include negative/failed runs.
- [ ] Check domain shift, adversarial claims, hidden standards and the sensitivity of ranking to cost estimates.

## Gate E — Self-improvement and protected controls

- [ ] Reuse `g6.core.failure_modes` canonical codes; register new failure codes before claiming they are mitigated.
- [ ] Feed an adaptation proposal through actual `g6.core.adaptation_gate`, plus its protected-path check and external-review process.
- [ ] Require premortem → observed root-cause signature → candidate guard → replay → negative controls → paired heldout → shadow observation.
- [ ] Apply preregistered multiplicity/sample-size controls and **no critical regressions**.
- [ ] Independently review any modification to G6's own certificate-bound core files; reject changes that weaken validation or test provenance.
- [ ] Owner-only approval must remain necessary for merge, promotion, release and live actuation; have tested recall and rollback.

## Gate F — Operational and documentary assurance

- [ ] Export a signed, machine-readable dossier and a human-readable certificate containing all failures, exclusions, sources, cost, versions and reviewer limitations.
- [ ] Reproduce evidence and checks on a separate machine using only published permitted artifacts.
- [ ] Test opt-out, permissions, privacy, licence, confidential source redaction, retention and full traceability.
- [ ] Confirm the new component loads through G6's real installed-wheel registry, MCP `g6_invoke` and the G6 permission gates.
- [ ] Run the full repository test suite on Python 3.12 and 3.13; test optional G6 ML/DoWhy/Z3/OR-Tools backends if enabled.
- [ ] Have an external security/code-quality reviewer challenge the certificate, provenance and tampering claims.

## Current local verification record

Unit tests in G6's suite and a worked two-variable Boolean example are supplied. These check the **reference implementation's internal logic and refusal paths**, with each executed step going through G6's checked door. They do not satisfy gates A–F for a production product; no completed professional assurance certificate is being issued by this bundle.
