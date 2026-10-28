# SPDX-License-Identifier: Apache-2.0
"""V10's arm E, model-written tests without G6: its run mechanics (docs/validation/patch_review_search/PROTOCOL.md,
"Arms", "Inputs and the model", "Arm E in detail", "For all arms of this study", and E's two variants under "Secondary
measures").

**What waits for VS-5.** Verification search's proposer interface (VS-5) defines the repository context G6's proposer
selects and the hosted interface every search arm reaches the model through. Until it exists, this module builds none
of these, and takes them as inputs:

- the model call (the hosted interface, the model ID, the sampling settings and the per-call limits on input and
  output): ``ask``, a callable that takes one request and returns the reply's text (or ``{"text", "usage"}``, the
  provider's token counts). E's frozen prompt is built inside it, from the request;
- the repository context: ``context``, passed to every request unchanged. E uses G6's selection and nothing else of G6;
- the reply format: a JSON object ``{"files": [{"path", "text"}]}`` (``parse``), proposed here until E's prompt fixes
  it (INTERPRETATIONS.md);
- the spending share: ``share_left``, asked before every call; tokens and dollars are counted by whoever implements
  ``ask``;
- the frozen parameters the freeze entry records (``Frozen``): R and each run's own time limit, read from G6's frozen
  commit (``g6_parameters``, ``frozen_from_g6``); the feedback switch (whether G6's proposer gets the results of its
  earlier checks between calls); the seed, always recorded and sent to the model only when the hosted interface takes
  one and G6's proposer sends it (``send_seed``, which VS-5 decides); the arm's wall-clock limit; and the model-call
  cap. E's script makes no random choice.

Nothing here calls a model, imports G6 or reads a label.

**One patch** (``arm_e``). E makes ``calls(c, t)`` model calls: as many as the larger of arms C and T made on the patch,
and at least one, never more than the cap. Each request asks for further tests; with feedback, each after the first
carries the results of running the tests kept so far. The files of every reply are pooled in call order. The kept tests
(``keep``): only files G6 would take as free-form checks (``file_problem`` and ``source_problem``: G6's rule for a
proposed check's file and its text, at most 64 kilobytes),
placed where G6 places one in its copy (``placement``: a folder of its own, ``g6_check_`` and the first 12 hexadecimal
digits of the SHA-256 of its text, inside the folder the file names, never where a path already exists), and at most
``MAX_TESTS`` test functions in file order. Everything else is discarded and counted.

Each kept file runs ``R`` times on the patched tree with V9's arm 1 command and its outcome plugin (``executor``: V9's
``Executor`` with arm 1's container command at a study level), on its own, in a fresh copy, each run within its own time
limit; then ``R`` times at the base commit, which changes no disposition. Each test's outcome in each run is read from
the plugin's report, written outside the copy (``reading``): ``passed``; ``failed`` (in its body: an assertion or an
exception raised there); ``error`` (it could not be collected or set up, a teardown failed, or the run did not finish in
time); ``skipped``; or ``missing`` (no recorded outcome). A test **passes** when it passed in more than half of its runs;
it is a **finding** when it failed in its body in more than half of them.

**The disposition.** E accepts when V9's arm 1 accepted and every kept test passes; when arm 1 did not accept, E does
not, in arm 1's category. A parsed reply with no test file, on every call, gives E arm 1's disposition, counted
(``no_test_file``); so does a pool from which no test was kept (``none_kept``). A provider error, a time-out, a reply
that cannot be parsed, or a call the spending share no longer allows is a counted tool error for E; so is arm 1 having
no disposition of its own. **E-filtered** sets aside the kept tests that also fail at the base, or that end in an error
on the patched tree, and accepts when arm 1 accepts and every remaining test passes. **A-and-E** accepts when arm A
accepts and every kept test passes. Both are computed from E's own runs; a tool error of E is theirs too.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT, ERROR, make

#: At most this many kept test functions: G6's own limit on checks (``verification_search.MAX_CHECKS``).
MAX_TESTS = 20
#: At most this many bytes of UTF-8 in a kept file: G6's own limit on a check's text (``MAX_SOURCE_BYTES``).
MAX_SOURCE_BYTES = 64 * 1024
#: The folder G6 places each free-form check in begins with this (``verification_search.FOLDER``).
FOLDER = "g6_check_"
#: E's tool errors (``error_kind``), and arm 1's or arm A's having no disposition to build on.
PROVIDER_ERROR, TIME_OUT, UNPARSEABLE, SPEND_SHARE = ("provider_error", "time_out", "unparseable_reply",
                                                      "spend_share_reached")
ARM_1, ARM_A = "arm_1_without_disposition", "arm_a_without_disposition"
#: A kept test's outcome in one run.
PASSED, FAILED, ERRORED, SKIPPED, MISSING = "passed", "failed", "error", "skipped", "missing"
#: pytest's exit codes for a run that did not get to its tests (interrupted, internal error, usage error).
_NOT_RUN = frozenset({2, 3, 4})
# G6's rule for a proposed check's file (``verification_search._file_problem`` on the verification search branch),
# mirrored, not imported: E uses nothing of G6 but its context selection.
_NAME = re.compile(r"test_[A-Za-z0-9_]+\.py")
_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_NOT_A_PLACE = frozenset({"__pycache__", "venv", "site-packages", "dist-packages", "node_modules"})
_METADATA = (".egg-info", ".dist-info", ".pyc")


class Unparseable(ValueError):
    """A reply the frozen script cannot read as files."""


class _OutOfTime(Exception):
    pass


@dataclass(frozen=True)
class Frozen:
    """The parameters the freeze entry records: ``runs`` (R, the times each kept test runs on each tree, read from
    G6's frozen commit: ``g6_parameters``), ``feedback`` (whether each call after the first gets the results of the
    tests kept so far), ``seed`` (always recorded; sent in the requests only when ``send_seed``, which holds when the
    hosted interface takes a seed and G6's proposer sends one, as VS-5 decides), ``run_s`` (each run's own time limit,
    G6's per-run limit for a proposed check, read from its frozen commit like R), ``limit_s`` (the arm's wall-clock
    limit, V9's arm 3's) and ``call_cap`` (the model-call cap per patch, the same as arms C and T)."""
    runs: int
    feedback: bool
    seed: int
    send_seed: bool
    run_s: int
    limit_s: int
    call_cap: int

    def __post_init__(self):
        for name in ("runs", "run_s", "limit_s", "call_cap"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a whole number of at least 1")
        if (not isinstance(self.feedback, bool) or not isinstance(self.send_seed, bool)
                or not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise ValueError("feedback and send_seed must be true or false, and the seed a whole number")


def g6_parameters(search_module) -> dict:
    """R and each run's own limit, read from G6's verification search module at its frozen commit
    (``g6.harness.verification_search``, passed in: this module never imports G6): ``runs``, the runs of one proposed
    check on the patched tree, counted in ``search``'s source as its calls with ``at_base=False`` inside its loop over
    the checks; and ``run_s``, its ``RUN_S``. A ``ValueError`` when either is not there in that shape: a change in how
    G6 runs a check is refused, never guessed."""
    run_s = getattr(search_module, "RUN_S", None)
    if not isinstance(run_s, int) or isinstance(run_s, bool) or run_s < 1:
        raise ValueError("G6's verification search has no RUN_S of at least 1 second")
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(search_module.search)))
    except (AttributeError, OSError, TypeError, SyntaxError) as exc:
        raise ValueError(f"G6's verification search has no readable search function: {exc}") from exc
    depths: list[int] = []

    def visit(node, depth: int) -> None:
        if isinstance(node, ast.Call) and any(
                word.arg == "at_base" and isinstance(word.value, ast.Constant) and word.value.value is False
                for word in node.keywords):
            depths.append(depth)
        for child in ast.iter_child_nodes(node):
            inner = isinstance(child, (ast.For, ast.AsyncFor, ast.While))
            visit(child, depth + inner)

    visit(tree, 0)
    if not depths or any(depth != 1 for depth in depths):
        raise ValueError("G6's search no longer runs each check on the patched tree in the shape this script reads "
                         f"(calls with at_base=False at loop depths {depths}, where one loop over the checks is read)")
    return {"runs": len(depths), "run_s": run_s}


def frozen_from_g6(search_module, *, feedback: bool, seed: int, send_seed: bool, limit_s: int, call_cap: int) -> Frozen:
    """The frozen parameters, with R and each run's own limit read from G6's frozen commit (``g6_parameters``)."""
    found = g6_parameters(search_module)
    return Frozen(runs=found["runs"], feedback=feedback, seed=seed, send_seed=send_seed, run_s=found["run_s"],
                  limit_s=limit_s, call_cap=call_cap)


def calls(c_calls: int, t_calls: int, *, cap: int) -> int:
    """How many model calls E makes on a patch: the larger of arms C and T made on it, at least one, at most ``cap``."""
    return min(max(int(c_calls), int(t_calls), 1), cap)


def parse(reply: str) -> list[dict]:
    """The files of one reply, ``[{path, text}]``, or ``Unparseable``."""
    try:
        found = json.loads(reply)
    except (TypeError, ValueError) as exc:
        raise Unparseable("the reply is not JSON") from exc
    files = found.get("files") if isinstance(found, dict) else None
    if not isinstance(files, list) or not all(isinstance(item, dict) and isinstance(item.get("path"), str)
                                              and isinstance(item.get("text"), str) for item in files):
        raise Unparseable('the reply is not {"files": [{"path": ..., "text": ...}]}')
    return [{"path": item["path"], "text": item["text"]} for item in files]


def file_problem(file: str) -> str:
    """Why G6 would not take ``file`` as a free-form check's file ("" when it would), as G6's rule says it."""
    if (any(ord(ch) < 32 or ord(ch) == 127 for ch in file) or "\\" in file or file.startswith("/")
            or re.match(r"[A-Za-z]:", file)):
        return "not a relative path inside the repository, with / between its folders"
    parts = file.split("/")
    if ".." in parts:
        return "it holds '..'"
    if any(part in ("", ".") for part in parts):
        return "not a relative path inside the repository, with / between its folders"
    if not _NAME.fullmatch(parts[-1]):
        return "its name is not test_<name>.py, of letters, digits and _"
    for part in parts[:-1]:
        if not _PART.fullmatch(part) or part.lower() in _NOT_A_PLACE or part.lower().endswith(_METADATA):
            return f"it names a folder G6 does not place a check in: {part!r}"
    return ""


def source_problem(text: str) -> str:
    """Why G6 would not take ``text`` as a check's text ("" when it would): text UTF-8 cannot hold, or more than
    ``MAX_SOURCE_BYTES`` bytes of it, as G6's rule says it."""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return "its text is not text UTF-8 can hold"
    return (f"it holds {size} bytes; a check holds at most {MAX_SOURCE_BYTES} (64 kilobytes)"
            if size > MAX_SOURCE_BYTES else "")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def placement(file: str, text: str) -> str:
    """Where G6 places a free-form check with ``file`` and ``text`` in its copy."""
    parts = file.split("/")
    return "/".join([*parts[:-1], FOLDER + _sha(text)[:12], parts[-1]])


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _place_problem(root: Path, placed: str) -> str:
    """Why ``placed`` cannot be written in a copy of ``root`` ("" when it can), as G6's ``placements`` says it."""
    parts = placed.split("/")
    for end in range(1, len(parts)):
        path, rel = Path(root).joinpath(*parts[:end]), "/".join(parts[:end])
        if _linked(path):
            return f"{rel} is a link in the repository"
        if end == len(parts) - 1 and path.exists():
            return f"the folder it would be placed in, {rel}, already exists in the repository"
        if path.exists() and not path.is_dir():
            return f"{rel} is a file in the repository, not a folder"
    return ""


