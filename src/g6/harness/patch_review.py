# SPDX-License-Identifier: Apache-2.0
"""Reference application: assess an AI-written Python patch against stated requirements, with the evidence for each.

``review_patch`` gives exactly one technical outcome, decided in this order:

1. ``test_integrity_compromised``: a sign of weakening ``g6.safety_engine.detect_test_weakening`` finds in a changed
   test-related file (``repo_tests.is_test_related``: a test file by pytest's default names, ``conftest.py`` or a
   start-up file, any Python file under a test folder; a moved one is compared with its old text), a changed
   test-runner file (``conftest.py``, ``pytest.toml``, ``.pytest.toml``, ``pytest.ini``, ``.pytest.ini`` or a
   start-up file; ``pyproject.toml``, ``setup.cfg`` and ``tox.ini`` only when their pytest section changed, an empty
   one included), a git-ignored file that could change the tests (``repo_tests.ignored_inputs``: a runner, start-up,
   path or test-named file, one that could shadow a tracked module, bytecode, an extension module, a link, or package
   metadata declaring a pytest plugin; outside git's view, so outside the digest; any other ignored Python file is an
   unknown, bound into the receipt), a run that changed tracked files, whose result cannot be believed or that began
   on a tree other than the one under review, the repository changing during the review, or the coding gate finding
   a shortcut in the diff against the base of the test, test-runner and continuous-integration files (the review
   binds the gate to that part of the diff; ``_test_diff``);
2. ``defect_detected``: a stated check or extra test failed, or failed to collect because of the repository's own
   code; or a test that touches a changed module fails, or fails to collect, on the patch and passed at the base
   commit (a moved test file runs there at its old path). One that does not pass at the base commit either, or could
   not be run there, is not counted against the patch: an unknown;
3. ``insufficient_evidence``: no requirement stated, a requirement with no stated check, a stated check that did not
   run (missing, not collected, could not be collected in this environment, skipped, deselected, started and cut off
   by the time limit, never reached before it), the tier 1 run out of time, a shortcut only the claim's text reports
   (``claim_reports_shortcut``), no test ran, no change against the base, or the scan for ignored files stopping at
   its limit;
4. ``supported_within_scope``: otherwise. Every requirement passed its stated checks and integrity is intact. That is
   support on the checks named, never a claim that the patch does everything the task needs.

Each requirement reads ``passed_on_stated_checks``, ``failed`` or ``not_established`` (with a reason and a suggested
test in words); G6 never invents a passing test. Checks adapt to cost: tier 1 (always) is the digest, integrity, and
the stated checks with the extra tests in one confined run; tier 2, the tests that touch the changed modules, runs
only when tier 1 found no integrity problem and no defect, and then, when nothing so far found a problem, the stated
checks that passed run against the base commit's code in a copy: one that passes there too may not exercise the
change, an unknown with a next action and never an outcome (review I8); tier 3 (mutation, old-against-new probes)
is not run here.

Every in-place run asks for a test receipt (``repo_tests.run(..., receipt=True)``): the scope lists each id, and the
coding gate, which this review builds bound to its own snapshot digest (never a digest from the request), counts a
run as G6-observed only when its store holds that receipt for that digest (status ``matched``).

Every outcome is ``qualified-draft``. A refusal (invalid input or repository, a git error, a stale artifact digest, a
run no operating-system boundary confined, even one the person allowed) is ``blocked-escalated`` with no outcome, and
is never a detection.
Merging is a separate decision: action gating is not consulted, and nothing here merges code. Supplied sources stay
caller assertions; their lexical support is reported and decides nothing.
"""
from __future__ import annotations

import configparser
import platform
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.coding_agent_gate import CodingAgentGateBlock
from g6.core import component_process
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.compose import Stage, compose
from g6.core.door import blocked_reply
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result
from g6.safety_engine.weakening import _kind as _weakening_kind
from g6.safety_engine.weakening import _pytest_settings, detect_test_weakening
from g6.workforce import repo_tests

SUPPORTED, DEFECT = "supported_within_scope", "defect_detected"
INTEGRITY, INSUFFICIENT = "test_integrity_compromised", "insufficient_evidence"
PASSED_ON_CHECKS, FAILED, NOT_ESTABLISHED = "passed_on_stated_checks", "failed", "not_established"
CODE = "G6_E_PATCH_REVIEW"
#: Files that configure, or could stand in for, the runner: ``repo_tests``'s list, every configuration file pytest
#: looks for by name among them (``pytest.toml``, ``.pytest.toml``, ``pytest.ini``, ``.pytest.ini`` and the project
#: files below; review C1).
RUNNER_FILES = repo_tests.RUNNER_FILES
#: Project files that hold more than pytest's settings: only a change to the pytest section named counts.
PYTEST_SECTIONS = {"pyproject.toml": "[tool.pytest]", "setup.cfg": "[tool:pytest]", "tox.ini": "[pytest]"}
#: Run verdicts whose results are not believed: an integrity problem, never evidence.
UNTRUSTED = frozenset({repo_tests.CHANGED, "inconsistent_report", "incomplete"})
#: A check's verdicts that are a defect when it is stated or extra; every other verdict but "passed" did not run.
#: ``failed`` includes a collection error G6 places in the repository's own code; ``collection_error`` is the
#: environment's (a dependency it lacks), which leaves the check not run. A run out of time is no defect (review I5):
#: a check it started reads ``timed_out``, one it never reached ``not_run``, and the outcome is insufficient evidence.
DEFECTS = frozenset({repo_tests.FAILED})
ENVIRONMENT = "could not be collected in this environment"
#: pytest's own lines naming a file it could not collect: "ERROR collecting x.py" and, in its summary, "ERROR x.py".
_COLLECTING = re.compile(r"ERROR (?:collecting )?([^\s:]+\.py)(?=\s|$)", re.MULTILINE)
BOUNDARY_HELP = ("Allowing unconfined runs (g6 setup or G6_ALLOW_UNCONFINED) does not change this: a patch review "
                 "uses only confined runs, the only ones that get test receipts. To get a boundary, install bubblewrap "
                 "on Linux, or on Windows make the one-time access grant g6 doctor prints; macOS has none yet.")
