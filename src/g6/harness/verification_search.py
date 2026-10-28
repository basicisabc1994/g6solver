# SPDX-License-Identifier: Apache-2.0
"""Verification search for G6's patch review: what the task asks, what it must keep and what it assumes (part 1);
checks the caller proposes, run on the base commit's code and on the patch and classified (part 2); and the completion
report every reply carries (``completion``).

Part 1. ``requirements_from_intent`` maps an intent contract (``g6.intent_engine.schema.IntentContract``) that the caller
passes onto the review's requirements. Its items count as caller-stated:

- the caller's own requirements come first, unchanged (``stated``, of kind ``requirement``);
- each success criterion becomes a ``stated`` requirement, ``intent.success_criteria.<index>``, with no check, so it
  is not established until a check is stated for it (by a caller's requirement with the same text, below);
- each non-goal and each failure criterion becomes a ``stated`` obligation to keep behaviour (kind ``preserve``),
  ``intent.non_goals.<index>`` or ``intent.failure_criteria.<index>``, not established without a check;
- text that is the same as an earlier normative requirement's, up to case, spacing and a closing full stop, is merged
  into that requirement (its ``merged_from`` lists the item), never doubled; nothing is merged into a requirement no one
  stated;
- the success and failure checks are left out, each named in an unknown. The ruling: an intent's ``Check`` is a
  predicate on the text of an answer (contains, absent, a bounded regular expression, a word count). It names no
  repository test, so none is a check of the patch;
- each assumption keeps its source (``explicit``, ``inferred`` or ``fallback``) and goes into the scope as an
  assumption the assessment depends on. An ``explicit`` one is normative. None becomes a requirement or gets a status;
- the other fields that hold items (constraints, declared risks, guardrails, checkpoints, breakpoints, subtasks) are
  not mapped, and each that holds any is named in an unknown.

Ids come from the field and the index, so the same intent gives the same ids on every call. Nothing in part 1 calls a
model, runs a check or reads the task's text (``IntentElicitor`` is not used).

Part 2. ``proposed_checks`` validates the checks a caller proposes, ``{id, requirement_id, intent, file, source,
test}``, at most ``MAX_CHECKS`` in all and ``MAX_SOURCE_BYTES`` of text each; anything else is refused before anything
runs. ``file`` is a relative path whose name is ``test_<name>.py``: never a conftest, a pytest or tox configuration, a
``.pth`` file or an ``__init__.py``, a path with ``..`` or an absolute one, a folder G6's copy leaves out or one that
holds installed code, or a link (``placements``). G6 places each check in a folder of its own, ``g6_check_`` and the
first 12 hexadecimal digits of the SHA-256 of its text, inside the folder ``file`` names (``placement``), never where a
path already exists in the repository. Each check's provenance is ``proposed``: the calling agent is a model, and its
proposals are never taken as independent of the patch's author (VS-D2). Nothing here calls a model.

``search`` runs each check twice, confined with no network, each time in a fresh scratch copy that holds no repository
history (no ``.git``) and only that check overlaid: on the patched tree, then at the base commit's code
(``repo_tests.run_at_base``); one whose run on the patch gives no result is not run at the base commit, since that
would decide nothing. Each run has its own time limit (``RUN_S``) inside the review's deadline; a check is started only
when both its runs could have ``MIN_RUN_S``, and the stage stops at the first that could not and lists the checks it
skipped. A run no operating-system backend confines is refused (``G6_E_ISOLATION_UNAVAILABLE``) before it
starts, whatever the person allowed: never run unconfined. The repository is never written.

``classify`` (plan rule 3), from what each run says (``reading``, which reads each failure from the message the run
recorded in full, ``repo_tests``'s ``failure_messages``, before pytest's output): a run that did not finish in time,
changed its copy, could not be believed, collected or set up, was skipped, or failed on an operating-system error or a
refusal a client library wrapped (G6's network refusal, a name that could not be looked up, an unreachable network:
inside the boundary that is what a refused access looks like), or without saying how, is a ``check_error``, counted
and never a finding. A check whose own file could not be collected (an import the patch broke) has failed, as tier 2
reads a test file, and its run at the base commit decides. Otherwise:

- ``preserve``: passes at the base commit and fails on the patch, ``counterexample``; fails on both,
  ``fails_on_base_too`` (nobody is accused); passes on the patch, ``not_refuted``;
- ``change``: fails on the patch, ``unconfirmed_failure`` (the incomplete fix a filter at the base would hide),
  whatever the base gives (a new function cannot even be imported there), unless its file could not be collected for a
  reason outside the repository's own code, a ``check_error``; passes on both, ``not_refuted``, noted as not exercising
  the change; fails at the base commit and passes on the patch, ``not_refuted``;
- a probe, a check that records observations instead of asserting: each observation is one line of its output,
  ``G6-OBSERVATION`` and a space then a JSON object ``{"input": ..., "output": ...}``, printed to the real output (in a
  test, ``with capsys.disabled(): print(...)``), at most ``MAX_OBSERVATIONS``, all within the output G6 keeps of a run,
  each at most ``MAX_OBSERVATION_CHARS`` characters and ``MAX_OBSERVATION_DEPTH`` levels deep. When both runs pass and
  their observations differ, ``behaviour_changed``: a question only. The inputs and outputs that differ are reported as
  canonical JSON text, so nothing nested reaches the reply.

A proposed check never passes a requirement (plan rule 2): the requirement keeps its status from its stated checks, and
the reply says how many proposed checks did not refute it. A ``counterexample`` gives the reason
``unconfirmed_counterexample`` and an ``unconfirmed_failure`` the reason ``unconfirmed_failure``, both with stage
``evidence``, so the patch review's precedence makes the outcome at most ``insufficient_evidence``; never
``defect_detected`` or ``test_integrity_compromised``; the reason says what was found in G6's own words, and what the
check printed or failed with, the caller's text, stays in ``assessment.completion``. Each finding's reproduction is the
check's text, its SHA-256, the path and node identifier, the plain pytest run G6's run amounted to (``reproduce``:
``PYTHONPATH=src`` where G6 put ``src`` on the import path, G6's empty configuration where the repository has none),
which needs no G6, the interpreter and boundary each run had, the digests of the two copies it ran in (each as the run
began, the check included), and ``CAUTION``: the check is the caller's code. A stated check's reproduction is derived
from its run the same way. ``fails_on_base_too`` and ``behaviour_changed`` become questions that show the competing
behaviours, at most ``MAX_QUESTIONS``.

``completion`` builds ``assessment.completion`` for every reply, from what ran only: the requirements demonstrated on
their stated checks with the receipts, those not established with the activity that would settle them, the defects
reproduced kept apart by authority, each preservation obligation and what was checked for it, test adequacy (empty in
this version), the open questions, and every activity with its result, seconds and model calls (none). With no
proposed checks, nothing of part 2 runs but that report, and every decided field is as before.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core import component_process
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness.patch_review import (
    NORMATIVE,
    NOT_ESTABLISHED,
    PASSED_ON_CHECKS,
    Requirement,
    _collection_errors,
    _covers,
    _in_repository,
)
from g6.intent_engine.schema import Assumption, IntentContract
from g6.workforce import repo_tests

#: The intent fields mapped to requirements, with the kind each item becomes.
MAPPED = (("success_criteria", "requirement"), ("non_goals", "preserve"), ("failure_criteria", "preserve"))
#: The intent's checks: predicates on an answer's text, never a check of a patch (the ruling above).
CHECKS = ("success_checks", "failure_checks")
#: Fields that hold items but are not part of a patch's assessment: each that holds any is named in an unknown.
UNMAPPED = ("constraints", "declared_risks", "guardrails", "checkpoints", "breakpoints", "subtasks")
ASSUMED = "an assumption the assessment depends on: it is not a requirement, gets no status and decides nothing"


@dataclass(frozen=True)
class IntentMapping:
    """The requirements (the caller's, then the intent's), the assumptions for the scope, and the unknowns."""
    requirements: list[Requirement]
    assumptions: list[dict]
    unknowns: list[str]


def intent_contract(value) -> IntentContract:
    """``value`` as an ``IntentContract``, or a ``ValueError`` that says what is wrong. A field the contract does not
    have is refused (pydantic would drop it, and a misspelt ``non_goals`` would lose an obligation unseen)."""
    if isinstance(value, IntentContract):
        return value
    if not isinstance(value, dict):
        raise ValueError("an intent is an object (an intent contract)")
    unknown = sorted(set(value) - set(IntentContract.model_fields))
    if unknown:
        raise ValueError("unknown intent field(s): " + ", ".join(map(str, unknown)))
    for index, item in enumerate(value.get("assumptions") or []):
        extra = sorted(set(item) - set(Assumption.model_fields)) if isinstance(item, dict) else []
        if extra:
            raise ValueError(f"assumptions.{index}: unknown field(s): " + ", ".join(map(str, extra)))
    try:
        return IntentContract.model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(".".join(str(part) for part in first["loc"]) + f": {first['msg']}") from exc


def _same(text: str) -> str:
    """Text as compared for merging: case, spacing and a closing full stop do not count."""
    return " ".join(text.split()).rstrip(". ").casefold()


def requirements_from_intent(intent: IntentContract, requirements: list[Requirement]) -> IntentMapping:
    """The review's requirements with ``intent``'s items mapped in (the module's rules); a ``ValueError`` when an item
    cannot be a requirement (empty, or too long)."""
    found = list(requirements)
    seen: dict[str, int] = {}
    for index, req in enumerate(found):
        if req.provenance in NORMATIVE:
            seen.setdefault(_same(req.text), index)
    for name, kind in MAPPED:
        for index, text in enumerate(getattr(intent, name)):
            ident, key = f"intent.{name}.{index}", _same(text)
            if not key:
                raise ValueError(f"{ident} is empty")
            if key in seen:
                earlier = found[seen[key]]
                found[seen[key]] = earlier.model_copy(update={"merged_from": [*earlier.merged_from, ident]})
                continue
            try:
                found.append(Requirement(id=ident, text=text, kind=kind))
            except ValidationError as exc:
                raise ValueError(f"{ident}: {exc.errors()[0]['msg']}") from exc
            seen[key] = len(found) - 1
    unknowns = [f"intent.{name} for {criterion!r} is a {check.kind} check on the text of an answer and names no "
                "repository test, so G6 did not use it as a check of this patch"
                for name in CHECKS for criterion, check in getattr(intent, name).items()]
    unknowns += [f"intent.{name} holds {len(items)} item(s) G6 does not assess a patch against: state any the patch "
                 "must meet as a requirement, with the checks that test it" for name in UNMAPPED
                 if (items := getattr(intent, name))]
    assumptions = [{"id": f"intent.assumptions.{index}", "statement": item.statement, "source": item.source,
                    "normative": item.source == "explicit", "note": ASSUMED}
                   for index, item in enumerate(intent.assumptions)]
    return IntentMapping(found, assumptions, unknowns)


# --- part 2: the checks a caller proposes -------------------------------------------------------------------------------

#: At most this many proposed checks in all, each with at most this many bytes of text (plan rule 7).
MAX_CHECKS = 20
MAX_SOURCE_BYTES = 64 * 1024
#: Each run's own time limit in seconds, inside the review's deadline, and the least time a run is started with.
RUN_S = 60
MIN_RUN_S = 5
MAX_QUESTIONS = 3
MAX_OBSERVATIONS = 32
#: An observation line holds at most this many characters, and its JSON object at most this many levels of nesting
#: (the object itself is one level, and so is a value inside it); beyond either, the probe is not read.
MAX_OBSERVATION_CHARS = 1000
MAX_OBSERVATION_DEPTH = 16
#: Said with every reproduction: G6 hands back code it did not write.
CAUTION = ("The check is code the caller wrote: G6 ran it only inside its operating-system boundary, with no network. "
           "Read it before you run it, or run it in a container.")
#: How a probe's observation line begins.
OBSERVATION = "G6-OBSERVATION"
FOLDER = "g6_check_"
PROPOSED = "proposed"
#: Who proposed a caller's check (VS-D2): recorded, never authenticated, never taken as independent.
PROPOSED_BY = "the caller: a calling agent's proposal, never assumed independent of the patch's author"
COUNTEREXAMPLE, FAILS_ON_BASE_TOO, NOT_REFUTED = "counterexample", "fails_on_base_too", "not_refuted"
UNCONFIRMED_FAILURE, BEHAVIOUR_CHANGED, CHECK_ERROR = "unconfirmed_failure", "behaviour_changed", "check_error"
#: The reason each finding gives, always with stage ``evidence`` (plan rule 4), and what the reason says of it.
REASONS = {COUNTEREXAMPLE: "unconfirmed_counterexample", UNCONFIRMED_FAILURE: "unconfirmed_failure"}
SHAPE = {COUNTEREXAMPLE: "passes at the base commit and fails on the patch",
         UNCONFIRMED_FAILURE: "fails on the patch, where the change should make it pass"}
OUT_OF_TIME = "the review's time budget would be passed"
HOW = ("copy the tree without its .git folder (the working tree under review, or the base commit's tree to see the "
       "other result), write text to file inside the copy, and run command from the copy's top folder (python -m "
       "pytest with arguments, and environment set): that is what G6's run amounted to, and it needs the repository's "
       "own pytest, not G6. The two tree digests are of the copies G6 ran it in, each as its run began, the check "
       "included")
#: The arguments that stand for the empty configuration G6 names, rooted at the copy's top, when none applies.
OWN_CONFIG = ("--rootdir=.", "--confcutdir=.", "-c", "/dev/null")
_NAME = re.compile(r"test_[A-Za-z0-9_]+\.py")
_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
#: Folders a check is never placed in: G6's copy leaves them out (any whose name begins with a dot, ``__pycache__``,
#: ``venv``, a ``.pyc`` name), or they hold installed code or its metadata.
_NOT_A_PLACE = frozenset({"__pycache__", "venv", "site-packages", "dist-packages", "node_modules"})
_METADATA = (".egg-info", ".dist-info", ".pyc")
_NODE = re.compile(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*(?:\[[^\]\x00-\x1f\x7f]*\])?")
_FAILED_LINE = re.compile(r"^FAILED (\S+)(?: - (.*))?$", re.MULTILINE)
_RAISED_LINE = re.compile(r"^E\s+(\S.*)$", re.MULTILINE)
#: A failure on an operating-system error: inside the boundary a refused access (a file outside the copy, the network,
#: a name lookup) raises one, so G6 cannot tell it from a failure of the code. A check error, never a finding.
_OS_NAME = (r"(?:[\w.]+\.)?(?:OSError|IOError|EnvironmentError|PermissionError|FileNotFoundError|FileExistsError|"
            r"IsADirectoryError|NotADirectoryError|ConnectionError|ConnectionRefusedError|ConnectionResetError|"
            r"ConnectionAbortedError|BrokenPipeError|TimeoutError|BlockingIOError|InterruptedError|ChildProcessError|"
            r"ProcessLookupError|gaierror|herror|timeout|URLError|SSLError)\b")
#: In pytest's output, on an ``E`` line or the summary line; in a message the run recorded, on any line.
_OS_ERROR = re.compile(rf"^(?:E\s+|FAILED \S+ - ){_OS_NAME}", re.MULTILINE)
_OS_RAISED = re.compile(rf"^(?:E\s+)?{_OS_NAME}", re.MULTILINE)
#: The same seen through a client library that wraps it in an exception of its own (review E12): G6's own network
#: refusal, a name that could not be looked up, an unreachable network, or the private loopback's refusal.
_REFUSED = re.compile("|".join([re.escape(repo_tests.SAID["network_off"]), "Temporary failure in name resolution",
                                "Name or service not known", "No address associated with hostname",
                                "nodename nor servname", "getaddrinfo failed", "Network is unreachable",
                                "Connection refused"]))
#: ``repo_tests._output``'s mark where it cut a run's output.
_CUT = re.compile(r"^\.\.\. \[\d+ bytes of output omitted\] \.\.\.$", re.MULTILINE)
_OBSERVED = re.compile(rf"{OBSERVATION} (.*)$", re.MULTILINE)
#: G6's clock for the stage's deadline (a test replaces it).
_clock = time.monotonic


class ProposedCheck(BaseModel):
    """A check the caller proposes: ``{id, requirement_id, intent, file, source, test}`` (``proposed_checks``)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
    requirement_id: str = Field(min_length=1, max_length=200)
    intent: Literal["preserve", "change"]
    file: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1)
    test: str = Field(default="", max_length=300)


