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
    "G6_E_SYNTH_EXECUTION": "none",
    "G6_E_SYNTH_MODEL": "none",
    "G6_E_SYNTH_NO_CALLER": "person",
    "G6_E_SYNTH_PACING": "person",
    "G6_E_SYNTH_SOURCE": "input",
    "G6_E_SYNTH_TRAINING_ONLY": "input",
}