#: Coding gate findings about the checks themselves: they change the outcome. Every other finding is a merge reason.
#: A shortcut in the diff is an integrity problem, and so is one the claim reports when the diff shows one too; one
#: only the claim's text reports is insufficient evidence (``claim_reports_shortcut``; review I9(c)).
INTEGRITY_FINDINGS = frozenset({"shortcut_in_answer", "shortcut_in_diff"})
SPECIFICATION_NOTE = ("The task's specification may be incomplete: G6 checks the requirements stated here, and it "
                      "cannot rule out in general that the task needs something no requirement states.")
MERGE_REASON = ("G6 does not authorise merging. This assessment reports evidence from the stated checks; merging is a "
                "separate decision that needs its own approval.")
NOT_CHECKED = ("behaviour no stated check exercises", "requirements the task needs but does not state",
               "whether the tests are independent of the patch's author", "source freshness",
               "benefit relative to a baseline", "merge authorisation")
TIER_3 = "tier 3 not run: mutation and old-against-new probes are not part of this version"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    checks: list[str] = Field(default_factory=list, max_length=200)


class GateAdapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    task: str
    claim: str
    test_runs: list[dict]


class GateAdapter(AIBlock):
    """Reviewed mapping from G6's own test runs to the coding gate's evidence: one test item per run, citing its
    receipt. The gate makes an item G6-observed only through a receipt its store holds for the artifact digest."""
    __g6_block_contract__ = BlockContract(
        name="patch_gate_adapter", inputs_required=("task", "claim", "test_runs"),
        outputs=("task", "answer", "task_contract", "evidence", "verifier"),
        verification_method="unit_tests_only",
        guarantees=("does not convert source support into test evidence", "a run that did not happen is not evidence",
                    "a run is G6-observed at the gate only through its stored receipt"))

    def __init__(self):
        super().__init__("patch_gate_adapter")

    def infer(self, data: GateAdapterInput) -> Result[dict]:
        evidence = [{"kind": "test", "ref": ran.get("command", "pytest"), "receipt": ran.get("receipt") or "",
                     "exit_code": 0 if (ran.get("verdict") == "passed" and ran.get("passed", 0) > 0
                                        and not ran.get("not_passed") and not ran.get("degraded")) else 1}
                    for ran in data.test_runs if ran.get("verdict")]
        # No verifier claim: a caller-declared one would make the gate's evidence basis mixed whatever the receipts say.
        return Result.ok({"task": data.task, "answer": data.claim, "task_contract": data.task, "evidence": evidence,
                          "verifier": {"status": "not_run"}, "completion_state": "qualified-draft"})


class _Refused(repo_tests.RepoRefused):
    pass


def _execute(root: Path, ids: list[str], *, timeout_s: int, change: repo_tests.Change | None = None) -> dict:
    """Every test run of a review. In place on the patched tree (``repo_tests.run``); or, given ``change``, each test
    file of ``ids`` replayed at the base commit and then on the patch, in a copy (``repo_tests.run_before``), as
    ``{file: result}``: a file that did not pass at the base commit reads ``not_run``."""
    if change is not None:
        texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in ids}
        return repo_tests.run_before(replace(change, before=texts), timeout_s=timeout_s)
    return repo_tests.run(root, ids, timeout_s=timeout_s, receipt=True)


def _execute_at_base(change: repo_tests.Change, ids: list[str], *, timeout_s: int) -> dict:
    """The stated checks that passed, as their test files are now, run against the base commit's code in a copy
    (``repo_tests.run_at_base``; review I8)."""
    return repo_tests.run_at_base(change, ids, timeout_s=timeout_s)


def _confined(ran: dict) -> dict:
    """``ran``, or a refusal when no operating-system boundary confined it: its results are never assessed."""
    isolation = ran.get("isolation") or {}
    if "isolation" in ran and not isolation.get("backend"):
        raise _Refused(ISOLATION_UNAVAILABLE, "G6 assesses a patch only from test runs inside an operating-system "
                       f"boundary, and none confined this one ({isolation.get('why') or 'no backend'}), so its results "
                       f"are not used. {BOUNDARY_HELP} Run g6 doctor: it says what this computer needs.")
    return ran


