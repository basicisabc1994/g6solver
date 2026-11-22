# Theory implementation

The six theory documents are the historical design record. They stay as written.
This document records what the code does at commit `458262ad802d5650db0fe450c4de5db6611b3c45`
(`458262ad`), from a static audit dated **22 November 2026**. It is not a test result.
No tests, model calls or study runs were performed for this document.

The audit covered 307 claims. It also inspected 18 review branches. Evidence on a
named `claude/` branch describes that branch alone. It does not establish that the
branches are integrated or that their combined behaviour works.

The counts below retain the audit's classifications. IMPLEMENTED means a mechanism
was located. PARTIAL means only part of the claim was located. NOT IMPLEMENTED means
the proposed mechanism was not located in the inspected trees. DIVERGES means the
located behaviour differs. NOT CHECKABLE FROM CODE covers measurements, history and
other claims that source inspection cannot settle. These are claim counts, not
percentages of product completion. Some claims cover several mechanisms.

Evidence uses `458262ad:path:line`, or a named public branch followed by `path:line`.
U, F, P, E and C identify rows in the audits of the first five documents below.
S identifies the numbered rows in the CSIR audit. Each discrepancy classified
DIVERGES or NOT IMPLEMENTED has its own row. The other two unresolved categories
are grouped by theme. An absence finding is bounded by the inspected trees;
its citation identifies the nearest mechanism, not evidence of a universal absence.

## Unified mathematical theory of agentic intelligence

Source: [Unified theory](G6_Unified_Theory_of_Agentic_Intelligence.md).

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 4 | 19 | 15 | 9 | 22 | 69 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| U9: admission thresholds | DIVERGES | `458262ad:src/g6/distillation/admission.py:20`; `claude/distill-gate-v3:src/g6/distill/acceptance_gate.py:2223` | Controls, paired outcomes, false alarms and regressions decide admission. There is no general 0.85/0.60 threshold pair. |
| U11: default step counts | DIVERGES | `458262ad:src/g6/solver/step_profiles.py:11` | Defaults are 4/5/15, rather than 4/8/15. |
| U13: store size and stopping | DIVERGES | `458262ad:src/g6/learning/runner.py:172`; `458262ad:src/g6/learning/runner.py:230` | Candidate and hint limits are not a twenty-entry store cap. The loop does not stop by a one-percent marginal-gain rule. |
| U18: utility objective | DIVERGES | `458262ad:src/g6/metaharness/gate.py:204`; `458262ad:src/g6/metaharness/pool.py:53` | Gates use observed gains and constraints. They do not maximise the stated accuracy-over-cost function. |
| U19: agent-count allocator | NOT IMPLEMENTED | `458262ad:src/g6/metaharness/trace.py:92`; `claude/graph-ir:src/g6/distill/graph.py:253` | Critical paths exist. An allocator based on graph width, budget and diminishing returns was not located. |
| U22: calibrated trust boundary | NOT IMPLEMENTED | `458262ad:src/g6/llm_router/advice.py:217`; `458262ad:src/g6/epistemic_integrity/adjudicator.py:244` | Routing scores and evidence ceilings do not measure the proposed calibration threshold. |
| U23: subtask error budgets | NOT IMPLEMENTED | `458262ad:src/g6/llm_router/supervision.py:132` | The cheapest-sufficient model calculation and criticality-weighted error allocation were not located. |
| U25: supervision cascade | DIVERGES | `458262ad:src/g6/llm_router/supervision.py:151` | Selection uses score and tier rules. It does not implement the stated economic threshold or mandatory final human fallback. |
| U27: capacity estimator | NOT IMPLEMENTED | `458262ad:src/g6/llm_router/registry.py:104` | Model metadata is not the fitted capacity formula. |
| U32: minimum decomposition | NOT IMPLEMENTED | `458262ad:src/g6/solver/step_profiles.py:11`; `458262ad:src/g6/solver/agentic_runtime.py:121` | Plans use heuristics, proposals or overrides. They do not solve the stated depth optimisation. |
| U34: complexity classifier | DIVERGES | `458262ad:src/g6/solver/complexity_classifier.py:44` | Integer counts and structural markers replace the pseudocode's fractional scores. The middle profile has five steps. |
| U37: recursive summarisation | NOT IMPLEMENTED | `458262ad:src/g6/metaharness/executor.py:126`; `claude/wire-reply-followup:src/g6/mcp/review_reply.py:61` | Compact review replies exist. The named recursive summarisation workflow was not located. |
| U38: automatic horizontal scaling | NOT IMPLEMENTED | `458262ad:src/g6/solver/engine.py:155` | There is no located complexity-based switch to more agents beyond twenty steps. |
| U41: debate return calculation | NOT IMPLEMENTED | `458262ad:src/g6/debate/debate.py:119` | Debate does not calculate expected gain, error correlation and inference cost before running. |
| U47: speedup-based allocation | NOT IMPLEMENTED | `458262ad:src/g6/metaharness/trace.py:92` | Neither the classical nor the proposed coordination-cost formula allocates agents. |
| U50: learned independence scheduling | NOT IMPLEMENTED | `458262ad:src/g6/agents/agent_langgraph/adapter.py:174` | Declared dependencies do not estimate mutual information or provide all the proposed scheduling rules. |
| U51: concurrent layers | DIVERGES | `458262ad:src/g6/solver/engine.py:155`; `claude/graph-ir:src/g6/distill/graph.py:253` | The inspected traversal is serial, including children of a graph node labelled `par`. |
| U52: retirement only by strength | DIVERGES | `458262ad:src/g6/learning/theory_store.py:245`; `claude/distill-gate-v3:src/g6/distill/acceptance_gate.py:2340` | Manual retirement and dependency-driven authority loss also exist. |
| U57: general idempotence | NOT IMPLEMENTED | `458262ad:src/g6/core/ai_block.py:182` | Arbitrary runners and mutable state have no general purity restriction. Repeating an input also differs from feeding an output back as input. |
| U59: universal baseline floor | DIVERGES | `458262ad:src/g6/solver/step_executor.py:99`; `claude/pilot-gate:src/g6/distill/distill_operator.py:359` | Exhausted fallbacks can fail. A scoped teacher cascade does not establish a floor for every task. |
| U61: capacity invariant | NOT IMPLEMENTED | `458262ad:src/g6/solver/engine.py:77` | Refusals cover concrete conditions, rather than a computed capacity-versus-variety comparison. |
| U64: eventual store stability | NOT IMPLEMENTED | `458262ad:src/g6/learning/runner.py:202` | Run budgets terminate work. They do not establish eventual stability of the accumulated store. |
| U65: universal calibration invariant | NOT IMPLEMENTED | `458262ad:src/g6/epistemic_integrity/adjudicator.py:244` | Confidence ceilings do not enforce the stated correlation with actual accuracy. |
| U66: system correctness probability | NOT IMPLEMENTED | `458262ad:src/g6/solver/schema.py:179` | Results carry scoped states and evidence. The four-term probability formula is not computed. |

