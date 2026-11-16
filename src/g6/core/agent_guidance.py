# SPDX-License-Identifier: Apache-2.0
"""Program guidance for agents never teaches them to lift the person's block."""
from __future__ import annotations

import re

PERSON = "This op is sensitive. The person must allow it themselves. Tell the person and stop."
WITHHELD = "Results were withheld from a source not shared with this provider. Tell the person."
_AUTHORITY = re.compile(
    r"allow\s*=|G6_MCP_SENSITIVE|allow_execution|\bconfirm\(|\bapprover\b|"
    r"autonomous_low_risk|\bpromote\(|setup --allow|allow parameter")


def skill_for_agent(text: str | None) -> str | None:
    """Do not route a person's Python approval examples into an agent's instructions.

    Reject the whole instructional document rather than leaving a partly executable example.
    Component operation names and input fields remain available through describe and help.
    """
    if text is not None and _AUTHORITY.search(text):
        return ("Use g6_describe for this component's declared operations and input fields. "
                "The person decides every sensitive operation themselves. Tell the person and stop at a refusal.")
    return text


def help_for_agent(component: str) -> str:
    """Use the declared operation templates, never a module's free functions or help prose."""
    from g6.skills import corpus

    lines = []
    for entry in corpus.load()["entries"]:
        if entry["kind"] == "op" and entry["component"] == component:
            lines.append(entry["id"][3:] + (": " + PERSON if entry["sensitive"] else
                         ": params_json template " + entry["call"]["arguments"]["params_json"]))
    return "\n".join(lines) or "No registered operation is available through the G6 door."