def test_functions(text: str) -> list[str] | None:
    """The test functions pytest collects by default from ``text``, in file order (``test*`` functions, and ``test*``
    methods of ``Test*`` classes), as node ids within the file; None when the text cannot be parsed."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found = []
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, defs) and node.name.startswith("test"):
            found.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found += [f"{node.name}::{item.name}" for item in node.body
                      if isinstance(item, defs) and item.name.startswith("test")]
    return found


def keep(root: Path, pool: list[dict], *, also: tuple = ()) -> tuple[list[dict], list[dict]]:
    """The kept test files of ``pool`` (``[{path, text, call}]`` in call order), and everything discarded. A file is
    kept when G6 would take it as a free-form check's file and can place it in a copy of ``root`` (and of each tree in
    ``also``), it is not one already kept, and the limit of ``MAX_TESTS`` test functions is not reached; a file that
    cannot be parsed is kept as one test (its file), which will not pass. Each kept file is ``{file, placed, text,
    sha256, call, tests}``; each discarded item ``{file, call, test, why}`` (``test`` "" for a whole file)."""
    kept: list[dict] = []
    discarded: list[dict] = []
    taken: set[str] = set()
    count = 0
    for item in pool:
        file, text, call = item["path"], item["text"], item.get("call", 0)

        def drop(why: str, test: str = "", file=file, call=call) -> None:
            discarded.append({"file": file, "call": call, "test": test, "why": why})

        problem = file_problem(file) or source_problem(text)
        if problem:
            drop(problem)
            continue
        placed = placement(file, text)
        problem = next((found for tree in (root, *also) if (found := _place_problem(tree, placed))), "")
        if problem:
            drop(problem)
            continue
        if placed in taken:
            drop("the same file was already kept")
            continue
        names = test_functions(text)
        if names == []:
            drop("it holds no test function")
            continue
        tests = [placed] if names is None else [f"{placed}::{name}" for name in names]
        room = MAX_TESTS - count
        for test in tests[room:]:
            drop(f"over the limit of {MAX_TESTS} test functions", test.partition("::")[2] or placed)
        tests = tests[:room]
        if not tests:
            continue
        taken.add(placed)
        count += len(tests)
        kept.append({"file": file, "placed": placed, "text": text, "sha256": _sha(text), "call": call,
                     "tests": tests})
    return kept, discarded


def reading(run: dict, test: str) -> str:
    """``test``'s outcome in one run of its file (a node id, or the file itself for a file that cannot be parsed)."""
    if run.get("timed_out") or run.get("error"):
        return ERRORED
    phases = run.get("phases") or {}
    if "::" in test:
        items = [found for node, found in phases.items() if node == test or node.startswith(test + "[")]
    else:
        items = [found for node, found in phases.items() if node.startswith(test + "::")]
    if not items:
        return ERRORED if run.get("collection_errors") or run.get("exit") in _NOT_RUN else MISSING
    if any(found.get("call") == "failed" for found in items):
        return FAILED
    if any("failed" in found.values() for found in items):
        return ERRORED
    if all(found.get("call") == "passed" for found in items):
        return PASSED
    return SKIPPED if any("skipped" in found.values() for found in items) else MISSING


