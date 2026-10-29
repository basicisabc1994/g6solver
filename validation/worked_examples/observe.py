# SPDX-License-Identifier: Apache-2.0
"""What a run's transcript shows about G6's launch fixes W1 to W5 (PLAN.md section 9), read only from the archived
stream:

* ``server``: whether Claude Code's ``g6`` server started and how many of G6's tools it listed (W1, W5);
* ``replies``: per review, the size of G6's finished reply as the transcript holds it, whether Claude Code saved it
  to a file instead, and whether it was a refusal (W2);
* ``report_shown_verbatim``: per finished review, whether the agent showed G6's report whole, in a message of its
  own or its final report (W2);
* ``templates``: how many reviews carried template instances, how many G6 refused for their bindings, and whether a
  review with template instances was accepted after such a refusal (W4).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

SAVED = re.compile(r"Output has been saved to (/\S+?\.txt)")
BINDINGS = re.compile(r"template_instances\.\d+.*bindings")


def _text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def _report(parsed: Any) -> str | None:
    if isinstance(parsed, dict):
        if isinstance(parsed.get("report"), str) and "outcome" in parsed:
            return parsed["report"]
        for value in parsed.values():
            found = _report(value)
            if found:
                return found
    elif isinstance(parsed, list):
        for value in parsed:
            found = _report(value)
            if found:
                return found
    return None


def _norm(text: str) -> str:
    """Whitespace collapsed, and a Markdown quote's ``>`` markers taken off: a report quoted whole is shown whole."""
    return re.sub(r"\s+", " ", re.sub(r"(?m)^[ \t]*>[ \t]?", "", text)).strip()


def observe(objects: Iterable[Mapping[str, Any]], *, saved: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``saved``: the replies Claude Code saved to files, by the container path the transcript names."""
    server: dict[str, Any] = {}
    tools: dict[str, str] = {}
    reviews: list[dict[str, Any]] = []
    reports: list[str] = []
    shown: list[str] = []
    for obj in objects:
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            status = {s.get("name"): s.get("status") for s in obj.get("mcp_servers") or []}
            server = {"g6": status.get("g6"),
                      "g6_tools": sum(1 for t in obj.get("tools") or [] if str(t).startswith("mcp__g6__"))}
        if obj.get("type") == "result":
            shown.append(str(obj.get("result") or ""))
        msg = obj.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and obj.get("type") == "assistant":
                shown.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools[str(block.get("id"))] = name
                if name.endswith("g6_review_patch"):
                    reviews.append({"round": len(reviews) + 1, "instances": bool((block.get("input") or {})
                                                                                .get("template_instances")),
                                    "reply": None})
            elif block.get("type") == "tool_result" and reviews:
                if not tools.get(str(block.get("tool_use_id")), "").startswith("mcp__g6__"):
                    continue
                text = _text(block)
                found = SAVED.search(text)
                full = (saved or {}).get(found.group(1)) if found else None
                try:
                    parsed = json.loads(full if full is not None else text)
                except ValueError:
                    continue
                inner = parsed.get("result") if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict) \
                    else parsed
                report = _report(parsed)
                refused = isinstance(inner, dict) and bool(inner.get("error")) and not report
                if report is None and not refused:
                    continue                       # a pending or running job: not the review's reply
                reviews[-1]["reply"] = {"round": reviews[-1]["round"], "chars": len(text) if not found else
                                        (len(full) if full is not None else None),
                                        "saved_to_file": bool(found), "refused": refused,
                                        "bindings_refusal": refused and bool(BINDINGS.search(str(inner.get("error"))))}
                if report:
                    reports.append(report)
    said = [_norm(s) for s in shown]
    refused_bindings = [r for r in reviews if r["reply"] and r["reply"]["bindings_refusal"]]
    first_refusal = min((r["round"] for r in refused_bindings), default=None)
    return {"server": server,
            "replies": [{k: v for k, v in r["reply"].items() if k != "bindings_refusal"} for r in reviews if r["reply"]],
            "report_shown_verbatim": [any(_norm(rep) in s for s in said) for rep in reports],
            "templates": {"rounds_with_instances": sum(r["instances"] for r in reviews),
                          "refused_for_bindings": len(refused_bindings),
                          "accepted_after_a_refusal": first_refusal is not None and any(
                              r["instances"] and r["round"] > first_refusal and r["reply"] and not r["reply"]["refused"]
                              for r in reviews)}}


def _assessment(obj: Any) -> dict | None:
    """The first dict under ``obj`` holding ``completion`` (G6's full assessment), outermost first."""
    if isinstance(obj, dict):
        if isinstance(obj.get("completion"), dict):
            return obj
        for value in obj.values():
            found = _assessment(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _assessment(value)
            if found is not None:
                return found
    return None


def proposer_summary(saved: Any) -> dict[str, Any]:
    """Round 2: what G6's own model did in one review (its full assessment, as G6 saved it): the outcome and reasons,
    the proposer's stages and model calls, the checks and template instances it proposed (ids ``g6p-``) with their
    results, the proposals G6 refused, the defects reproduced and the next actions (where a reproduction's command
    is)."""
    a = _assessment(saved) or {}
    acts = [x for x in (a.get("completion") or {}).get("activities") or [] if isinstance(x, dict)]
    calls = [x for x in acts if x.get("activity") == "model call"]
    return {"outcome": a.get("outcome"),
            "reasons": [(r.get("stage"), r.get("code")) for r in a.get("reasons") or [] if isinstance(r, dict)],
            "stages": [{k: x.get(k) for k in ("stage", "result", "reason") if x.get(k) is not None}
                       for x in acts if x.get("activity") == "proposer stage"],
            "model_calls": len(calls), "model_call_errors": sum(1 for x in calls if x.get("result") == "error"),
            "proposed": [{"check": x.get("check"), "template": x.get("template", ""), "result": x.get("result")}
                         for x in acts if str(x.get("check", "")).startswith("g6p-")],
            "refused": [{k: x.get(k) for k in ("stage", "requirement", "reason")}
                        for x in acts if x.get("activity") == "proposal"],
            "defects_reproduced": (a.get("completion") or {}).get("defects_reproduced") or [],
            "next_actions": a.get("next_actions") or []}


def observe_run(folder: Any) -> dict[str, Any]:
    """``observe`` on an archived run folder: its ``stream.jsonl``, with the replies Claude Code saved to files read
    from the run's ``claude_projects`` copy."""
    from pathlib import Path

    folder = Path(folder)
    lines = (folder / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    objects = [json.loads(line) for line in lines if line.strip().startswith("{")]
    saved = {}
    for line in lines:
        for found in SAVED.finditer(line):
            path = found.group(1)
            if "/.claude/projects/" in path:
                local = folder / "claude_projects" / path.split("/.claude/projects/", 1)[1]
                if local.is_file():
                    saved[path] = local.read_text(encoding="utf-8", errors="replace")
    return observe(objects, saved=saved)


if __name__ == "__main__":
    import sys

    print(json.dumps(observe_run(sys.argv[1]), indent=1))