**PARTIAL themes.** U8/U10 cover synthesis and voting. Fixed failure templates and
caller-selected sampling exist; the advertised single loop does not
(`458262ad:src/g6/learning/strategies.py:50`). U15/U20/U21/U29–31/U36/U40/U49/U60/U67/U68
cover planning, retrieval, supervision and review. The facilities exist without the
unified optimiser or all the advertised policies
(`458262ad:src/g6/core/selector.py:701`). U53/U54/U58/U62/U63 cover freshness,
isolation, gains, budgets and review. Controls are specific to particular paths.
Post-call overspend detection does not prevent spending
(`458262ad:src/g6/learning/runner.py:63`; `claude/graph-ir:src/g6/distill/graph.py:331`).
Scoped fallback also exists at `458262ad:src/g6/_agentic/kernel.py:1668`.
It retains an original result for opted-in operations, under its stated trust boundary.

**NOT CHECKABLE themes.** U2/U4/U6/U7/U14/U16/U17/U24/U26/U28/U33/U35/U39/U42–46/U48/U55/U56/U69
cover fitted rates, capacity, token efficiency, decomposition, correlations, scaling,
cost distributions and deployment accuracy. Audited headline summaries exist at
`458262ad:web/data/archive_benchmarks.json:3`. The original fitting data and scripts
needed to reconstruct these quantities were not located. Later validation code
cannot establish those earlier fits.

## Theoretical foundations