def _file_problem(file: str) -> str:
    """Why ``file`` cannot name a proposed check ("" when it can)."""
    relative = "file must be a relative path inside the repository, with / between its folders"
    if (any(ord(ch) < 32 or ord(ch) == 127 for ch in file) or "\\" in file or file.startswith("/")
            or re.match(r"[A-Za-z]:", file)):
        return relative
    parts = file.split("/")
    if ".." in parts:
        return "file must not hold '..': a proposed check stays inside the repository"
    if any(part in ("", ".") for part in parts):
        return relative
    if not _NAME.fullmatch(parts[-1]):
        return (f"file's name must be test_<name>.py, of letters, digits and _ ({parts[-1]!r} is not): a conftest, a "
                "pytest or tox configuration, a .pth file or an __init__.py is never a proposed check")
    for part in parts[:-1]:
        if not _PART.fullmatch(part) or part.lower() in _NOT_A_PLACE or part.lower().endswith(_METADATA):
            return f"file names a folder G6 does not place a check in: {part!r}"
    return ""


def _source_problem(source: str) -> str:
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError:
        return "source must be text UTF-8 can hold"
    return (f"source holds {size} bytes; a proposed check holds at most {MAX_SOURCE_BYTES} (64 kilobytes)"
            if size > MAX_SOURCE_BYTES else "")