def _runner_changes(change: repo_tests.Change) -> list[str]:
    """The changed runner files: any change to one that only configures or stands in for the runner; for a project
    file (``PYTEST_SECTIONS``), a change to its pytest section, read as ``g6.safety_engine`` reads it."""
    found = []
    for rel in sorted(set(change.runner_files) | {rel for rel in change.files
                                                  if PurePosixPath(rel).name.lower() in RUNNER_FILES}):
        name = PurePosixPath(rel).name.lower()
        if name not in PYTEST_SECTIONS:
            found.append(rel)
            continue
        reader = "pytest.ini" if name == "tox.ini" else name   # tox.ini's [pytest] section reads as pytest.ini's does
        path = change.repo / rel

        def settings(text: str | None):
            # An empty section is still the configuration pytest reads (review I7). Read as pytest.ini, a tox.ini with
            # no [pytest] section reads {}, as an empty one does: it is none. pytest reads nothing from an empty
            # [tool.pytest] table in pyproject.toml, so that is none too.
            found = None if text is None else _pytest_settings(reader, text)
            if found == {} and name == "tox.ini":
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.read_string(text)
                found = {} if parser.has_section("pytest") else None
            return None if name == "pyproject.toml" and found == {} else found

        try:
            before = repo_tests.read_at(change.repo, change.base_commit, rel)
            now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            old, new = settings(before), settings(now)
        except ValueError as exc:
            found.append(f"{rel}: could not be read ({exc}), so its {PYTEST_SECTIONS[name]} settings may have changed")
            continue
        if old != new:
            how = "added" if old is None else "removed" if new is None else "changed"
            found.append(f"{rel}: its {PYTEST_SECTIONS[name]} settings {how}")
    return found


def _collection_errors(output: str) -> set[str]:
    """The files pytest said it could not collect (it names them relative to its root folder)."""
    return set(_COLLECTING.findall(output.replace("\\", "/")))


def _names(check: str, files: set[str]) -> bool:
    head = check.partition("::")[0].rstrip("/")
    return any(path == head or path.startswith(head + "/") for path in files)


def _inputs(task, claim, tests, sources, timeout_s, requirements) -> list[Requirement]:
    def bad(message: str) -> _Refused:
        return _Refused("G6_E_INPUT_VALIDATION", message)

    if not isinstance(task, str) or not task.strip():
        raise bad("task must say what the patch is for")
    if not isinstance(claim, str):
        raise bad("claim must be text")
    if not isinstance(tests, (list, tuple)) or not all(isinstance(t, str) for t in tests):
        raise bad("tests must be a list of pytest node ids")
    if sources is not None and not (isinstance(sources, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in sources.items())):
        raise bad("sources must map source names to text")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 1 <= timeout_s <= repo_tests.MAX_TIMEOUT_S:
        raise bad(f"timeout_s must be a whole number of seconds from 1 to {repo_tests.MAX_TIMEOUT_S}")
    if not isinstance(requirements, (list, tuple)):
        raise bad("requirements must be a list of {id, text, checks}")
    try:
        stated = [Requirement.model_validate(item) for item in requirements]
    except ValidationError as exc:
        raise bad(f"each requirement is {{id, text, checks}}: {exc.errors()[0]['msg']}") from exc
    if len({r.id for r in stated}) != len(stated):
        raise bad("requirement ids must be unique")
    return stated


def _covers(check: str, node: str) -> bool:
    check = check.rstrip("/")
    return node == check or node.startswith((check + "::", check + "[", check + "/"))


def _node(phases: dict, failed: bool) -> str:
    states = set(phases.values())
    if failed or "failed" in states:
        return "failed"
    if "skipped" in states:
        return "skipped"
    return "passed" if phases.get("call") == "passed" else "not_run"


def _not_found(check: str, output: str) -> bool:
    """Whether pytest said it found no test named ``check`` (it prints the path it was given, or the full path)."""
    for line in output.replace("\\", "/").splitlines():
        _, sep, rest = line.partition("not found: ")
        if sep and (rest.strip() == check or rest.strip().endswith("/" + check)):
            return True
    return False


def _in_repository(ran: dict):
    """A function from a test id or file as pytest named it in ``ran``, relative to its root folder, to the same id
    relative to the repository's top, as checks name them (review I9: a pytest config below the top moves the root)."""
    prefix = (ran.get("rootdir") or "").strip("/")
    return (lambda node: f"{prefix}/{node}") if prefix else (lambda node: node)


def _verdicts(ids: list[str], ran: dict) -> dict[str, dict]:
    """Each check's own verdict in one run, read from pytest's per-test outcomes."""
    run, said, output = ran["verdict"], ran.get("said") or "", ran.get("output") or ""
    full = _in_repository(ran)
    outcomes = {full(node): phases for node, phases in (ran.get("test_outcomes") or {}).items()}
    failed = {full(node) for node in [*(ran.get("failed_ids") or []), *(ran.get("setup_errors") or {})]}
    omitted = {full(node) for node in ran.get("omitted_ids") or []}
    missing = {check for check in ids if _not_found(check, output)}
    broken = {full(rel) for rel in _collection_errors(output)}
    found: dict[str, dict] = {}
    for check in ids:
        nodes = {node: _node(phases, node in failed) for node, phases in outcomes.items() if _covers(check, node)}
        dropped = [node for node in omitted if _covers(check, node)]
        states = set(nodes.values())
        if run in UNTRUSTED:
            verdict, detail = run, said or "the run's results are not believed"
        elif "failed" in states:
            verdict, detail = FAILED, "failed: " + ", ".join(sorted(n for n, s in nodes.items() if s == "failed")[:5])
        elif "skipped" in states:
            verdict, detail = "skipped", f"{sum(s == 'skipped' for s in nodes.values())} of {len(nodes)} skipped"
        elif dropped:
            verdict, detail = "deselected", f"{len(dropped)} test(s) deselected by the repository's configuration"
        elif states == {"passed"}:
            verdict, detail = "passed", f"{len(nodes)} test(s) passed"
        elif nodes:
            verdict, detail = ("timed_out", said) if run == "timed_out" else ("not_run", "started, never finished")
        elif check in missing:
            verdict, detail = "not_collected", "pytest found no test with this id"
        elif missing:
            verdict, detail = "not_run", "the run stopped first: another stated check names a test pytest cannot find"
        elif broken and not _names(check, broken) and run in ("collection_error", repo_tests.FAILED):
            verdict, detail = "not_run", "the run stopped first: pytest could not collect " + ", ".join(sorted(broken))
        elif run == repo_tests.FAILED:
            verdict, detail = FAILED, "failed to collect, because of an error in the repository's own code"
        elif run == "collection_error":
            verdict, detail = "collection_error", ENVIRONMENT + (f" ({said})" if said else "")
        elif run == "timed_out":
            verdict, detail = "not_run", "the run's time ran out before this check started" + (f" ({said})" if said
                                                                                                  else "")
        elif run in repo_tests.RAN or run == "no_tests_collected":
            verdict, detail = "not_collected", "no test with this id ran"
        else:
            verdict, detail = run, said
        found[check] = {"verdict": verdict, "detail": detail}
    return found


