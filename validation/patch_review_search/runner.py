# SPDX-License-Identifier: Apache-2.0
"""V10's study runner: the driver's inputs, from V9's results (docs/validation/patch_review_search/PROTOCOL.md,
"Data": exactly V9's draw, patches, label contract and review contract; "Arms": arm A is V9's arm 3, reused).

``driver_inputs`` gives, in draw order, each non-empty task of V9's draw as the driver's patch (its id, draw index,
arm A's disposition, the review contract's ids, arm A's results on the stated checks, and arm A's findings), and each
empty or non-applying one as an excluded patch with its reason, never dropped.

**Arm A's findings** (``arm_a_findings``): its failed stated checks, from V9's recorded arm-3 reply, the defects it
reproduced (``assessment.completion.defects_reproduced.stated_and_repository_checks``, kept by V9's
``arms.defects_reproduced``): each one whose provenance is ``stated`` and code ``check_failed``, as the reproduction
the reruns run, ``{file, text, node}``: the stated check's node, its file, and the file's text on the patched tree V9
reviewed. A finding whose file is not on that tree is kept with no text, so the reruns count it as one they cannot run.
A regression of the repository's own tests (``derived``) is not a failed stated check, and a reply without a completion
section (G6's review before verification search) gives none.

Building V9's trees, the arms' callables and the frozen parameters is the study's entry point, not built yet.
"""
from __future__ import annotations

from pathlib import Path

EXCLUDED = ("empty", "not_applying")


def arm_a_findings(task: dict, patched_tree: Path) -> list[dict]:
    """Arm A's findings on ``task`` (one of V9's results' tasks), as reproductions for the reruns."""
    found = (((task.get("details") or {}).get("g6") or {}).get("defects_reproduced") or {})
    findings = []
    for defect in found.get("stated_and_repository_checks") or []:
        if not isinstance(defect, dict) or defect.get("provenance") != "stated" or defect.get("code") != "check_failed":
            continue
        node = (defect.get("reproduction") or {}).get("node") or defect.get("check")
        file = node.partition("::")[0]
        path = Path(patched_tree) / file
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        findings.append({"file": file, "text": text, "node": node, "check": defect.get("check"),
                         "code": defect.get("code")})
    return findings


def driver_inputs(v9: dict, patched_tree) -> tuple[list[dict], list[dict]]:
    """The driver's patches and excluded patches from V9's results (``v9["tasks"]``, in draw order);
    ``patched_tree(task_id)`` is the patched tree V9 reviewed for that task."""
    patches, excluded = [], []
    for index, task in enumerate(v9["tasks"]):
        if task.get("patch") in EXCLUDED:
            excluded.append({"id": task["task_id"], "draw_index": index, "reason": task["patch"]})
            continue
        if task.get("patch") != "non_empty":
            raise ValueError(f"{task['task_id']}: a patch state V9 does not give ({task.get('patch')!r})")
        details = (task.get("details") or {}).get("g6") or {}
        patches.append({"id": task["task_id"], "draw_index": index, "repository": task.get("repository"),
                        "arm_a": task["arms"].get("g6"),
                        "contract_ids": [requirement["id"] for requirement in task.get("contract") or []],
                        "arm_a_stated_checks": details.get("stated_checks"),
                        "arm_a_findings": arm_a_findings(task, patched_tree(task["task_id"]))})
    return patches, excluded
