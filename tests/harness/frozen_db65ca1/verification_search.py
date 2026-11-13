"""Frozen db65ca1 reading; stdlib and sibling fixtures only.

Copied source; receipt version/executable/digest metadata use fixed test adapters.
"""
from __future__ import annotations

import re
import json
from . import repo_tests
from .patch_review import _covers, _in_repository, _collection_errors


MAX_OBSERVATIONS = 32


MAX_OBSERVATION_CHARS = 1000


MAX_OBSERVATION_DEPTH = 16


OBSERVATION = "G6-OBSERVATION"


_FAILED_LINE = re.compile(r"^FAILED (\S+)(?: - (.*))?$", re.MULTILINE)


_RAISED_LINE = re.compile(r"^E\s+(\S.*)$", re.MULTILINE)


_CUT = re.compile(r"^\.\.\. \[\d+ bytes of output omitted\] \.\.\.$", re.MULTILINE)


_OBSERVED = re.compile(rf"{OBSERVATION} (.*)$", re.MULTILINE)


RAN_WITH = ("python", "interpreter_source", "isolation", "import_path", "own_config")


def _messages(ran: dict, node: str) -> list[str]:
    """The failure messages the run recorded in full (``failure_messages``) for the tests of ``node`` or its file."""
    full, file = _in_repository(ran), node.partition("::")[0]
    return [text for named, text in (ran.get("failure_messages") or {}).items()
            if _covers(node, full(named)) or full(named) == file]


def _failure(ran: dict, node: str) -> str:
    """How a check failed: its recorded message (a collection's: its last ``E`` line), else pytest's summary or its
    last ``E`` line in the output ("" when none says)."""
    for text in _messages(ran, node):
        raised = _RAISED_LINE.findall(text)
        line = raised[-1] if raised else next((part for part in text.splitlines() if part.strip()), "")
        if line.strip():
            return line.strip()[:300]
    output = ran.get("output") or ""
    for match in _FAILED_LINE.finditer(output):
        if match.group(2):
            return match.group(2).strip()[:300]
    raised = _RAISED_LINE.findall(output)
    return raised[-1].strip()[:300] if raised else ""


def _refused_access(ran: dict, node: str) -> bool:
    """Whether the failure is an operating-system error, or was raised from or during one (a refusal a client library
    wraps): inside the boundary a refused access (a file outside the copy, the network, a name lookup) raises one, so
    G6 cannot tell it from a failure of the code. Read only from G6's own record of the run (``os_errors``, which its
    plugin reads from the exception objects) for the check, a test of it or its file; never from text the check or
    the code under test printed (reviewer B's B2)."""
    full, file = _in_repository(ran), node.partition("::")[0]
    return any(_covers(node, full(named)) or full(named) == file for named in ran.get("os_errors") or ())


def _uncollected(ran: dict, node: str) -> bool:
    """Whether pytest could not collect the check's own file (a collection error named after it)."""
    full, file = _in_repository(ran), node.partition("::")[0]
    return file in {full(rel) for rel in _collection_errors(ran.get("output") or "")} or any(
        full(named) == file for named in ran.get("failure_messages") or {})


def _depth(value) -> int:
    """Levels of nesting, counted without recursion: a value is one level, and each list or object around it adds one;
    counting stops past ``MAX_OBSERVATION_DEPTH``."""
    deepest, pending = 0, [(value, 1)]
    while pending and deepest <= MAX_OBSERVATION_DEPTH:
        item, level = pending.pop()
        deepest = max(deepest, level)
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, level + 1) for child in children)
    return deepest