Source: [Theoretical foundations](G6_Theoretical_Foundations.md).

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 7 | 24 | 2 | 8 | 1 | 42 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| F3: decomposition as branch-and-bound | DIVERGES | `458262ad:src/g6/goal_engine/decomposition.py:184` | The algorithm uses bounded breadth-first expansion. |
| F4: planning over the component library | DIVERGES | `458262ad:src/g6/goal_engine/plan.py:173`; `458262ad:src/g6/cogarch/cog_arch_gps/engine.py:168` | Goal trees and search over caller-declared operators are separate mechanisms. |
| F6: Yoneda extension | NOT IMPLEMENTED | `458262ad:src/g6/core/registry.py:114` | Extension discovery exists. The named mathematical tooling was not located. |
| F8: logical reading of pipeline errors | DIVERGES | `458262ad:src/g6/core/ai_block.py:182`; `458262ad:src/g6/cegis/loop.py:202` | An error object is a value. Runtime checks do not establish arbitrary semantic correctness. |
| F10: sheaf extension | NOT IMPLEMENTED | `458262ad:src/g6/core/registry.py:114` | The named extension was not located. Typed composition does not supply its mathematical machinery. |
| F11: each tool decision as a bandit arm | DIVERGES | `458262ad:src/g6/metaharness/level2.py:43`; `458262ad:src/g6/core/selector.py:701` | Bandits choose proposal strategies. Other selectors use model outcomes or coverage per cost. |
| F20: every change gets formal checks and approval | DIVERGES | `458262ad:src/g6/self_training/patch_promotion_gate.py:325` | Formal checking is optional. Explicitly enabled low-risk autonomous promotion exists. |
| F24: permanent lookup-only cost | DIVERGES | `458262ad:src/g6/metaharness/executor.py:116`; `claude/distill-reach:src/g6/distill/library.py:70` | Reused methods still execute. They incur costs and can lose authority. |
| F30: safety framework on every call | DIVERGES | `458262ad:src/g6/goal_engine/predicates.py:118`; `458262ad:src/g6/core/compose.py:157` | Selected guardrails use hazard priors. Ordinary composition does not universally invoke that guardrail. |
| F36: every execution trains the optimiser | DIVERGES | `458262ad:src/g6/solver/history.py:85`; `claude/wire-learn-fu:src/g6/harness/outcome_history.py:176` | History and outcome admission are explicit. They do not update one shared bandit after every goal. |

**PARTIAL themes.** F1/F2/F5/F7/F9/F12–14 concern symbolic search, composition and
optimisation. Real engines and contract checks exist. They cover declared encodings
and interfaces, rather than every tool sequence
(`458262ad:src/g6/core/compose.py:100`). F17/F18/F21/F23 concern generation,
the self-model and reuse. Scaffolding, bounded sketches and branch-level synthesis
have narrower contracts (`claude/distill-reach:src/g6/self_model/model.py:100`).
F26–29 concern supplied corpora, source checks and repair proposals. These do not
establish source truth or a universal persistent healing service
(`458262ad:src/g6/code/adapt_healing/heal.py:159`). F32/F34/F35/F37–40/F42 concern
typed components, directability, team value and adaptation. Execution boundaries,
host configuration and measured outcomes limit those claims
(`458262ad:src/g6/self_training/improvement_loop.py:283`).

**NOT CHECKABLE theme.** F41 concerns transfer and the proposed adaptation hierarchy.
Policy proposals do not establish a measured adaptive ceiling or benefit in new
domains (`claude/distill-reach:src/g6/distill/meta/controller.py:59`).

## The validation paradox

Source: [The validation paradox](G6_Self_Validation_Paradox_Resolution.md).

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 8 | 12 | 0 | 1 | 1 | 22 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| P10: independently bounded evaluator error | DIVERGES | `458262ad:src/g6/research/echa/claims.py:154`; `458262ad:validation/v6_metaharness/analyze.py:272` | The claim rule accepts a supplied bound without calibration records. The older analyzer pools error terms instead of establishing separate bounds for both harnesses. |

**PARTIAL themes.** P2/P4/P6/P8/P17–19 concern evaluation choice, controls,
independence, custody and scope. Author labels and module boundaries do not establish
independent control. A declared formal category can admit general breadth without
checking its semantic scope (`458262ad:src/g6/research/echa/claims.py:145`).
P11–13/P21/P22 concern the learning event and its comparison. Branch synthesis,
split checks and retained methods are real. The cited integration example uses
fake model responses and mocked repository reads. It does not establish independent
cross-repository transfer (`claude/distill-reach:tests/distill/test_integration_end_to_end.py:43`).

**NOT CHECKABLE theme.** P14 is the historical collateral-edit result. The matching
original run artifact was not located. A later study runner is a different record
(`458262ad:validation/v6_metaharness/run.py:375`).

## Evidence-constrained harness adaptation specification