def proposed_checks(value, requirement_ids) -> list[ProposedCheck]:
    """``value`` as the review's proposed checks, or a ``ValueError`` that names the check and what is wrong. Each
    names a requirement of the review (``requirement_ids``: the normative ones) and declares an intent."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("proposed_checks must be a list of {id, requirement_id, intent, file, source, test}")
    if len(value) > MAX_CHECKS:
        raise ValueError(f"at most {MAX_CHECKS} proposed checks in all; {len(value)} were given")
    found: list[ProposedCheck] = []
    for index, item in enumerate(value):
        name = item.get("id") if isinstance(item, dict) else None
        where = f"proposed_checks.{index}" + (f" ({name})" if isinstance(name, str) and name else "")
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object {{id, requirement_id, intent, file, source, test}}")
        if item.get("intent") not in ("preserve", "change"):
            raise ValueError(f"{where}: intent must be preserve (behaviour the patch must keep) or change (behaviour "
                             "the patch must bring); a check that declares neither is not run")
        try:
            one = ProposedCheck.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"{where}: {'.'.join(str(p) for p in first['loc']) or 'check'}: {first['msg']}") from exc
        problem = _file_problem(one.file) or _source_problem(one.source) or (
            "test must name a test inside the file as pytest does: test_x, TestX::test_x or test_x[case]"
            if one.test and not _NODE.fullmatch(one.test) else "")
        if problem:
            raise ValueError(f"{where}: {problem}")
        if one.requirement_id not in requirement_ids:
            raise ValueError(f"{where}: requirement_id {one.requirement_id!r} names no requirement this review assesses")
        found.append(one)
    if len({one.id for one in found}) != len(found):
        raise ValueError("proposed check ids must be unique")
    return found


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def placement(check: ProposedCheck) -> str:
    """Where G6 puts ``check`` in a copy: in a folder of its own, named from its text's SHA-256, inside the folder its
    ``file`` names."""
    parts = check.file.split("/")
    return "/".join([*parts[:-1], FOLDER + _sha(check.source)[:12], parts[-1]])


def placements(root: Path, checks: list[ProposedCheck]) -> dict[str, str]:
    """Each check's place in the copies (``placement``), by id, or a ``ValueError``: a folder on the way is a link or a
    file in the repository, or the folder G6 would make already exists there."""
    found: dict[str, str] = {}
    for one in checks:
        placed = placement(one)
        parts = placed.split("/")
        for end in range(1, len(parts)):
            path, rel = root.joinpath(*parts[:end]), "/".join(parts[:end])
            if repo_tests._linked(path):
                raise ValueError(f"{one.id}: {rel} is a link in the repository; G6 places a check in real folders only")
            if end == len(parts) - 1 and path.exists():
                raise ValueError(f"{one.id}: the folder G6 would place it in, {rel}, already exists in the repository")
            if path.exists() and not path.is_dir():
                raise ValueError(f"{one.id}: {rel} is a file in the repository, not a folder")
        try:
            repo_tests.inside(root, placed, "proposed check")
        except repo_tests.RepoRefused as exc:
            raise ValueError(f"{one.id}: {exc}") from exc
        found[one.id] = placed
    return found


def _node(check: ProposedCheck, placed: str) -> str:
    return placed + (f"::{check.test}" if check.test else "")


#: What a run says about the interpreter, the boundary and what G6 gave pytest, kept with each run's reading.
RAN_WITH = ("python", "interpreter_source", "isolation", "import_path", "own_config")


def reproduce(node: str, ran: dict) -> dict:
    """The plain pytest run that stands for G6's run ``ran`` of ``node``, from the top of a copy of the tree:
    ``environment`` (``PYTHONPATH`` for each folder G6 put on the import path besides the top, which ``python -m``
    puts there itself), ``arguments`` (after ``python -m pytest``: G6's empty configuration, rooted at the top, when
    the repository had none) and the ``command`` that runs it in a POSIX shell. A run that does not say is read as one
    with the top alone on the path and the repository's own configuration."""
    folders = [rel for rel in ran.get("import_path") or ["."] if rel != "."]
    environment = {"PYTHONPATH": os.pathsep.join(folders)} if folders else {}
    arguments = ["-p", "no:cacheprovider", *(OWN_CONFIG if ran.get("own_config") else ()), node]
    command = (f"PYTHONPATH={shlex.quote(environment['PYTHONPATH'])} " if environment else "") + "python -m pytest " + \
        " ".join(shlex.quote(argument) for argument in arguments)
    return {"environment": environment, "arguments": arguments, "command": command,
            **{key: ran[key] for key in ("python", "interpreter_source") if key in ran}}


