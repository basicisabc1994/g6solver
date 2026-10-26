# SPDX-License-Identifier: Apache-2.0
"""The honest decision trace: for each planning decision, whether the model or the deterministic fallback made it.

Pure: no model, no I/O. Absolute paths in rationales (often exception text) are redacted before they reach an
envelope.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_UNIX_PATH = re.compile(r"/(?:home|Users|tmp|var|etc|root|mnt|opt|usr|private)/[^\s\"']*")


def redact(value: Any) -> str:
    if not value:
        return ""
    return _UNIX_PATH.sub("<path>", _WIN_PATH.sub("<path>", str(value)))


def summarize_agentic_evidence(decisions: Iterable[Mapping[str, Any]], *, model_supplied: bool) -> dict[str, Any]:
    """Summarise ``{decision, llm_used, degraded, detail, rationale}`` records.

    ``degraded`` is true when a model was supplied and a decision still fell back to the deterministic path. No
    model supplied is the library default, not a degradation; it is reported as ``model_supplied: False``.
    """
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for d in decisions:
        item = {"decision": str(d.get("decision") or "unknown"), "llm_used": bool(d.get("llm_used")),
                "degraded": bool(d.get("degraded")), "detail": redact(d.get("detail")),
                "rationale": redact(d.get("rationale"))}
        items.append(item)
        if item["degraded"]:
            warnings.append(f"{item['decision']} decision fell back to the deterministic path "
                            f"(reason: {item['rationale'] or 'unspecified'})")
    llm = sum(1 for i in items if i["llm_used"])
    degraded = sum(1 for i in items if i["degraded"])
    return {
        "model_supplied": model_supplied,
        "decisions": items,
        "decisions_by_type": {i["decision"]: {k: i[k] for k in ("llm_used", "degraded", "detail")} for i in items},
        "total_decisions": len(items),
        "llm_decisions": llm,
        "fallback_decisions": len(items) - llm,
        "degraded_count": degraded,
        "degraded": degraded > 0,
        "warnings": warnings,
    }


__all__ = ["redact", "summarize_agentic_evidence"]
