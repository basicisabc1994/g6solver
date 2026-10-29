# SPDX-License-Identifier: Apache-2.0
"""V10's study runner: the driver's inputs, from V9's results (docs/validation/patch_review_search/PROTOCOL.md,
"Data": exactly V9's draw, patches, label contract and review contract; "Arms": arm A is V9's arm 3, reused).

``driver_inputs`` gives, in draw order, each non-empty task of V9's draw as the driver's patch (its id, draw index,
arm A's disposition, the review contract's ids, arm A's results on the stated checks, and arm A's findings), and each
empty or non-applying one as an excluded patch with its reason, never dropped.

**Arm A's findings** (``arm_a_findings``, by ``findings``): its failed stated checks and the regressions of the
repository's own tests G6 ran (V10 version 2, "Findings"), from V9's recorded arm-3 reply, the defects it reproduced
(``assessment.completion.defects_reproduced.stated_and_repository_checks``, kept by V9's ``arms.defects_reproduced``):
each one whose provenance and code are ``stated`` and ``check_failed``, or ``derived`` and ``derived_test_regressed``,
as the reproduction the reruns run with V9's arm 1 command, ``{file, text, node}``: the check's or test's node, its
file, and the file's text on the patched tree V9 reviewed. A finding whose file is not on that tree is kept with no
text, so the reruns count it as one they cannot run. A reply without a completion section (G6's review before
verification search) gives none. ``findings`` with ``search_arm`` gives a search arm's from its report the same way,
with its proposed checks' counterexamples and unconfirmed failures, for the arms' callables (VS-5) to use.

Building V9's trees, the arms' callables and the frozen parameters is the study's entry point, not built yet.
"""
from __future__ import annotations

from pathlib import Path

EXCLUDED = ("empty", "not_applying")


#: The defects of G6's own and the repository's checks that are findings (V10 version 2, "Findings"): a failed
#: stated check, and a regression of one of the repository's own tests that G6 ran.
STATED_FINDINGS = {("stated", "check_failed"), ("derived", "derived_test_regressed")}
#: A search arm's further findings: a proposed check's counterexample or unconfirmed failure (a formal trace
#: reproduced on the code is one of these too, its correspondence ``reproduced``).
PROPOSED_FINDINGS = frozenset({"unconfirmed_counterexample", "unconfirmed_failure"})


def findings(defects_reproduced: dict | None, patched_tree: Path, *, search_arm: bool = False) -> list[dict]:
    """The findings among the defects a G6 reply reproduced (``assessment.completion.defects_reproduced``), as the
    reproductions the reruns run, ``{file, text, node, check, code}``. A stated check's or a repository test's: its
    node, its file, and the file's text on the patched tree (none when the file is not there, so the reruns count it
    as not reproducible). A proposed check's (for a search arm only): the file, text and node G6 gives."""
    found = defects_reproduced if isinstance(defects_reproduced, dict) else {}
    kept = []
    for defect in found.get("stated_and_repository_checks") or []:
        if not isinstance(defect, dict) or (defect.get("provenance"), defect.get("code")) not in STATED_FINDINGS:
            continue
        node = (defect.get("reproduction") or {}).get("node") or defect.get("check")
        file = node.partition("::")[0]
        path = Path(patched_tree) / file
        kept.append({"file": file, "text": path.read_text(encoding="utf-8") if path.is_file() else None,
                     "node": node, "check": defect.get("check"), "code": defect.get("code")})
    for defect in (found.get("proposed_checks") or []) if search_arm else []:
        if not isinstance(defect, dict) or defect.get("code") not in PROPOSED_FINDINGS:
            continue
        if "formal" in defect and (defect["formal"].get("correspondence") or {}).get("result") != "reproduced":
            continue
        reproduction = defect.get("reproduction") or {}
        kept.append({"file": reproduction.get("file"), "text": reproduction.get("text"),
                     "node": reproduction.get("node"), "check": defect.get("check"), "code": defect.get("code")})
    return kept


def arm_a_findings(task: dict, patched_tree: Path) -> list[dict]:
    """Arm A's findings on ``task`` (one of V9's results' tasks), from V9's recorded arm-3 reply, as reproductions."""
    return findings(((task.get("details") or {}).get("g6") or {}).get("defects_reproduced"), patched_tree)


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
