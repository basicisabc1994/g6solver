# SPDX-License-Identifier: Apache-2.0
"""Untrusted pytest JUnit outcomes; console text is only a cross-check."""
from collections import Counter
from pathlib import Path
import re

from g6.core.xml import read_xml


def read_junit(path: Path):
    root = read_xml(path)
    if root.tag not in ("testsuites", "testsuite"):
        raise ValueError("not a JUnit report")
    return root


def tally(root) -> dict[str, int]:
    counts = Counter()
    for case in root.iter("testcase"):
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["error"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return dict(counts)


def aggregate(counts: dict) -> dict[str, int]:
    return {"passed": counts.get("passed", 0) + counts.get("xpassed", 0),
            "failed": counts.get("failed", counts.get("failures", 0)),
            "error": counts.get("error", counts.get("errors", 0)),
            "skipped": counts.get("skipped", 0) + counts.get("xfailed", 0)}


def outcomes(root, *, control: bool = False) -> dict[str, str]:
    """Per-node outcomes bound by pytest's user properties; no node ID is guessed from prose."""
    found = {}
    order = {"passed": 0, "skipped": 1, "xfailed": 1, "error": 2, "failed": 3}
    for case in root.iter("testcase"):
        if control and case.get("name") == "g6-positive-control":
            continue
        problems = [*case.findall("error"), *case.findall("skipped")]
        if any(p.get("message") in ("collection failure", "collection skipped") for p in problems):
            continue
        ids = [p.get("value") for p in case.findall("properties/property") if p.get("name") == "g6.nodeid"]
        if len(ids) != 1 or not ids[0]:
            raise ValueError("JUnit testcase has no unique recorded identity")
        node = ids[0]
        skipped = case.find("skipped")
        outcome = ("failed" if case.find("failure") is not None else
                   "error" if case.find("error") is not None else
                   "xfailed" if skipped is not None and skipped.get("type") == "pytest.xfail" else
                   "skipped" if skipped is not None else "passed")
        if node not in found or order[outcome] > order[found[node]]:
            found[node] = outcome
    return found


def agrees(counts: dict, console: str) -> bool:
    said = console_tally(console)
    return said is not None and aggregate(counts) == aggregate(said)


_COUNT = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")


def console_tally(stdout: str) -> dict | None:
    """A diagnostic cross-check only; console output can never establish a pass."""
    for line in reversed(str(stdout or "").strip().splitlines()):
        found = _COUNT.findall(line)
        if found:
            counts = Counter()
            for count, word in found:
                counts["error" if word.startswith("error") else word] += int(count)
            return dict(counts)
        if "no tests ran" in line:
            return {}
    return None
