# SPDX-License-Identifier: Apache-2.0
"""Broken tasks (PROTOCOL.md, version 2, "Broken tasks"): tasks whose hidden tests cannot judge a patch by what the
task asks. Four kinds: 1 an unstated detail, 2 an underspecified statement, 3 a misleading statement, 4 weak tests.

- **The freeze entry** (``frozen_problems``) records, at each level, whether a published list of broken tasks was
  found (with its source, its file and the file's SHA-256 when it was), and whether the task screen runs (with its
  screener, who is not the label auditor, and the screen's record).
- **A published list's IDs** (``published_ids``) join the exclusions: every pool ID the list's file holds as a whole
  ID, read only when the file has the SHA-256 the entry records.
- **The task screen** (``screened``): the tasks the screener marks label-invalid (a hidden test requires behaviour of
  the first three kinds, named with the test in one sentence), which the draw replaces, and the tasks with weak tests,
  which are kept.
- **The label audit** (``audit_problems``, ``audit``) records a kind, 1 to 4 or "other", for each label error. A patch
  labelled defective whose error is of the first three kinds is label-invalid: left out of the primary analysis,
  counted as a label error, and kept by a sensitivity analysis. The number of errors of the fourth kind is reported.
  Without an audit no patch is left out, and the report says the labels were not checked (``NOT_CHECKED``).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

KINDS = ("1", "2", "3", "4", "other")
#: The kinds on which a patch can do what the task asks and still be labelled defective.
INVALIDATING = frozenset({"1", "2", "3"})
NOT_CHECKED = ("the labels were not checked for broken tasks; OpenAI's audit of SWE-bench Pro's public tasks "
               "estimates that about 30% of them are broken")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ID_CHARACTER = "A-Za-z0-9_-"


def _named(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def frozen_problems(entry, *, auditor: str | None = None) -> list[str]:
    """What is wrong with a level's ``broken_tasks`` entry: ``published_list`` is ``{"found": false}`` or ``{"found":
    true, "source", "sha256", "file"}``; ``screen`` is ``{"on": false}`` or ``{"on": true, "screener", "record"}``, the
    screener not being ``auditor``, the label auditor."""
    if not isinstance(entry, dict):
        return ["the entry records the published-list check and whether the task screen runs (broken_tasks)"]
    problems = []
    listed = entry.get("published_list")
    if not isinstance(listed, dict) or listed.get("found") not in (True, False):
        problems.append("the published-list check says whether a list was found (broken_tasks.published_list.found)")
    elif listed["found"]:
        if not _named(listed.get("source")):
            problems.append("a published list that was found names its source")
        if not _SHA256.fullmatch(str(listed.get("sha256", ""))):
            problems.append("a published list that was found gives its file's SHA-256, 64 lower-case hexadecimal digits")
        if not _named(listed.get("file")):
            problems.append("a published list that was found names its file")
    screen = entry.get("screen")
    if not isinstance(screen, dict) or screen.get("on") not in (True, False):
        problems.append("the entry says whether the task screen runs (broken_tasks.screen.on)")
    elif screen["on"]:
        if not _named(screen.get("screener")):
            problems.append("a task screen that runs names its screener")
        elif auditor is not None and screen["screener"] == auditor:
            problems.append("the screener is not the label auditor: a screener could predict a patch's label")
        if not _named(screen.get("record")):
            problems.append("a task screen that runs names its record")
    return problems


def published_ids(path: str | Path, sha256: str, pool_ids) -> set[str]:
    """The pool IDs a published list's file holds, each as a whole ID (not part of a longer one); refused when the
    file's SHA-256 is not ``sha256``."""
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("the published list's file does not have the SHA-256 its freeze entry records")
    text = data.decode("utf-8", errors="replace")
    return {task for task in pool_ids
            if re.search(rf"(?<![{_ID_CHARACTER}]){re.escape(task)}(?![{_ID_CHARACTER}])", text)}


def screened(record) -> dict:
    """The task screen's record, ``{"screener", "tasks": {task_id: {"label_invalid", "test", "sentence",
    "weak_tests"}}}``: the tasks it marks label-invalid, each with the test and the screener's sentence, and the tasks
    it found with weak tests."""
    if not isinstance(record, dict) or not _named(record.get("screener")) or not isinstance(record.get("tasks"), dict):
        raise ValueError("the screen's record names its screener and gives its finding on each task it screened")
    invalid, weak = {}, []
    for task, found in sorted(record["tasks"].items()):
        if not isinstance(found, dict) or found.get("label_invalid") not in (True, False):
            raise ValueError(f"the screen's finding on {task} says whether it is label-invalid")
        if found["label_invalid"]:
            if not (_named(found.get("test")) and _named(found.get("sentence"))):
                raise ValueError(f"the screen marks {task} label-invalid without naming the test and the behaviour")
            invalid[task] = {"test": found["test"], "sentence": found["sentence"]}
        if found.get("weak_tests") is True:
            weak.append(task)
    return {"screener": record["screener"], "label_invalid": invalid, "weak_tests": weak}


def audit_problems(record) -> list[str]:
    """What is wrong with the label audit's record: null (no audit), or ``{"auditor", "audited", "label_errors",
    "errors": [{"task_id", "kind", "test"}]}``, one error per label error, each with its kind."""
    if record is None:
        return []
    if not isinstance(record, dict):
        return ["the audit is a record, or null when there was none"]
    problems = []
    audited, count = record.get("audited"), record.get("label_errors")
    if not (isinstance(audited, int) and isinstance(count, int) and 0 <= count <= audited):
        problems.append("the audit gives the patches audited and its label errors as whole numbers, the errors at most "
                        "the patches")
    errors = record.get("errors", [])
    if not isinstance(errors, list) or (isinstance(count, int) and len(errors) != count):
        problems.append("the audit records each label error with its kind (errors: one entry per label error)")
        return problems
    seen = set()
    for item in errors:
        if not isinstance(item, dict) or not _named(item.get("task_id")):
            problems.append("each label error names its patch (task_id)")
            continue
        if str(item.get("kind")) not in KINDS:
            problems.append(f"the label error on {item['task_id']} records its kind: one of {', '.join(KINDS)}")
        if item["task_id"] in seen:
            problems.append(f"{item['task_id']} has more than one label error")
        seen.add(item["task_id"])
    return problems


def audit(record, labels: dict[str, str | None]) -> dict | None:
    """The label audit's summary, with each patch's label from the run (``labels``): its counts, its errors by kind,
    the label-invalid patches (an error of the first three kinds on a patch labelled defective), the number of errors
    of the fourth kind, and any error of the first three kinds on a patch not labelled defective (``inconsistent``:
    counted, and its patch kept). None without an audit."""
    if record is None:
        return None
    by_kind = dict.fromkeys(KINDS, 0)
    invalid, inconsistent = [], []
    for item in record.get("errors", []):
        kind = str(item["kind"])
        by_kind[kind] += 1
        if kind in INVALIDATING:
            (invalid if labels.get(item["task_id"]) == "defective" else inconsistent).append(item["task_id"])
    return {"auditor": record.get("auditor"), "audited": record["audited"], "label_errors": record["label_errors"],
            "by_kind": by_kind, "label_invalid": sorted(invalid), "kind_4": by_kind["4"],
            "inconsistent": sorted(inconsistent)}
