# SPDX-License-Identifier: Apache-2.0
"""g6.metaharness — tune G6 to a task you repeat: it proposes changes, tests each one on held-out examples, and changes nothing until a person approves.

g6.metaharness — tune G6 to a task you repeat: it proposes changes to how G6 runs that task, tests each one on
held-out examples you trust, and changes nothing until a person approves.

Capability label: ``implemented``. The studies run with it are on the site's Research page, as a worked example.

When an attempt fails or finds no gain, it says why and what can be done next (``recovery``, DECISIONS #27): every
refused decision and every empty proposal carries a recovery plan read from the gate's aggregates, and
``mh.diagnose()`` reads the active version's dev failures, names their root causes and maps each to the G6 components
the meta-harness can try, and to what only a person can unblock. It never reads a held-out case. The remedies it can
try itself: ``Profile(exemplars=k)`` (labelled examples, ``g6.metaharness.exemplars``) and the built-in pipeline steps
in ``g6.metaharness.steps`` (``repair_with_tests``, ``second_look``), which a person declares in the workflow.
``mh.propose(RecoveryStrategy(workflow))`` turns the diagnosis into those candidates, for the gate to judge.

The design (``docs/theory/G6_Kernel_*.md``, owner rulings DECISIONS #26) splits two documents:

* the **workflow** judges: your standard and its checks, your objectives, your capabilities, your suite (dev and
  held-out), your evidence policy and audit floor. A person writes and confirms it, and it is frozen by digest;
* the **profile** acts: which of your models runs each step, budgets, context, learned hints, extra checks and optional
  pipeline steps. Only a profile can be proposed, tested and, after a person approves, activated.

What it can never do: change the workflow, the stop, the envelope rules, the gates or its own evaluator; activate a
change without a person; take an irreversible action; give a result a better label than its evidence (its own outputs
are ``qualified-draft`` at best, never ``verified``). The only change it makes on its own is a move back to a version a
person approved before.

    from g6.harness import Check
    from g6.metaharness import Model, Suite, SuiteCase, Workflow, adapt
    wf = Workflow(name="unit-answers", task="Answer with a number and its unit: {input}",
                  checks=(Check("has a unit", lambda out: any(u in out for u in ("kg", "m", "s"))),),
                  oracle=lambda out, expected: out.strip() == expected,
                  suite=Suite(dev=dev_cases, held_out=held_out_cases,
                              provenance={"dev": "caller", "held_out": "independent"}),
                  models={"local": Model(my_llm_caller)})
    mh = adapt(wf)                                   # mh.envelope says whether the workflow is valid
    mh.confirm(accepted_by="Ada")                    # a person freezes the workflow; version 0 is the initial profile
    mh.baseline()                                    # the active profile on dev and held-out, with a power estimate
    p = mh.candidate({"routing": {"answer": "local"}, "prompt": "Always give the SI unit."}, proposer="Bo")["proposal"]
    r = mh.evaluate(p)["report"]                     # paired, compute-matched, with a negative control
    d = mh.decide(r, p)                              # "recommend" at best (qualified-draft), or refuse/block with a code
    d["recovery"]                                    # when not recommended: why, and what to do next
    mh.diagnose()                                    # root causes on dev, and the components that address them
    mh.activate(d["decision"], accepted_by="Ada")    # a person, not the proposer; snapshot first
    mh.run("How heavy is a litre of water?")         # the harness.run result under the active profile
    mh.rollback(0, accepted_by="Ada")                # back to any version a person approved

The meta-gate needs at least 30 held-out cases, 5 where base and candidate differ, a win by the exact sign test at
0.05 / 5, no more compute than the base (or a win over the base given the same compute), costs within 5%, no rise in
false accepts, and a suite that can tell the base from an empty answer (owner ruling Q9). Phases 2-7 add proposers,
the monitor, the builder flow, structural changes, LoRA adapters, the MCP surface and the coding specialisation.

Not ported from the archive: the meta-harness is new code. Parts of the owner's kernel documents left out by ruling:

* environment interposition (PATH shims, egress and model proxies, K1 over an arbitrary agent; Q11, out of scope);
* a live canary before approval and live exploration (Q4: opt-in shadow runs only, whose output is never used);
* promotion without a person, and an LLM confidence that admits an irreversible action (ECHA C2; Q2);
* rewriting G6's own source in a user's install (Q7: code changes are person-approved patches to the user's own
  workspace only);
* a certificate that prints "proved": report lines are tagged checked, measured or assumed instead.
"""
from g6.metaharness.adapters import RELEASED, bundle_factory, bundle_workflow
from g6.metaharness.artifacts import ArtifactError, ArtifactStore
from g6.metaharness.lora import LoraSpec, adapter_loader
from g6.metaharness.api import JUDGING_PATTERNS, MetaHarness, adapt, judging_sha256
from g6.metaharness.builder import QUESTIONS, Builder
from g6.metaharness.evaluate import EvaluationReport, RunRecord, evaluate, power
from g6.metaharness.executor import (
    DEFAULT_SYSTEM,
    ExecutorFactory,
    ProfileContext,
    code_factory,
    model_call_factory,
    null_factory,
)
from g6.metaharness.gate import GateDecision, decide
from g6.metaharness.propose import (
    DevFailure,
    HintStrategy,
    ModelStrategy,
    ProposerView,
    RecoveryStrategy,
    SearchStrategy,
    Strategy,
)
from g6.metaharness.recovery import Cause, Recovery, Remedy
from g6.metaharness.schema import (
    Budgets,
    Capability,
    EvidencePolicy,
    Model,
    Objective,
    PipelineStep,
    Profile,
    Proposal,
    Suite,
    SuiteCase,
    Workflow,
    default_objectives,
    parse_profile,
)
from g6.metaharness.store import ProfileStore, RunLedger
from g6.metaharness.trace import MeasuredStep, MeasuredTrace, validate_dag
from g6.metaharness.trace_proposers import CostStrategy, QualityStrategy, SpeedStrategy
from g6.metaharness.monitor import detect_agreement_collapse, detect_anchor_decoupling
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.promotion import PromotionPolicy, PromotionRequest

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
# ``coding`` (imported on its own, never by this package) is the only module that reaches workforce and the gate.
__g6_requires__: tuple[str, ...] = ("action_gating", "align_prompt_library", "bundles", "coding_agent_gate", "context",
                                     "distillation", "guards", "harness", "integrity", "intent_engine", "learning",
                                     "llm_router", "navigator", "reasoning", "self_training", "workforce", "workspace")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = (
    "repeated_mistake", "unchecked_self_change", "untested_prompt_change", "unseen_early_warning",
    "excessive_token_use", "hidden_tradeoff", "weak_link", "inconsistent_workflow_model", "slow_workflow",
)
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"lora": ("torch", "transformers", "peft")}

__all__ = ["PromotionPolicy", "PromotionRequest", "FailureTraceStrategy", "detect_agreement_collapse", "detect_anchor_decoupling", "CostStrategy", "QualityStrategy", "SpeedStrategy", "MeasuredStep", "MeasuredTrace", "validate_dag", "DEFAULT_SYSTEM", "JUDGING_PATTERNS", "QUESTIONS", "RELEASED", "ArtifactError", "ArtifactStore", "Budgets",
           "Builder", "Capability", "Cause", "DevFailure", "EvaluationReport", "EvidencePolicy", "ExecutorFactory",
           "GateDecision", "HintStrategy", "LoraSpec", "MetaHarness", "Model", "ModelStrategy", "Objective",
           "PipelineStep", "Profile", "ProfileContext", "ProfileStore", "Proposal", "ProposerView", "Recovery",
           "RecoveryStrategy", "Remedy", "RunLedger", "RunRecord", "SearchStrategy", "Strategy", "Suite", "SuiteCase", "Workflow", "adapt",
           "adapter_loader",
           "bundle_factory", "bundle_workflow", "code_factory", "decide", "default_objectives", "evaluate",
           "judging_sha256", "model_call_factory", "null_factory", "parse_profile", "power"]