def _observations(output: str) -> list[dict] | None | str:
    """A probe's observations in its output (None when it recorded none), or why they cannot be read."""
    lines = [match.group(1) for match in _OBSERVED.finditer(output)]
    if not lines:
        return None
    if _CUT.search(output):
        return "its output was longer than G6 keeps of a run, so its observations cannot be read in full"
    if len(lines) > MAX_OBSERVATIONS:
        return f"it recorded {len(lines)} observations; a probe records at most {MAX_OBSERVATIONS}"
    found = []
    for line in lines:
        if len(line) > MAX_OBSERVATION_CHARS:
            return f"an observation holds more than {MAX_OBSERVATION_CHARS} characters"
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            return f"an observation is not JSON: {line[:80]!r}"
        if not (isinstance(item, dict) and set(item) == {"input", "output"}):
            return f"an observation is not {{\"input\": ..., \"output\": ...}}: {line[:80]!r}"
        if _depth(item) > MAX_OBSERVATION_DEPTH:
            return f"an observation is nested more than {MAX_OBSERVATION_DEPTH} levels deep"
        found.append(item)
    return found


def reading(ran: dict, node: str) -> dict:
    """What one run of a check says: ``result`` ``passed`` (every test of the check passed its call), ``failed`` (one
    failed its call, or the check's own file could not be collected, ``collection`` true; either way not on an
    operating-system error or a refusal, and its messages or output say how) or ``error`` (anything else: not finished
    in time, its copy changed, its report not believed, nothing collected, a setup that failed or a skip, an
    operating-system error, a failure that does not say how); with the run's ``verdict``, ``detail``, ``seconds``,
    ``tree_digest`` (its copy as the run began), what it ran with (``RAN_WITH``) and, for a pass, its ``observations``
    (None when it recorded none)."""
    verdict, output = ran.get("verdict"), ran.get("output") or ""
    found = {"verdict": verdict, "seconds": float(ran.get("seconds") or 0.0),
             "tree_digest": ran.get("source_before") or "", **{key: ran[key] for key in RAN_WITH if key in ran}}

    def error(why: str) -> dict:
        return {**found, "result": "error", "detail": why}

    def failed(how: str, **extra) -> dict:
        if not how:   # the repository's options hid it (--tb=no -rN, say): a refused access cannot be ruled out
            return error("it failed, and nothing it recorded says how, so G6 cannot tell a failure of the code from "
                         "an access the boundary refused")
        if _refused_access(ran, node):
            return error("it failed on an operating-system error, which is how a boundary refuses an access (a file "
                         f"outside its copy, the network, a name lookup), or on such a refusal: {how}")
        return {**found, "result": "failed", "detail": how, **extra}

    if verdict == "timed_out":
        return error(f"it did not finish within its time limit ({ran.get('said') or 'timed out'})")
    if verdict == repo_tests.CHANGED:
        return error("it changed files in its copy, so its result is not believed: "
                     + ", ".join((ran.get("changed") or [])[:5]))
    full = _in_repository(ran)
    mine = {full(n): phases for n, phases in (ran.get("test_outcomes") or {}).items() if _covers(node, full(n))}
    if not mine and verdict in (repo_tests.FAILED, "collection_error") and _uncollected(ran, node):
        # review 5, as tier 2 reads a test file: an import the patch broke fails the check where it is collected
        how = _failure(ran, node)
        return failed(f"could not be collected: {how}" if how else "", collection=True)
    if verdict not in repo_tests.RAN:
        return error(f"{verdict}: {ran.get('said') or 'no result'}")
    if not mine:
        return error("pytest ran no test of the check: it could not be collected, or names no test")
    if any(phases.get("setup") != "passed" or phases.get("teardown") == "failed" for phases in mine.values()):
        return error("a test of the check could not be set up, was skipped, or failed after its call")
    calls = {phases.get("call") for phases in mine.values()}
    if calls == {"passed"} and verdict == repo_tests.PASSED:
        observed = _observations(output)
        if isinstance(observed, str):
            return error(observed)
        return {**found, "result": "passed", "detail": f"{len(mine)} test(s) passed", "observations": observed}
    if "failed" in calls and verdict == repo_tests.FAILED:
        return failed(_failure(ran, node))
    return error(f"its tests did not run to a pass or a fail ({verdict})")


LOADED_ELSEWHERE = ("the run loaded changed code from outside the tree it tested, or cannot say what it loaded ({}), "
                    "so its result says nothing about the patch: name the folder that holds the package in "
                    "import_roots, or install the repository in editable mode, and review again")