def _most(outcomes: list[str], state: str, runs: int) -> bool:
    return sum(outcome == state for outcome in outcomes) * 2 > runs


def _run(executor, tree: Path, kept: list[dict], *, runs: int, timeout) -> dict[str, list[str]]:
    """Each kept file ``runs`` times on ``tree``, on its own: each test's outcome in each run."""
    found: dict[str, list[str]] = {}
    for one in kept:
        for _ in range(runs):
            limit = timeout()
            if limit <= 0:
                raise _OutOfTime
            ran = executor.run(tree, list(one["tests"]), timeout_s=limit, overlay={"files": {one["placed"]: one["text"]}})
            for test in one["tests"]:
                found.setdefault(test, []).append(reading(ran, test))
    return found


def _combine(arm: dict, which: str, tests: dict[str, dict], error_kind: str) -> dict:
    """``arm``'s acceptance, and every test in ``tests`` passing."""
    if arm["category"] == ERROR:
        return make(ERROR, f"{which} has no disposition: {arm.get('detail', '')}"[:300], error_kind=error_kind)
    if not arm["accepted"]:
        return make(arm["category"], f"{which} did not accept", time_or_cap=bool(arm.get("time_or_cap_reached")))
    failing = [test for test, found in tests.items() if not found["passes"]]
    if not failing:
        return make(ACCEPT)
    category = DEFECT if any(tests[test]["finding"] for test in failing) else ABSTAIN
    return make(category, f"{len(failing)} kept test(s) not passing: " + ", ".join(failing[:5]))


