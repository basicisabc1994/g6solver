# SPDX-License-Identifier: Apache-2.0
"""Reference application: assess an AI-written Python patch against stated requirements, with the evidence for each.

``review_patch`` gives exactly one technical outcome, decided in this order:

1. ``test_integrity_compromised``: a sign of weakening ``g6.safety_engine.detect_test_weakening`` finds in a changed
   test-related file (``repo_tests.test_related``: a test file by pytest's, unittest's or Django's default names or
   the repository's own ``python_files``, ``conftest.py`` or a start-up file, any Python file under a test folder,
   or a file a stated check or extra test names; a moved one is compared with its old text), a changed
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
   commit (a moved test file runs there at its old path). One that does not pass at the base commit either is not
   counted against the patch: an unknown. Or the patch adds an import that a contract the
   repository declares (its import-linter configuration at the base commit) or the caller states forbids
   (``forbidden_dependency_added``, a ``forbidden_dependency.v1`` template instance, read from the files);
3. ``insufficient_evidence``: no requirement stated, a requirement with no stated check, a stated check that did not
   run (missing, not collected, could not be collected in this environment, skipped, deselected, started and cut off
   by the time limit, never reached before it), the tier 1 run out of time, a test that touches a changed module
   failing on the patch where G6 cannot say it passed at the base commit (no failing test it can name, the time
   budget spent, a copy over its cap or a base run that never happened, a replay not read or not comparable, one
   that passed in the copy), or the run of those tests giving no result (``derived_test_not_compared``; the launch
   review's I4), a changed file of test data or a snapshot (one not Python under a ``tests``, ``test``, ``testing``
   or ``__snapshots__`` folder, which G6 does not compare: ``test_data_changed``, with a next action asking the
   person to read its diff; the launch review's I3), a module the tests could not import that a line the patch adds
   imports (``patch_imports_missing_module``: the next action asks the person to check that it is the package
   intended before installing anything, never to make the tests run; the launch review's I6), a shortcut only the
   claim's text reports
   (``claim_reports_shortcut``), no test ran, no change against the base, the scan for ignored files stopping at
   its limit, a changed entry of a submodule git does not look into (``change_inside_submodule``: git cannot check
   the files a submodule holds), or such a submodule folder G6 could not hash (``submodule_not_hashed``). Each such
   folder is an unknown too: G6 hashes it into the digest, but cannot tell whether the patch changed its files. Or a
   tracked file git's assume-unchanged or skip-worktree mark hides that is not in the working tree, a sparse checkout
   (``file_not_in_working_tree``): one so marked that is there is compared with the base commit's text, as every other
   tracked file is, so the marks never hide a change (``repo_tests.change``). Also a
   tier 1 or tier 2 run that loaded a changed module from outside the tree it tested, an installed copy of the code
   in place of the file the patch changed, or that cannot say what it loaded (``changed_code_not_loaded``): its
   passes and its failures are not used, the requirements it checks are not established, and an unknown names the
   module, where it came from, and the import root (``import_roots``) or editable install that would load the patch.
   Or a proposed check, or a check G6 compiled from a template instance, finding a counterexample or a failure of the
   change, neither confirmed (``unconfirmed_counterexample``, ``unconfirmed_failure``);
4. ``supported_within_scope``: otherwise. Every requirement passed its stated checks and integrity is intact. That is
   support on the checks named, never a claim that the patch does everything the task needs.

Each requirement reads ``passed_on_stated_checks``, ``failed`` or ``not_established`` (with a reason and a suggested
test in words); G6 never invents a passing test. Checks adapt to cost: tier 1 (always) is the digest, integrity, and
the stated checks with the extra tests in one confined run; tier 2, the tests that touch the changed modules, runs
only when tier 1 found no integrity problem and no defect, and then, when nothing so far found a problem, the stated
checks that passed run against the base commit's code in a copy: one that passes there too may not exercise the
change, an unknown with a next action and never an outcome (review I8); tier 3 (mutation, old-against-new probes)
is not run here, except mutation for test adequacy and old-against-new probes as template instances, when asked.

Each requirement and each check says where it came from (``provenance``). A request's requirements are ``stated``, of
kind ``requirement``; only a ``stated``, ``repository_stated`` or ``person_confirmed`` one is assessed. An ``inferred``
requirement is listed among the scope's assumptions and a ``model_hypothesis`` among the proposals: neither gets a
status or takes part in the outcome, and their checks never run. A check the caller cited is ``stated``; a test of the
changed modules tier 2 found is ``derived``. An optional ``intent`` (an intent contract) adds its items as stated
(``g6.harness.verification_search``): its success criteria as requirements, its non-goals and failure criteria as
obligations to keep behaviour (kind ``preserve``), each not established until a check is stated for it, and its
assumptions, with their source, in the scope. With no intent nothing of this runs.

Optional ``proposed_checks`` (verification search, ``g6.harness.verification_search``): test files the caller proposes,
each naming a requirement and declaring an intent (``preserve`` or ``change``), checked before anything runs (an
invalid one refuses the review). After tier 2, unless integrity is already in doubt, each runs confined in a fresh copy
of the patched tree and in one at the base commit's code, and is classified. A proposed check never passes a
requirement and never gives ``defect_detected`` or ``test_integrity_compromised``: a counterexample or a failure of the
change gives a reason with stage ``evidence`` (so at most ``insufficient_evidence``) and its reproduction in the next
actions. A reason says what the check found in G6's own words; what the check printed or failed with, which is the
caller's text, stays in ``assessment.completion``. Where no operating-system boundary can confine those runs the review is refused, whatever the person allowed.

Optional ``template_instances`` (``g6.harness.verification_templates``): instances of G6's reviewed templates, each
naming a requirement, an intent and bindings that are data (dotted names G6 finds in the files, JSON literals), counted
with the proposed checks within 20 and checked before anything runs (an invalid one refuses the review), then read
against the tree after tier 1, so that never takes tier 1's time (one the tree gives nothing to apply to is refused
alone and reported). What G6's own work in its process raises in any search stage (a RecursionError or a MemoryError
from a file nested too deeply, any other) is that activity's error: the outcome from the other checks stands, with an
unknown that the stage did not complete. G6 compiles ``example.v1``, ``relation.v1``,
``access_rule.v1`` and ``differential.v1`` from each template's fixed text into checks run as proposed checks are,
which never pass a requirement; ``forbidden_dependency.v1`` reads imports only: against a contract the repository or
the caller states, an import the patch adds that the contract forbids fails its requirement, and a contract that holds
passes none (the owner's ruling R-C1), reported in ``completion.preservation``, as ``vacuous`` when the patch could
not have broken it. Optional ``search``: ``templates`` false
runs free-form checks only; ``test_adequacy`` true reports, for each requirement, the mutants of the changed lines its
stated checks killed and those that survived, which decides nothing. The configuration is in ``scope.search``.
The formal path (``g6.harness.verification_formal``): ``state_invariant.v1``, and access_rule.v1's multi-step form
through it, checks the invariants of a model the caller states, searches the model, and replays each trace on the real
code; ``relation.v1`` and ``differential.v1`` functions in Z3Verifier's subset get a symbolic search whose inputs are
re-run on the real code. Only a trace or an input that reproduces on the real code is a finding, unconfirmed
(``unconfirmed_counterexample``); a result about a model never passes a requirement and never gives
``defect_detected``. Each comes in three parts: specification, model, correspondence.
Proposed checks, compiled checks and mutant runs run as the stated checks do (the import roots, ``G6_REPO_PYTHON``,
``G6_REPO_ENV``); one of their runs that loaded a changed module from outside the tree, or cannot say what it loaded,
is a ``check_error`` (a mutant: not run), never ``not_refuted`` or a counterexample.
Every reply carries ``assessment.completion``, built from what ran; with no proposed checks, no template instance and
no search setting, every decided field is as before.

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
from collections.abc import Collection
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal

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
#: The owner's ruling (2026-10-29), asymmetric on purpose: a run whose tests all passed by pytest's own count and whose
#: process then exited with an error cannot get a patch accepted, so it is insufficient evidence, never integrity (a
#: correct patch's environment called tampering is the false alarm people give up over); exit 0 with tallies that show
#: a failure, an error or fewer tests finished stays untrusted, as a tampered runner would show it.
RUN_INCONSISTENT = repo_tests.RUN_INCONSISTENT
#: The launch review's I4: a test of the changed modules fails on the patch (or their run gave no result), and G6
#: cannot say it passed at the base commit and fails on the patch.
NOT_COMPARED = "derived_test_not_compared"
RUN_INCONSISTENT_DETAIL = ("{run}: the tests passed by pytest's own count, but the process exited with an error "
                           "afterwards, so nothing is counted as passed on it; run them with the repository's own "
                           "command to see what fails at exit")
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
#: Where a requirement's expectation came from. Only ``NORMATIVE`` ones get a status and take part in the outcome; an
#: ``inferred`` one is listed among the assumptions, a ``model_hypothesis`` among the proposals, and neither's checks
#: run. A request's requirements are ``stated``.
RequirementProvenance = Literal["stated", "repository_stated", "person_confirmed", "inferred", "model_hypothesis"]
NORMATIVE = frozenset({"stated", "repository_stated", "person_confirmed"})
#: Where a check came from: ``stated`` (the caller cited it) or ``derived`` (tier 2's tests of the changed modules); a
#: check the caller proposes is ``proposed`` (``assessment.completion``). ``structural`` and ``adopted_proposal`` are
#: kept for verification search's later phases; no check has them yet.
CheckProvenance = Literal["stated", "derived", "structural", "proposed", "adopted_proposal"]
STATED, DERIVED, STRUCTURAL = "stated", "derived", "structural"
#: A structural check of a stated contract that found an import the patch adds and the contract forbids: a defect.
STRUCTURAL_DEFECT = "forbidden_dependency_added"
DEFECT_CODES = frozenset({"check_failed", "derived_test_regressed", STRUCTURAL_DEFECT})
INTEGRITY_SKIP = ("the review found an integrity problem, so the checks would run under tests or a test runner that "
                  "may be weakened")
TIER_3_ADEQUACY = ("tier 3: mutation of the changed lines ran only for test adequacy (assessment.completion."
                   "test_adequacy), which changes no outcome; old-against-new probes run only as differential template "
                   "instances")
#: The fields only G6, or Python code that builds a ``Requirement``, sets: a request's requirements cannot carry them.
SET_BY_G6 = ("provenance", "kind", "merged_from")
# PENDING copy (pool compatibility): a submodule folder git does not look into (``repo_tests._gitlinks``).
SUBMODULE_UNKNOWN = ("git cannot check the files inside {path}, a submodule that was never set up here: G6 hashed them "
                     "itself, so a change made while the tests ran is seen, but not whether the patch changed them "
                     "since the base commit")
SUBMODULE_CHANGED = ("{path}: the patch changes this submodule's entry (the commit it names, or whether it is there), "
                     "and git cannot check the files a submodule holds")
SUBMODULE_NOT_HASHED = "{path}: G6 could not hash the files inside this submodule folder ({why}), so a change there is not seen"
#: Review C2: a tracked file git's assume-unchanged or skip-worktree mark hides that is not in the working tree.
UNSEEN_REASON = ("git is told not to look at {paths} (assume-unchanged or skip-worktree, as a sparse checkout leaves "
                 "files), and it is not in the working tree, so G6 cannot compare it with the base commit")
UNSEEN_UNKNOWN = "whether the patch changed {path} is unknown: git is told not to look at it, and it is not on the disk"
#: The launch review's I3: the folders whose files other than Python are the tests' data (expected values, fixtures,
#: golden files) or their snapshots, which G6 does not compare. Case is ignored, as for the test folders.
TEST_DATA_FOLDERS = frozenset({"tests", "test", "testing", "__snapshots__"})
# PENDING copy (the launch review's I3): changed test data, asked about, never an accusation.
TEST_DATA_REASON = ("the patch changes test data or snapshots, which G6 does not compare, so a re-pinned expected "
                    "value would look to it like any other change: {paths}")
TEST_DATA_ASK = ("Read the diff of {path} (git diff {base} -- {path}): it is test data or a snapshot the patch "
                 "changed, which G6 does not compare; confirm the new values are intended, then review again.")
#: What the person does about git-ignored files the tests could load (worked examples, W3b): the next actions name each
#: file once, the files named like a tracked module apart from the others; the outcome stays what it was. Never "git
#: add -f": that only stages the file into the patch under review (the security review of W1-W5, F4).
IGNORED_SETTLE = ("If they belong to the project, commit them before the change under review; otherwise remove them or "
                  "move them out of the repository; then review again.")
IGNORED_NAMED_ASK = ("{files}: untracked and git-ignored, but named like modules in the repository, so G6 cannot rule "
                     "out that one replaces a module during the tests. " + IGNORED_SETTLE)
IGNORED_OTHER_ASK = ("{files}: untracked and git-ignored, but pytest or Python could load them during the tests (a "
                     "conftest, start-up or test file, bytecode, a compiled module, a link or a plugin declaration), "
                     "and G6 does not compare them. Read them. " + IGNORED_SETTLE)
IGNORED_SHOWN = 10


def _each_once(paths) -> list[str]:
    """``paths`` in order, each once, without a folder that holds another of them (the file is shown, not its folder)."""
    unique = list(dict.fromkeys(paths))
    return [rel for rel in unique if not any(other.startswith(rel.rstrip("/") + "/") for other in unique)]


def _ignored_asks(scan: repo_tests.Ignored) -> list[str]:
    """The next actions for the git-ignored files ``scan`` found the tests could load, each file as the report shows
    repository text (``patch_report.code``: one printable line in a code span, never a line or Markdown of its own:
    the security review of W1-W5, F5)."""
    from g6.harness.patch_report import code

    named = _each_once(scan.named_like_modules)
    rest = [rel for rel in _each_once(scan.loadable) if rel not in set(named)]
    asks = []
    for files, ask in ((named, IGNORED_NAMED_ASK), (rest, IGNORED_OTHER_ASK)):
        if files:
            shown = ", ".join(code(rel, 200) for rel in files[:IGNORED_SHOWN]) + (
                f" and {len(files) - IGNORED_SHOWN} more (git status --ignored lists them all)"
                if len(files) > IGNORED_SHOWN else "")
            asks.append(ask.format(files=shown))
    return asks


#: The launch review's I6: a module a run could not import, and the import statements of the lines the patch adds.
_NO_MODULE = re.compile(r"No module named '([\w.]+)'")
_ADDED_IMPORT = re.compile(r"^\+[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b|import[ \t]+([^\n#;]+))", re.MULTILINE)
# PENDING copy (the launch review's I6): ask, don't accuse; never "make those tests run here" for this case.
MISSING_IMPORT_REASON = ("the tests could not import {names}, which the patch imports on a line it adds and which is "
                         "not installed here")
MISSING_IMPORT_ASK = ("The patch imports {name}, which is not installed here: check that it is the package you intend, "
                      "and that it exists under that name, before installing anything.")
#: The most ``import_roots`` a review takes.
MAX_IMPORT_ROOTS = 8
# PENDING copy (review C1): a run that loaded a changed module from outside the tree it tested (an installed copy).
NOT_LOADED = "changed_code_not_loaded"
NOT_LOADED_DETAIL = ("the run loaded a changed module from outside the tree it tested, or cannot say what it loaded, so "
                     "its result says nothing about the patch")
SHADOW_REASON = "tier {tier} run: {path} is changed, but the tests loaded {module} from {file}"
SHADOW_UNKNOWN = ("{path} is changed, but the tier {tier} run loaded its module {module} from {file}, outside the tree "
                  "it tested: those tests ran code the patch did not change. Name the folder that holds the package as "
                  "an import root (import_roots, such as 'lib'), or install the repository in editable mode, and review "
                  "again")
SHADOW_UNCHECKED = ("tier {tier} run: G6 could not check that the tests loaded the changed code (more than {n} modules "
                    "came from outside the tree, or the run did not say which), so its result is not used")


class Requirement(BaseModel):
    """A requirement: ``{id, text, checks}`` as the caller states it, with where it came from (``provenance``), whether
    it asks for behaviour or asks that behaviour be kept (``kind``: ``requirement`` or ``preserve``), and the ids of
    intent items with the same text that were merged into it (``merged_from``)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    checks: list[str] = Field(default_factory=list, max_length=200)
    provenance: RequirementProvenance = STATED
    kind: Literal["requirement", "preserve"] = "requirement"
    merged_from: list[str] = Field(default_factory=list)


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
    """A refusal of the review's own inputs; ``help``, when set, is what the refusal's warning card suggests doing (for
    a template instance's bindings, the template's bindings and one valid instance: worked examples, W4)."""
    help = ""


