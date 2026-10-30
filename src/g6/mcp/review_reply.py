# SPDX-License-Identifier: Apache-2.0
"""The patch review's reply over MCP: compact by default (worked examples, W2).

In the worked examples a finished review's whole reply ran to 64,000 characters and more, over what Claude Code takes
as a tool result: it saves a result over 50,000 characters (or 25,000 tokens) to a file, and warns over 10,000 tokens.
:func:`finish` saves the whole reply as JSON under G6's state folder (``reviews/<job id>.json``) and gives the compact
reply ``metaharness_job`` returns by default: ``assessment.report`` first, then the outcome, the reasons, the next
actions and the unknowns, the merge authorisation, the job id, the envelope's fields, and where the full assessment is
(that file, and ``metaharness_job`` with ``detail="full"``, which returns the whole reply as it was before). The report
is never cut, and every integrity reason is kept whole and first. The other lists keep their first items and each
text its first characters, at most, so the rest of the reply stays within ``BUDGET`` characters whatever the review
holds; ``full_assessment.shortened`` names what was cut.
A reply with no assessment, a refusal, is returned as it is. Nothing here decides anything.
"""
from __future__ import annotations

import json
import os
from typing import Any

#: The most characters the compact reply takes besides its report.
BUDGET = 12_000
#: The items each list keeps and the characters each text keeps, tried in turn until the reply fits ``BUDGET``.
SHORTENINGS = ((12, 1_000), (6, 300), (2, 120))
#: The envelope's fields the compact reply carries as they are.
ENVELOPE = ("ok", "completion_state", "reliability_label", "request_id", "run_id", "artifact_digest")
#: The fields of each evidence item it keeps.
EVIDENCE = ("source", "summary", "authority", "artifact_digest", "base_commit", "scope")
HOLDS = ("every field of the review: each requirement's status, assessment.completion (what was demonstrated, what is "
         "not established, each reproduction with its text, preservation, open questions, every activity), the scope "
         "with every check run, the gate, the grounding and all the evidence")


def save(reply: dict, job_id: str) -> str | None:
    """The whole reply as JSON, in a new file ``reviews/<job_id>.json`` under G6's state folder, readable by its owner
    only; its path, or ``None`` when it could not be written."""
    from g6.core.storage import state_path

    try:
        path = state_path("reviews", f"{job_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8",
                       newline="\n") as out:
            json.dump(reply, out, ensure_ascii=False, indent=1, default=str)
    except (OSError, ValueError, TypeError):
        return None
    return str(path)


def _shorten(value: Any, items: int, chars: int, where: str, cut: set[str]) -> Any:
    """``value`` with each list cut to ``items`` and each text to ``chars``; each place it cut is added to ``cut``."""
    if isinstance(value, str):
        if len(value) <= chars:
            return value
        cut.add(where)
        return value[:chars - 3] + "..."
    if isinstance(value, list | tuple):
        if len(value) > items:
            cut.add(where)
        return [_shorten(one, items, chars, where, cut) for one in value[:items]]
    if isinstance(value, dict):
        return {key: _shorten(one, items, chars, where, cut) for key, one in value.items()}
    return value


def compact(reply: dict, job_id: str, saved: str | None) -> dict:
    """The compact form of a finished review's ``reply`` (see the module's docstring); ``saved``, where the whole reply
    is, or ``None`` when it could not be saved."""
    assessment = reply.get("assessment")
    if not isinstance(assessment, dict):
        return reply
    report = str(assessment.get("report", ""))
    evidence = [{key: item[key] for key in EVIDENCE if key in item}
                for item in reply.get("evidence") or [] if isinstance(item, dict)]
    # every integrity reason comes before every other finding: kept whole and first, never cut (the security review
    # of W1-W5, F6); only the others are shortened
    reasons = [r for r in assessment.get("reasons") or [] if isinstance(r, dict)]
    integrity = [r for r in reasons if r.get("stage") == "integrity"]
    others = [r for r in reasons if r.get("stage") != "integrity"]
    how = f'metaharness_job(job_id="{job_id}", detail="full")'
    for items, chars in SHORTENINGS:
        cut: set[str] = set()

        def short(value: Any, where: str) -> Any:
            return _shorten(value, items, chars, where, cut)

        shown = {
            "assessment": {"report": report, "outcome": assessment.get("outcome"),
                           "reasons": [*integrity, *short(others, "assessment.reasons")],
                           "next_actions": short(assessment.get("next_actions") or [], "assessment.next_actions"),
                           "unknowns": short(assessment.get("unknowns") or [], "assessment.unknowns")},
            "merge": short(reply.get("merge"), "merge"),
            "job_id": job_id,
            "full_assessment": {
                "file": saved, "detail": how, "holds": HOLDS,
                "note": ("This is the compact reply: show assessment.report as it is. The full assessment is in the "
                         "file named here" if saved else
                         "This is the compact reply: show assessment.report as it is. G6 could not save the full "
                         "assessment to a file, and this server keeps it only until it restarts") +
                        f"; {how} returns it."},
            **{field: reply[field] for field in ENVELOPE if field in reply},
            "warning_card": short(reply.get("warning_card"), "warning_card"),
            "evidence": short(evidence, "evidence"),
        }
        shown["full_assessment"]["shortened"] = sorted(cut)
        kept = len(json.dumps(report, ensure_ascii=False)) + len(json.dumps(integrity, ensure_ascii=False))
        if len(json.dumps(shown, ensure_ascii=False)) - kept <= BUDGET:
            break
    return shown


def finish(reply: Any, job_id: str) -> Any:
    """What ``metaharness_job`` returns by default for a finished review: a refusal as it is; an assessment saved
    whole, then in its compact form."""
    if not isinstance(reply, dict) or not isinstance(reply.get("assessment"), dict):
        return reply
    return compact(reply, job_id, save(reply, job_id))


__all__ = ["BUDGET", "compact", "finish", "save"]
