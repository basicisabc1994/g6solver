# SPDX-License-Identifier: Apache-2.0
"""Shared tool guidance storage below the MCP surfaces."""
from __future__ import annotations

import re
from typing import Any

# Kept from the tool objects before shortening their wire descriptions. No component or host text belongs here.
_DETAILS: dict[str, dict[str, dict[str, Any]]] = {}

# Concise purposes for tools whose original opening sentence also described fields or a workflow.
_PURPOSES = {
    "g6_describe": "Describe a family, a tool's full guidance, or a component's operations and dependencies.",
    "g6_review_patch": "Assess a Python patch against stated requirements in confined runs.",
    "g6_review_until_clean": "Run one step of the patch review and repair loop.",
    "g6_knowledge_sources": "List indexed sources, their states and sharing status.",
    "g6_knowledge_revoke": "Revoke an indexed source and its sharing grants immediately.",
    "g6_knowledge_job": "Read an indexing job's state and publication outcome.",
    "metaharness_diagnose": "Diagnose failures on the development split and suggest recovery steps.",
    "g6_premortem": "Propose intent, completion criteria and failure hypotheses before a change.",
    "g6_confirm_behaviour": "Ask the person a concrete behaviour question in their client's dialog.",
    "g6_mission_create": "Freeze a coding mission's completion criteria before changes exist.",
    "g6_improve_start": "Start the enrolled self-improvement loop after the person's consent.",
    "g6_improve_enrol": "Ask the person to enrol a domain and freeze its evaluation contract.",
    "g6_improve_resume": "Ask the person to lift a self-improvement pause.",
    "g6_improve_demote": "Ask the person to restore the previous self-improvement generation.",
    "g6_agentic_run": "Run an operation and a confined model substitute for a declared part.",
    "g6_workflow_export": "Export the local review record for the person to inspect.",
    "g6_workflow_delete": "Ask the person to delete part of the local review record.",
    "g6_suggestion_decide": "Ask the person to adopt, dismiss or reopen a review suggestion.",
}


def short_description(name: str, guidance: str) -> str:
    lead = re.split(r"(?<=[.!?])\s+", " ".join(guidance.split()), maxsplit=1)[0]
    purpose = _PURPOSES.get(name, lead)
    rule = "Do not upgrade reliability or answer consent dialogs."
    if name in {"g6_review_patch", "g6_review_until_clean"}:
        rule = "Never upgrade a patch's reliability."
    elif name == "g6_allowances":
        rule = "Unknown readings never justify increased use."
    description = f'{purpose} {rule} Details: g6_describe("{name}").'
    if not purpose or len(description.split()) > 40:
        raise ValueError(f"{name} needs a concise tool purpose")
    return description


def register_guidance(server_id: str, name: str, guidance: str) -> str:
    """SDK servers construct wire Tool copies; capture guidance at their registration boundary."""
    _DETAILS.setdefault(server_id, {})[name] = {"guidance": guidance}
    return short_description(name, guidance)