def _run_check(change: repo_tests.Change, placed: str, source: str, node: str, *, at_base: bool,
               timeout_s: int) -> dict:
    """One run of a proposed check, confined, with no network, in a fresh scratch copy with no git metadata and only
    that check overlaid at ``placed``: of the patched tree, or at the base commit's code (``repo_tests.run_at_base``).
    A run no backend confines is refused (raised), never run unconfined."""
    overlay = {placed: source}
    if at_base:
        return repo_tests.run_at_base(change, [node], timeout_s=timeout_s, overlay=overlay, require_isolation=True)
    deadline = time.monotonic() + timeout_s
    try:
        with repo_tests.repository_copy(change.repo, deadline=deadline, before=overlay) as copy:
            return repo_tests._run(copy, [node], roots=(), timeout_s=timeout_s, network=False, copied=True,
                                   python=repo_tests.interpreter(change.repo), scratch_dir=copy.parent,
                                   deadline=deadline, require_isolation=True)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except repo_tests.RepoRefused as exc:
        if exc.code == ISOLATION_UNAVAILABLE:
            raise
        return {"verdict": "not_run", "said": str(exc)}
    except (OSError, repo_tests.CopyNotRun) as exc:
        return {"verdict": "not_run", "said": str(exc)}


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
    """Whether the failure is an operating-system error or a refusal seen through a library (``_REFUSED``)."""
    output = ran.get("output") or ""
    return bool(_OS_ERROR.search(output) or _REFUSED.search(output) or any(
        _OS_RAISED.search(text) or _REFUSED.search(text) for text in _messages(ran, node)))


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


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True)


