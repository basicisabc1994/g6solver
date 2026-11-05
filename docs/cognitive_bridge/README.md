# G6 Cognitive Learning & Assurance Bridge v0.2

**Status:** research/reference implementation; `qualified-draft` at best. **Not** a production certificate issuer. Submitted for review in a draft PR; not a production-certified component.

A compositional extension for G6 that unifies cognitive research, LLM-assisted critical reasoning, graph/causal/formal/quantitative ML methods, learning to select methods, evidence-constrained assurance, and governed harness adaptation.

## Design thesis

> The generator proposes; a different authority with frozen expectations establishes which claims the evidence supports.

The new system has two learning loops and a guarded evaluation boundary:

```text
USER GOAL + FROZEN DOMAIN ACCEPTANCE PROFILE
        │
        ▼
NATURAL LANGUAGE → REVIEWABLE CLAIM IR ← LLM premortem / falsification proposals
        │                              (untrusted candidates only)
        ▼
METACOGNITIVE SELECTOR: (representation × G6 engine × operation)
       ├── requires every mandatory domain method
       ├── learns from evaluator-admitted held-out observations only
       └── uses explicit budgets, risk and bounded context
        │
        ▼
CANONICAL TYPED METHOD ROUTER → approved G6 registry execution
       ├── graph_reasoning (rules, explain, query)
       ├── causal (identification → conditional estimation)
       ├── formal (SMT and propositional)
       ├── adapt_sklearn (fit_eval and rank)
       └── optimisation (LP and MIP)
        │
        ▼
ARTIFACT BYTES + SOURCE REFS + MODEL ASSUMPTIONS
        │
        ▼
FROZEN EVALUATOR-OWNED DOMAIN CHECKS (must live outside candidate scope)
       ├── independent artifact replay
       ├── positive and planted-negative controls
       ├── normative edition / jurisdiction / source digest
       ├── mandatory claim-to-check coverage
       ├── risk-matched authenticated expert review
       └── complete documentary sections and limitations
        │
        ▼
CANDIDATE VERIFICATION DOSSIER: SHA-256-bound, non-authenticating
        │
        ▼
G6 INDEPENDENT PROMOTION DOOR / PROFESSIONAL AUTHORITY (NOT implemented by this bridge)
        │
        ▼
FAILURE SIGNATURE → ROOT-CAUSE HYPOTHESIS → GUARD CANDIDATE
        │
        ▼
FROZEN PAIRS / CONTROLS / NONREGRESSION / BONFERRONI GATE
        └── at most recommendation for isolated SHADOW use, then separate owner decision
```

**No universal absolute correctness claim.** The strongest achievable guarantee is *relative to a precise claim, a faithful task specification, trusted normative sources, the competence and custody of an evaluator, and a reproducible verification method*. These assumptions are printed in the dossier. A computation being correct on the wrong model is **not** verification of the original user intent.

## Package contents

| File | Purpose |
|---|---|
| `src/g6/cognitive_bridge/engine.py` | Existing v0.1 cognitive hypothesis/probe/observation loop and swappable explicit/ML selectors |
| `src/g6/cognitive_bridge/xai.py` | Exact supported decision-tree transcription and approximate black-box surrogate (with fidelity measurement) |
| `src/g6/cognitive_bridge/mixed_methods.py` | Canonical typed evidence obligations; allowlisted backend/operation variants; guarded G6 invocation |
| `src/g6/cognitive_bridge/metacognition.py` | Contextual, evidence-admitted method selection over alternative representations and G6 engines; budgeted portfolio selection |
| `src/g6/cognitive_bridge/critique.py` | LLM premortem, assumption auditing, counterexample suggestions; **proposal only** |
| `src/g6/cognitive_bridge/epistemic.py` | Scope-bound, evaluator-admitted cross-method contradiction tripwire; never averages conflicts |
| `src/g6/cognitive_bridge/assurance.py` | Frozen standard and verifier profiles; control execution; reproducible, scoped candidate dossier |
| `src/g6/cognitive_bridge/adaptation.py` | Failure-mode graph and conservative held-out adaptation/shadow gate |
| `src/g6/cognitive_bridge/orchestration.py` | Join meta-choice to the original router and independent evaluator without bypassing either |
| `src/g6/cognitive_bridge/block.py` | Stateless G6 registry/MCP-safe read-only planning, ranking and schema operations |
| `docs/TECHNICAL_SPEC.md` | Formal contracts, assurance semantics, statistical validity, threat model, adoption plan |
| `docs/INTEGRATION_GATES.md` | Required outside-process and G6-kernel integration gates before production claims |
| `examples/assurance_worked_example.py` | Reproducible 2-Boolean worked example with a planted negative control |
| `examples/illustrative_candidate_dossier.json` | Its **candidate** dossier, not an attested certificate |
| `tests/` | Offline regression and failure-path tests |