Source: [Adaptation specification](G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md).
Its research-only scope and proposed requirements describe the historical milestone.

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 22 | 34 | 0 | 4 | 3 | 63 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| E9: research-only promotion | DIVERGES | `458262ad:src/g6/core/adaptation_gate.py:243`; `claude/distill-gate-v3:src/g6/distill/acceptance_gate.py:1662` | The core gate grants shadow use. Later branches also admit methods to operational library authority. This does not establish an unauthorised deployment. |
| E16: unknown evaluator error | DIVERGES | `458262ad:validation/v6_metaharness/analyze.py:272` | Missing error terms become zero in this analysis path. The ECHA claim rule retains absent bounds. |
| E44: learning gradient | DIVERGES | `458262ad:validation/v6_metaharness/analyze.py:361` | The field is a success-rate difference from a baseline. It is not divided by comparable experience increments. |
| E45: unidentified true-success uplift | DIVERGES | `458262ad:validation/v6_metaharness/analyze.py:303` | A pooled quantity with missing terms filled by zero can enter the positive-verdict rule. It does not establish the specified two-harness correction. |

**PARTIAL themes.** E1/E4/E7/E35/E37/E39–43/E46/E54/E59/E61 concern acquisition,
transfer, study arms and reporting. Machinery exists without the complete specified
study or handoff (`458262ad:docs/validation/VALIDATION_PLAN.md:132`).
E2 concerns retention. A ledger cannot establish that every attempt was submitted
(`458262ad:src/g6/research/echa/ledger.py:113`).
E8/E12/E14/E15/E17/E20/E21/E26–29/E50 concern record completeness, calibration,
scope, chronology and effectiveness. Required fields are narrower than the conceptual
contracts. A nonempty counterfactual reference is not a checked causal comparison
(`458262ad:src/g6/research/ahi/contracts.py:84`). The research standard hashes text;
the product acceptance contract binds the wider policy
(`458262ad:src/g6/research/echa/records.py:88`; `458262ad:src/g6/workforce/acceptance.py:243`).
E10/E11/E19/E22/E38/E51/E63 concern activation, host authority and isolation.
AHI remains opt-in. Shared evidence utilities have graduated. The vault guards its
API and explicitly reports unblinded execution; it does not confine arbitrary
same-user file access (`458262ad:src/g6/core/vault.py:152`).

**NOT CHECKABLE themes.** E3 concerns the earlier 352-episode figures. Their original
rows were not located. E56 concerns which checks actually ran; source assertions
are not a release-run record (`458262ad:tests/research/ahi/test_ahi.py:85`).
E58 concerns historical approvals and wording decisions, which current gate code
cannot establish (`458262ad:src/g6/core/adaptation_gate.py:208`).

## Integrated research protocol

Source: [Composition, evidence and learning protocol](CSIR_ECHA_RESEARCH_PROTOCOL.md).

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 0 | 12 | 1 | 1 | 4 | 18 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| C2: five-arm mission runner | NOT IMPLEMENTED | `458262ad:src/g6/research/mission_bridge/replay.py:63`; `458262ad:docs/validation/VALIDATION_PLAN.md:67` | Replay consumes recordings. The specified executable five-arm comparison was not located. |
| C16: unknown oracle error stays unidentified | DIVERGES | `458262ad:validation/v6_metaharness/analyze.py:272` | The older analyzer substitutes zero for missing terms. |

**PARTIAL themes.** C1/C5–8/C10/C11/C15 concern separate estimands, comparators,
splits, resources and statistics. Their components exist across separate workflows,
without the joined protocol (`claude/pilot-gate:validation/distill/pilot/register.py:34`).
C4/C9/C17/C18 concern evidence, real checker runs and authority. Generic record
admission still depends on trusted inputs. Detected contamination blocks a result;
this does not establish that all contamination is detected
(`458262ad:src/g6/evidence/certificate.py:201`).

**NOT CHECKABLE themes.** C3 concerns the exact planned model and execution history.
A general model default does not bind a study (`458262ad:src/g6/core/llm.py:83`).
C12–14 concern comparative effects. Existing evaluators and fake-input integration
examples do not establish these effects (`458262ad:src/g6/metaharness/evaluate.py:349`).

## Computational structural intermediate representation

Source: [Computational structural intermediate representation](G6_Computational_Structural_Intermediate_Representation.md).

| IMPLEMENTED | PARTIAL | NOT IMPLEMENTED | DIVERGES | NOT CHECKABLE FROM CODE | Total |
|---:|---:|---:|---:|---:|---:|
| 14 | 51 | 11 | 3 | 14 | 93 |