NOT_OBSERVED = "(not observed)"


def _differences(base: list[dict] | None, patch: list[dict] | None) -> list[dict]:
    """The inputs a probe observed differently at the base commit and on the patch, with both outputs, each as
    canonical JSON text (``NOT_OBSERVED`` where one run did not observe it), so nothing nested reaches the reply."""
    before = {_canonical(item["input"]): item for item in base or []}
    after = {_canonical(item["input"]): item for item in patch or []}
    found = []
    for key in dict.fromkeys([*before, *after]):
        old, new = before.get(key), after.get(key)
        if old is None or new is None or _canonical(old["output"]) != _canonical(new["output"]):
            found.append({"input": key, "base": _canonical(old["output"]) if old else NOT_OBSERVED,
                          "patch": _canonical(new["output"]) if new else NOT_OBSERVED})
    return found


def classify(intent: str, on_patch: dict, at_base: dict | None) -> dict:
    """One check's result from its two runs (``reading``) and its intent (plan rule 3): ``{result, detail}``, with a
    ``note`` or the probe's ``differences`` where they apply."""
    if on_patch["result"] == "error":
        return {"result": CHECK_ERROR, "detail": f"on the patch: {on_patch['detail']}"}
    if intent == "change" and on_patch["result"] == "failed":
        # review 1: what the change must bring fails on the patch, whatever the base gives (a new function cannot
        # even be imported there); the base result is detail only
        if on_patch.get("collection") and on_patch["verdict"] != repo_tests.FAILED:
            return {"result": CHECK_ERROR, "detail": "on the patch: it could not be collected for a reason G6 places "
                                                     "outside the repository's own code (a module this environment "
                                                     f"lacks, say), so it says nothing of the change: "
                                                     f"{on_patch['detail']}"}
        base = (f"at the base commit it {'passed' if at_base['result'] == 'passed' else 'gave ' + at_base['result']}"
                f" ({at_base['detail']})" if at_base else "it was not run at the base commit")
        return {"result": UNCONFIRMED_FAILURE, "detail": f"fails on the patch ({on_patch['detail']}); {base}"}
    if at_base is None or at_base["result"] == "error":
        return {"result": CHECK_ERROR,
                "detail": "at the base commit: " + (at_base["detail"] if at_base else "it did not run")}
    patch, base = on_patch["result"], at_base["result"]
    if patch == base == "passed":
        differences = _differences(at_base.get("observations"), on_patch.get("observations"))
        if differences:
            return {"result": BEHAVIOUR_CHANGED, "differences": differences,
                    "detail": f"passes at the base commit and on the patch, and observes {len(differences)} input(s) "
                              "differently"}
        found = {"result": NOT_REFUTED, "detail": "passes at the base commit and on the patch"}
        if intent == "change":
            found["note"] = "does not exercise the change: it passes at the base commit too"
        return found
    if intent == "change":   # it passes on the patch and fails at the base commit: what the change should do
        return {"result": NOT_REFUTED, "detail": "fails at the base commit and passes on the patch"}
    if patch == "failed" and base == "passed":
        return {"result": COUNTEREXAMPLE,
                "detail": f"passes at the base commit and fails on the patch ({on_patch['detail']})"}
    if patch == "failed":
        return {"result": FAILS_ON_BASE_TOO, "detail": f"fails at the base commit ({at_base['detail']}) and on the "
                                                       f"patch ({on_patch['detail']})"}
    return {"result": NOT_REFUTED, "detail": "fails at the base commit and passes on the patch",
            "note": "it fails at the base commit, so it does not describe behaviour the base commit had"}