The provided `_local_shims` directory supports standalone unit testing **only**. Do not deploy it or copy it into G6.

## Install into the current G6 checkout

Use a **new branch** of your checked-out `main` (not the `growth/workforce-os` branch). The current G6 repository already discovers top-level packages that declare `__g6_component__ = True`.

```bash
# In a trusted local G6 clone, on your intended feature branch:
cp -r /path/to/g6_cognitive_assurance_v0_2/src/g6/cognitive_bridge ./src/g6/
python -m pytest -q tests
# Then add the bundle's tests to the G6 tests suite and rerun full CI.
```

Only the `src/g6/cognitive_bridge` directory is the extension to copy. This bundle does not change G6's core trust law or its current certificates. No repository changes were made by this ChatGPT run.

From this standalone directory:

```bash
PYTHONPATH=_local_shims:src python -m pytest -q tests
PYTHONPATH=_local_shims:src python examples/assurance_worked_example.py
```

PowerShell:

```powershell
$env:PYTHONPATH = '_local_shims;src'
python -m pytest -q tests
python examples/assurance_worked_example.py
```

## Metacognitive method selection

`ProblemSignature(domain, uncertainty, claim_kind, risk, budget)` is a bounded task signature. `MethodVariant` is an allowlisted implementation with a representation, operation and estimated cost. `EvaluatedTrial` is a historical outcome, **admitted only by a separately owned validator**. Without a real validator, no past trials enter the selector. The current learner is a smoothed, contextual UCB-inspired ranking, not a calibrated probability model.

```python
from g6.cognitive_bridge import (
    MetacognitiveMethodSelector, ProblemSignature, MethodVariant
)
selector = MetacognitiveMethodSelector()  # no self-graded training data
ctx = ProblemSignature("software_engineering", "formal_specification", "invariant", 1, 5)
choices = [
    MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2.0),
    MethodVariant("dpll", "formal", "propositional", "formal", "check_propositional", 1.0),
]
choice = selector.select(ctx, choices, required_methods=["formal"])
print(choice.to_dict())
```

For real learning, construct the selector in a separate, evaluator-controlled process with `admit_trial=...` verifying evaluator signatures, frozen suite IDs, the exact evaluated artifact, absence of development-set leakage and documented adjudication. A permissive lambda is **only a unit-test fixture**. The algorithm deliberately refuses data with duplicate case/variant/suite identity, train-split records or missing provenance. Method outcomes from unrelated domain signatures cannot become matched-domain successes.

If the selection must run, use `CognitiveAssuranceOrchestrator.compile` with variant-specific **declarative model payloads**. The chosen operation enters the canonical `MethodRouter` as an allowlisted `variant_op`; the existing executor independently reconstructs the route before invocation. Supplemental methods are proposals until their obligations and models are explicitly added to the claim and approved. `execute(approved=True)` still requires the real G6 registry and the operator's approval. The stateless G6 component interface **never** fits ML models, invokes a solver or installs an adaptation by itself.

## Candidate assurance dossier, not an automatic certificate

