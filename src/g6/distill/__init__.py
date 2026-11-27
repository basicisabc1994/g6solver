# SPDX-License-Identifier: Apache-2.0
"""Training-only method proposals, static test-change observations and independently gated library admission.

The distill Python API keeps failure records and candidate methods in append-only
ledgers. facts.extract_facts summarises explicit source mappings without running
them; detectors reuse the existing weakening scanner. Synthesis needs an injected
caller and proposes candidates only. acceptance_gate owns held-out evaluation and
library authority; runner uses host-injected capabilities and observes stop and
autonomy gates. Reported failures and predictions grant no admission authority.
"""

__g6_component__ = True
__g6_mitigates__: tuple[str, ...] = ("test_suite_missing_or_weak", "unhelpful_lesson")
__g6_introduces__: tuple[str, ...] = ("overfit_to_examples",)
__g6_requires__: tuple[str, ...] = ("cognitive_bridge", "distillation", "harness", "metaharness", "reasoning",
                                    "safety_engine", "self_maintenance", "self_model", "self_training", "workforce")

# Synthesis refusals are local to this component; core bound registries are unchanged.
__g6_refusals__ = {
    "G6_E_DISTILL_SEALED_ACCESS": "person",
    "G6_E_JOB_HOST_REQUIRED": "person",
    "G6_E_SELF_MODEL_INPUTS": "person",
    "G6_E_RESEARCH_AUTHORITY": "person",
    "G6_E_RESEARCH_HOST_REQUIRED": "person",
    "G6_E_RESEARCH_STOPPED": "person",
    "G6_E_ISOLATION_UNAVAILABLE": "person",
    "G6_E_SYNTH_BINDINGS": "input",
    "G6_E_SYNTH_CALL_LIMIT": "none",
    "G6_E_SYNTH_CASE_LITERAL": "input",
    "G6_E_SYNTH_DEPENDENCY": "input",
    "G6_E_SYNTH_DUPLICATE_CASE": "none",
    "G6_E_SYNTH_EXECUTION": "none",
    "G6_E_SYNTH_MODEL": "none",
    "G6_E_SYNTH_NO_CALLER": "person",
    "G6_E_SYNTH_PACING": "person",
    "G6_E_SYNTH_SOURCE": "input",
    "G6_E_SYNTH_TRAINING_ONLY": "input",
    # The distill-reach-fu repairs (07e0ffe8, 5fbc2bc2, fef7645e, 96188b1d), git on PATH (34314d95) and intake
    # (61e71811): who repairs each, read from where it is written.
    "G6_E_DISTILL_IDENTITY_UNKNOWN": ("none", "G6 could not tell whether this method is one held or withdrawn: tell "
                                              "the person what the refusal says."),
    "G6_E_DISTILL_INPUT": ("input", "The input did not fit, an entry is missing, or the request stopped after earlier "
                                    "steps were undone: read the current durable state, then fix your own call; never "
                                    "send it unchanged."),
    "G6_E_DISTILL_JOURNAL_TARGET": "none",
    "G6_E_DISTILL_PARTIAL_WRITE": ("person", "A write's recovery is incomplete: tell the person the durable state the "
                                             "refusal names; nothing may be retried until it is inspected."),
    "G6_E_DISTILL_RECORDED_CASES": "none",
    "G6_E_DISTILL_RECURRENCE_HELD": ("person", "wait"),
    "G6_E_DISTILL_STATUS_CHANGED_DURING_RUN": "retry",
    "G6_E_GIT_MISSING_BINARY": ("person",
        "git is not installed where G6 runs: tell the person; g6 doctor says what this computer needs.", "g6 doctor"),
    "G6_E_SYNTH_COHORT": ("input", "The record names a cohort that conflicts with the one already held for its unit: "
                                   "the earlier custody is kept, so fix the cohort in your own call."),
}