def _record(tier: int, kind: str, ids: list[str], ran: dict, command: str = "") -> dict:
    return {"tier": tier, "check": kind, "command": command or "pytest " + " ".join(ids), "tests": list(ids),
            "verdict": ran.get("verdict"), "seconds": ran.get("seconds", 0.0), "passed": ran.get("passed", 0),
            "failed": ran.get("failed", 0), "skipped": ran.get("not_passed", 0), "said": ran.get("said", ""),
            "receipt": ran.get("receipt", "")}


def _requirement(req: Requirement, verdicts: dict[str, dict], norm: dict[str, str]) -> dict:
    checks = [{"id": check, **verdicts[check]} for check in dict.fromkeys(norm[raw] for raw in req.checks)]
    out = {"id": req.id, "text": req.text, "checks": checks}
    failing = [c["id"] for c in checks if c["verdict"] in DEFECTS]
    unrun = [f"{c['id']} {ENVIRONMENT}" if c["verdict"] == "collection_error" else f"{c['id']} ({c['verdict']})"
             for c in checks if c["verdict"] != "passed" and c["id"] not in failing]
    if not checks:
        return {**out, "status": NOT_ESTABLISHED, "reason": "no check was stated for this requirement",
                "suggested_test": "a test that runs the changed code the way this requirement describes and asserts "
                                  f"its result: {req.text}"}
    if failing:
        return {**out, "status": FAILED, "reason": "failed on its stated checks: " + ", ".join(failing)}
    if unrun:
        return {**out, "status": NOT_ESTABLISHED, "reason": "stated checks did not run to a pass: " + ", ".join(unrun),
                "suggested_test": "make those checks run here, or add a test that runs the changed code the way this "
                                  f"requirement describes and asserts its result: {req.text}"}
    return {**out, "status": PASSED_ON_CHECKS, "reason": "passed on its stated checks: " + ", ".join(
        c["id"] for c in checks)}


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_ESCAPE = re.compile(rb"\\([0-7]{3}|.)")
#: Continuous-integration and task-runner files the weakening detector's list leaves out (``weakening._kind``'s
#: "runner" kind holds the others: tox.ini, noxfile.py, .gitlab-ci.yml, .github/workflows/, .circleci/, ...).
_CI_FILES = frozenset({"makefile", ".pre-commit-config.yaml"})


def _unquoted(raw: str) -> str:
    """A path as git writes it in a diff: unquoted (git quotes a name holding a control character, a quote or a
    backslash), without the line's end or the tab git ends a name holding a space with."""
    name = raw.rstrip("\n").removesuffix("\t")
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = _ESCAPE.sub(lambda m: bytes([int(m[1], 8)]) if len(m[1]) == 3 else {b"t": b"\t", b"n": b"\n"}.get(
            m[1], m[1]), name[1:-1].encode("utf-8")).decode("utf-8", "replace")
    return name


def _diff_name(raw: str) -> str:
    """A path as a ``---`` / ``+++`` line gives it (``_unquoted``), without ``a/`` or ``b/``; "" for ``/dev/null``."""
    name = _unquoted(raw)
    return "" if name == "/dev/null" else name[2:] if name[:2] in ("a/", "b/") else name


def _diff_files(diff: str) -> list[tuple[set[str], str]]:
    """``diff`` (``repo_tests.diff_text``) cut into one part per file, each with the paths it names, old and new. A
    part begins at ``diff --git``, or at a ``---`` line outside a hunk once the part has had one (the untracked files
    ``diff_text`` appends have no ``diff --git`` line); a hunk's lines are counted from its header, so a removed line
    that itself starts with ``-- `` is never taken for a header."""
    parts: list[tuple[set[str], list[str]]] = []
    old = new = 0
    for line in diff.splitlines(keepends=True):
        if old > 0 or new > 0:   # inside a hunk
            mark = line[:1]
            old -= mark in ("-", " ", "\n")
            new -= mark in ("+", " ", "\n")
            parts[-1][1].append(line)
            continue
        names, lines = parts[-1] if parts else (set(), [])
        if not parts or line.startswith("diff --git ") or (line.startswith("--- ") and any(
                seen.startswith(("--- ", "@@")) for seen in lines)):
            parts.append((set(), []))
            names, lines = parts[-1]
        lines.append(line)
        if line.startswith(("--- ", "+++ ")):
            names.add(_diff_name(line[4:]))
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            names.add(_unquoted(line.split(" ", 2)[2]))
        elif (found := _HUNK_HEADER.match(line)) is not None:
            old = int(found[1]) if found[1] is not None else 1
            new = int(found[2]) if found[2] is not None else 1
    return [(names - {""}, "".join(lines)) for names, lines in parts]


