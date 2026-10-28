# SPDX-License-Identifier: Apache-2.0
"""The A5 report check (PROTOCOL.md, "Design of Experiment 2", amendment A5): every arm-3 report must keep three
things apart, checked before results are unblinded. A report missing a part is a tool error in Experiment 1 and is not
shown in Experiment 2.

1. ``contract_with_evidence``: ``assessment.requirements`` holds the review contract's requirements, the same ids in
   the same order, each with a status G6 defines and a reason; a requirement that passed or failed names at least one
   check, each with its id and verdict.
2. ``requirements_without_adequate_check``: present, and exactly the requirements whose status is ``not_established``.
3. ``specification_note``: the standing note, word for word as frozen here, that the task's specification may be
   incomplete and G6 cannot rule that out.
"""
from __future__ import annotations

PARTS = ("contract_with_evidence", "requirements_without_adequate_check", "specification_note")
STATUSES = ("passed_on_stated_checks", "failed", "not_established")
#: The note as G6's review gives it (``g6.harness.patch_review.SPECIFICATION_NOTE``), frozen with this script.
SPECIFICATION_NOTE = ("The task's specification may be incomplete: G6 checks the requirements stated here, and it "
                      "cannot rule out in general that the task needs something no requirement states.")


def _evidence(requirements: object, ids: list[str]) -> bool:
    if not isinstance(requirements, list) or [r.get("id") for r in requirements if isinstance(r, dict)] != ids:
        return False
    for req in requirements:
        if req.get("status") not in STATUSES or not req.get("reason") or not isinstance(req.get("checks"), list):
            return False
        if req["status"] != "not_established" and not (req["checks"] and all(
                isinstance(c, dict) and c.get("id") and c.get("verdict") for c in req["checks"])):
            return False
    return True


def check(reply: dict, contract_ids: list[str]) -> dict:
    """``{"complete", "missing"}`` for one arm-3 reply, against the ids of the review contract it was given."""
    assessment = reply.get("assessment") if isinstance(reply, dict) else None
    if not isinstance(assessment, dict):
        return {"complete": False, "missing": list(PARTS)}
    missing = []
    requirements = assessment.get("requirements")
    if not _evidence(requirements, list(contract_ids)):
        missing.append(PARTS[0])
    unchecked = assessment.get("requirements_without_adequate_check")
    expected = ([r["id"] for r in requirements if r.get("status") == "not_established"]
                if isinstance(requirements, list) and all(isinstance(r, dict) for r in requirements) else None)
    if not isinstance(unchecked, list) or expected is None or unchecked != expected:
        missing.append(PARTS[1])
    if assessment.get("specification_note") != SPECIFICATION_NOTE:
        missing.append(PARTS[2])
    return {"complete": not missing, "missing": missing}
