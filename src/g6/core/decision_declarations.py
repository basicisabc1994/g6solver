# SPDX-License-Identifier: Apache-2.0
"""Declared output positions for certificate-bound components we cannot edit.

Keys are the imported producer modules themselves. A same-named module is not
the producer, and component-owned declarations always take precedence.
"""
from g6 import action_gating, action_logic, epistemic_integrity

DECISIONS = {
    action_gating: {
        "evaluate": {
            "field": ("gate_decision", "decision"),
            "values": {"allow": True, "allow_with_notice": True,
                       "block": False, "BLOCK": False, "revise": False,
                       "require_confirmation": False, "require_additional_information": False,
                       "require_mitigation": False, "require_human_review": False},
            "authorization": {"allow": "allowed", "allow_with_notice": "allowed",
                              "block": "refused", "BLOCK": "refused", "revise": "refused",
                              "require_confirmation": "requires_person",
                              "require_additional_information": "requires_person",
                              "require_mitigation": "requires_person", "require_human_review": "requires_person"}},
        "budget_check": {"field": ("decision",), "values": {"allow": True, "block": False}},
        # Non-dry record returns recorded/code, no decision: deliberately unknown there.
        "budget_record": {"field": ("decision",), "values": {"allow": True, "block": False}},
    },
    action_logic: {
        op: {"field": ("result", "severity"), "values": {"block": False, "review": False}}
        for op in ("assess_material_action", "check_crisis_monitoring",
                   "disconfirming_evidence_severity", "reauthorisation_severity")
    },
    epistemic_integrity: {
        "adjudicate": {"field": ("overall_disposition",), "values": {
            "ALLOW": True, "ALLOW_WITH_LABEL": True, "ASK": False, "ESCALATE": False, "BLOCK": False}}
    },
}