def _scanned(rel: str) -> bool:
    """A file whose diff is a question of the tests: test-related (``repo_tests.is_test_related``), a runner file
    (``RUNNER_FILES``), or continuous-integration or task-runner configuration."""
    name = PurePosixPath(rel).name.lower()
    return (repo_tests.is_test_related(rel) or name in RUNNER_FILES or name in _CI_FILES
            or _weakening_kind(rel) == "runner")


def _test_diff(diff: str) -> str:
    """The parts of ``diff`` the coding gate scans for shortcuts: those of the files ``_scanned`` keeps, and any part
    whose file cannot be read from it. A source file's hunks are left out: an assert it drops, or a setting named
    SKIP_..., weakens no test (``detect_test_weakening`` reads the test files themselves)."""
    return "".join(text for names, text in _diff_files(diff) if not names or any(_scanned(rel) for rel in names))


def _gate(task: str, claim: str, runs: list[dict], artifact: str, diff: str) -> dict:
    """The test runs through the coding gate, which this host binds to the review's own snapshot digest: the only
    way a receipt counts as G6-observed (review C1); no digest travels in the request. The host binds the diff it read
    from git too, which the gate scans for shortcuts and does not count as evidence (review I8)."""
    flow = compose(
        Stage(GateAdapter(), "adapt", inputs={"task": "caller.task", "claim": "caller.claim",
                                              "test_runs": "caller.test_runs"}),
        Stage(CodingAgentGateBlock(receipt_artifact_digest=artifact, scanned_diff=diff), "verify_answer",
              inputs={key: f"0.{key}" for key in ("task", "answer", "task_contract", "evidence", "verifier")}),
        caller_inputs=("task", "claim", "test_runs"))
    composition = flow.infer({"task": task, "claim": claim, "test_runs": runs}).value
    found = composition.get("result") or {}
    return {"verdict": found.get("verdict"), "completion_state": composition["completion_state"],
            "findings": list(found.get("findings") or []), "evidence_basis": found.get("evidence_basis"),
            "receipts": list(found.get("receipts") or []), "warning_card": composition.get("warning_card"),
            "assurance": composition["assurance"]}


def _grounding(claim: str, sources: dict[str, str] | None) -> dict:
    if not sources:
        return {"status": "not run", "reason": "no sources were given", "decides_outcome": False}
    named = {name: {"digest": digest(text), "authority": "caller-supplied", "freshness": "not established"}
             for name, text in sources.items()}
    if not claim.strip():
        return {"status": "not run", "reason": "no claim was given", "sources": named, "decides_outcome": False}
    flow = compose(Stage("grounding", "check_claims"), caller_inputs=("text", "sources"))
    got = flow.infer({"text": claim, "sources": sources}).value
    return {"status": "run" if got.get("result") else "not run", "completion_state": got["completion_state"],
            "checks": (got.get("result") or {}).get("checks", []), "warning_card": got.get("warning_card"),
            "sources": named, "decides_outcome": False,
            "note": "lexical support against caller-supplied text, not entailment; it does not decide the outcome"}


def review_patch(repo: str | Path, *, task: str, requirements: list[dict], tests: list[str] | tuple[str, ...] = (),
                 claim: str = "", sources: dict[str, str] | None = None, base: str = "HEAD",
                 expected_artifact_digest: str = "", timeout_s: int = 300) -> dict:
    """Assess the working tree's patch against ``base`` on the stated requirements (``[{id, text, checks}]``, checks
    being pytest node ids) and the extra ``tests``. Never accepts claimed test results; never authorises a merge.

    Optional ``expected_artifact_digest`` pins a prior snapshot; a changed one is refused (``G6_E_PATCH_STALE``).
    """
    started = time.monotonic()
    try:
        stated = _inputs(task, claim, tests, sources, timeout_s, requirements)
        root = repo_tests.checked_repo(repo)
        before = repo_tests.snapshot(root)
        artifact = digest(before)
        if expected_artifact_digest and artifact != expected_artifact_digest:
            return blocked_reply("[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest")
        change = repo_tests.change(root, base)
        norm = {raw: repo_tests.inside(root, raw, "test") for req in stated for raw in req.checks}
        norm.update({raw: repo_tests.inside(root, raw, "test") for raw in tests})
        stated_ids = list(dict.fromkeys(norm[raw] for req in stated for raw in req.checks))
        ids = list(dict.fromkeys([*stated_ids, *(norm[raw] for raw in tests)]))
        return _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources,
                       deadline=started + timeout_s, started=started)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise   # an operator stop, a spent allowance or unconfirmed process cleanup must reach the enclosing door
    except repo_tests.RepoRefused as exc:
        said = str(exc)
        if exc.code == ISOLATION_UNAVAILABLE and not isinstance(exc, _Refused):
            # the run's own refusal offers allowing unconfined runs, which a review would refuse anyway
            said = said.removeprefix(f"[{exc.code}] ").split(" To run such code without one")[0] + " " + BOUNDARY_HELP
        return blocked_reply(f"[{exc.code}] {said}")
    except (OSError, ValueError, TypeError) as exc:
        return blocked_reply(f"[{CODE}] {exc}")


def _left(deadline: float) -> int:
    return int(deadline - time.monotonic())


