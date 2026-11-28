# SPDX-License-Identifier: Apache-2.0
"""G6's one text for the moment of need (plan revision 8, A): when a coding agent should reach for G6, in at most 150
words, coding first. It reaches the agent three ways: as the MCP server's ``instructions`` (``g6.mcp.build_server``), in
a delimited block of Codex's ``AGENTS.md`` that ``g6 setup --client codex`` offers the person (``agents_block``), and,
by its lead, at the end of the prompt hook's hint when a task starts (``g6.core.selector.hint``). It names only tools
the server registers; the routing check reads it as guidance and holds it to that.
"""
from __future__ import annotations

#: The first sentence, which also ends the prompt hook's hint; core keeps it, since core imports nothing of g6.mcp.
from g6.core.selector import LEAD

TEXT = LEAD + (
    " To check: call g6_review_patch with the repository, the task and the requirements, poll metaharness_job, and "
    "show the person its report as it is; or call g6_review_until_clean for the review loop. Before an unclear "
    "change, call g6_premortem, and g6_confirm_behaviour for a behaviour the task leaves open: only the person "
    "answers it. On G6_E_CLOUD_CONSENT_REQUIRED, g6_cloud_grant asks the person in their client's dialog; never "
    "grant for them. A refused sensitive operation is for the person to allow, with g6 setup --allow <name>. G6 "
    "checks and challenges work; it does not do the task. Never call a result verified unless G6 says so."
)

#: The lines that delimit G6's block in a Codex ``AGENTS.md``: setup replaces what stands between them and nothing else.
AGENTS_BEGIN = "<!-- g6:begin (g6 setup wrote this block; running it again replaces only this block) -->"
AGENTS_END = "<!-- g6:end -->"


def agents_block() -> str:
    """G6's block for a Codex ``AGENTS.md``: the text under a heading, between the delimiting lines."""
    return f"{AGENTS_BEGIN}\n## G6\n\n{TEXT}\n{AGENTS_END}"


__all__ = ["AGENTS_BEGIN", "AGENTS_END", "LEAD", "TEXT", "agents_block"]