@dataclass
class Search:
    """What the stage did: each check's activity, the reasons and next actions its findings give, the findings with
    their reproductions, and the questions (ranked and cut to ``MAX_QUESTIONS`` by ``completion``)."""
    activities: list[dict] = field(default_factory=list)
    reasons: list[tuple[str, str]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    defects: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)


def _activity(one: ProposedCheck, placed: str) -> dict:
    node = _node(one, placed)
    return {"activity": "proposed check", "check": one.id, "requirement": one.requirement_id, "intent": one.intent,
            "provenance": PROPOSED, "proposed_by": PROPOSED_BY, "file": one.file, "placed": placed, "node": node,
            "sha256": _sha(one.source), "runs": [], "seconds": 0.0, "model_calls": 0}


def not_run(checks: list[ProposedCheck], placed: dict[str, str], why: str) -> Search:
    """The stage, not run: every check listed as skipped, with why."""
    return Search(activities=[{**_activity(one, placed[one.id]), "result": "skipped", "reason": why}
                              for one in checks])


def _summary(found: dict) -> dict:
    """A run as its activity lists it: its result, and the interpreter, boundary and import path it ran with."""
    return {key: found[key] for key in ("result", "verdict", "detail", "seconds", "tree_digest", *RAN_WITH)
            if key in found}


def _bound(left: float) -> int:
    return max(1, int(min(RUN_S, left)))