| Claim | Status | Evidence | Gap |
|---|---|---|---|
| S11: unknown costs | DIVERGES | `458262ad:src/g6/research/mission_bridge/csir/judgement.py:60`; `claude/graph-ir:src/g6/distill/graph_records.py:18` | The commit retains unknown monetary bounds. The graph branch defaults omitted cost dimensions to zero and has no unknown state. |
| S14: computational category | NOT IMPLEMENTED | `458262ad:src/g6/core/compose.py:58`; `claude/graph-ir:src/g6/distill/graph_lowering.py:13` | Typed composition does not implement the categorical quotient or evidence-forgetting projection. |
| S18: seven burden coordinates | NOT IMPLEMENTED | `claude/graph-ir:src/g6/distill/graph_records.py:18` | Resource records do not separately measure semantic, access, decoding, interaction, evidence, meta and governance burdens. |
| S19: resource and assurance frontier | NOT IMPLEMENTED | `claude/graph-ir:src/g6/distill/graph_records.py:32` | Vector comparison is not the proposed frontier over legal programs under identical policies. |
| S20: contextual equivalence | NOT IMPLEMENTED | `458262ad:src/g6/core/compose.py:118` | Port compatibility does not establish equality under every admissible continuation. |
| S23: resource-conditioned equivalence | NOT IMPLEMENTED | `claude/graph-ir:src/g6/distill/graph_lowering.py:13` | Resource-bounded distinguishing contexts and simulation-cost guarantees were not located. |
| S33: specific scaling oracle | NOT IMPLEMENTED | `458262ad:src/g6/algorithm_design/kernels.py:58` | Bounded exact checks exist. The stated positive-scaling relation is not wired as the CSIR oracle. |
| S34: joint solve and evidence optimiser | NOT IMPLEMENTED | `claude/pilot-gate:src/g6/distill/pilot_gate.py:463` | Accounting for validation costs does not optimise both frontiers and escape risk. |
| S36: checked serial refinement | NOT IMPLEMENTED | `458262ad:src/g6/research/mission_bridge/csir/judgement.py:80` | A validator's absence of findings does not discharge all trace-refinement assumptions. |
| S46: capability closure | NOT IMPLEMENTED | `claude/graph-ir:src/g6/distill/graph_records.py:97` | No general closure enumeration or complete novelty classifier was located. |
| S47: separation witnesses | NOT IMPLEMENTED | `458262ad:src/g6/complexity/atlas.py:17` | Static dependencies do not establish nondefinability in the old grammar. |
| S70: ECHA default-off description | DIVERGES | `458262ad:src/g6/research/echa/__init__.py:13`; `458262ad:src/g6/research/echa/claims.py:76` | The analysis evaluator is directly callable. MissionIR remains default-off. |
| S75: kernel-checked certificate | DIVERGES | `458262ad:src/g6/evidence/certificate.py:206`; `458262ad:src/g6/evidence/certificate.py:450` | A formatted digest can enter the formal list and support `verified`, with ordinary grounding and ledger conditions. This API requires no checker run or statement, axiom and encoding binding. |
| S88: machine-checked interface contracts | NOT IMPLEMENTED | `458262ad:src/g6/core/compose.py:118`; `claude/wire-typed-fu:src/g6/core/compose.py:177` | Runtime contract checks exist. The proposed machine-checked serial and real-operation derivations were not located. |

**PARTIAL themes.** S2–5/S7–10/S12/S13/S15/S16/S37/S38/S67/S71/S73/S92 concern
plans, effects, execution and resources. Native graphs refuse unsupported branches
and iteration. Trusted callbacks can exceed a bound before their results are
rejected. Currency, network, energy and reviewer time are not all accounted for
(`claude/graph-ir:src/g6/distill/graph.py:207`; `claude/graph-ir:src/g6/distill/graph.py:331`).
S21/S22/S48/S58/S65/S79/S91 concern restricted algorithms, compression and capability
descriptions. Sum-keyed dynamic programming and shortest-path checks do not supply
general contextual equivalence (`458262ad:src/g6/algorithm_design/kernels.py:43`).
S25/S32/S35/S39–43/S50/S54/S55/S59/S72/S86/S87 concern evidence, authority,
diagnosis and statistics. Generic records do not authenticate evaluator custody.
The product issuer and research analysis have distinct roles
(`458262ad:src/g6/evidence/certificate.py:233`).
S51–53/S57/S77/S78/S80/S82/S83/S85/S89 concern acquisition, transfer and worked
missions. Research replay and acceptance machinery do not establish complete
operational domain workflows or the proposed comparative advantages
(`458262ad:src/g6/research/mission_bridge/replay.py:78`).