`DomainStandard` is a curator-owned snapshot: authority, jurisdiction, edition, normative document SHA-256, risk and criteria. Each criterion names its specific verifier, evidence kind, known failure modes and required reasoning methods. The authority/source string is a **declared input**, not evidence the source was actually authentic or current. External source custody is mandatory for professional use.

`IndependentAssuranceEvaluator` accepts actual immutable artifact bytes and a separately owned `FrozenVerifier` whose positive controls must pass and whose planted-negative controls must **return false**. Exceptions are **not** successful defect detection. Missing critical checks, failing controls, mismatched producers, missing documentation and advisory exceptions all block a dossier.

`CandidateDossier.content_sha256` binds its JSON payload to a digest. **This is not an identity signature:** an attacker could replace the document and recompute its digest. For risk R2/R3, the local gate also requires a correctly scoped, authenticated independent professional reviewer. This must be backed by an external reviewer key/identity registry, not by text saying “reviewed”.

Every emitted result is `qualified-draft` or `blocked-escalated`. `verified` can be granted only by G6's existing independently rerunning promotion-door mechanism when the integration has a valid rechecker for this claim. As of the repository state examined for this bundle, the G6 reliability envelope's built-in door rechecker is restricted to the integrity package; this extension does not add a trusted rechecker for general research claims and therefore **cannot** issue their `verified` state.

**Required documentary sections:** objective and scope; methodology; standards and sources; data provenance; assumptions; uncertainty and limitations; counterexamples and refutations; reproduction instructions; exceptions and unresolved items. The current prototype checks that sections are supplied; qualified domain reviewers must assess whether their **substance** meets the applicable professional standard.

## Governed adaptation

`detect_failure_modes(dossier)` emits *observed failure signatures*; `CompositionalFailureModel` connects them to separately labeled *root-cause and guard proposals*. It never conflates a proposed explanation with an established cause.

`AdaptationPromotionGate` requires:

- A named defect linked to the proposed guard and unmodified frozen evaluator/protected paths.
- Positive and negative controls that the evaluator actually ran and authenticated.
- Paired original-versus-adapted outcomes on exactly the same **untouched held-out cases**.
- No regression on critical cases, no duplicate identities or overlap with development cases.
- Enough pairs and disagreements, a positive advantage, and a preregistered one-sided sign test with Bonferroni control (`alpha / max_evaluations`).

The most permissive result is `recommend_isolated_shadow_only`. It cannot activate an agent, relax a policy, modify checks, merge a PR or publish a release. External owner review and G6's existing `adaptation_gate` / `learning.promotion` must govern such actions. A strong result on one distribution does **not** imply universal monotonic self-improvement.

## Existing G6 integrations and boundaries

This implementation was designed against G6 `main` on **2026-11-05**. The relevant existing interfaces are `g6.core.registry`, `g6.core.llm`, `g6.cogarch.decision_making`, `g6.graph_reasoning`, `g6.causal`, `g6.formal`, `g6.ml.adapt_sklearn`, `g6.optimisation`, `g6.core.evidence_grade`, `g6.core.reliability_envelope`, `g6.core.component_certificate`, `g6.core.promotion_evaluator`, `g6.core.adaptation_gate`, `g6.core.failure_modes`, `g6.learning.paired`, and `g6.learning.promotion`.

The quantitative G6 `adapt_sklearn` adapter fits/evaluates within its own bounded child but **does not persist fitted models**. Arbitrary pickle deserialization is excluded. `CognitiveAgent.MLAcquisitionSelector` still requires an explicitly trusted in-memory predictor. `xai.py` only transcribes supported model families or approximates a supplied black box with measured held-out fidelity.

**Not implemented here:** independently authenticated source registries; outside-process evaluator custody; external reviewer PKI/signature verification; automatic G6 reliability-door promotion; public certificate signatures; production integration tests against the current complete G6 repository; evidence-based reward calibration on real third-party tasks; continuous autonomous deployments. `docs/INTEGRATION_GATES.md` defines those release blockers.