def _untrusted(tier: int, ran: dict) -> str:
    said = ran.get("said") or ""
    changed = ", ".join(ran.get("changed") or [])
    return f"tier {tier} run: {ran['verdict']}" + (f": {said}" if said else "") + (f" ({changed})" if changed else "")


def _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources, *, deadline,
            started) -> dict:
    reasons: list[dict] = []
    unknowns: list[str] = []
    runs: list[dict] = []
    skipped: list[dict] = []

    def reason(stage: str, code: str, detail: str) -> None:
        reasons.append({"stage": stage, "code": code, "detail": detail})

    # Integrity, before anything runs: every changed test-related file through G6's weakening detector (review C2); a
    # moved one is compared with its old text, never read as deleted.
    for finding in detect_test_weakening(*repo_tests.related_texts(change)).reasons:
        reason("integrity", "test_weakened", finding)
    runner = _runner_changes(change)
    if runner:
        reason("integrity", "runner_configuration_changed", "; ".join(runner))
    scan = repo_tests.ignored_inputs(root)   # review I2: outside git's view, so outside the digest
    if scan.loadable:
        hidden = scan.loadable
        reason("integrity", "ignored_file_can_change_tests", "git ignores files that could change the tests: "
               + ", ".join(hidden[:10]) + (f" and {len(hidden) - 10} more" if len(hidden) > 10 else ""))
    unknowns.extend(f"an ignored file G6 did not judge: {rel}" for rel in scan.other)
    if not scan.complete:
        reason("evidence", "ignored_files_not_checked", "the scan for git-ignored files pytest could load stopped "
               f"at its limit of {repo_tests.IGNORED_SCAN_LIMIT} entries")
    if not change.files:
        reason("evidence", "no_change", f"the working tree shows no change against {change.base}")

    # Tier 1: the stated checks and the extra tests, in one confined run.
    present = [i for i in ids if (root / i.partition("::")[0]).exists()]
    verdicts = {i: {"verdict": "missing", "detail": "no such file on the patched tree"} for i in ids
                if i not in present}
    first: dict = {}
    if present:
        first = _confined(_execute(root, present, timeout_s=max(1, _left(deadline))))
        runs.append(_record(1, "stated checks and extra tests", present, first))
        verdicts.update(_verdicts(present, first))
        if first["verdict"] in UNTRUSTED:
            reason("integrity", "run_not_trusted", _untrusted(1, first))
        elif first["verdict"] == "timed_out":
            reason("evidence", "run_timed_out", f"the tier 1 run did not finish: {first.get('said') or 'timed out'}")
    else:
        skipped.append({"tier": 1, "check": "stated checks and extra tests",
                        "reason": "no check was stated" if not ids else "no stated check names a file on the tree"})
    for check in ids:
        if verdicts[check]["verdict"] in DEFECTS:
            reason("tier_1", "check_failed", f"{check}: {verdicts[check]['verdict']} ({verdicts[check]['detail']})")
        elif verdicts[check]["verdict"] != "passed" and check not in stated_ids:
            unknowns.append(f"extra test {check} did not run to a pass: {verdicts[check]['verdict']}")

    # Tier 2: the tests that touch the changed modules, only when tier 1 found no integrity problem and no defect.
    second: dict = {}
    if any(r["stage"] in ("integrity", "tier_1") for r in reasons):
        found = "an integrity problem" if any(r["stage"] == "integrity" for r in reasons) else "a defect"
        skipped.append({"tier": 2, "check": "tests that touch the changed modules", "reason": f"tier 1 found {found}"})
    elif _left(deadline) < 1:
        skipped.append({"tier": 2, "check": "tests that touch the changed modules", "reason": "the time budget is spent"})
    else:
        derived, more = repo_tests.derive_tests(change)
        derived = [rel for rel in derived if rel not in present]
        if more:
            unknowns.append(f"{more} more test file(s) touch the changed modules and were not run (the limit is "
                            f"{repo_tests.MAX_DERIVED})")
        if not derived:
            skipped.append({"tier": 2, "check": "tests that touch the changed modules",
                            "reason": "no other test file names a changed module"})
        else:
            second = _confined(_execute(root, derived, timeout_s=max(1, _left(deadline))))
            runs.append(_record(2, "tests that touch the changed modules", derived, second))
            if second["verdict"] in UNTRUSTED:
                reason("integrity", "run_not_trusted", _untrusted(2, second))
            elif second["verdict"] in (repo_tests.FAILED, "collection_error"):
                _on_base(root, change, derived, second, deadline, runs, unknowns, reason)
            elif second["verdict"] not in repo_tests.RAN:
                unknowns.append(f"the tests that touch the changed modules did not run to a result "
                                f"({second['verdict']}): {second.get('said', '')}")
    follow_ups = _at_base(change, stated, norm, verdicts, deadline, reasons, runs, skipped, unknowns)
    skipped.append({"tier": 3, "check": "mutation and old-against-new probes", "reason": TIER_3})

    if repo_tests.snapshot(root) != before:
        reason("integrity", "repository_changed_during_review", "the working tree changed while G6 reviewed it")
    in_place = [(tier, ran) for tier, ran in ((1, first), (2, second)) if ran]
    for tier, ran in in_place:   # review I4: changed and changed back around a run, the snapshots above still match
        if ran.get("source_before") and ran["source_before"] != artifact:
            reason("integrity", "repository_changed_during_review", f"the tier {tier} run began on a working tree "
                   "other than the one under review (its digest is not the artifact digest)")
    gate = _gate(task, claim, [ran for _, ran in in_place], artifact, _test_diff(repo_tests.diff_text(change)))
    statuses = [found.get("receipt_status", "unknown") for found in gate["receipts"]]
    receipts = [{"tier": tier, "receipt": ran.get("receipt", ""),
                 "receipt_status": statuses[index] if index < len(statuses) else "unknown"}
                for index, (tier, ran) in enumerate(in_place)]
    for found, (_, ran) in zip(receipts, in_place):
        if found["receipt_status"] != "matched":
            why = ran.get("receipt_withheld") or ran.get("receipt_error") or ""
            unknowns.append(f"tier {found['tier']}'s test receipt did not match ({found['receipt_status']}"
                            + (f": {why}" if why else "") + "), so the coding gate counted that run as caller-declared")
    merge_reasons = []
    in_diff = any(finding.get("code") == "shortcut_in_diff" for finding in gate["findings"])
    for finding in gate["findings"]:
        if finding.get("code") == "shortcut_in_answer" and not in_diff:
            # review I9(c), the owner's ruling: the claim's words alone say nothing about the tests
            reason("evidence", "claim_reports_shortcut", finding.get("message", ""))
        elif finding.get("code") in INTEGRITY_FINDINGS:
            reason("integrity", "gate_shortcut", finding.get("message", ""))
        else:
            merge_reasons.append(finding.get("message", ""))

    evaluated = [_requirement(req, verdicts, norm) for req in stated]
    if not stated:
        reason("evidence", "no_requirement_stated", "no requirement was stated, so none can be supported")
    for req in evaluated:
        if not req["checks"]:
            reason("evidence", "requirement_without_check", f"{req['id']}: no check was stated")
    for check in stated_ids:
        if verdicts[check]["verdict"] not in DEFECTS and verdicts[check]["verdict"] != "passed":
            reason("evidence", "check_did_not_run", f"{check}: {verdicts[check]['verdict']} "
                                                    f"({verdicts[check]['detail']})")
    if not any(run.get("passed", 0) + run.get("failed", 0) for run in runs):
        reason("evidence", "no_test_ran", "no test ran to a result")
    return _reply(change, evaluated, reasons, unknowns, runs, skipped, receipts, first or second, gate, merge_reasons,
                  artifact, claim, sources, started, follow_ups)