def _execute(root: Path, ids: list[str], *, timeout_s: int, change: repo_tests.Change | None = None,
             roots: tuple[str, ...] = ()) -> dict:
    """Every test run of a review, with ``roots`` (the review's ``import_roots``) first on the import path. In place on
    the patched tree (``repo_tests.run``); or, given ``change``, each test file of ``ids`` replayed at the base commit
    and then on the patch, in a copy (``repo_tests.run_before``), as ``{file: result}``: a file that did not pass at
    the base commit reads ``not_run``. Every one requires the boundary (the launch review's I1): a run no
    operating-system boundary would confine is refused before it starts, whatever the person allowed."""
    if change is not None:
        texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in ids}
        return repo_tests.run_before(replace(change, before=texts), roots=roots, timeout_s=timeout_s,
                                     require_isolation=True)
    return repo_tests.run(root, ids, roots=roots, timeout_s=timeout_s, receipt=True, require_isolation=True)


def _execute_at_base(change: repo_tests.Change, ids: list[str], *, timeout_s: int, roots: tuple[str, ...] = ()) -> dict:
    """The stated checks that passed, as their test files are now, run against the base commit's code in a copy
    (``repo_tests.run_at_base``; review I8), only inside the boundary (I1)."""
    return repo_tests.run_at_base(change, ids, roots=roots, timeout_s=timeout_s, require_isolation=True)


