# SPDX-License-Identifier: Apache-2.0
"""Profile packs: share a tuned profile (META_HARNESS_PLAN.md 4.5 item 4, Phase 4).

A pack holds one profile, the text of its hints, and the evidence its exporter states. Importing one yields a
*candidate* (the ``learning.bundle`` rule): its stated evidence is shown back and never used, its hints enter the
importer's theory store as candidates, and it must win on the importer's own held-out split like any other proposal.
A pack whose text trips ``g6.learning``'s secret scan is refused on export and on import.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from g6.context import scrub
from g6.core.ledger import digest
from g6.learning.bundle import scan_text
from g6.learning.schema import Theory

FORMAT = "g6-profile-pack/1"
_THEORY_FIELDS = ("theory_id", "failure_code", "instruction", "trigger", "rationale", "prior_strength")


def build_pack(*, workflow: str, workflow_digest: str, profile: dict[str, Any], theories: list[Theory],
               stated_evidence: dict[str, Any] | None) -> dict[str, Any]:
    body = {"format": FORMAT, "workflow": workflow, "workflow_digest": workflow_digest, "profile": profile,
            "theories": [{k: asdict(t)[k] for k in _THEORY_FIELDS} for t in theories],
            "stated_evidence": stated_evidence or {}}
    return {**body, "content_digest": digest(body)}


def risks(text: str) -> list[str]:
    """``g6.learning``'s risky-content codes, plus ``secret_value`` when ``g6.context.scrub`` would redact something."""
    return scan_text(text) + (["secret_value"] if scrub(text) != text else [])


def write_pack(pack: dict[str, Any], path: str | Path) -> list[str]:
    """The risk codes found (the pack is not written when there are any)."""
    text = json.dumps(pack, sort_keys=True, ensure_ascii=False, indent=1)
    found = risks(text)
    if not found:
        Path(path).write_text(text + "\n", encoding="utf-8")
    return found


def read_pack(path: str | Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
        pack = json.loads(text)
    except (OSError, ValueError) as exc:
        return None, [f"not a readable pack: {type(exc).__name__}"]
    problems = []
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        return None, [f"not a {FORMAT} pack"]
    body = {k: v for k, v in pack.items() if k != "content_digest"}
    try:
        ok = digest(body) == pack.get("content_digest")
    except ValueError:
        ok = False
    if not ok:
        problems.append("the pack's content does not match its digest")
    found = risks(text)
    if found:
        problems.append(f"the pack trips the secret scan: {found}")
    return pack, problems


def theories_of(pack: dict[str, Any]) -> list[Theory]:
    out = []
    for doc in pack.get("theories") or []:
        if isinstance(doc, dict) and all(isinstance(doc.get(k), str) for k in ("theory_id", "failure_code",
                                                                                 "instruction")):
            out.append(Theory(doc["theory_id"], doc["failure_code"], doc["instruction"],
                              trigger=str(doc.get("trigger") or "always"), rationale=str(doc.get("rationale") or ""),
                              prior_strength=float(doc.get("prior_strength") or 0.5)))
    return out
