# SPDX-License-Identifier: Apache-2.0
"""V10's comparison of each search arm's results on the stated checks with arm A's (docs/validation/
patch_review_search/PROTOCOL.md, feasibility criterion 5): before the labels are read, a frozen script compares them
on every patch and lists the patches where they differ; every patch that a search arm's own review accepted and arm A
did not must be on that list.

A result on the stated checks is each stated check's verdict, by its id, as G6's reply gives it (V9's
``arms.stated_check_verdicts``): arm A's from V9's arm 3 (the patch's ``arm_a_stated_checks``), each search arm's from
its report. Two results differ when any check's verdict differs or a check is in one and not the other. The driver
runs ``compare`` once every arm has run on every patch, and records its result before any label is read.
"""
from __future__ import annotations

SEARCH_ARMS = ("C", "T")


def _accepts(disposition) -> bool:
    return bool(isinstance(disposition, dict) and disposition.get("accepted") is True)


def compare(rows: list[dict]) -> dict:
    """``rows``: one per patch, ``{patch, arm_a, arm_a_stated_checks, arms: {C, T: {own, stated_checks}}}``. Per search
    arm: the patches whose results differ from arm A's (``differ``, the list), those the list must hold (``must_hold``:
    its own review accepted, arm A did not), those of them it does not hold (``not_on_list``), and those whose results
    cannot be read (``unread``); ``holds`` is whether the rule holds, None while any result cannot be read."""
    found = {key: {arm: [] for arm in SEARCH_ARMS} for key in ("differ", "must_hold", "not_on_list", "unread")}
    for row in rows:
        ours = row.get("arm_a_stated_checks")
        for arm in SEARCH_ARMS:
            output = (row.get("arms") or {}).get(arm) or {}
            theirs = output.get("stated_checks")
            if not isinstance(ours, dict) or not isinstance(theirs, dict):
                found["unread"][arm].append(row["patch"])
            elif ours != theirs:
                found["differ"][arm].append(row["patch"])
            if _accepts(output.get("own")) and not _accepts(row.get("arm_a")):
                found["must_hold"][arm].append(row["patch"])
    for arm in SEARCH_ARMS:
        found["not_on_list"][arm] = [patch for patch in found["must_hold"][arm]
                                     if patch not in found["differ"][arm] and patch not in found["unread"][arm]]
    unread = any(found["unread"][arm] for arm in SEARCH_ARMS)
    found["holds"] = None if unread else not any(found["not_on_list"][arm] for arm in SEARCH_ARMS)
    return found