def _rooted(roots: tuple[str, ...]) -> dict:
    """The keyword that passes the review's ``import_roots`` to every run, when it names any."""
    return {"roots": roots} if roots else {}


def _import_roots(root: Path, values) -> tuple[str, ...]:
    """``import_roots`` as folders inside the repository, or a refusal (``G6_E_INPUT_VALIDATION``): a list of at most
    ``MAX_IMPORT_ROOTS``, each a path ``repo_tests.inside`` accepts that names a folder below the top, with no link on
    the way."""
    if not isinstance(values, (list, tuple)) or len(values) > MAX_IMPORT_ROOTS or not all(
            isinstance(value, str) for value in values):
        raise _Refused("G6_E_INPUT_VALIDATION", f"import_roots must be a list of at most {MAX_IMPORT_ROOTS} folders "
                                                "inside the repository")
    found = []
    for value in values:
        rel = repo_tests.inside(root, value, "import root").rstrip("/")
        parts = PurePosixPath(rel).parts
        if ("::" in value or not parts or not (root / rel).is_dir()
                or any(repo_tests._linked(root.joinpath(*parts[:end])) for end in range(1, len(parts) + 1))):
            raise _Refused("G6_E_INPUT_VALIDATION", f"import root {value!r} must name a folder inside the repository, "
                                                    "with no link on the way")
        found.append(rel)
    return tuple(dict.fromkeys(found))


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
        if name not in PYTEST_SECTIONS or rel in change.outside_links:   # a link outside is never read (review M1)
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


def _test_data(change: repo_tests.Change) -> list[str]:
    """The changed files, added and deleted ones too, that are not Python and lie under a test folder or a
    ``__snapshots__`` folder (``TEST_DATA_FOLDERS``; the launch review's I3)."""
    return [rel for rel in change.files if not rel.lower().endswith(".py")
            and any(part.lower() in TEST_DATA_FOLDERS for part in PurePosixPath(rel).parts[:-1])]


def _patch_imports_missing(change: repo_tests.Change, runs: list[dict]) -> list[str]:
    """The modules ``runs`` could not import (``No module named ...``, in their output or their recorded messages) that
    an import statement on a line the patch adds to a Python file names, itself, a package above it or a module below
    it (the launch review's I6)."""
    missing = {name for ran in runs for text in [ran.get("output") or "", *(ran.get("failure_messages") or {}).values()]
               for name in _NO_MODULE.findall(str(text))}
    if not missing:
        return []
    added = set()
    for names, text in _diff_files(repo_tests.diff_text(change)):
        if any(name.endswith(".py") for name in names):
            for found in _ADDED_IMPORT.finditer(text):
                added.update([found[1]] if found[1] else [part.split()[0] for part in found[2].split(",")
                                                          if part.split()])
    return sorted(name for name in missing if any(name == mine or mine.startswith(name + ".")
                                                  or name.startswith(mine + ".") for mine in added
                                                  if not mine.startswith(".")))


def _collection_errors(output: str) -> set[str]:
    """The files pytest said it could not collect (it names them relative to its root folder)."""
    return set(_COLLECTING.findall(output.replace("\\", "/")))


def _names(check: str, files: set[str]) -> bool:
    head = check.partition("::")[0].rstrip("/")
    return any(path == head or path.startswith(head + "/") for path in files)


def _inputs(task, claim, tests, sources, timeout_s, requirements, intent=None, proposed_checks=None,
            template_instances=None, search=None) -> tuple[list[Requirement], list, list, list, list, dict | None]:
    """The requirements (with an intent's items mapped in), the intent's assumptions and its unknowns, the proposed
    checks, the template instances and the search configuration (None when no search setting or instance was given);
    a refusal for any invalid input."""
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
    if any(isinstance(item, dict) and set(SET_BY_G6) & set(item) for item in requirements):
        raise bad("each requirement is {id, text, checks}: " + ", ".join(SET_BY_G6) + " are set by G6, never by a "
                  "request")
    try:
        stated = [Requirement.model_validate(item) for item in requirements]
    except ValidationError as exc:
        raise bad(f"each requirement is {{id, text, checks}}: {exc.errors()[0]['msg']}") from exc
    assumed, unmapped = [], []
    if intent is not None:
        from g6.harness import verification_search   # only for an intent: with none, nothing new is imported or runs
        try:
            mapped = verification_search.requirements_from_intent(verification_search.intent_contract(intent), stated)
        except ValueError as exc:
            raise bad(f"intent must be an intent contract: {exc}") from exc
        stated, assumed, unmapped = mapped.requirements, mapped.assumptions, mapped.unknowns
    if len({r.id for r in stated}) != len(stated):
        raise bad("requirement ids must be unique")
    proposed = []
    assessed = {req.id for req in stated if req.provenance in NORMATIVE}
    if proposed_checks is not None and proposed_checks != []:
        from g6.harness import verification_search   # only for proposed checks
        try:
            proposed = verification_search.proposed_checks(proposed_checks, assessed)
        except ValueError as exc:
            raise bad(str(exc)) from exc
    instances, config = [], None
    if search is not None:
        from g6.harness import verification_search   # only for a search setting
        try:
            config = verification_search.search_config(search)
        except ValueError as exc:
            raise bad(str(exc)) from exc
    if template_instances is not None and template_instances != []:
        from g6.harness import verification_search, verification_templates   # only for template instances
        try:
            instances = verification_templates.template_instances(template_instances, assessed)
        except ValueError as exc:
            refused = bad(str(exc))
            refused.help = getattr(exc, "help", "")
            raise refused from exc
        if len(instances) + len(proposed) > verification_search.MAX_CHECKS:
            raise bad(f"at most {verification_search.MAX_CHECKS} checks in all, proposed checks and template "
                      f"instances together; {len(proposed) + len(instances)} were given")
        if {one.id for one in instances} & {one.id for one in proposed}:
            raise bad("check ids must be unique across proposed checks and template instances")
        config = config or verification_search.search_config(None)
    return stated, assumed, unmapped, proposed, instances, config


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
        elif run == repo_tests.RUN_INCONSISTENT:   # the owner's ruling (2026-10-29): no check passes on such a run
            verdict, detail = run, said or repo_tests.SAID[run]
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