AT_BASE = "stated checks at the base commit"
#: The base-commit run's verdicts under which each check's own verdict is read; any other, nothing is said per check.
_READABLE_AT_BASE = frozenset({*repo_tests.RAN, "collection_error", "no_tests_collected"})


def _at_base(change, stated, norm, verdicts, deadline, reasons, runs, skipped, unknowns) -> list[str]:
    """Review I8, the owner's ruling: the stated checks that passed, as their test files are now, run against the
    base commit's code, only when nothing so far found a problem. One that passes there too may not exercise the
    change: an unknown, and a next action for each requirement it checks; never an outcome, since a requirement to
    keep behaviour passes at the base by design. Returns those next actions."""
    passing = [check for check in dict.fromkeys(norm[raw] for req in stated for raw in req.checks)
               if verdicts[check]["verdict"] == "passed"]
    if not passing or not change.files:
        return []
    if any(r["stage"] in ("integrity", "tier_1", "tier_2") for r in reasons):
        skipped.append({"tier": 2, "check": AT_BASE, "reason": "an earlier check found a problem"})
        return []
    if _left(deadline) < 1:
        skipped.append({"tier": 2, "check": AT_BASE, "reason": "the time budget is spent"})
        unknowns.append("whether the stated checks pass against the base commit's code too was not checked: the "
                        "time budget was spent")
        return []
    ran = _confined(_execute_at_base(change, passing, timeout_s=max(1, _left(deadline))))
    if "isolation" in ran:   # it ran, in a copy
        runs.append(_record(2, AT_BASE, passing, ran, command=f"pytest {' '.join(passing)} against the base "
                                                              "commit's code, in a copy"))
    if ran.get("verdict") not in _READABLE_AT_BASE:
        unknowns.append(f"whether the stated checks pass against the base commit's code too was not established "
                        f"({ran.get('verdict')}: {ran.get('said') or 'no result'})")
        return []
    actions = []
    for check, found in _verdicts(passing, ran).items():
        if found["verdict"] != "passed":
            continue
        unknowns.append(f"{check} passes against the base commit's code too, so it may not exercise the change")
        actions += [f"{req.id}: {check} passes at the base commit as well; if {req.id} asks for new behaviour, state "
                    "a check that fails at the base commit and passes on the patch (if it asks to keep behaviour, "
                    "this is expected)" for req in stated if check in {norm[raw] for raw in req.checks}]
    return actions