**NOT CHECKABLE themes.** S1/S49/S64/S69/S84/S90 concern accompanying artifacts and
history. The specific sixteen-test package, thirty-five-task comparison and cited
October reports were not located. Existing kernel tests are not substitutes
(`458262ad:tests/research/mission_bridge/test_mission_bridge_csir_kernel.py:100`).
S45/S60–63/S81 concern comparative benefit and transfer. Available mechanisms do
not establish the hypotheses (`458262ad:src/g6/metaharness/evaluate.py:286`).
S56/S93 concern mathematical limitations and scientific novelty. Source inspection
does not settle those external questions.

## Cross-cutting findings

Practice is more careful in several places. The issuer keeps support and refutation
separate. An unknown result beside support remains unresolved. Human approval alone
cannot supply support (`458262ad:src/g6/evidence/certificate.py:426`,
`458262ad:src/g6/evidence/certificate.py:463`). Negative controls are required even
for formal evidence (`458262ad:src/g6/evidence/certificate.py:212`). Mission bundles
cap caller-declared evidence below `verified`
(`458262ad:src/g6/research/mission_bridge/bundle.py:304`).

Review branches add useful downward transitions. Methods can be demoted, withdrawn
or superseded. Dependency authority loss cascades. Stale self-model fields become
unknown, and predictions cannot replace measurements
(`claude/distill-gate-v3:src/g6/distill/acceptance_gate.py:2340`;
`claude/distill-reach:src/g6/self_model/model.py:63`). These controls are more
careful than permanent-reuse and monotonic-growth language.

The theory overstates universal optimisation, automatic formal routing, general
baseline preservation, unrestricted semantic guarantees and enforcement on every
call. Current controls have named entry points and assumptions. A hash establishes
identity under its storage assumptions. It does not establish source truth,
independent authorship or an observed checker run. The generic issuer's refutation
branch also lacks the ledger-resolution check required by its support branch
(`458262ad:src/g6/evidence/certificate.py:420`;
`458262ad:src/g6/evidence/certificate.py:426`).

Code cannot settle historical approvals, host deployment, specialist suitability,
scientific novelty or fitted constants without their supporting records. The
historical benchmark summaries do not reconstruct missing fits. Test source shows
intended assertions; it does not show that a checker run occurred or passed.

## Known gaps in the code

The five gaps identified by the audit lead are:

1. **Formal record admission.** A digest-bearing formal record can support `verified`
   without an actual checker run. The issuer is certificate-bound
   (`458262ad:src/g6/evidence/certificate.py:206`).
2. **Unknown graph costs.** Omitted graph costs read as zero on `claude/graph-ir`
   (`src/g6/distill/graph_records.py:18`).
3. **Oracle calibration.** The ECHA analysis rule accepts any supplied error bound,
   including a vacuous bound, without calibration provenance
   (`458262ad:src/g6/research/echa/claims.py:154`). This is a research claim result,
   not the product issuer's requirement state.
4. **Older study analysis.** Missing error terms become zero. The field called
   `learning_gradient` is a baseline rate difference
   (`458262ad:validation/v6_metaharness/analyze.py:272`,
   `458262ad:validation/v6_metaharness/analyze.py:361`).
5. **New tests and protected evaluators.** Blanket test-path protection also blocks
   adding learned tests (`458262ad:src/g6/core/adaptation_gate.py:35`). This is a
   policy mismatch. It does not justify relaxing protection of existing evaluators.

These have different remedies. The first needs the issuance process. The second
needs an explicit unknown cost state. The third and fourth need narrower evidence
and reporting rules. The fifth needs a separately governed distinction between an
added safeguard and a changed acceptance evaluator.

## Not built by design

The current design uses staged selection and scoped checks. A universal bandit or
Pareto optimiser over tool sequences, fitted capacity estimates, economic trust
thresholds and optimal parallel scheduling are later directions. They are not
prerequisites for the current bounded coding workflow.

Contextual and resource equivalence, complete capability closure, separation
witnesses and the integrated five-arm mission study remain research directions.
The newer compiled-method study has different comparators. Its preparation cannot
substitute for the older protocol or establish its hypotheses.

The design includes bounded acquisition, the self-model, authority loss and a
compiled-method comparison. Their presence on review branches does not make them
part of `458262ad`. No benefit or transfer result follows from that wiring alone.