#: What a run says of what it could not see, kept in the reply's record of it when it says anything (review M1): the
#: links its copy left out or copied in place, the modules it loaded from outside the tree, the submodule folders G6
#: could not hash.
_SEEN_KEYS = ("links_left_out", "links_materialised", "loaded_outside", "submodules_not_hashed")


def _record(tier: int, kind: str, ids: list[str], ran: dict, command: str = "", provenance: str = STATED) -> dict:
    found = {"tier": tier, "check": kind, "command": command or "pytest " + " ".join(ids), "tests": list(ids),
             "verdict": ran.get("verdict"), "seconds": ran.get("seconds", 0.0), "passed": ran.get("passed", 0),
             "failed": ran.get("failed", 0), "skipped": ran.get("not_passed", 0), "said": ran.get("said", ""),
             "receipt": ran.get("receipt", ""), "provenance": provenance}
    found.update({key: ran[key] for key in _SEEN_KEYS if ran.get(key)})
    if ran.get("loaded_complete") is False:
        found["loaded_complete"] = False
    return found


def _requirement(req: Requirement, verdicts: dict[str, dict], norm: dict[str, str], structural=(),
                 missing: Collection[str] = ()) -> dict:
    """A requirement's status from its stated checks, and from ``structural``: the structural checks of a stated
    contract that name it and found an import the patch adds and the contract forbids (verification search's
    ``forbidden_dependency.v1``), which fail it. The owner's ruling R-C1: a structural check never passes one.
    ``missing``: the modules the patch imports that are not installed here (the launch review's I6), which a check
    that could not be collected is suggested to look at, never to make run."""
    checks = [{"id": check, "provenance": STATED, **verdicts[check]}
              for check in dict.fromkeys(norm[raw] for raw in req.checks)]
    checks += list(structural)
    out = {"id": req.id, "text": req.text, "provenance": req.provenance, "kind": req.kind, "checks": checks}
    if req.merged_from:
        out["merged_from"] = list(req.merged_from)
    failing = [c["id"] for c in checks if c["verdict"] in DEFECTS]
    unrun = [f"{c['id']} {ENVIRONMENT}" if c["verdict"] == "collection_error" else f"{c['id']} ({c['verdict']})"
             for c in checks if c["verdict"] != "passed" and c["id"] not in failing]
    test = ("a test that passes at the base commit and on the patch, and asserts the behaviour this requirement keeps"
            if req.kind == "preserve" else
            "a test that runs the changed code the way this requirement describes and asserts its result")
    if not checks:
        return {**out, "status": NOT_ESTABLISHED, "reason": "no check was stated for this requirement",
                "suggested_test": f"{test}: {req.text}"}
    if failing:
        return {**out, "status": FAILED, "reason": "failed on its stated checks: " + ", ".join(failing)}
    if unrun and missing and any(c["verdict"] == "collection_error" for c in checks):
        return {**out, "status": NOT_ESTABLISHED, "reason": "stated checks did not run to a pass: " + ", ".join(unrun),
                "suggested_test": f"check that {', '.join(missing)}, which the patch imports, is the package you "
                                  f"intend and exists under that name, before installing anything; or add {test}: "
                                  f"{req.text}"}
    if unrun:
        return {**out, "status": NOT_ESTABLISHED, "reason": "stated checks did not run to a pass: " + ", ".join(unrun),
                "suggested_test": f"make those checks run here, or add {test}: {req.text}"}
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


def _scanned(rel: str, related: Collection[str] = ()) -> bool:
    """A file whose diff is a question of the tests: test-related (``repo_tests.is_test_related``, or among
    ``related``: ``repo_tests.test_related``'s), a runner file (``RUNNER_FILES``), or continuous-integration or
    task-runner configuration."""
    name = PurePosixPath(rel).name.lower()
    return (repo_tests.is_test_related(rel) or rel in related or name in RUNNER_FILES or name in _CI_FILES
            or _weakening_kind(rel) == "runner")


def _test_diff(diff: str, related: Collection[str] = ()) -> str:
    """The parts of ``diff`` the coding gate scans for shortcuts: those of the files ``_scanned`` keeps, and any part
    whose file cannot be read from it. A source file's hunks are left out: an assert it drops, or a setting named
    SKIP_..., weakens no test (``detect_test_weakening`` reads the test files themselves)."""
    related = set(related)
    return "".join(text for names, text in _diff_files(diff)
                   if not names or any(_scanned(rel, related) for rel in names))


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
                 expected_artifact_digest: str = "", timeout_s: int = 300, intent: dict | None = None,
                 proposed_checks: list[dict] | None = None, template_instances: list[dict] | None = None,
                 search: dict | None = None, import_roots: list[str] | tuple[str, ...] = (),
                 llm=None) -> dict:
    """Assess the working tree's patch against ``base`` on the stated requirements (``[{id, text, checks}]``, checks
    being pytest node ids) and the extra ``tests``. Never accepts claimed test results; never authorises a merge.

    Optional ``expected_artifact_digest`` pins a prior snapshot; a changed one is refused (``G6_E_PATCH_STALE``).
    Optional ``intent``, an intent contract (``g6.intent_engine.schema.IntentContract``), adds its items as
    caller-stated (``g6.harness.verification_search.requirements_from_intent``); one that is not valid is refused.
    Optional ``proposed_checks`` (``[{id, requirement_id, intent, file, source, test}]``, at most 20) run in confined
    copies of the patch and of the base commit's code and never pass a requirement
    (``g6.harness.verification_search``); an invalid one is refused before anything runs.
    Optional ``template_instances`` (``[{id, template, requirement_id, intent, bindings}]``, counted with the
    proposed checks within the 20) fill G6's templates (``g6.harness.verification_templates``); an invalid one is
    refused before anything runs, and one the tree gives nothing to apply to is refused and reported only. Optional
    ``search`` (``{"templates": bool, "test_adequacy": bool}``) runs free-form checks only, or measures test adequacy.
    Optional ``import_roots`` (at most ``MAX_IMPORT_ROOTS`` folders inside the repository, such as ``lib``) go first on
    the import path of every run of the review, in place and in copies, and are recorded in its scope.
    ``search={"propose": true}`` asks a model for checks (``g6.harness.verification_proposer``): ``llm``, the caller to
    ask (Python only), else the model the person configured for G6; what it proposes stays untrusted.
    """
    started = time.monotonic()
    try:
        stated, assumed, unmapped, proposed, instances, config = _inputs(
            task, claim, tests, sources, timeout_s, requirements, intent, proposed_checks, template_instances, search)
        root = repo_tests.checked_repo(repo)
        roots = _import_roots(root, import_roots)
        before = repo_tests.snapshot(root)
        artifact = digest(before)
        if expected_artifact_digest and artifact != expected_artifact_digest:
            return blocked_reply("[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest")
        change = repo_tests.change(root, base)
        placed = {}
        if proposed:
            from g6.harness import verification_search
            try:
                placed = verification_search.placements(root, proposed)
            except ValueError as exc:
                raise _Refused("G6_E_INPUT_VALIDATION", str(exc)) from exc
        # Rule 11: only a normative requirement is assessed; the others are listed, and their checks never run.
        listed = [req for req in stated if req.provenance not in NORMATIVE]
        stated = [req for req in stated if req.provenance in NORMATIVE]
        norm = {raw: repo_tests.inside(root, raw, "test") for req in stated for raw in req.checks}
        norm.update({raw: repo_tests.inside(root, raw, "test") for raw in tests})
        stated_ids = list(dict.fromkeys(norm[raw] for req in stated for raw in req.checks))
        ids = list(dict.fromkeys([*stated_ids, *(norm[raw] for raw in tests)]))
        return _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources,
                       deadline=started + timeout_s, started=started, listed=listed, assumed=assumed,
                       unmapped=unmapped, proposed=proposed, placed=placed, instances=instances, config=config,
                       roots=roots, llm=llm)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise   # an operator stop, a spent allowance or unconfirmed process cleanup must reach the enclosing door
    except repo_tests.RepoRefused as exc:
        said = str(exc)
        if exc.code == ISOLATION_UNAVAILABLE and not isinstance(exc, _Refused):
            # the run's own refusal offers allowing unconfined runs, which a review would refuse anyway
            said = said.removeprefix(f"[{exc.code}] ").split(" To run such code without one")[0] + " " + BOUNDARY_HELP
        refused = blocked_reply(f"[{exc.code}] {said}")
        if getattr(exc, "help", ""):
            refused["warning_card"].update(suggested_action=exc.help, next_step=exc.help)
        return refused
    except (OSError, ValueError, TypeError) as exc:
        return blocked_reply(f"[{CODE}] {exc}")


