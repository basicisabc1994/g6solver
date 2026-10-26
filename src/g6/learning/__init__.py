# SPDX-License-Identifier: Apache-2.0
"""g6.learning — learn prompt hints from an agent's failures, and credit each one only from paired runs with and without it.

g6.learning — learn prompt hints from an agent's failures, and credit each one only from paired runs with and without it
(audit 006 §11). A hint is recommended for use only on held-out paired evidence, and a person accepts it.

Capability label: ``implemented``.

    from g6.learning import Dataset, TaskInstance, TheoryStore, T3Strategy, build_addendum, promote, run_learning
    train = Dataset("train", [TaskInstance("2+2=?", "4", "t1"), ...], split="train")
    held = Dataset("held", [TaskInstance("3+3=?", "6", "h1"), ...], split="holdout")
    store = TheoryStore(state_dir / "theories.db", scope="my-agent")
    report = run_learning(T3Strategy(), train, my_agent, holdout=held, store=store)
    report.completion_state                  # "qualified-draft" at best, never verified
    report.holdout.to_dict()                 # raw agent vs frozen harness on the same held-out tasks
    for theory in report.harness:
        decision = promote(store.get(theory.theory_id), report.evidence[theory.theory_id])
        if decision.recommendation == "promote":          # a person reviews it, then:
            store.activate(decision, accepted_by="reviewer name")
    build_addendum(store, task_meta={})      # hint text from active theories only

``TheoryStore`` (``theory_store``) keeps one scope's candidate, active and retired theories in SQLite at an explicit
path, credits them only through ``record_paired_outcome`` and activates one only through an accepted
``PromotionDecision``. ``run_learning`` (``runner``) runs a strategy's proposals with and without each theory on every
training instance, then the raw agent against the frozen harness on a disjoint held-out set; ``compare_arms`` does the
held-out half alone. ``promote`` (``promotion``) reads paired held-out evidence: bound to the theory, disjoint from
training, enough pairs, a positive one-sided sign test. ``export_bundle`` / ``import_bundle`` (``bundle``) move
theories between stores, and import yields candidates only. ``DatasetBuilder``, ``DefaultClassifier`` and the
observers are the archive's utilities. Scoring is keyed by instance and fails closed; the agent and any judge are
injected (``g6.core.llm.LLMCaller`` or a plain callable). ``g6.learning.baselines`` holds the baselines
``T0Strategy`` and ``TemplateStrategy`` (T1 and T2 merged), and ``render_skill``, off by default and unmeasured.

Not ported from the archive's ``learning_layer``: the ``LearningLayerBlock`` and its op dispatch, the 9-tool MCP
server, preflight cost estimates and workspace sandboxing (a caller's concern); progressive staging, checkpoint and
resume; the bucketed McNemar p-value; ``ConfigOverride`` (ignored by every agent, LL-009) and ``GeneratedTool``; the
separate T1 and T2 tiers; per-tenant SQLCipher routing and the ``~/.g6/learning_layer.db`` default; the boolean
evidence-resolver ``PromotionGate``; ``patterns.py``, ``skills.py``, ``honesty.py``, ``shadow_value.py``, the skill zip
and the ``docs_meta.yaml`` claims (LL-014); the ``ALLOW_PAID_API`` echo; ``discover_extension_datasets``; and
``formal_experience.py``, which belongs with ``g6.formal`` and is not moved by this port. Nothing here activates a
theory by itself, and nothing here emits ``verified``.
"""
from g6.learning.addendum import build_addendum, render_addendum, trigger_matches
from g6.learning.bundle import CandidateBundle, export_bundle, import_bundle, leakage_attestation, scan_text
from g6.learning.dataset_builder import DatasetBuilder
from g6.learning.failure_classifier import FAILURE_CODES, DefaultClassifier, FailureClassifier
from g6.learning.observer import CallbackObserver, EventEmitter, JsonlObserver, LearningEvent, LogObserver
from g6.learning.ooda import ObservationResult
from g6.learning.paired import ArmComparison, PairedOutcome, sign_test
from g6.learning.promotion import HeldOutEvidence, PromotionDecision, PromotionPolicy, promote
from g6.learning.runner import LearningReport, compare_arms, run_learning
from g6.learning.schema import (AgentReply, AgentTrace, Dataset, FailureAnalysis, PromptAddendum, ReviewRecord,
                                SkillDocument, TaskInstance, TaskResult, Theory)
from g6.learning.scoring import score_instance, score_output
from g6.learning.strategies import THEORY_TEMPLATES, LearningStrategy, T3Strategy
from g6.learning.theory_store import TheoryStore
from g6.learning.seeds import load_seed_heuristics

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("repeated_mistake", "unhelpful_lesson", "unranked_heuristics")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["FAILURE_CODES", "THEORY_TEMPLATES", "AgentReply", "AgentTrace", "ArmComparison", "CallbackObserver",
           "CandidateBundle", "Dataset", "DatasetBuilder", "DefaultClassifier", "EventEmitter", "FailureAnalysis",
           "FailureClassifier", "HeldOutEvidence", "JsonlObserver", "LearningEvent", "LearningReport",
           "LearningStrategy", "LogObserver", "ObservationResult", "PairedOutcome", "PromotionDecision",
           "PromotionPolicy", "PromptAddendum", "ReviewRecord", "SkillDocument", "T3Strategy", "TaskInstance",
           "TaskResult", "Theory", "TheoryStore", "build_addendum", "compare_arms", "export_bundle", "import_bundle",
           "leakage_attestation", "load_seed_heuristics", "promote", "render_addendum", "run_learning", "scan_text", "score_instance",
           "score_output", "sign_test", "trigger_matches"]