def arm_e(*, ask, patched_tree: Path, base_tree: Path, patch: str, contract: str, context, arm_1: dict, arm_a: dict,
          c_calls: int, t_calls: int, frozen: Frozen, executor, share_left=lambda: True, clock=time.monotonic) -> dict:
    """Arm E on one patch (the module docstring): its disposition, E-filtered's and A-and-E's, the calls, every
    request and reply (``exchanges``, for the gaming scan), the kept and discarded tests, each kept test's outcomes
    (``tests``), the findings and the counts."""
    started, cpu = clock(), os.times()
    deadline = started + frozen.limit_s
    due = calls(c_calls, t_calls, cap=frozen.call_cap)
    pool: list[dict] = []
    exchanges: list[dict] = []
    outcomes: dict[str, list[str]] = {}
    files_seen = False
    found = {"frozen": asdict(frozen), "calls": {"due": due, "made": 0}, "exchanges": exchanges, "kept": [],
             "discarded": [], "tests": {}, "findings": [], "counts": {}}

    def cost() -> dict:
        now = os.times()
        return {"seconds": round(clock() - started, 3), "model_calls": found["calls"]["made"],
                "processor_seconds": round(sum(now[:4]) - sum(cpu[:4]), 3)}

    def failed(kind: str, detail: str, *, time_or_cap: bool = False) -> dict:
        disposition = make(ERROR, detail[:300], error_kind=kind, time_or_cap=time_or_cap)
        return {**found, "disposition": disposition, "e_filtered": disposition, "a_and_e": disposition,
                "cost": cost()}

    def timeout() -> float:
        return min(frozen.run_s, deadline - clock())

    def kept_now() -> list[dict]:
        return keep(patched_tree, pool, also=(base_tree,))[0]

    try:
        for call in range(1, due + 1):
            if not share_left():
                return failed(SPEND_SHARE, f"E's share of the spending cap was reached before call {call}")
            left = deadline - clock()
            if left <= 0:
                raise _OutOfTime
            feedback = None
            if frozen.feedback and call > 1:
                feedback = [{"file": one["file"], "test": test, "outcomes": outcomes[test]}
                            for one in kept_now() for test in one["tests"] if test in outcomes]
            request = {"call": call, "calls": due, "patch": patch, "contract": contract, "context": context,
                       "feedback": feedback, "timeout_s": left, **({"seed": frozen.seed} if frozen.send_seed else {})}
            exchange = {"call": call, "request": request}
            exchanges.append(exchange)
            found["calls"]["made"] += 1
            try:
                answer = ask(request)
            except TimeoutError as exc:
                exchange["error"] = f"{type(exc).__name__}: {exc}"
                return failed(TIME_OUT, f"call {call} timed out: {exc}", time_or_cap=deadline - clock() <= 0)
            except Exception as exc:  # a provider error is counted, never dropped
                exchange["error"] = f"{type(exc).__name__}: {exc}"
                return failed(PROVIDER_ERROR, f"call {call}: {type(exc).__name__}: {exc}")
            text = answer.get("text") if isinstance(answer, dict) else answer
            exchange.update(reply=text, usage=answer.get("usage") if isinstance(answer, dict) else None)
            try:
                files = parse(text)
            except Unparseable as exc:
                return failed(UNPARSEABLE, f"call {call}: {exc}")
            files_seen = files_seen or bool(files)
            pool += [{**item, "call": call} for item in files]
            if frozen.feedback and call < due:
                fresh = [one for one in kept_now() if not all(test in outcomes for test in one["tests"])]
                outcomes.update(_run(executor, patched_tree, fresh, runs=frozen.runs, timeout=timeout))
        kept, discarded = keep(patched_tree, pool, also=(base_tree,))
        fresh = [one for one in kept if not all(test in outcomes for test in one["tests"])]
        outcomes.update(_run(executor, patched_tree, fresh, runs=frozen.runs, timeout=timeout))
        at_base = _run(executor, base_tree, kept, runs=frozen.runs, timeout=timeout)
    except _OutOfTime:
        return failed(TIME_OUT, f"the arm's wall-clock limit of {frozen.limit_s} seconds was reached", time_or_cap=True)

    tests = {}
    for one in kept:
        for test in one["tests"]:
            patched, base = outcomes[test], at_base[test]
            tests[test] = {"file": one["placed"], "call": one["call"], "patched": patched, "base": base,
                           "passes": _most(patched, PASSED, frozen.runs),
                           "finding": _most(patched, FAILED, frozen.runs),
                           "error": _most(patched, ERRORED, frozen.runs),
                           "fails_at_base": _most(base, FAILED, frozen.runs)}
    found.update(kept=[{key: one[key] for key in ("file", "placed", "sha256", "call", "tests")} for one in kept],
                 discarded=discarded, tests=tests,
                 findings=[{"file": tests[test]["file"], "node": test, "call": tests[test]["call"],
                            "sha256": next(one["sha256"] for one in kept if one["placed"] == tests[test]["file"])}
                           for test in tests if tests[test]["finding"]],
                 counts={"no_test_file": not files_seen, "none_kept": files_seen and not kept,
                         "kept_files": len(kept), "kept_tests": len(tests), "discarded": len(discarded),
                         "errors": sum(one["error"] for one in tests.values()),
                         "findings": sum(one["finding"] for one in tests.values())})
    remaining = {test: one for test, one in tests.items() if not (one["fails_at_base"] or one["error"])}
    return {**found, "disposition": _combine(arm_1, "arm 1", tests, ARM_1),
            "e_filtered": _combine(arm_1, "arm 1", remaining, ARM_1),
            "a_and_e": _combine(arm_a, "arm A", tests, ARM_A), "cost": cost()}