def _left(deadline: float) -> int:
    return int(deadline - time.monotonic())


def _untrusted(tier: int, ran: dict) -> str:
    said = ran.get("said") or ""
    changed = ", ".join(ran.get("changed") or [])
    return f"tier {tier} run: {ran['verdict']}" + (f": {said}" if said else "") + (f" ({changed})" if changed else "")


def _stage(stopped: list[dict], what: str, run, *args, **kwargs):
    """``run``'s result, or None when G6's own work in it raised (the VS-3 review's fix pass, item 5: a RecursionError,
    a MemoryError, any other): that is an activity's error, and the stage did not complete; an operator stop,
    unconfirmed process cleanup and a refusal still reach the door."""
    try:
        return run(*args, **kwargs)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure, repo_tests.RepoRefused):
        raise
    except Exception as exc:
        from g6.harness import verification_search
        stopped.append({"activity": "search stage", "stage": what, "result": "error",
                        "reason": verification_search.stopped(exc), "stopped": what, "seconds": 0.0, "model_calls": 0})
        return None


def _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources, *, deadline,
            started, listed=(), assumed=(), unmapped=(), proposed=(), placed=None, instances=(),
            config=None, roots=(), llm=None) -> dict:
    reasons: list[dict] = []
    unknowns: list[str] = list(unmapped)   # what an intent held that is not a check of this patch
    runs: list[dict] = []
    skipped: list[dict] = []
    defects: list[dict] = []   # each stated or derived check that failed, for the completion report's reproductions

    def reason(stage: str, code: str, detail: str, node: str = "", ran: dict | None = None) -> None:
        reasons.append({"stage": stage, "code": code, "detail": detail})
        if node:   # ``ran``: the run it failed in, from which its reproduction is derived
            defects.append({"code": code, "node": node, "detail": detail, "ran": ran or {}})

    def unusable(tier: int, ran: dict) -> bool:
        """Whether a run that ran is no evidence of the patch (review C1): it loaded a changed module from outside the
        tree it tested (an installed copy), or cannot say what it loaded."""
        if ran.get("verdict") not in repo_tests.RAN:
            return False
        found = repo_tests.shadowed(change.sources, ran)
        if found is None:
            said = SHADOW_UNCHECKED.format(tier=tier, n=repo_tests.LOADED_LIMIT)
            reason("evidence", "changed_code_not_loaded", said)
            unknowns.append(said)
            return True
        for rel, where in found.items():
            reason("evidence", "changed_code_not_loaded", SHADOW_REASON.format(tier=tier, path=rel, **where))
            unknowns.append(SHADOW_UNKNOWN.format(tier=tier, path=rel, **where))
        return bool(found)

    # Integrity, before anything runs: every changed test-related file through G6's weakening detector (review C2); a
    # moved one is compared with its old text, never read as deleted. Test-related (the launch review's C1): by
    # pytest's, unittest's and Django's names, the repository's own python_files, a test folder, a start-up file, or
    # a file a stated check or extra test names, whatever it is called.
    related = repo_tests.test_related(change, ids)
    for finding in detect_test_weakening(*repo_tests.related_texts(change, related), tests=related).reasons:
        reason("integrity", "test_weakened", finding)
    runner = _runner_changes(change)
    if runner:
        reason("integrity", "runner_configuration_changed", "; ".join(runner))
    # the launch review's I2: what ignored_inputs' judge finds in a git-ignored file, in a file the patch adds or
    # changes in git's view too (a plugin's metadata, an extension module, bytecode, a link, a module's twin)
    loadable = list(repo_tests.loadable_changes(change, roots).items())
    if loadable:
        reason("integrity", "changed_file_can_change_tests", "the patch adds or changes files that could change what "
               "the tests load: " + "; ".join(f"{rel}: {why}" for rel, why in loadable[:10])
               + (f"; and {len(loadable) - 10} more" if len(loadable) > 10 else ""))
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
    # A submodule folder git does not look into: G6 hashes it into the digest, but nothing says what it held at the
    # base. A changed gitlink entry, or a folder G6 could not hash, leaves the evidence short; neither is an accusation.
    for rel in change.submodule_changes:
        reason("evidence", "change_inside_submodule", SUBMODULE_CHANGED.format(path=rel))
    unknowns.extend(SUBMODULE_UNKNOWN.format(path=rel) for rel in change.submodules)
    for rel, why in repo_tests.submodules_not_hashed(before).items():
        reason("evidence", "submodule_not_hashed", SUBMODULE_NOT_HASHED.format(path=rel, why=why))
    if change.unseen:   # review C2: a file git's index flags hide that is not on the disk is never "unchanged"
        shown = ", ".join(change.unseen[:10]) + (f" and {len(change.unseen) - 10} more" if len(change.unseen) > 10
                                                 else "")
        reason("evidence", "file_not_in_working_tree", UNSEEN_REASON.format(paths=shown))
        unknowns.extend(UNSEEN_UNKNOWN.format(path=rel) for rel in change.unseen[:10])
    data = _test_data(change)   # the launch review's I3: ask, don't accuse
    if data:
        shown = ", ".join(data[:10]) + (f" and {len(data) - 10} more" if len(data) > 10 else "")
        reason("evidence", "test_data_changed", TEST_DATA_REASON.format(paths=shown))
    asks = [*_ignored_asks(scan),
            *(TEST_DATA_ASK.format(base=change.base_commit[:12], path=rel) for rel in data[:10])]

    # Tier 1: the stated checks and the extra tests, in one confined run.
    present = [i for i in ids if (root / i.partition("::")[0]).exists()]
    verdicts = {i: {"verdict": "missing", "detail": "no such file on the patched tree"} for i in ids
                if i not in present}
    first: dict = {}
    if present:
        first = _confined(_execute(root, present, timeout_s=max(1, _left(deadline)), **_rooted(roots)))
        runs.append(_record(1, "stated checks and extra tests", present, first))
        verdicts.update(_verdicts(present, first))
        if first["verdict"] in UNTRUSTED:
            reason("integrity", "run_not_trusted", _untrusted(1, first))
        elif first["verdict"] == repo_tests.RUN_INCONSISTENT:   # the owner's ruling (2026-10-29): evidence, not integrity
            reason("evidence", RUN_INCONSISTENT, RUN_INCONSISTENT_DETAIL.format(run="the stated checks' run"))
        elif first["verdict"] == "timed_out":
            reason("evidence", "run_timed_out", f"the tier 1 run did not finish: {first.get('said') or 'timed out'}")
        elif unusable(1, first):   # a pass or a failure of code the patch did not change says nothing of the patch
            verdicts.update({check: {"verdict": NOT_LOADED, "detail": NOT_LOADED_DETAIL} for check in present})
    else:
        skipped.append({"tier": 1, "check": "stated checks and extra tests",
                        "reason": "no check was stated" if not ids else "no stated check names a file on the tree"})
    for check in ids:
        if verdicts[check]["verdict"] in DEFECTS:
            reason("tier_1", "check_failed", f"{check}: {verdicts[check]['verdict']} ({verdicts[check]['detail']})",
                   node=check, ran=first)
        elif verdicts[check]["verdict"] != "passed" and check not in stated_ids:
            unknowns.append(f"extra test {check} did not run to a pass: {verdicts[check]['verdict']}")
    # The template instances, after tier 1, so reading and parsing the files never takes its time (review I7): read
    # from the files, never imported; one the tree gives nothing to apply to is refused alone, and one G6's own
    # reading fails on is an activity's error (review I2).
    stopped: list[dict] = []   # each search stage that stopped on an error in G6's own work
    instances, proposed, placed = list(instances), list(proposed), dict(placed or {})
    proposer = proposals = None
    if config is not None and config["propose"]:   # VS-5: what a model proposes joins the caller's, as untrusted data
        from g6.harness import verification_proposer, verification_search
        proposer = _stage(stopped, "the proposer", verification_proposer.Proposer,
                          root, change, stated, config, llm=llm, deadline=deadline,
                          room=verification_search.MAX_CHECKS - len(instances) - len(proposed),
                          skip=INTEGRITY_SKIP if any(r["stage"] == "integrity" for r in reasons) else "")
        if proposer is not None:
            proposals = proposer.found
            instances += _stage(stopped, "the proposer's template instances", proposer.templates) or []
    prepared = None
    if instances:
        from g6.harness import verification_templates
        prepared = _stage(stopped, "reading the template instances", verification_templates.prepare, root, change,
                          instances, enabled=config["templates"], deadline=deadline, roots=roots)
    if proposer is not None:   # then the scenarios and the free-form checks, where no proposed instance applied
        mine = {one.id for one in proposals.instances}
        applied = {a["requirement"] for a in (prepared.activities if prepared is not None else [])
                   if a.get("check") in mine and a.get("result") not in ("refused", "error", "skipped")}
        applied |= {item["instance"].requirement_id for item in (prepared.formal if prepared is not None else [])
                    if item["instance"].id in mine}
        applied |= {one.requirement_id for one in (prepared.checks if prepared is not None else []) if one.id in mine}
        if _stage(stopped, "the proposer's free-form checks", proposer.rest, applied) is not None:
            proposed += proposals.checks
            placed.update(proposals.placed)
    # Verification search's structural checks (forbidden_dependency.v1), read from the files: one against a stated
    # contract that finds an import the patch adds and the contract forbids fails its requirement; one that finds none
    # passes no requirement (the owner's ruling R-C1); an instance a model proposed decides nothing, whatever contract
    # it names (reviewer B's B1).
    structural = list(prepared.structural) if prepared is not None else []
    for found in structural:
        if found["verdict"] == FAILED:
            reasons.append({"stage": "tier_1", "code": STRUCTURAL_DEFECT, "detail": found["detail"]})
            defects.append({"code": STRUCTURAL_DEFECT, "node": found["id"], "detail": found["detail"],
                            "provenance": STRUCTURAL, "contract_provenance": found["contract_provenance"],
                            "reproduction": found["reproduction"]})
        elif found["verdict"] == "counterexample":
            reason("evidence", "unconfirmed_counterexample", f"{found['id']} (an instance of forbidden_dependency.v1 "
                   "a model proposed): the patch adds an import its contract forbids; unconfirmed: a model's "
                   "proposal decides no requirement, and the imports are in assessment.completion")

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
            second = _confined(_execute(root, derived, timeout_s=max(1, _left(deadline)), **_rooted(roots)))
            runs.append(_record(2, "tests that touch the changed modules", derived, second, provenance=DERIVED))
            if second["verdict"] in UNTRUSTED:
                reason("integrity", "run_not_trusted", _untrusted(2, second))
            elif second["verdict"] == repo_tests.RUN_INCONSISTENT:
                reason("evidence", RUN_INCONSISTENT,
                       RUN_INCONSISTENT_DETAIL.format(run="the run of the tests that touch the changed modules"))
            elif unusable(2, second):
                pass   # its failures, and its passes, are of code the patch did not change
            elif second["verdict"] in (repo_tests.FAILED, "collection_error"):
                _on_base(root, change, derived, second, deadline, runs, unknowns, reason, roots)
            elif second["verdict"] not in repo_tests.RAN:   # the launch review's I4: nothing compared, no support
                said = (f"the tests that touch the changed modules did not run to a result ({second['verdict']}): "
                        f"{second.get('said', '')}")
                unknowns.append(said)
                reason("evidence", NOT_COMPARED, said)
    # the launch review's I6: a module the patch imports that is not installed here is asked about, never put on the
    # environment
    missing = _patch_imports_missing(change, [ran for ran in (first, second) if ran.get("verdict") == "collection_error"])
    if missing:
        reason("evidence", "patch_imports_missing_module", MISSING_IMPORT_REASON.format(names=", ".join(missing)))
    asks = [*(MISSING_IMPORT_ASK.format(name=name) for name in missing), *asks]
    follow_ups = [*asks, *_at_base(change, stated, norm, verdicts, deadline, reasons, runs, skipped, unknowns, roots)]
    search = None
    formal = None
    # Each search stage through _stage: what G6's own work raises is its activity's error, never the review's (I2).
    if prepared is not None and (prepared.formal or prepared.symbolic):   # the formal path: obligations, search,
        from g6.harness import verification_formal, verification_search   # each trace's replay, Z3's counterexamples
        formal = _stage(
            stopped, "the formal path", verification_formal.run, prepared, deadline=deadline,
            skip=INTEGRITY_SKIP if any(r["stage"] == "integrity" for r in reasons) else "",
            budget=verification_search.MAX_CHECKS - prepared.instances - len(proposed))
    compiled = list(prepared.checks) if prepared is not None else []
    if proposed or compiled:   # verification search: compiled template instances first, then the caller's proposed
        from g6.harness import verification_search   # checks, each on the patch and at the base commit
        checks, where = [*compiled, *proposed], {**(prepared.placed if prepared else {}), **(placed or {})}
        origins = {**(prepared.origins if prepared is not None else {}), **(proposals.origins if proposals else {})}
        if any(r["stage"] == "integrity" for r in reasons):
            search = verification_search.not_run(checks, where, INTEGRITY_SKIP, origins)
        else:
            search = _stage(stopped, "the proposed and compiled checks", verification_search.search, change, checks,
                            where, deadline=deadline, artifact=artifact, origins=origins, roots=roots)
        for code, detail in (search.reasons if search is not None else ()):
            reason("evidence", code, detail)
        follow_ups = [*follow_ups, *(search.next_actions if search is not None else ())]
    if formal is not None:
        verification_formal.finish(formal, search)
    adequacy = None
    if config is not None and config["test_adequacy"]:   # report only: it changes no outcome
        from g6.harness import verification_search
        integrity = any(r["stage"] == "integrity" for r in reasons)
        adequacy = _stage(
            stopped, "test adequacy", verification_search.measure_adequacy, change, stated, norm, verdicts,
            deadline=deadline, base_seconds=float(first.get("seconds") or 1.0), skip=INTEGRITY_SKIP if integrity
            else "", roots=roots)
    skipped.append({"tier": 3, "check": "mutation and old-against-new probes",
                    "reason": TIER_3_ADEQUACY if config is not None and config["test_adequacy"] else TIER_3})
    if instances or proposed or adequacy is not None or stopped:   # each stage that did not complete, an unknown
        from g6.harness import verification_search
        unknowns.extend(verification_search.stopped_unknowns([
            *(prepared.activities if prepared is not None else []), *(search.activities if search else []),
            *(adequacy[1] if adequacy is not None else []), *stopped]))
    for rel in change.outside_links:   # review M1: G6 never reads one in its own process
        unknowns.append(f"{rel} is a link to a path outside the repository: G6 did not read it in its own process, "
                        "and the copies leave it out")
    for found in (proposals.activities if proposals else []):   # a proposer that failed says so, with G6's reason
        if found.get("activity") == "proposer stage" and found.get("result") == "failed":
            unknowns.append(f"the proposer's {found['stage']} stage failed, so what it would have proposed is "
                            f"missing: {found['reason']}"[:400])

    final = repo_tests.snapshot(root)
    if repo_tests._moved(before, final):   # review I2: a folder G6 did not hash on one side is not a change
        reason("integrity", "repository_changed_during_review", "the working tree changed while G6 reviewed it")
    for rel, why in repo_tests.submodules_not_hashed(final).items():
        if rel not in repo_tests.submodules_not_hashed(before):
            reason("evidence", "submodule_not_hashed", SUBMODULE_NOT_HASHED.format(path=rel, why=why))
    in_place = [(tier, ran) for tier, ran in ((1, first), (2, second)) if ran]
    for tier, ran in in_place:   # review I4: changed and changed back around a run, the snapshots above still match
        if ran.get("source_before") and ran["source_before"] != artifact:
            reason("integrity", "repository_changed_during_review", f"the tier {tier} run began on a working tree "
                   "other than the one under review (its digest is not the artifact digest)")
    gate = _gate(task, claim, [ran for _, ran in in_place], artifact,
                 _test_diff(repo_tests.diff_text(change), related))
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

    normative = [found for found in structural if found["normative"]]
    evaluated = [_requirement(req, verdicts, norm, [_structural_check(found) for found in normative
                                                    if found["requirement"] == req.id and found["verdict"] == FAILED],
                              missing=missing)
                 for req in stated]
    if search is not None:
        verification_search.annotate(evaluated, search)
    if formal is not None:
        verification_formal.annotate(evaluated, formal)
    if not stated:
        reason("evidence", "no_requirement_stated", "no requirement was stated, so none can be supported")
    for req in evaluated:
        if not req["checks"]:
            reason("evidence", "requirement_without_check", f"{req['id']}: no check was stated")
    for check in stated_ids:
        if verdicts[check]["verdict"] not in DEFECTS and verdicts[check]["verdict"] != "passed":
            reason("evidence", "check_did_not_run", f"{check}: {verdicts[check]['verdict']} "
                                                    f"({verdicts[check]['detail']})")
    for found in normative:   # it would not have passed its requirement, so it leaves it as its stated checks did
        if found["verdict"] == "not_run":
            unknowns.append(f"{found['id']}: forbidden_dependency.v1 could not be applied or could not read every "
                            "file, so the contract was not checked (why is in assessment.completion.activities)")
    if not any(run.get("passed", 0) + run.get("failed", 0) for run in runs):
        reason("evidence", "no_test_ran", "no test ran to a result")
    return _reply(change, evaluated, reasons, unknowns, runs, skipped, receipts, first or second, gate, merge_reasons,
                  artifact, claim, sources, started, follow_ups, listed, assumed, search=search, defects=defects,
                  first_action=MISSING_IMPORT_ASK.format(name=missing[0]) if missing else "",
                  prepared=prepared, adequacy=adequacy, config=config,
                  counts={"template_instances": len(instances), "proposed_checks": len(proposed),
                          "model_calls": proposals.calls if proposals else 0}, roots=roots,
                  stopped=stopped, proposer=proposals)


