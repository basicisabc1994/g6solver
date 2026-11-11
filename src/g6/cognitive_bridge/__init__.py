# SPDX-License-Identifier: Apache-2.0
"""G6 Cognitive Learning Bridge: explicitly swappable learning strategies and XAI.

Safe stateless rank/update calls are available through G6 registry; stateful
research agents and trained predictors require explicit Python injection.
"""
from .block import CognitiveBridgeBlock, CognitiveInput
from .engine import (BayesianUpdater, CognitiveAgent, Hypothesis, InformationGainSelector,
                     LLMProposer, LLMIdeaGenerator, IdeaHypothesis, MLAcquisitionSelector, Observation, Probe, StaticProposer,
                     ToolSpec, check_beliefs, expected_information_gain)
from .mixed_methods import (ENGINE_SPECS, ClaimIR, MethodStep, MethodPlan, MethodRouter,
                            MixedMethodExecutor, LLMClaimCompiler)
from .xai import ExplicitTree, TreeNode, distill_blackbox, extract_tree
from .quantitative import ACQUISITION_FEATURES, UTILITY_TARGET, evaluated_trace_rows, sklearn_payload
from .metacognition import (ProblemSignature, MethodVariant, EvaluatedTrial, SelectionDecision,
                            MetacognitiveMethodSelector, MetacognitivePlanner)
from .assurance import (Criterion, DomainStandard, EvidenceArtifact, FrozenVerifier,
                        ProfessionalAttestation, CandidateDossier, InProcessAssuranceEvaluator,
                        DOCUMENT_FIELDS)
from .critique import CritiqueCandidate, CritiqueReport, LLMMetacognitiveCritic
from .adaptation import (FailureModeFinding, MitigationHypothesis, CompositionalFailureModel,
                         detect_failure_modes, GuardChange, PairedTrial, ControlTrial,
                         AdaptationPromotionGate)
from .orchestration import CompiledResearch, CognitiveAssuranceOrchestrator
from .epistemic import ScopedAssertion, CrossMethodConflictGate

__g6_component__ = True
__g6_block__ = "CognitiveBridgeBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("epistemic_integrity", "invariants", "learning", "reasoning")
__g6_mitigates__: tuple[str, ...] = ("ad_hoc_choice", "false_confidence", "repetition_as_corroboration")
__g6_introduces__: tuple[str, ...] = ("unevaluated_model_shipped",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"ml": ("sklearn",)}
__all__ = ["BayesianUpdater", "CognitiveAgent", "CognitiveBridgeBlock", "CognitiveInput",
           "ExplicitTree", "Hypothesis", "InformationGainSelector", "LLMProposer", "LLMIdeaGenerator", "IdeaHypothesis",
           "MLAcquisitionSelector", "Observation", "Probe", "StaticProposer", "ToolSpec",
           "TreeNode", "check_beliefs", "distill_blackbox", "expected_information_gain", "extract_tree",
           "ACQUISITION_FEATURES", "UTILITY_TARGET", "evaluated_trace_rows", "sklearn_payload",
           "ENGINE_SPECS", "ClaimIR", "MethodStep", "MethodPlan", "MethodRouter",
           "MixedMethodExecutor", "LLMClaimCompiler",
           "ProblemSignature", "MethodVariant", "EvaluatedTrial", "SelectionDecision",
           "MetacognitiveMethodSelector", "MetacognitivePlanner",
           "Criterion", "DomainStandard", "EvidenceArtifact", "FrozenVerifier",
           "ProfessionalAttestation", "CandidateDossier", "InProcessAssuranceEvaluator", "DOCUMENT_FIELDS",
           "CritiqueCandidate", "CritiqueReport", "LLMMetacognitiveCritic",
           "FailureModeFinding", "MitigationHypothesis", "CompositionalFailureModel",
           "detect_failure_modes", "GuardChange", "PairedTrial", "ControlTrial",
           "AdaptationPromotionGate", "CompiledResearch", "CognitiveAssuranceOrchestrator",
           "ScopedAssertion", "CrossMethodConflictGate"]