def _on_base(root, change, derived, second, deadline, runs, unknowns, reason) -> None:
    """Replay each failing derived test file, and each one pytest could not collect, at the base commit: a defect only
    when it passed there."""
    full = _in_repository(second)
    failing = sorted({full(rel) for rel in {node.partition("::")[0] for node in [*second.get("failed_ids", []),
                                                                              *second.get("setup_errors", {})]}
                      | _collection_errors(second.get("output") or "")} & set(derived))
    if not failing:
        unknowns.append(f"the tests that touch the changed modules failed without a test G6 can name: "
                        f"{second.get('said') or 'read their output'}")
        return
    if _left(deadline) < 1:
        unknowns.extend(f"{rel} fails on the patch; the time budget was spent before it ran at the base commit"
                        for rel in failing)
        return
    replays = _execute(root, failing, timeout_s=max(1, _left(deadline)), change=change)
    for rel in failing:
        out = _confined(replays.get(rel) or {"verdict": "not_run", "said": "not replayed"})
        runs.append(_record(2, "base-commit comparison", [rel], out, command=f"pytest {rel} at the base commit "
                                                                              "and on the patch, in a copy"))
        if out.get("verdict") == repo_tests.FAILED:
            reason("tier_2", "derived_test_regressed", f"{rel} passed at the base commit and fails on the patch: "
                   + ", ".join(out.get("failed_ids", [])[:5]))
        elif out.get("baseline") == "passed" and out.get("patched") in (repo_tests.FAILED, "collection_error"):
            reason("tier_2", "derived_test_regressed", f"{rel} collects and passes at the base commit, and fails to "
                   f"collect or run on the patch ({out.get('said') or out.get('patched')})")
        elif out.get("verdict") == "passed":
            unknowns.append(f"{rel} failed in place on the patch but passed when replayed in a copy")
        elif out.get("baseline") == "passed":
            unknowns.append(f"{rel} fails on the patch, and its run there could not be compared with its passing run "
                            f"at the base commit ({out.get('said') or out.get('patched')})")
        elif out.get("baseline"):
            unknowns.append(f"{rel} fails on the patch and did not pass at the base commit either "
                            f"({out['baseline']}), so G6 does not count it as a defect of this patch"
                            + (f" ({out['said']})" if out.get("said") else ""))
        else:   # review I6: the base run never happened, so nothing says the file failed there
            unknowns.append(f"{rel} fails on the patch and could not be run at the base commit, so G6 cannot say "
                            f"whether the patch broke it ({out.get('said') or out.get('verdict')})")


_SUMMARY = {
    SUPPORTED: "Supported within scope: every stated requirement passed its stated checks, and G6 found no sign that "
               "the tests or the test runner were weakened. This covers only the checks named here; it does not show "
               "that the patch does everything the task needs, and it is not permission to merge.",
    DEFECT: "Defect detected: {}.",
    INTEGRITY: "Test integrity compromised: the tests may not be trustworthy for this patch ({}).",
    INSUFFICIENT: "Insufficient evidence: G6 can neither support nor reject the patch on the checks it has ({}).",
}
_NEXT = {
    SUPPORTED: "Read the diff for behaviour no stated check exercises; merging is a separate decision with its own "
               "approval.",
    DEFECT: "Fix the patch so the named checks pass, then review it again.",
    INTEGRITY: "Read the changed tests and runner files yourself: restore what the patch weakened, or confirm the "
               "change is intended, then review it again.",
    INSUFFICIENT: "State each requirement with the tests that check it, make those tests run here, and review again.",
}


def _reply(change, evaluated, reasons, unknowns, runs, skipped, receipts, ran, gate, merge_reasons, artifact, claim,
           sources, started, follow_ups=()) -> dict:
    stages = {r["stage"] for r in reasons}
    codes = {r["code"] for r in reasons}
    if "integrity" in stages:
        outcome, why = INTEGRITY, [r["detail"] for r in reasons if r["stage"] == "integrity"]
    elif codes & {"check_failed", "derived_test_regressed"}:
        outcome, why = DEFECT, [r["detail"] for r in reasons if r["stage"] in ("tier_1", "tier_2")]
    elif "evidence" in stages:
        outcome, why = INSUFFICIENT, [r["detail"] for r in reasons if r["stage"] == "evidence"]
    else:
        outcome, why = SUPPORTED, []
    summary = _SUMMARY[outcome].format("; ".join(why[:5]))
    next_actions = [_NEXT[outcome]]
    next_actions += [f"{req['id']}: {req['reason']}; suggested: {req['suggested_test']}" for req in evaluated
                     if "suggested_test" in req]
    next_actions += list(follow_ups)
    if unknowns:
        next_actions.append("Look into the unknowns yourself: G6 does not count them for or against the patch.")
    environment = {"isolation": ran.get("isolation"), "python": ran.get("python"),
                   "interpreter_source": ran.get("interpreter_source"),
                   "host_python_version": platform.python_version(), "platform": platform.platform()}
    evidence = [{"source": "g6.workforce.repo_tests", "summary": f"tier {run['tier']}: {run['command']}: "
                 f"{run['verdict']}"[:200], "authority": "host-observed", "artifact_digest": artifact,
                 "base_commit": change.base_commit,
                 "scope": "these test outcomes on this working tree; not complete correctness", **run} for run in runs]
    assessment = {
        "outcome": outcome, "summary": summary, "reasons": reasons,
        "requirements": evaluated,
        "requirements_without_adequate_check": [r["id"] for r in evaluated if r["status"] == NOT_ESTABLISHED],
        "specification_note": SPECIFICATION_NOTE,
        "scope": {"artifact_digest": artifact, "base_commit": change.base_commit, "changed_files": list(change.files),
                  "environment": environment, "checks_run": runs, "checks_skipped": skipped, "receipts": receipts,
                  "seconds": round(time.monotonic() - started, 3)},
        "unknowns": unknowns, "next_actions": next_actions, "not_checked": list(NOT_CHECKED)}
    card = warning_card(CODE, summary, next_actions[0])
    return {**envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                              request_id=uuid.uuid4().hex[:12], degradation_reason=summary[:300]),
            "ok": True, "assessment": assessment,
            "merge": {"status": "requires_separate_approval", "reason": MERGE_REASON, "reasons": merge_reasons},
            "gate": gate, "grounding": _grounding(claim, sources), "artifact_digest": artifact}