def _structural_check(found: dict) -> dict:
    """A structural check of a stated contract, as its requirement lists it."""
    return {"id": found["id"], "provenance": STRUCTURAL, "verdict": found["verdict"],
            "detail": found["activity"].get("reason") or found.get("detail", ""), "template": "forbidden_dependency.v1",
            "contract_provenance": found["contract_provenance"]}


AT_BASE = "stated checks at the base commit"
#: The base-commit run's verdicts under which each check's own verdict is read; any other, nothing is said per check.
_READABLE_AT_BASE = frozenset({*repo_tests.RAN, "collection_error", "no_tests_collected"})


def _loaded_elsewhere(change, ran) -> str:
    """What a copy run that loaded a changed module from outside its copy (``repo_tests.shadowed``), or cannot say,
    loaded: "" when neither."""
    if ran.get("verdict") not in repo_tests.RAN:
        return ""
    found = repo_tests.shadowed(change.sources, ran)
    if found is None:
        return "G6 cannot say which code it loaded"
    return "; ".join(f"{rel} is changed, but it loaded {where['module']} from {where['file']}"
                     for rel, where in found.items())


def _at_base(change, stated, norm, verdicts, deadline, reasons, runs, skipped, unknowns, roots=()) -> list[str]:
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
    ran = _confined(_execute_at_base(change, passing, timeout_s=max(1, _left(deadline)), **_rooted(roots)))
    if "isolation" in ran:   # it ran, in a copy
        runs.append(_record(2, AT_BASE, passing, ran, command=f"pytest {' '.join(passing)} against the base "
                                                              "commit's code, in a copy"))
    elsewhere = _loaded_elsewhere(change, ran)
    if elsewhere:   # review C1: it ran code other than the base commit's, so it says nothing of it
        unknowns.append(f"the run against the base commit's code is not read: {elsewhere}")
        return []
    if ran.get("verdict") not in _READABLE_AT_BASE:
        unknowns.append(f"whether the stated checks pass against the base commit's code too was not established "
                        f"({ran.get('verdict')}: {ran.get('said') or 'no result'})")
        return []
    actions = []
    at_base = _verdicts(passing, ran)
    if "isolation" in ran:   # each check's own verdict at the base commit, which the readable report says in words
        runs[-1]["outcomes"] = {check: found["verdict"] for check, found in at_base.items()}
    for check, found in at_base.items():
        if found["verdict"] != "passed":
            continue
        unknowns.append(f"{check} passes against the base commit's code too, so it may not exercise the change")
        actions += [f"{req.id}: {check} passes at the base commit as well; if {req.id} asks for new behaviour, state "
                    "a check that fails at the base commit and passes on the patch (if it asks to keep behaviour, "
                    "this is expected)" for req in stated if check in {norm[raw] for raw in req.checks}]
    return actions


