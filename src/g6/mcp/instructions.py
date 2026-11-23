# SPDX-License-Identifier: Apache-2.0
"""Family-first MCP instructions, with the existing setup and prompt-hook text kept for frozen client surfaces."""
from __future__ import annotations

from g6.mcp.families import family_table

#: The first sentence, which also ends the prompt hook's hint.
LEAD = ("Before you say a Python task is done, have G6 check it; when stuck, unsure which G6 tool fits, or after a "
        "G6 refusal, call g6_select with the task, your phase and the refusal's code.")

# Setup writes this established client guidance; changing the MCP instructions must not change CLI output.
SETUP_TEXT = LEAD + (
    " To check: call g6_review_patch with the repository, the task and the requirements, poll metaharness_job, and "
    "show the person its report as it is; or call g6_review_until_clean for the review loop. Before an unclear "
    "change, call g6_premortem, and g6_confirm_behaviour for a behaviour the task leaves open: only the person "
    "answers it. On G6_E_CLOUD_CONSENT_REQUIRED, g6_cloud_grant asks the person in their client's dialog; never "
    "grant for them. A refused sensitive operation is for the person to allow, with g6 setup --allow <name>. G6 "
    "checks and challenges work; it does not do the task. Never call a result verified unless G6 says so."
)

TEXT = family_table() + (
    '\nUse g6_describe("family") for members, then g6_describe("tool") for full guidance. '
    "Unsure? g6_select returns ranked calls. Before done: g6_review_patch or g6_review_until_clean; poll "
    "metaharness_job; show its report unchanged. Plan: g6_premortem; unclear behaviour: g6_confirm_behaviour; "
    "only the person answers. G6_E_CLOUD_CONSENT_REQUIRED: g6_cloud_grant asks them; never grant for them. "
    "Sensitive refusal: the person uses g6 setup --allow <name>. Never call a result verified unless G6 says so."
)

#: The lines that delimit G6's block in a Codex ``AGENTS.md``: setup replaces what stands between them and nothing else.
AGENTS_BEGIN = "<!-- g6:begin (g6 setup wrote this block; running it again replaces only this block) -->"
AGENTS_END = "<!-- g6:end -->"


def agents_block() -> str:
    """G6's block for a Codex ``AGENTS.md``: the text under a heading, between the delimiting lines."""
    return f"{AGENTS_BEGIN}\n## G6\n\n{SETUP_TEXT}\n{AGENTS_END}"


__all__ = ["AGENTS_BEGIN", "AGENTS_END", "LEAD", "SETUP_TEXT", "TEXT", "agents_block"]