def search(change: repo_tests.Change, checks: list[ProposedCheck], placed: dict[str, str], *, deadline: float,
           artifact: str) -> Search:
    """Run each proposed check on the patch and at the base commit (``_run_check``), classify it, and say what it
    found; the stage stops when the review's deadline would be passed."""
    done = Search()
    stopped = False
    for one in checks:
        activity = _activity(one, placed[one.id])
        done.activities.append(activity)
        if stopped or deadline - _clock() < 2 * MIN_RUN_S:
            stopped = True
            activity.update(result="skipped", reason=OUT_OF_TIME)
            continue
        node = activity["node"]
        on_patch = reading(_run_check(change, activity["placed"], one.source, node, at_base=False,
                                      timeout_s=_bound(deadline - _clock())), node)
        activity["runs"].append({"on": "patch", **_summary(on_patch)})
        at_base, skip = None, ""
        if on_patch["result"] == "error":
            skip = "the run on the patch did not run to a result, so a run at the base commit would decide nothing"
        elif deadline - _clock() < MIN_RUN_S:
            skip = OUT_OF_TIME
        else:
            at_base = reading(_run_check(change, activity["placed"], one.source, node, at_base=True,
                                         timeout_s=_bound(deadline - _clock())), node)
            activity["runs"].append({"on": "base commit", **_summary(at_base)})
        if skip:
            activity["runs"].append({"on": "base commit", "result": "skipped", "reason": skip})
        verdict = classify(one.intent, on_patch, at_base)
        if skip == OUT_OF_TIME and verdict["result"] == CHECK_ERROR:   # only where the base run would have decided
            verdict = {"result": CHECK_ERROR, "detail": f"its run at the base commit was not started: {OUT_OF_TIME}"}
        activity.update(verdict)
        activity["seconds"] = round(sum(run.get("seconds", 0.0) for run in activity["runs"]), 3)
        _found(done, one, activity, on_patch, at_base, verdict, artifact, change.base_commit)
    return done


def _found(done: Search, one: ProposedCheck, activity: dict, on_patch: dict, at_base: dict | None, verdict: dict,
           artifact: str, base_commit: str) -> None:
    """A finding's reason, reproduction and next action; a question for a check that fails at the base commit too or a
    probe whose observations differ."""
    result, about = verdict["result"], f"{one.id} (proposed for {one.requirement_id}, intent {one.intent})"
    if result in REASONS:
        node = activity["node"]
        reproduction = {"check": one.id, "file": activity["placed"], "text": one.source, "sha256": activity["sha256"],
                        "node": node, **reproduce(node, on_patch), "how": HOW, "caution": CAUTION,
                        "patched_tree_digest": on_patch["tree_digest"],
                        "base_tree_digest": at_base["tree_digest"] if at_base else "",
                        "artifact_digest": artifact, "base_commit": base_commit,
                        "on_patch": f"{on_patch['result']}: {on_patch['detail']}",
                        "at_base": f"{at_base['result']}: {at_base['detail']}" if at_base else "not run"}
        if at_base is not None:
            reproduction["at_base_command"] = reproduce(node, at_base)["command"]
        # review 10: in G6's own words only; what the check printed or failed with stays in assessment.completion
        done.reasons.append((REASONS[result], f"{about}: {SHAPE[result]}; unconfirmed: the caller proposed it, a "
                                              "proposed check decides no requirement, and what it failed with is in "
                                              "assessment.completion"))
        done.defects.append({"check": one.id, "requirement": one.requirement_id, "provenance": PROPOSED,
                             "result": result, "code": REASONS[result], "detail": verdict["detail"],
                             "reproduction": reproduction})
        done.next_actions.append(
            f"{about}: {result.replace('_', ' ')}, not confirmed. To reproduce it, copy the tree without its .git "
            f"folder, write the check's text (sha256 {activity['sha256']}) to {activity['placed']}, and run "
            f"{reproduction['command']} from the copy's top folder; the text and the digests of the trees it ran on "
            f"are in assessment.completion.defects_reproduced. {CAUTION}")
    elif result == BEHAVIOUR_CHANGED:
        first = verdict["differences"][0]
        more = len(verdict["differences"]) - 1
        done.questions.append({
            "check": one.id, "requirement": one.requirement_id, "intent": one.intent, "result": result,
            "question": f"For input {first['input']} the base commit's code gives {first['base']} and the patch gives "
                        f"{first['patch']}"
                        + (f" ({more} more input(s) differ)" if more else "")
                        + f", observed by {about}. Which is intended for {one.requirement_id}?",
            "behaviours": {"differences": verdict["differences"]}})
    elif result == FAILS_ON_BASE_TOO:
        done.questions.append({
            "check": one.id, "requirement": one.requirement_id, "intent": one.intent, "result": result,
            "question": f"{about} fails at the base commit ({at_base['detail']}) and on the patch "
                        f"({on_patch['detail']}). Does what it expects describe the behaviour {one.requirement_id} "
                        "asks to keep, or is the check wrong?",
            "behaviours": {"base": at_base["detail"], "patch": on_patch["detail"]}})