def _on_base(root, change, derived, second, deadline, runs, unknowns, reason, roots=()) -> None:
    """Replay each failing derived test file, and each one pytest could not collect, at the base commit: a defect only
    when it passed there. Where one fails on the patch and G6 cannot say it passed at the base commit and fails on the
    patch, an evidence reason of its own (``derived_test_not_compared``; the launch review's I4), never support; one
    that did not pass at the base commit either stays an unknown (the patch did not cause it)."""
    def uncompared(text: str) -> None:
        unknowns.append(text)
        reason("evidence", NOT_COMPARED, text)

    full = _in_repository(second)
    failing = sorted({full(rel) for rel in {node.partition("::")[0] for node in [*second.get("failed_ids", []),
                                                                              *second.get("setup_errors", {})]}
                      | _collection_errors(second.get("output") or "")} & set(derived))
    if not failing:
        uncompared(f"the tests that touch the changed modules failed without a test G6 can name: "
                   f"{second.get('said') or 'read their output'}")
        return
    if _left(deadline) < 1:
        for rel in failing:
            uncompared(f"{rel} fails on the patch; the time budget was spent before it ran at the base commit")
        return
    replays = _execute(root, failing, timeout_s=max(1, _left(deadline)), change=change, **_rooted(roots))
    for rel in failing:
        out = _confined(replays.get(rel) or {"verdict": "not_run", "said": "not replayed"})
        runs.append(_record(2, "base-commit comparison", [rel], out, command=f"pytest {rel} at the base commit "
                                                                              "and on the patch, in a copy",
                            provenance=DERIVED))
        elsewhere = _loaded_elsewhere(change, out)
        if elsewhere:   # review C1: a replay of code the patch did not change says nothing of the patch
            uncompared(f"{rel} fails on the patch, and its replay in a copy is not read: {elsewhere}")
        elif out.get("verdict") == repo_tests.FAILED:
            reason("tier_2", "derived_test_regressed", f"{rel} passed at the base commit and fails on the patch: "
                   + ", ".join(out.get("failed_ids", [])[:5]), node=rel, ran=out)
        elif out.get("baseline") == "passed" and out.get("patched") in (repo_tests.FAILED, "collection_error"):
            reason("tier_2", "derived_test_regressed", f"{rel} collects and passes at the base commit, and fails to "
                   f"collect or run on the patch ({out.get('said') or out.get('patched')})", node=rel, ran=out)
        elif out.get("verdict") == "passed":
            uncompared(f"{rel} failed in place on the patch but passed when replayed in a copy")
        elif out.get("baseline") == "passed":
            uncompared(f"{rel} fails on the patch, and its run there could not be compared with its passing run at "
                       f"the base commit ({out.get('said') or out.get('patched')})")
        elif out.get("baseline"):
            unknowns.append(f"{rel} fails on the patch and did not pass at the base commit either "
                            f"({out['baseline']}), so G6 does not count it as a defect of this patch"
                            + (f" ({out['said']})" if out.get("said") else ""))
        else:   # review I6: the base run never happened, so nothing says the file failed there
            uncompared(f"{rel} fails on the patch and could not be run at the base commit, so G6 cannot say whether "
                       f"the patch broke it ({out.get('said') or out.get('verdict')})")


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


