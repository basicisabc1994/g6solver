# SPDX-License-Identifier: Apache-2.0
"""Explicit declarations for G6's built-in dictionary result producers.

These producers write the standard envelope and root message prose beside their
own data. Typed knowledge outcomes add their reason declarations at ingress.
New built-ins require a declaration here; the census checks registry coverage.
Names locate an import only: the actual class identity must match before use.
"""
from __future__ import annotations

from importlib import import_module

BUILTIN_PRODUCERS = {
    "g6.action_gating.action_gating_block": "ActionGatingBlock",
    "g6.action_logic.block": "ActionLogicBlock",
    "g6.agents.agent_autogen.agent_autogen_block": "AgentAutoGenBlock",
    "g6.agents.agent_claude.agent_claude_block": "AgentClaudeBlock",
    "g6.agents.agent_codex.agent_codex_block": "AgentCodexBlock",
    "g6.agents.agent_langgraph.agent_langgraph_block": "AgentLangGraphBlock",
    "g6.agents.agent_nanoclaw.agent_nanoclaw_block": "AgentNanoclawBlock",
    "g6.agents.agent_runtime.agent_runtime_block": "AgentRuntimeBlock",
    "g6.agents.agent_smolagents.agent_smolagents_block": "AgentSmolagentsBlock",
    "g6.algebra.algebra_block": "AlgebraBlock",
    "g6.algorithm_design.block": "AlgorithmDesignBlock",
    "g6.cegis.block": "CegisBlock",
    "g6.causal.causal_block": "CausalBlock",
    "g6.code.adapt_django.block": "AdaptDjangoBlock",
    "g6.code.adapt_healing.block": "AdaptHealingBlock",
    "g6.code.adapt_library_synthesizer.block": "AdaptLibrarySynthesizerBlock",
    "g6.code.adapt_webpage.block": "AdaptWebpageBlock",
    "g6.code.component_creator.block": "ComponentCreatorBlock",
    "g6.code.design_governance.block": "DesignGovernanceBlock",
    "g6.code.meta_programming.block": "MetaProgrammingBlock",
    "g6.code.swe_diagnostics.block": "SweDiagnosticsBlock",
    "g6.coding_agent_gate.coding_agent_gate_block": "CodingAgentGateBlock",
    "g6.cogarch.cog_arch_actr.block": "CogArchActrBlock",
    "g6.cogarch.cog_arch_aixi.block": "CogArchAixiBlock",
    "g6.cogarch.cog_arch_gps.block": "CogArchGpsBlock",
    "g6.cogarch.decision_making.block": "DecisionMakingBlock",
    "g6.cognitive_bridge.block": "CognitiveBridgeBlock",
    "g6.complexity.block": "ComplexityBlock",
    "g6.datalog.datalog_block": "DatalogBlock",
    "g6.embodied.adapt_physical_ai.block": "AdaptPhysicalAIBlock",
    "g6.epistemic_integrity.epistemic_integrity_block": "EpistemicIntegrityBlock",
    "g6.formal.formal_block": "FormalBlock",
    "g6.goal_engine.block": "GoalEngineBlock",
    "g6.graph_reasoning.graph_reasoning_block": "GraphReasoningBlock",
    "g6.grounding.grounding_block": "GroundingBlock",
    "g6.integrations.adapt_rq.block": "AdaptRQBlock",
    "g6.integrations.database.database_block": "DatabaseBlock",
    "g6.integrations.hat_orchestrator.block": "HATOrchestratorBlock",
    "g6.jobs.duty_of_care.block": "DutyOfCareBlock",
    "g6.knowledge.ctx_colbert.block": "CtxColbertBlock",
    "g6.knowledge.ctx_contexthub.block": "CtxContexthubBlock",
    "g6.knowledge.ctx_elastic.block": "CtxElasticBlock",
    "g6.knowledge.ctx_search.block": "CtxSearchBlock",
    "g6.knowledge.knowledge_chunker.block": "KnowledgeChunkerBlock",
    "g6.knowledge.knowledge_converter.block": "KnowledgeConverterBlock",
    "g6.knowledge.knowledge_fetcher.block": "KnowledgeFetcherBlock",
    "g6.knowledge.knowledge_indexer.block": "KnowledgeIndexerBlock",
    "g6.knowledge.knowledge_manifest.block": "KnowledgeManifestBlock",
    "g6.lean.lean_block": "LeanBlock",
    "g6.llm_router.llm_router_block": "LLMRouterBlock",
    "g6.media.adapt_audio.block": "AdaptAudioBlock",
    "g6.media.adapt_blender.block": "AdaptBlenderBlock",
    "g6.media.adapt_comfyui.block": "AdaptComfyUIBlock",
    "g6.media.adapt_diagrams.block": "AdaptDiagramsBlock",
    "g6.media.adapt_ffmpeg.block": "AdaptFFmpegBlock",
    "g6.media.adapt_generative_art.block": "AdaptGenerativeArtBlock",
    "g6.media.adapt_image.block": "AdaptImageBlock",
    "g6.media.adapt_synth.block": "AdaptSynthBlock",
    "g6.media.adapt_visualisation.block": "AdaptVisualisationBlock",
    "g6.media.adapt_voice.block": "AdaptVoiceBlock",
    "g6.memory.ctx_ace.block": "CtxAceBlock",
    "g6.memory.ctx_cognee.block": "CtxCogneeBlock",
    "g6.ml.adapt_automl.block": "AdaptAutoMLBlock",
    "g6.ml.adapt_bayesian.block": "AdaptBayesianBlock",
    "g6.ml.adapt_pandas.block": "AdaptPandasBlock",
    "g6.ml.adapt_pytorch.block": "AdaptPytorchBlock",
    "g6.ml.adapt_sklearn.block": "AdaptSklearnBlock",
    "g6.model_checking.model_checking_block": "ModelCheckingBlock",
    "g6.optimisation.optimisation_block": "OptimisationBlock",
    "g6.prolog.prolog_block": "PrologBlock",
    "g6.self_maintenance.block": "SelfMaintenanceBlock",
    "g6.sources.australian_legal_corpus.block": "AustralianLegalCorpusBlock",
    "g6.sources.ctx_geo.block": "CtxGeoBlock",
    "g6.sources.ctx_git.block": "CtxGitBlock",
    "g6.sources.ctx_langextract.block": "CtxLangExtractBlock",
    "g6.sources.ctx_library_mapper.block": "CtxLibraryMapperBlock",
    "g6.sources.ctx_vision.block": "CtxVisionBlock",
    "g6.workspace.align_prompt_library.block": "AlignPromptLibraryBlock",
    "g6.workspace.shared_workspace.block": "SharedWorkspaceBlock",
    "g6.workspace.workspace_manager.block": "WorkspaceManagerBlock",
}


def builtin_result_contract(owner):
    cls = type(owner)
    module = cls.__module__
    name = BUILTIN_PRODUCERS.get(module)
    if name is not None and cls is getattr(import_module(module), name):
        return "builtin.envelope"
    return None
