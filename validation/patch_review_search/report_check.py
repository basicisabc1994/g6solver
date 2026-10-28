# SPDX-License-Identifier: Apache-2.0
"""V10's report check of arms C and T (docs/validation/patch_review_search/PROTOCOL.md, "The report check", and
feasibility criterion 4). A frozen script checks every report of arms C and T before the labels are read:

1. the three parts V9's protocol requires (amendment A5): V9's own check, ``patch_review_utility.report_check``,
   reused, not repeated;
2. the completion section (``assessment.completion``): what was demonstrated, what was not established, which defects
   were reproduced (the stated and repository checks', and the proposed checks'), what was checked for preservation,
   test adequacy, open questions and the activities performed, each present in the form G6 gives it;
3. that no check a model originated is listed as passing a requirement: a requirement whose status is
   ``passed_on_stated_checks`` names only checks whose provenance is ``stated`` or ``derived`` (a check without a
   provenance is a stated one, as in G6's review before provenance was recorded);
4. that every formal result (the ``formal`` part of an activity or of a reproduced defect) gives its specification,
   its model and its correspondence to the code as separate parts, and that only a reproduced trace is reported as a
   finding about the code (a defect whose formal correspondence is ``reproduced``). A requirement's ``formal`` notes
   are G6's notes that a result is about the model only, not results of their own, and are not read here.

A report that fails the check counts as a tool error for that patch (the driver makes it one). A report that lists a
model-originated check as passing a requirement, or a formal result without its three parts, also fails feasibility
criterion 4.
"""
from __future__ import annotations

from patch_review_utility import report_check as a5

#: The completion section's parts, as G6's review gives them (``verification_search.completion``).
LISTS = ("demonstrated", "not_established", "preservation", "test_adequacy", "open_questions", "activities")
DEFECTS = ("stated_and_repository_checks", "proposed_checks")
COMPLETION = (*LISTS[:2], "defects_reproduced", *LISTS[2:])
#: The provenances of checks no model originated: the task's stated checks and the repository's own tests.
NOT_MODEL = frozenset({"stated", "derived"})
FORMAL_PARTS = ("specification", "model", "correspondence")


def _completion(completion) -> list[str]:
    if not isinstance(completion, dict):
        return ["the report has no completion section"]
    problems = [f"{name} is not given as a list" for name in LISTS if not isinstance(completion.get(name), list)]
    defects = completion.get("defects_reproduced")
    if not isinstance(defects, dict):
        problems.append("defects_reproduced is not given")
    else:
        problems += [f"defects_reproduced.{name} is not given as a list" for name in DEFECTS
                     if not isinstance(defects.get(name), list)]
    return problems


def _model_originated(requirements) -> list[dict]:
    found = []
    for req in requirements if isinstance(requirements, list) else []:
        if not isinstance(req, dict) or req.get("status") != "passed_on_stated_checks":
            continue
        for one in req.get("checks") or []:
            provenance = one.get("provenance", "stated") if isinstance(one, dict) else "stated"
            if provenance not in NOT_MODEL:
                found.append({"requirement": req.get("id"), "check": one.get("id"), "provenance": provenance})
    return found


def _split(formal) -> bool:
    return isinstance(formal, dict) and all(isinstance(formal.get(part), dict) for part in FORMAL_PARTS)


def _formal(assessment: dict) -> list[str]:
    completion = assessment.get("completion") if isinstance(assessment.get("completion"), dict) else {}
    defects = completion.get("defects_reproduced") if isinstance(completion.get("defects_reproduced"), dict) else {}
    problems = []
    for index, activity in enumerate(completion.get("activities") or [], 1):
        if isinstance(activity, dict) and "formal" in activity and not _split(activity["formal"]):
            problems.append(f"activity {index}: a formal result without its three parts (specification, model, "
                            "correspondence)")
    for name in DEFECTS:
        for defect in defects.get(name) or []:
            if not isinstance(defect, dict) or "formal" not in defect:
                continue
            label = defect.get("check") or "a defect"
            if not _split(defect["formal"]):
                problems.append(f"{label}: a formal finding without its three parts (specification, model, "
                                "correspondence)")
            elif defect["formal"]["correspondence"].get("result") != "reproduced":
                problems.append(f"{label}: a formal result that did not reproduce on the code is reported as a "
                                "finding")
    return problems


def check(reply: dict, contract_ids: list[str]) -> dict:
    """``{complete, missing, completion_problems, model_originated_passing, formal_problems}`` for one report of arm C
    or T, against the ids of the review contract it was given. ``missing`` names V9's parts that are missing and,
    when its completion section is short, ``completion_section``."""
    base = a5.check(reply, contract_ids)
    assessment = reply.get("assessment") if isinstance(reply, dict) else None
    assessment = assessment if isinstance(assessment, dict) else {}
    completion = _completion(assessment.get("completion"))
    missing = [*base["missing"], *(["completion_section"] if completion else [])]
    passing = _model_originated(assessment.get("requirements"))
    formal = _formal(assessment)
    return {"complete": not (missing or passing or formal), "missing": missing, "completion_problems": completion,
            "model_originated_passing": passing, "formal_problems": formal}