#: What a requirement no one stated is (rule 11): listed, and nothing more.
LISTED_ONLY = ("no one stated this: it is listed only, has no status, its checks did not run, and it does not change "
               "the outcome")


def _listed(listed, assumed) -> tuple[list[dict], list[dict]]:
    """The proposals (each ``model_hypothesis`` requirement) and the assumptions the assessment depends on (``assumed``,
    then each ``inferred`` requirement): none has a status."""
    proposals = [{"id": req.id, "text": req.text, "provenance": req.provenance, "kind": req.kind,
                  "checks": list(req.checks), "note": LISTED_ONLY} for req in listed if req.provenance != "inferred"]
    assumptions = [*assumed, *({"id": req.id, "statement": req.text, "source": "inferred", "normative": False,
                                "kind": req.kind, "checks": list(req.checks), "note": LISTED_ONLY}
                               for req in listed if req.provenance == "inferred")]
    return proposals, assumptions


def _one_line(text: object) -> str:
    """``text`` with each control character (a line break among them) shown as a space: a name a repository chose can
    never start a line of its own in the reply's guidance. It decides nothing."""
    return "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(text))


def _reply(change, evaluated, reasons, unknowns, runs, skipped, receipts, ran, gate, merge_reasons, artifact, claim,
           sources, started, follow_ups=(), listed=(), assumed=(), search=None, defects=(), prepared=None,
           adequacy=None, config=None, counts=None, roots=(), stopped=(), proposer=None, first_action="") -> dict:
    """The reply. ``first_action``: the next action that stands first for an insufficient-evidence outcome in place of
    the general one (the launch review's I6: a module the patch imports that is not installed here)."""
    from g6.harness import verification_search   # the completion report, in every reply
    # a repository's names reach these texts as they are; each is one line (the fresh review of the F1-F8 fix pass)
    reasons = [{**r, "detail": _one_line(r["detail"])} for r in reasons]
    unknowns = [_one_line(text) for text in unknowns]
    follow_ups, first_action = [_one_line(text) for text in follow_ups], _one_line(first_action)
    evaluated = [{**req, **{key: _one_line(req[key]) for key in ("reason", "suggested_test")
                            if isinstance(req.get(key), str)}} for req in evaluated]
    stages = {r["stage"] for r in reasons}
    codes = {r["code"] for r in reasons}
    if "integrity" in stages:
        outcome, why = INTEGRITY, [r["detail"] for r in reasons if r["stage"] == "integrity"]
    elif codes & DEFECT_CODES:
        outcome, why = DEFECT, [r["detail"] for r in reasons if r["stage"] in ("tier_1", "tier_2")]
    elif "evidence" in stages:
        outcome, why = INSUFFICIENT, [r["detail"] for r in reasons if r["stage"] == "evidence"]
    else:
        outcome, why = SUPPORTED, []
    summary = _SUMMARY[outcome].format("; ".join(why[:5]))
    next_actions = [first_action if first_action and outcome == INSUFFICIENT else _NEXT[outcome]]
    next_actions += [f"{req['id']}: {req['reason']}; suggested: {req['suggested_test']}" for req in evaluated
                     if "suggested_test" in req]
    next_actions += [action for action in follow_ups if action != next_actions[0]]
    if unknowns:
        next_actions.append("Look into the unknowns yourself: G6 does not count them for or against the patch.")
    environment = {"isolation": ran.get("isolation"), "python": ran.get("python"),
                   "interpreter_source": ran.get("interpreter_source"),
                   # the variables G6_REPO_ENV gave the tests, each with the SHA-256 of its value (never the value)
                   "repo_env": ran.get("repo_env"), "env_set_by_g6": ran.get("env_set_by_g6"),
                   "host_python_version": platform.python_version(), "platform": platform.platform()}
    # the envelope's evidence items are as they were before checks carried a provenance (in scope.checks_run)
    evidence = [{"source": "g6.workforce.repo_tests", "summary": f"tier {run['tier']}: {run['command']}: "
                 f"{run['verdict']}"[:200], "authority": "host-observed", "artifact_digest": artifact,
                 "base_commit": change.base_commit,
                 "scope": "these test outcomes on this working tree; not complete correctness",
                 **{key: value for key, value in run.items() if key != "provenance"}} for run in runs]
    proposals, assumptions = _listed(listed, assumed)
    assessment = {
        "outcome": outcome, "summary": summary, "reasons": reasons,
        "requirements": evaluated,
        "requirements_without_adequate_check": [r["id"] for r in evaluated if r["status"] == NOT_ESTABLISHED],
        "specification_note": SPECIFICATION_NOTE, "proposals": proposals,
        "scope": {"artifact_digest": artifact, "base_commit": change.base_commit, "changed_files": list(change.files),
                  "environment": environment, "checks_run": runs, "checks_skipped": skipped, "receipts": receipts,
                  "assumptions": assumptions,
                  # the search configuration (protocol item 1): what ran besides the stated checks, and no model call
                  "search": {**verification_search.shown_config(config or verification_search.search_config(None)),
                             **{"template_instances": 0, "proposed_checks": 0, "model_calls": 0, **(counts or {})}},
                  "import_roots": list(roots), "seconds": round(time.monotonic() - started, 3)},
        "unknowns": unknowns, "next_actions": next_actions, "not_checked": list(NOT_CHECKED),
        "completion": verification_search.completion(evaluated, receipts, runs, skipped, list(defects), search,
                                                     artifact=artifact, base_commit=change.base_commit,
                                                     templates=prepared, adequacy=adequacy, stopped=stopped,
                                                     proposals=proposer)}
    from g6.harness import patch_report   # A1: the readable report, derived from the fields above; it decides nothing
    assessment["report"] = patch_report.safe_render(assessment)
    card = warning_card(CODE, summary, next_actions[0])
    return {**envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                              request_id=uuid.uuid4().hex[:12], degradation_reason=summary[:300]),
            "ok": True, "assessment": assessment,
            "merge": {"status": "requires_separate_approval", "reason": MERGE_REASON, "reasons": merge_reasons},
            "gate": gate, "grounding": _grounding(claim, sources), "artifact_digest": artifact}