def annotate(evaluated: list[dict], done: Search) -> None:
    """Each requirement a proposed check names gets the checks and their results, and how many did not refute it.
    Its status is left as its stated checks made it."""
    for req in evaluated:
        mine = [a for a in done.activities if a["requirement"] == req["id"]]
        if not mine:
            continue
        req["proposed_checks"] = [{"id": a["check"], "intent": a["intent"], "result": a["result"]} for a in mine]
        clear = sum(a["result"] == NOT_REFUTED for a in mine)
        others = [f"{a['check']}: {a['result']}" for a in mine if a["result"] != NOT_REFUTED]
        req["proposed_note"] = (f"not refuted by {clear} proposed check{'' if clear == 1 else 's'}; a proposed check "
                                "never passes a requirement" + (f" ({', '.join(others)})" if others else ""))


#: The patch review's tier 2 run of the tests that touch the changed modules (``patch_review._assess``).
_DERIVED_RUN = "tests that touch the changed modules"


def completion(evaluated: list[dict], receipts: list[dict], runs: list[dict], skipped: list[dict], defects: list[dict],
               done: Search | None, *, artifact: str, base_commit: str) -> dict:
    """``assessment.completion``, from what ran only (the module's rules)."""
    tier_one = [found for found in receipts if found.get("tier") == 1]
    demonstrated = [{"requirement": req["id"], "text": req["text"], "kind": req["kind"],
                     "checks": [c["id"] for c in req["checks"]], "receipts": tier_one}
                    for req in evaluated if req["status"] == PASSED_ON_CHECKS]
    not_established = [{"requirement": req["id"], "text": req["text"], "kind": req["kind"], "reason": req["reason"],
                        "settle_by": req.get("suggested_test") or "make its stated checks run here to a pass",
                        **({"proposed_checks": req["proposed_note"]} if "proposed_note" in req else {})}
                       for req in evaluated if req["status"] == NOT_ESTABLISHED]
    stated = []
    for found in defects:
        named = found["code"] == "check_failed"
        stated.append({"check": found["node"], "provenance": "stated" if named else "derived",
                       "requirements": [req["id"] for req in evaluated
                                        if any(c["id"] == found["node"] for c in req["checks"])],
                       "code": found["code"], "detail": found["detail"],
                       "reproduction": {"node": found["node"], **reproduce(found["node"], found.get("ran") or {}),
                                        "on": "the working tree under review" + (
                                            "" if named else "; it passes at the base commit"),
                                        "artifact_digest": artifact, "base_commit": base_commit}})
    proposed = done.activities if done else []
    preservation = [{"obligation": req["id"], "text": req["text"], "provenance": req["provenance"],
                     "status": req["status"], "stated_checks": [{"id": c["id"], "verdict": c["verdict"]}
                                                                for c in req["checks"]],
                     "proposed_checks": req.get("proposed_checks", [])}
                    for req in evaluated if req["kind"] == "preserve"]
    derived = next((run for run in runs if run["tier"] == 2 and run["check"] == _DERIVED_RUN), None)
    passed_over = next((s for s in skipped if s["tier"] == 2 and s["check"] == _DERIVED_RUN), None)
    preservation.append({"obligation": "the repository's tests that touch the changed modules",
                         "provenance": "repository_stated",
                         "result": derived["verdict"] if derived else f"not run: {passed_over['reason']}"
                         if passed_over else "not run",
                         "regressions": [d["node"] for d in defects if d["code"] == "derived_test_regressed"]})
    kinds = {req["id"]: req["kind"] for req in evaluated}
    preservation += [{"obligation": f"behaviour {a['requirement']} keeps, as proposed check {a['check']} states it",
                      "provenance": PROPOSED, "check": a["check"], "result": a["result"]}
                     for a in proposed if a["intent"] == "preserve" and kinds.get(a["requirement"]) != "preserve"]
    status = {req["id"]: req["status"] for req in evaluated}
    questions = sorted(done.questions if done else [],   # open obligations first, then behaviour to keep (safety)
                       key=lambda q: (status.get(q["requirement"]) == PASSED_ON_CHECKS, q["intent"] != "preserve"))
    activities = [{"activity": "check run", "tier": run["tier"], "what": run["check"], "tests": run["tests"],
                   "provenance": run["provenance"], "result": run["verdict"], "seconds": float(run["seconds"] or 0.0),
                   "model_calls": 0} for run in runs]
    activities += [{"activity": "check run", "tier": s["tier"], "what": s["check"], "result": "skipped",
                    "reason": s["reason"], "seconds": 0.0, "model_calls": 0} for s in skipped]
    return {"demonstrated": demonstrated, "not_established": not_established,
            "defects_reproduced": {"stated_and_repository_checks": stated,
                                   "proposed_checks": list(done.defects) if done else []},
            "preservation": preservation, "test_adequacy": [], "open_questions": questions[:MAX_QUESTIONS],
            "activities": [*activities, *proposed]}
