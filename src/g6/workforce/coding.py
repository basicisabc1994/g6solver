# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Compile a bounded Python repository objective into the existing workforce."""
from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

from g6.core import confine
from g6.intent_engine.schema import CheckpointSpec
from g6.reliability_pipeline.failure_modes import SKILLS
from g6.reliability_pipeline.feasibility import (
    ACCESS_TERMS,
    classified_text,
    deterministic_assess,
)
from g6.reliability_pipeline.intake import (
    REPLAY_ANSWER,
    REPLAY_REQUIREMENT,
    replay_question,
    run_intake,
)
from g6.reliability_pipeline.premortem import construct_replay_check, constructor
from g6.reliability_pipeline.stages import StageState
from g6.workforce import repo_tests, truth
from g6.workforce import (
    acceptance,
    bench,
    patch_checks,
    readiness,
    reconfigure,
)
from g6.workforce.contracts import BLOCKED, Outcome

# COPY: The worker reads OBJECTIVE_INSTRUCTIONS in its brief, and the
# frozen contract carries STANDARD_EXPLANATION, so neither may be a placeholder.
OBJECTIVE_INSTRUCTIONS = (
    'Submit {"files": {"<path>": "<the full new text>"}} for the source files in scope only, and do not change ' 
    "the tests. G6 runs the frozen tests after each submission and tells you what failed.")
STANDARD_EXPLANATION = ("Code changes need at least R2. The baseline, tests, permissions, budget and the limit of "
                        "three proposals are fixed before work begins.")
REFUSAL = "G6 could not continue this repository objective under its frozen standard."
#: A repository G6 would copy to run (or a copy G6 made of one) holds a symbolic link or a junction, which may point
#: anywhere, outside it included: refused before anything is copied (``_snapshot``).
LINKED = "G6_E_WORKFORCE_REPOSITORY_LINKED"
# COPY: Refused before anything is copied: a link or junction in a repository G6 would copy and run.
LINKED_REFUSAL = ("[" + LINKED + "] G6 copied and ran nothing: {path} in this repository is a symbolic link or a "
                  "junction, which may point outside it. Remove it, commit, and create the mission again.")
POLICY_VERSION = "coding-production-v1"
MAX_ATTEMPTS = 3
MAX_HELPERS = 8
# COPY: Repair-loop stop reasons.
REPAIR_STOP = {"repeated": "The new proposal repeats an earlier one, so G6 stopped asking for repairs.",
               # COPY: Stop when the agreed checks show no improvement.
               "non_improving": ("The new proposal passed no more of the agreed checks than the last one, "
                                 "so G6 stopped asking for repairs."),
               # COPY: Stop at the frozen proposal limit.
               "attempt_limit": "All the proposals allowed before work began have been used.",
               # COPY: No executed failure to return for repair.
               "not_run": "The checks did not run, so there was no failure to send back for repair."}
# COPY: Repaired-result label.
REPAIRED = "Failure found and repaired"
#: The repair strategies, in order, frozen in the standard (E2E Build 6, test I): the checks' own feedback, then a
#: focused brief, then a person.
LADDER = ("feedback", "focused", "person")
# COPY: The person's step when the repair strategies are used up; the existing words "Human action required" lead.
PERSON = "Human action required: {why}"
#: Each ladder key's true trigger (review M-3: an attempt limit is never a repeated failure).
TRIGGER = {"repeated": "repeated_failure", "non_improving": "no_progress", "attempt_limit": "attempt_limit"}
# COPY: Why the strategy in force became inadequate, said without claiming G6 stopped (it may ask again).
CAUSE = {"repeated": "The new proposal repeats an earlier one.",
         "non_improving": "The new proposal passed no more of the agreed checks than the last one.",
         "attempt_limit": "All the proposals allowed before work began have been used."}
# COPY: The unit's reason when a repeated proposal goes back with a focused brief (G6 asks again).
ASKED_AGAIN = "The new proposal repeats an earlier one, so G6 did not check it again and asks with a focused brief."
# COPY: The rule a focused brief states.
REPEAT_RULE = "A proposal G6 has already judged is not judged again: sending it again uses an attempt and is not checked."
# COPY: The focused brief's message.
FOCUSED = ("G6 has changed how it asks for this repair. Below are only what the failing check showed, and the rejected "
           "proposal's digest and its difference from the baseline. " + REPEAT_RULE)
_ANSWERED = "the oracle answered: "
# COPY: Scope refusals name the evidence limit and any competing candidates.
NO_SCOPE = "G6 found no function under test in the executed failing tests."
# COPY: A person can resolve the ambiguity using these repository function names.
AMBIGUOUS_SCOPE = "G6 found several functions under test; name the intended scope: "
#: F-2 (founder, 2026-10-07): on a code change, an objective whose only access terms are these takes the risky route
#: (R3, the person confirms before any plan, then approves the checked patch) instead of the refusal. "private key"
#: and "production access" still refuse, alone or mixed with these.
CHANGE_ACCESS_TERMS = ("password", "credential")
# COPY: Why a password or credential fix waits for the person (the confirmation question, F-2; review M-6: it is a
# word match, so it says "mentions").
ACCESS_WHY = ("This objective mentions passwords or credentials, so G6 runs the checks and then asks you to approve "
              "the patch. ")
#: Spelling the classifier does not normalise: "private-key", "private_key" and "private  key" are one term (M-1).
_SEPARATORS = re.compile(r"[\s_\-]+")


def _without(text: str) -> str:
    """``text`` lower-cased, its separators made single spaces, the password and credential words taken out (M-1)."""
    return re.sub("|".join(CHANGE_ACCESS_TERMS), " ", _SEPARATORS.sub(" ", str(text or "").lower()))


def names_access(intake) -> bool:
    """The objective mentions an access term in any spelling ("private-key", "private_key", "privatekey"), negated or
    not: on a code change that is the F-2 route or the refusal, never the plain risky route."""
    contract = intake.metadata.get("task_contract")
    if contract is None:
        return False
    spaced = _SEPARATORS.sub(" ", classified_text(contract.intent))
    return any(term in spaced or term.replace(" ", "") in spaced.replace(" ", "") for term in ACCESS_TERMS)


def access_terms(intake) -> list[str]:
    """The access terms that send this code change to R3 instead of the refusal (F-2): the feasibility answer is
    ``risky`` (an access term is a tripwire signal there, audit 030 FEA-002) and every access term the objective
    mentions is a password or credential term. Review M-1: another access term in any spelling still refuses, and so
    does every other class the password or credential words hid (the objective without them must classify clear).
    Term names only, never the objective's text; ``[]`` otherwise, and the refusal stands."""
    feasibility, contract = intake.metadata.get("feasibility_report"), intake.metadata.get("task_contract")
    if feasibility is None or contract is None or feasibility.classification != "risky":
        return []
    text = classified_text(contract.intent)
    found = [term for term in ACCESS_TERMS if term in text]
    if not found or not set(found) <= set(CHANGE_ACCESS_TERMS):
        return []
    spaced = _SEPARATORS.sub(" ", text)
    if any(term in spaced or term.replace(" ", "") in spaced.replace(" ", "")
           for term in ACCESS_TERMS if term not in CHANGE_ACCESS_TERMS):
        return []
    rest = contract.intent.model_copy(update={"goal": _without(contract.intent.goal),
                                              "context": _without(contract.intent.context),
                                              "constraints": [_without(c) for c in contract.intent.constraints]})
    return found if deterministic_assess(rest).classification == "clear" else []


# COPY: Why a plan for a high-consequence code change is refused without a person's approval after its checks.
APPROVAL_KEPT = ("plan: a high-consequence code change keeps a person's approval: the plan must end at a gate that "
                 "every check leads to, as G6's own plan for it does (approve-patch)")


def plan_problems(standard: dict, plan) -> list[str]:
    """Bind every coding obligation to executable checks, regardless of who drew the plan.

    Check names/order and additive checks are free choices. The frozen repository,
    required tests, candidate artifact, regression scope and human gate are not.
    Run on admission, amendments and recovery; immutable metadata alone cannot
    prevent a graph from quietly checking something else.
    """
    if not standard:
        return []
    problems = []
    try:
        patch = plan.unit("patch")
    except KeyError:
        patch = None
    prefix = "plan: the frozen coding standard requires "
    if patch is None or patch.kind != "do" or patch.output_schema != "files":
        problems.append(prefix + "a patch artifact containing the changed files")
    if patch is not None and patch.max_attempts > standard["max_attempts"]:
        problems.append(prefix + f"at most {standard['max_attempts']} patch proposals")
    required = set(standard["required_tests"])
    covered, regression = set(), False
    signature = {**standard["task_signature"], "declared": standard.get("declared", [])}
    for unit in plan.units:
        oracle = unit.oracle or {}
        params = oracle.get("params", {})
        expect = oracle.get("expect", {})
        if not (unit.kind == "verify" and unit.verifies == "patch"
                and oracle.get("capability") == ["workforce", "run_tests"]
                and oracle.get("bind") == {"files": "patch.files"}
                and params.get("repo") == standard["snapshot"]
                and expect.get("field") == "passed" and expect.get("equals") is True):
            continue
        tests = params.get("tests")
        if not isinstance(tests, list) or not all(isinstance(node, str) for node in tests):
            continue
        if oracle.get("kind") == "test" and "regression" not in params:
            covered.update(tests)
        if (oracle.get("kind") == "deterministic" and params.get("regression") == signature
                and required <= set(tests)):
            regression = True
    if required - covered:
        problems.append(prefix + "executed test checks for " + ", ".join(sorted(required - covered))
                        + "; use the frozen snapshot, bind patch.files and require passed=true")
    if not regression:
        problems.append(prefix + "the regression check of patch.files against the frozen snapshot and task scope")
    if standard.get("derivation", {}).get("effective_risk") == "R3":
        checks = {unit.key for unit in plan.units if unit.kind == "verify"}
        gated = any(unit.kind == "gate" and checks <= plan.upstream(unit.key) for unit in plan.units)
        if not checks or not gated:
            problems.append(APPROVAL_KEPT)
    return problems


def access_reason(standard: dict) -> str:
    """The confirmation question's reason for a password or credential fix (F-2), or ``""``."""
    signals = standard.get("derivation", {}).get("consequence", {}).get("signals")
    return ACCESS_WHY if isinstance(signals, list) and signals else ""


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def retry_stop(*, attempts: int, limit: int, score: float, failure: bool,
               previous_score: float | None = None, repeated: bool = False) -> str:
    """The repair loop's deterministic stop decision over an unchanged oracle (from the archive's
    ``coder.micro_loop``); no generation or execution."""
    if score >= 1:
        return "complete"
    if not failure:
        return "not_run"
    if repeated:
        return "repeated"
    if previous_score is not None and score <= previous_score:
        return "non_improving"
    if attempts >= limit:
        return "attempt_limit"
    return ""


def frozen(wf, mission_id: str) -> dict:
    return wf.missions.acceptance(mission_id).metadata.get("coding") or {}


def snapshot_problem(standard: dict, venture: str) -> str:
    """Current snapshot bytes must still be the accepted bytes at every execution boundary."""
    if not standard:
        return ""
    try:
        snapshot = bench._repo(standard.get("snapshot"), venture)
        if snapshot is not None and bench._digest(snapshot) == standard.get("baseline_digest"):
            return ""
    except (OSError, ValueError):
        pass
    return ("The frozen coding snapshot changed or is unavailable. Restore its accepted bytes or use mission_create "
            "for a new standard; changed checks cannot be reused.")


def _reproduce(source, tests, venture, *, baseline, op="run_tests") -> dict:
    results, receipts = {}, []
    before = bench._digest(source)
    for node in tests:
        truth.LAST_RUN.clear()
        started = time.perf_counter()
        reply = bench.run(op, {"repo": source.name, "tests": [node]}, venture=venture)
        run = dict(truth.LAST_RUN)
        outcome = Outcome.from_envelope(reply)
        measured = reply.get("result", {})
        if op == bench.IN_PLACE:
            counts = measured.get("counts", {})
            executed = (measured.get("verdict") in (repo_tests.PASSED, repo_tests.FAILED)
                        and counts.get("total") == 1 and counts.get("passed", 0) + counts.get("failed", 0) == 1)
            returncode = None  # this envelope supplies counts and a verdict, not a process exit code
        else:
            found = run.get("tally") or {}  # audit 010 WF-003: the runner's record, never the printed tally
            executed = run.get("rc") in (0, 1) and bool(found.get("passed") or found.get("failed"))
            returncode = run.get("rc")
        results[node] = bool(executed and outcome.state != BLOCKED and not outcome.degraded and measured.get("passed"))
        # coverage ledger R2, lead ruling L-7: each receipt names the route that made it
        receipts.append({"execution_id": uuid.uuid4().hex, "op": f"{bench.COMPONENT}.{op}", "test": node,
                         "executed": executed,
                         "returncode": returncode, "seconds": time.perf_counter() - started,
                         "baseline_revision": baseline,
                         "test_digest": hashlib.sha256((source / node.split("::")[0]).read_bytes()).hexdigest(),
                         "cost": None})
        if op == bench.IN_PLACE:
            receipts[-1].update(counts=counts, verdict=measured.get("verdict"))
        if not executed:  # E2E Build 6: no blind run of the rest; the caller decides whether the route is unavailable
            break
    if bench._digest(source) != before:
        raise ValueError(REFUSAL)
    found = {"reproduced": all(r["executed"] for r in receipts) and len(receipts) == len(tests)
             and not all(results.values()), "tests": results, "receipts": receipts}
    if not receipts[-1]["executed"]:
        found["unexecuted"] = receipts[-1]
    return found


def _modules(parts):
    """The files a dotted module path may be, at the repository root or under ``src/`` (ECHA Stage A: a ``src`` layout
    project's package is not at the root), as a module or as a package's ``__init__``."""
    base = "/".join(parts)
    for root in ("", "src/"):
        yield f"{root}{base}.py"
        yield f"{root}{base}/__init__.py"


def _defined(source, qualified):
    parts = qualified.split(".")
    for split in range(len(parts) - 1, 0, -1):
        for module in _modules(parts[:split]):
            path = source / module
            if not path.is_file() or bench._TEST_FILE.search(module):
                continue
            body = ast.parse(path.read_text(encoding="utf-8"))
            for part in parts[split:]:
                body = next((n for n in getattr(body, "body", [])
                             if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part), None)
            if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return module, ".".join(parts[split:])
    return None


def derive_scope(source, tests, venture, *, baseline: str | None = None) -> dict:
    """Resolve only repository functions called by tests that actually failed in the bench's copy."""
    baseline = baseline or repo_tests._git(source, "rev-parse", "HEAD").decode().strip()
    reproduction = _reproduce(source, tests, venture, baseline=baseline)
    if not reproduction["reproduced"]:
        raise ValueError(NO_SCOPE)
    failing = [node for node, passed in reproduction["tests"].items() if not passed]
    candidates = set()
    for node in failing:
        relative, *path = node.split("::")
        tree = ast.parse((source / relative).read_text(encoding="utf-8"))
        test = _test_function(tree, path)
        if test is None:
            continue
        bindings = {}
        for statement in [*tree.body, *test.body]:
            if isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
                bindings.update({a.asname or a.name: f"{statement.module}.{a.name}" for a in statement.names})
            elif isinstance(statement, ast.Import):
                bindings.update({a.asname or a.name.split(".")[0]: a.name if a.asname else a.name.split(".")[0]
                                 for a in statement.names})
        module_statements = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        shadowed = {n.id for s in [*module_statements, test] for n in ast.walk(s)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
        shadowed.update(n.arg for n in ast.walk(test.args) if isinstance(n, ast.arg))
        shadowed.update(n.name for n in [*tree.body, *test.body]
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        bindings = {name: target for name, target in bindings.items() if name not in shadowed}
        for call in (n for n in ast.walk(test) if isinstance(n, ast.Call)):
            called, suffix = call.func, []
            while isinstance(called, ast.Attribute):
                suffix.insert(0, called.attr)
                called = called.value
            if isinstance(called, ast.Name) and called.id in bindings:
                candidate = _defined(source, ".".join([bindings[called.id], *suffix]))
                if candidate:
                    candidates.add(candidate)
    if not candidates:
        raise ValueError(NO_SCOPE)
    if len(candidates) != 1:
        raise ValueError(AMBIGUOUS_SCOPE + ", ".join(f"{m}:{f}" for m, f in sorted(candidates)))
    selected = [{"module": m, "function": f} for m, f in sorted(candidates)]
    return {"derived": True, "method": "failing-test-static-calls-v1", "failing": failing,
            "candidates": selected, "selected": selected[0], "receipts": reproduction["receipts"],
            "results": reproduction["tests"], "baseline_digest": bench._digest(source)}


def _declared(source: Path, module: str, function: str, helpers) -> list[dict]:
    """Founder, 2026-10-08 ("this should be a disclosure"): the functions of the named module that the caller says the
    objective asks to change (``helpers`` in repo_json). A difference the probe finds in one of them is disclosed to the
    person instead of sent back as a defect; a difference in any other function is still a defect. Declared by the
    caller, never read from the objective's prose, which cannot tell "change X" from "leave X alone". A name that is not
    a function of the module, or too long a list, refuses the mission, so a typo cannot widen what is excused."""
    if helpers in (None, [], ""):
        return []
    if not isinstance(helpers, list) or len(helpers) > MAX_HELPERS or not all(isinstance(h, str) for h in helpers):
        raise ValueError(REFUSAL)
    try:
        defined = patch_checks._functions(ast.parse((source / module).read_text(encoding="utf-8")).body)
    except (OSError, SyntaxError, ValueError):
        raise ValueError(REFUSAL) from None
    if any(name not in defined for name in helpers):
        raise ValueError(REFUSAL)
    return [{"module": module, "function": name} for name in dict.fromkeys(helpers) if name != function]


# COPY: Said in a finished result when some of the repository's tests could not be counted (skipped, expected to fail, failed to set up).
NOT_COUNTED = ("G6 could not count {count} of this repository's tests (skipped, expected to fail or not runnable), so none "
               "of them checks this change.")
# COPY: Said in a finished result when the repository's whole test suite could not be run to freeze the standard.
SUITE_NOT_RUN = ("G6 could not run this repository's whole test suite when it froze what counts as done, so it could not "
                 "tell which of its tests it could not count.")
# COPY: Said in a finished result when some of the repository's test files cannot be imported here, so their tests were not compared.
NOT_IMPORTED = "{count} of this repository's test files could not be imported here, so their tests were not compared with this change."
# COPY: Said in a finished result when the patched copy could not be compared with the rest of the repository's tests.
PATCH_NOT_COMPARED = ("G6 could not compare this change with the rest of the repository's tests, so only the tests named for "
                      "this task were checked.")


def suite_words(standard: dict) -> list[str]:
    """What the frozen standard says G6 did not count: the words for a person to read beside a result (ECHA Stage A)."""
    suite = standard.get("suite") if isinstance(standard.get("suite"), dict) else {}
    words = []
    if suite.get("status") == "not_run":
        words.append(SUITE_NOT_RUN)
    if suite.get("not_counted"):
        words.append(NOT_COUNTED.format(count=len(suite["not_counted"])))
    return words


def _with_class_tests(candidates: list[str], suite: dict) -> list[str]:
    """Integrated audit F01 (2026-10-11): the tests a repository keeps in classes. The AST read of top-level ``def test_*``
    never sees ``file::TestClass::test_x``, so a repository that keeps all its tests in classes had none and was refused. When
    the whole suite ran, pytest's own node ids of that shape (a parametrized case under its base id) are added after the
    top-level ones, in order, once each; ``_counted`` then keeps only those it can hold a patch to."""
    if not suite.get("ran"):
        return candidates
    nested = sorted({node.split("[", 1)[0] for node in suite["outcomes"] if node.split("[", 1)[0].count("::") >= 2})
    return list(dict.fromkeys([*candidates, *nested]))


def _test_function(tree, path: list[str]):
    """The ``def`` a test node id names inside a parsed test file (through its classes), or None."""
    body = tree
    for part in path:
        body = next((n for n in getattr(body, "body", [])
                     if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part), None)
    return body if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _counted(candidates: list[str], suite: dict) -> tuple[list[str], dict]:
    """ECHA Stage A (founder decision (a)): which of a repository's top-level ``def test_*`` functions G6 can hold a patch
    to. The runner reports only a pass or a failure for a test, so one pytest names as skipped, expected to fail, passed
    unexpectedly or errored at set-up is not countable, and a ``def test_*`` that pytest does not collect (a fixture named
    like a test) is not a test: each is left out of the frozen list and recorded with its reason, where before any one of
    them made G6 refuse the whole repository. ``suite``: ``truth.run_suite`` on the frozen copy. When it could not run, the
    candidates stand as they were and the record says it did not run."""
    if not suite["ran"]:
        return candidates, {"status": "not_run", "reason": suite["why"], "passed": 0, "not_counted": []}
    seen: dict[str, set] = {}
    for node, outcome in suite["outcomes"].items():
        seen.setdefault(node.split("[", 1)[0], set()).add(outcome)
    kept, dropped = [], []
    for node in candidates:
        outcomes = seen.get(node, set())
        if outcomes & {"passed", "failed"}:
            kept.append(node)
        else:
            file = node.split("::")[0]
            gone = ("skipped" if file in suite.get("collect_skipped", ())
                    else "could not be collected" if file in suite["collect_errors"] else "not a test")
            dropped.append({"test": node, "why": min(outcomes) if outcomes else gone})
    return kept, {"status": "ran", "reason": "", "passed": sum(o == "passed" for o in suite["outcomes"].values()),
                  "not_counted": sorted(dropped, key=lambda item: item["test"])}


def reuse_reference(missions, mission_id: str) -> dict:
    """A code-created reference, never caller-supplied executable or evidence authority."""
    events = missions.events(mission_id, kind="mission.created")
    if events:
        detail = events[0]["detail"]
        if detail.get("actor") == "code" and detail.get("template") == "code_change":
            ref = detail.get("materials", {}).get("harness_reuse")
            if isinstance(ref, dict):
                return ref
    return {}


def _reuse(wf, *, objective, repository, declared_risk, budget_cents):
    from g6.workforce import service

    ref = repository.get("harness_pack")
    if (set(repository) != {"harness_pack"} or not isinstance(ref, dict)
            or set(ref) != {"version", "mission_id", "standard_sha256", "reuse_id"}
            or type(ref.get("version")) is not int or ref["version"] != 1
            or not isinstance(ref.get("mission_id"), str) or not re.fullmatch(r"[a-f0-9]{12}", ref["mission_id"])
            or not isinstance(ref.get("standard_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", ref["standard_sha256"])
            or not isinstance(ref.get("reuse_id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ref["reuse_id"])):
        raise ValueError("harness_pack requires version 1, mission_id, standard_sha256 and a nonempty safe reuse_id only.")
    source = wf.missions.get(ref["mission_id"])
    accepted = wf.missions.acceptance(ref["mission_id"])
    standard = accepted.metadata.get("coding") or {}
    created = wf.missions.events(ref["mission_id"], kind="mission.created")[0]["detail"]
    if (source["venture_id"] != wf.venture_id or source["status"] != "done" or source["acceptance"] is None
            or created.get("actor") != "code" or created.get("template") != "code_change"
            or not standard.get("harness_intelligence", {}).get("checks")
            or accepted.sha256 != ref["standard_sha256"]):
        raise ValueError("Reuse needs this venture's completed coding Harness Pack and its unchanged accepted standard.")
    if (objective != source["objective"] or declared_risk != source["risk_tier"]
            or type(budget_cents) is not int or budget_cents != source["budget_cents"]):
        raise ValueError("Harness reuse preserves the original objective, risk and budget; create a new standard for changes.")
    problem = snapshot_problem(standard, wf.venture_id)
    if problem:
        raise ValueError(problem)
    inherited = reuse_reference(wf.missions, ref["mission_id"])
    origin = inherited.get("construction_mission_id", ref["mission_id"])
    root = wf.missions.get(origin)
    if root["venture_id"] != wf.venture_id or wf.missions.acceptance(origin).sha256 != accepted.sha256:
        raise ValueError("The original construction standard is unavailable or different.")
    receipts = [e for e in wf.missions.events(origin, kind="message")
                if e["detail"].get("actor") == "code" and e.get("idem_key") == f"{origin}:coding:harness-construction"
                and e["detail"].get("refs", {}).get("harness_construction", {}).get("standard_sha256") == accepted.sha256]
    if not receipts:
        raise ValueError("The Harness Pack has no bound original construction receipt.")
    provenance = {**ref, "construction_mission_id": origin, "construction_receipt": receipts[0]["evidence_id"]}
    mid = wf.missions.create(wf.venture_id, objective=objective, risk_tier=declared_risk, budget_cents=budget_cents,
        actor="code", contract=wf.missions.contract(ref["mission_id"]), template="code_change",
        task_sha256=standard["task_signature"]["sha256"], mode=wf.missions.mode(ref["mission_id"]),
        materials={"harness_reuse": provenance}, request_key="harness-reuse:" + _sha([ref["mission_id"], ref["reuse_id"]]))
    child = wf.missions.get(mid)
    if child["status"] == "eliciting" and child["acceptance"] is None:
        try:
            wf.missions.accept(mid, accepted, actor="code", contract=wf.missions.contract(ref["mission_id"]))
        except ValueError:
            # A concurrent identical request may already have committed the exact standard.
            if wf.missions.get(mid)["acceptance"] is None or wf.missions.acceptance(mid).sha256 != accepted.sha256:
                raise
    # Creation/retry never dispatches probes or repository code, even after confirmation.
    return service.status(wf, mid)


def clarification_request(missions, mission_id: str) -> dict:
    """Only code-created intake material is control data; caller materials cannot impersonate it."""
    events = missions.events(mission_id, kind="mission.created")
    if not events:
        return {}
    created = events[0]
    detail = created["detail"]
    if detail.get("actor") == "code" and detail.get("template") == "code_change":
        request = detail.get("materials", {}).get("coding_intake")
        if isinstance(request, dict):
            return {**request, "request_id": created["evidence_id"]}
    return {}


def _clarification_attempts(missions, mission_id: str) -> list[dict]:
    return [e for e in missions.events(mission_id, kind="message")
            if e["detail"].get("actor") == "code"
            and e.get("idem_key") in (f"{mission_id}:coding:intake-answer", f"{mission_id}:coding:intake-finished")]


def _unsupported_answer(missions, mission_id: str) -> dict:
    for event in missions.events(mission_id, kind="message"):
        if (event.get("idem_key") == f"{mission_id}:coding:intake-answer"
                and event["detail"].get("actor") == "chat"):
            return event["detail"]["refs"]["answer"]
    return {}


def clarification_status(wf, mission_id: str) -> dict:
    request = clarification_request(wf.missions, mission_id)
    mission = wf.missions.get(mission_id)
    if not request or mission["status"] != "eliciting":
        return {}
    attempts = _clarification_attempts(wf.missions, mission_id)
    unsupported = _unsupported_answer(wf.missions, mission_id)
    problem = (attempts[-1]["detail"].get("refs", {}).get("problem") if attempts else "")
    if unsupported:
        problem = ("This requirement is not covered by the available replay constructor. The answer is retained; "
                   "do not substitute the narrower check. Use mission_create with explicit requirements for another contract.")
    attention = bool(attempts or unsupported)
    return {"interaction_state": "attention_required" if attention else "awaiting_input",
            "clarification": {"mission_id": mission_id, "request_id": request["request_id"],
                **request["question"], "repository": request["repository"],
                "source_revision": request["baseline_revision"], "source_digest": request["baseline_digest"],
                "state": "unsupported_requirement" if unsupported else "preparation_recorded" if attempts else "unanswered",
                **({"answer": unsupported} if unsupported else {}),
                "problem": problem or ("Preparation may have executed repository code. It is not repeated; inspect the "
                                       "history before explicitly creating a new mission with mission_create." if attempts else ""),
                "permitted_answer": {"replay_semantics": ([o["key"] for o in request["question"]["options"]]
                    if "options" in request["question"] else REPLAY_ANSWER)},
                "next_action": {"tool": "mission_status" if attention else "mission_accept",
                    "arguments": {"mission_id": mission_id, **({"detail": "full"} if attention else {} if "options" in request["question"] else {
                        "contract_json": json.dumps({"replay_semantics": REPLAY_ANSWER})})}},
                "approval": "Answering supplies requirements and starts repository preparation checks. Human confirmation of the resulting coding standard is still required before patch work."}}


def _ask_replay(wf, *, source, baseline, question, objective, repository, declared_risk, budget_cents):
    from g6.workforce import service

    snapshot = source.parent / f"coding-intake-{uuid.uuid4().hex}"
    _snapshot(source, snapshot)
    if (repo_tests._git(source, "status", "--porcelain")
            or repo_tests._git(source, "rev-parse", "HEAD").decode().strip() != baseline
            or any(p.read_bytes() != (source / p.relative_to(snapshot)).read_bytes()
                   for p in snapshot.rglob("*") if p.is_file())):
        raise ValueError(REFUSAL)
    request = {"version": 1, "repository": repository, "snapshot": snapshot.name,
               "baseline_revision": baseline, "baseline_digest": bench._digest(snapshot),
               "declared_risk": declared_risk, "question": question if isinstance(question, dict) else question.model_dump()}
    mid = wf.missions.create(wf.venture_id, objective=objective, risk_tier=max("R2", declared_risk),
                             budget_cents=budget_cents, actor="code", template="code_change",
                             mode=repository.get("mode"), materials={"coding_intake": request})
    return service.status(wf, mid)


def clarify(wf, mission_id: str, answer: dict | None) -> dict:
    """Resolve a supported requirement once; the existing confirmation still follows."""
    from g6.workforce import service

    mission = wf.missions.get(mission_id)
    if mission["venture_id"] != wf.venture_id:
        raise ValueError("Resume this coding clarification in its original bound venture.")
    request = clarification_request(wf.missions, mission_id)
    if not request:
        raise ValueError("This mission has no coding clarification request.")
    # Persisted, code-authored options survive a reconnect, even if the operator later disables readiness.
    selected = next((i for skill in SKILLS for i in skill.interpretations
                     if answer == {"replay_semantics": i.key}
                     and i.key in {o["key"] for o in request["question"].get("options", [])}), None)
    not_applicable = (answer == {"replay_semantics": "not_applicable"}
                      and any(o["key"] == "not_applicable" for o in request["question"].get("options", [])))
    if selected is None and not not_applicable and answer != {"replay_semantics": REPLAY_ANSWER}:
        if mission["status"] != "eliciting" or answer and set(answer) != {"replay_semantics"}:
            raise ValueError("Resolve the coding clarification with replay_semantics; an arbitrary contract cannot replace it.")
        if answer:
            value = answer["replay_semantics"]
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError("replay_semantics must be a nonempty string of at most 1000 characters.")
            eid = wf.missions.record_event(mission_id, "message", actor="chat", detail={
                "from": "chat", "to": "intake", "text": "An unsupported replay requirement remains unresolved.",
                "refs": {"answer": answer, "request_id": request["request_id"]}}, idem_key="coding:intake-answer")
            recorded = next(e for e in wf.missions.events(mission_id, kind="message") if e["evidence_id"] == eid)
            if recorded["detail"]["refs"]["answer"] != answer:
                raise ValueError("The coding clarification already has a different answer; it cannot be silently replaced.")
        return service.status(wf, mission_id)
    if mission["status"] != "eliciting":
        if frozen(wf, mission_id).get("intake_clarification", {}).get("answer") == answer:
            return service.status(wf, mission_id)
        raise ValueError("The coding clarification cannot change an accepted or closed standard.")
    if _unsupported_answer(wf.missions, mission_id):
        raise ValueError("The coding clarification already requires unsupported semantics; use mission_create for a new contract.")
    attempts = _clarification_attempts(wf.missions, mission_id)
    if attempts:
        if (selected or not_applicable) and attempts[0]["detail"].get("refs", {}).get("answer") != answer:
            # COPY: A newly listed option cannot replace the answer that already owns preparation.
            raise ValueError("The coding clarification already has a different answer; it cannot be silently replaced.")
        return service.status(wf, mission_id)
    source = bench._repo(request["snapshot"], wf.venture_id)
    if source is not None:
        _refuse_links(source)   # before its digest, which would read through a junction
    if source is None or bench._digest(source) != request["baseline_digest"]:
        raise ValueError("The retained clarification snapshot changed or is unavailable. Use mission_create for a new "
                         "standard; no repository code was dispatched.")
    named = str(request["repository"].get("repo"))
    if not execution_allowed(wf, named, request["baseline_revision"], request["baseline_digest"]):  # WF-007
        raise ValueError(NOT_ALLOWED.format(repository=named, revision=request["baseline_revision"]))
    token = uuid.uuid4().hex
    eid = wf.missions.record_event(mission_id, "message", actor="code", detail={
        "from": "intake", "to": "coding", "text": "The chat supplied replay semantics; preparing the standard.",
        "refs": {"token": token, "answer": answer, "request_id": request["request_id"], "answer_actor": "chat"}},
        idem_key="coding:intake-answer")
    # record_event returns the old row to both a retry and a concurrent loser.
    # Only the token actually committed in that unique row owns this dispatch.
    recorded = next(e for e in wf.missions.events(mission_id, kind="message") if e["evidence_id"] == eid)
    if recorded["detail"]["refs"]["answer"] != answer:
        raise ValueError("The coding clarification already has a different answer; it cannot be silently replaced.")
    if recorded["detail"]["refs"].get("token") != token:
        return service.status(wf, mission_id)
    clarification = {"request_id": request["request_id"], "answer_receipt": eid, "answer": answer,
                     "answer_actor": "chat", "question": request["question"], "requirement": REPLAY_REQUIREMENT}
    if not_applicable:
        clarification.pop("requirement")
        clarification["not_applicable"] = True
        # COPY: Record the person's exclusion and existing scope, without claiming a check.
        wf.missions.record_event(mission_id, "message", actor="code", detail={
            "from": "readiness", "to": "coding",
            "text": "This is not about repeated events. Work proceeds on the existing tests. Assumptions: "
                    + request["question"]["preview"]["forbidden_effects"],
            "refs": {"readiness": {"decision": "fast_path", "model_calls": 0,
                "skills_consulted": [request["question"]["skill_id"]],
                "understanding": request["question"]["preview"], "version": "readiness.v1",
                "not_asked": [(request["question"]["skill_id"], "person_said_not_applicable")]}}},
            idem_key="coding:readiness")
    elif selected:
        clarification.update(requirement=selected.requirement, relation=selected.relation)
        if selected.relation is None:
            clarification["cannot_check"] = selected.missing_capability
            wf.missions.record_unmet(mission_id, kind="cannot_check", subject=selected.requirement,
                                     why=selected.missing_capability)
    try:
        with tempfile.TemporaryDirectory(prefix="clarified-", dir=source.parent) as directory:
            snapshot = Path(directory)
            _snapshot(source, snapshot, patterns=(), dirs_exist_ok=True)
            if bench._digest(snapshot) != request["baseline_digest"]:
                raise ValueError("The retained clarification snapshot changed during preparation.")
            _prepare(wf, source=snapshot, baseline=request["baseline_revision"], objective=mission["objective"],
                     repository=request["repository"], declared_risk=request["declared_risk"],
                     budget_cents=mission["budget_cents"], mission_id=mission_id, clarification=clarification)
    except Exception as exc:  # noqa: BLE001 - executed or uncertain preparation is durable and never silently retried
        wf.missions.record_event(mission_id, "message", actor="code", detail={
            "from": "intake", "to": "coding", "text": "Preparation did not return successfully.",
            "refs": {"problem": f"{type(exc).__name__}: {exc}. Inspect history before using mission_create for a new mission."[:2000]}},
            idem_key="coding:intake-finished")
    return service.status(wf, mission_id)


# COPY: What a person reads and allows before G6 runs any code of a repository (audit 010 WF-007).
EXECUTION_TERMS = (
    "Allow G6 to run this repository's code: its tests, their conftest.py files and every module they import.",
    "G6 runs it in a scratch copy, as you and with your permissions, when a coding mission is created on this "
    "revision (to freeze what counts as done) and for every check after that. This is not an operating-system sandbox.",
    "Repository: {repository}. Revision: {revision}. Content digest: {digest}.")
# COPY: Refused before anything runs: no person allowed G6 to run this repository's code at this revision (WF-007).
NOT_ALLOWED = ("G6 ran nothing: no person has allowed G6 to run the code of repository {repository!r} at revision "
               "{revision} with its present content. Allow it with g6.workforce.allow_execution, or run "
               "`python -m g6.workforce.decide allow {repository} --reason \"...\"` yourself, then create the mission "
               "again (code G6_E_HUMAN_APPROVAL_REQUIRED).")
#: The venture's record of each such permission (one evidence row, never a mission's).
EXECUTION_ALLOWED = "execution.allowed"
#: What G6 leaves out when it copies a repository to run it (the snapshot's own ignore patterns).
_NOT_COPIED = (".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc")


def _link_in(root: Path) -> str:
    """The first symbolic link or junction under ``root`` (``bench._linked``), relative to it, or "" when it holds none;
    it never looks inside a link."""
    folders = [root]
    while folders:
        for path in sorted(folders.pop().iterdir()):
            if bench._linked(path):
                return path.relative_to(root).as_posix()
            if path.is_dir():
                folders.append(path)
    return ""


def _refuse_links(source: Path) -> None:
    """Refuse (:data:`LINKED`) a ``source`` that holds a link or junction."""
    found = _link_in(source)
    if found:
        raise ValueError(LINKED_REFUSAL.format(path=found))


def _snapshot(source: Path, target: Path, *, patterns: tuple[str, ...] = _NOT_COPIED,
              dirs_exist_ok: bool = False) -> None:
    """Copy ``source`` to ``target`` to run it, leaving out ``patterns``: refused (:data:`LINKED`) before anything is
    copied when ``source`` holds a link or junction, and one that appears while it copies is left out, never followed
    (``shutil.copytree``'s default would copy in what it points at)."""
    _refuse_links(source)
    skip = shutil.ignore_patterns(*patterns)
    shutil.copytree(source, target, dirs_exist_ok=dirs_exist_ok, ignore=lambda folder, names: (
        set(skip(folder, names)) | {name for name in names if bench._linked(Path(folder) / name)}))


def content_digest(source: Path) -> str:
    """The digest ``bench._digest`` gives a copy G6 makes of ``source`` to run it (``_NOT_COPIED`` left out), read
    without copying: what a person's permission to run the repository's code is bound to (audit 010 WF-007)."""
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and not any(fnmatch.fnmatch(part, pattern) for part in relative.parts for pattern in _NOT_COPIED):
            digest.update(relative.as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def execution_terms(wf, repo: str) -> dict:
    """What a person allows before G6 runs any code of the venture's repository ``repo`` (audit 010 WF-007): its name,
    revision and content digest, as ``start`` checks them, and the ``lines`` the person reads. A repository ``start``
    would refuse (missing, with a link, uncommitted changes) is refused here too."""
    source = bench._repo(repo, wf.venture_id)
    if source is None:
        raise ValueError(REFUSAL)
    _refuse_links(source)
    if repo_tests._git(source, "status", "--porcelain"):
        raise ValueError(REFUSAL)
    terms = {"repository": repo, "revision": repo_tests._git(source, "rev-parse", "HEAD").decode().strip(),
             "digest": content_digest(source)}
    return {**terms, "lines": [line.format(**terms) for line in EXECUTION_TERMS]}


def allow_execution(wf, terms: dict, *, reason: str, decided_by: str, principal: dict,
                    timing: tuple | None = None) -> str:
    """Record a person's permission to run the code in ``terms`` (``execution_terms``), as the venture's evidence: the
    terms they read, why, who (``decided_by``, ``principal``: never more than the channel's own claim, WF-013) and when.
    Returns the record's id; the same terms allowed again keep the first record."""
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("a permission to run a repository's code gives its reason")
    detail = {key: terms[key] for key in ("repository", "revision", "digest", "lines")}
    detail.update(reason=reason[:2000], decided_by=decided_by, principal=principal, actor="human")
    if timing is not None:
        detail.update(started_at=timing[0].isoformat(timespec="seconds"), ended_at=timing[1].isoformat(timespec="seconds"))
    return wf.missions.scope.record_evidence(
        wf.venture_id, EXECUTION_ALLOWED, detail=detail,
        idem_key=f"execution-allowed:{terms['repository']}:{terms['revision']}:{terms['digest']}")


def execution_allowed(wf, repository: str, revision: str, digest: str) -> bool:
    """Whether a person allowed G6 to run the code of ``repository`` at exactly this revision and content digest."""
    return any(e["detail"].get("repository") == repository and e["detail"].get("revision") == revision
               and e["detail"].get("digest") == digest
               for e in wf.missions.scope.evidence(wf.venture_id, kind=EXECUTION_ALLOWED, limit=100_000))


# COPY: Said in every standard the premortem's templates did not apply to (audit 010 WF-015: no overclaim either way).
NOT_CONSTRUCTED = ("G6's premortem added no check to this standard: it has two fixed replay-check templates, each "
                   "applied only to one requirement sentence or a replay relation a person confirmed, and neither "
                   "applied here.")
# COPY: Refused before anything runs: formal analysis is quarantined out of the library (audit 010 §11).
NO_FORMAL = "Formal analysis is not part of the g6 library's coding mission; omit \"formal\" from the repository."
# COPY: Refused before anything runs: the A0 drafting executor is not ported (WF-014); submit the patch yourself.
NO_EXECUTOR = ("A coding executor is not part of the g6 library's coding mission; omit \"executor\" and submit the "
               "patch with submit_patch.")


def start(wf, *, objective: str, repository: dict, declared_risk: str, budget_cents: int) -> dict:
    if isinstance(repository, dict) and "formal" in repository:
        raise ValueError(NO_FORMAL)
    if "harness_pack" in repository:
        return _reuse(wf, objective=objective, repository=repository, declared_risk=declared_risk, budget_cents=budget_cents)
    _executor(wf, repository)
    if declared_risk not in acceptance.TIERS:
        raise ValueError(REFUSAL)
    source = bench._repo(repository.get("repo"), wf.venture_id)
    if source is None:
        raise ValueError(REFUSAL)
    _refuse_links(source)
    baseline = repo_tests._git(source, "rev-parse", "HEAD").decode().strip()
    dirty = repo_tests._git(source, "status", "--porcelain")
    if dirty:
        raise ValueError(REFUSAL)
    # audit 010 WF-007: the suite (and the scope's tests) run below, before the standard exists; a person allows that
    if not execution_allowed(wf, repository["repo"], baseline, content_digest(source)):
        raise ValueError(NOT_ALLOWED.format(repository=repository["repo"], revision=baseline))
    started = time.perf_counter()
    assessed = readiness.assess(objective, repository) if wf.readiness else None
    elapsed_ms = (time.perf_counter() - started) * 1000
    question = assessed.question if assessed else replay_question(objective)
    if question:
        return _ask_replay(wf, source=source, baseline=baseline, question=question, objective=objective,
                           repository=repository, declared_risk=declared_risk, budget_cents=budget_cents)
    with tempfile.TemporaryDirectory(prefix="scope-", dir=source.parent) as directory:
        snapshot = Path(directory)
        _snapshot(source, snapshot, dirs_exist_ok=True)
        if (repo_tests._git(source, "status", "--porcelain")
                or repo_tests._git(source, "rev-parse", "HEAD").decode().strip() != baseline):
            raise ValueError(REFUSAL)
        clarification = None
        if assessed and assessed.interpretation:
            selected = assessed.interpretation
            clarification = {"answer": {"replay_semantics": selected.key}, "answer_actor": "objective",
                             "requirement": selected.requirement, "relation": selected.relation}
            if selected.relation is None:
                clarification["cannot_check"] = selected.missing_capability
        result = _prepare(wf, source=snapshot, baseline=baseline, objective=objective, repository=repository,
                          declared_risk=declared_risk, budget_cents=budget_cents, clarification=clarification)
        if assessed and assessed.decision == "fast_path":
            # COPY: Disclose bounded scope assumptions without claiming a skill improved the outcome.
            wf.missions.record_event(result["mission_id"], "message", actor="code", detail={
                "from": "readiness", "to": "coding",
                "text": "G6 checks whether it has enough information to proceed. Assumptions: "
                        + assessed.understanding["forbidden_effects"],
                "refs": {"readiness": {"decision": assessed.decision, "skills_consulted": list(assessed.skills_consulted),
                    "model_calls": 0, "elapsed_ms": elapsed_ms, "understanding": assessed.understanding,
                    "not_asked": list(assessed.not_asked), "version": assessed.version}}}, idem_key="coding:readiness")
        return result


def _executor(wf, repository: dict) -> None:
    if "executor" in repository:
        raise ValueError(NO_EXECUTOR)


def _prepare(wf, *, source, baseline, objective, repository, declared_risk, budget_cents,
             mission_id=None, clarification=None):
    _executor(wf, repository)
    initial_digest = bench._digest(source)
    tests = []
    for path in sorted((source / "tests").rglob("test_*.py")):
        tests += [f"{path.relative_to(source).as_posix()}::{n.name}"
                  for n in ast.parse(path.read_text(encoding="utf-8")).body
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    # ECHA Stage A: pytest says which tests can be counted before the intake reads the success criteria
    with confine.run_folder(prefix="suite-", dir=source.parent) as trial:   # G6's own, so a confined run may use it
        probe = Path(trial) / "repo"  # a throwaway copy: what the suite writes must not become the frozen baseline (I-2)
        shutil.copytree(source, probe, ignore=lambda folder, names: {n for n in names if bench._linked(Path(folder) / n)})
        found = truth.run_suite(probe)
    if bench._digest(source) != initial_digest:
        raise ValueError("The retained coding source changed during repository execution; no standard was accepted.")
    tests, suite = _counted(_with_class_tests(tests, found), found)
    if not tests:
        raise ValueError(REFUSAL)
    intent_answers = {"why": objective, "success_criteria": tests, "failure_criteria": ["frozen_checks_failed"],
                      "non_goals": ["test_edits"], "constraints": ["frozen_scope_tests_permissions_budget_attempts"],
                      "risk_tolerance": "medium"}
    intake = run_intake(SimpleNamespace(goal=objective, intent_answers=intent_answers,
                                       context_sources=[repository.get("module") or repository["repo"]]),
                        llm=None, clarification=True)
    feasibility = intake.metadata.get("feasibility_report")
    risky = (intake.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"
             and feasibility is not None and feasibility.classification == "risky")
    access = access_terms(intake)   # F-2: a password or credential fix takes the risky route below
    if (feasibility is not None and feasibility.classification.startswith("blocked_") and not access) or (
            intake.state == StageState.BLOCK and not risky and not access) or (names_access(intake) and not access):
        raise ValueError(REFUSAL)
    return _freeze(wf, source=source, tests=tests, baseline=baseline, intake=intake, objective=objective,
                   repository=repository, declared_risk=declared_risk, budget_cents=budget_cents, suite=suite,
                   passed_tests=tuple(n for n, result in found["outcomes"].items() if result == "passed"),
                   mission_id=mission_id, clarification=clarification, expected_digest=initial_digest)


def _freeze(wf, *, source, tests, baseline, intake, objective, repository, declared_risk, budget_cents, suite, passed_tests,
            mission_id=None, clarification=None, expected_digest=None):
    before = bench._digest(source)
    if expected_digest is not None and before != expected_digest:
        raise ValueError("The retained coding source changed before freezing; no standard was accepted.")
    if "formal" in repository:
        raise ValueError(NO_FORMAL)
    if repository.get("module") and repository.get("function"):
        scope = {"derived": False, "method": "named", "selected": {
            "module": str(bench._inside(repository["module"])), "function": str(repository["function"])},
            "candidates": [], "failing": [], "receipts": []}
    else:
        scope = derive_scope(source, tests, wf.venture_id, baseline=baseline)
        if any(repository.get(key) and repository[key] != scope["selected"][key] for key in ("module", "function")):
            raise ValueError(REFUSAL)
    module, function = scope["selected"]["module"], scope["selected"]["function"]
    bench._files({module: ""})
    if not module.endswith(".py") or not function:
        raise ValueError(REFUSAL)
    body = ast.parse((source / module).read_text(encoding="utf-8"))
    for part in function.split("."):
        body = next((n for n in getattr(body, "body", []) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == part), None)
    if not isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)) or bench._digest(source) != before:
        raise ValueError(REFUSAL)
    declared = _declared(source, module, function, repository.get("helpers"))
    original_tests = list(tests)
    construction = None
    if (not (clarification or {}).get("cannot_check") and not (clarification or {}).get("not_applicable")
            and isinstance(body, ast.FunctionDef) and len(body.args.posonlyargs + body.args.args) == 2
            and not body.args.vararg and not body.args.kwarg and not body.args.kwonlyargs):
        construction = construct_replay_check(
            objective, module=module, function=function,
            test_sources={rel: (source / rel).read_text(encoding="utf-8")
                          for rel in sorted({t.split("::")[0] for t in tests})}, executed_tests=passed_tests,
            clarified_requirement=(clarification or {}).get("requirement", ""),
            **({"relation": clarification["relation"]} if clarification and "relation" in clarification else {}))
    if clarification and not clarification.get("cannot_check") and not clarification.get("not_applicable") and not construction:
        if mission_id:
            # COPY: A required relation cannot be silently dropped when its example is unsupported.
            wf.missions.record_unmet(mission_id, kind="cannot_check", subject=clarification["requirement"],
                                     why="No supported executed example and scope for the replay constructor.")
        raise ValueError("Cannot construct the required replay check for these materials; no coding standard was accepted.")
    if construction:
        # Additive, task-derived obligations are frozen BEFORE the confirmation.
        # The owner's repository and original fixed checks remain untouched.
        for check in construction["checks"]:
            path = source / check["nodeid"].split("::")[0]
            if path.exists():
                raise ValueError(REFUSAL)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(check["source"].encode("utf-8"))
            tests = [*tests, check["nodeid"]]
    intent = intake.metadata["task_contract"].intent
    intent = intent.model_copy(update={"checkpoints": [
        CheckpointSpec(name=node, predicate="tests_passed", params={"unit": "patch"}) for node in tests]})
    consequence = intake.metadata["feasibility_report"]
    floor = "R3" if consequence.classification == "risky" else "R2"
    access = access_terms(intake)
    floor = "R3" if access else floor   # F-2: a password or credential fix is high-consequence
    tier = max(declared_risk, floor)
    identity = _sha([wf.missions.owner, wf.venture_id, repository["repo"]])
    signature = {"version": 1, "repository_identity": identity, "task_kind": "code_change",
                 "module": module, "function": function}
    signature["sha256"] = _sha(signature)
    mid = mission_id or wf.missions.create(wf.venture_id, objective=objective, risk_tier=tier, budget_cents=budget_cents,
                             actor="code", contract=intent, task_sha256=signature["sha256"], template="code_change",
                             mode=repository.get("mode"))
    snapshot_name = f"coding-{mid}"
    snapshot = source.parent / snapshot_name
    _snapshot(source, snapshot)
    standard = {"version": 1, "repository": repository["repo"], "snapshot": snapshot_name,
                "baseline_revision": baseline, "baseline_digest": bench._digest(snapshot),
                "required_tests": tests, "test_provenance": "caller", "scope": scope, "suite": suite,
                "test_digests": {rel: hashlib.sha256((snapshot / rel).read_bytes()).hexdigest()
                                 for rel in sorted({t.split("::")[0] for t in tests})},
                "permissions": {"files": [module], "test_edits": False, "network": False},
                "budget_cents": budget_cents, "max_attempts": MAX_ATTEMPTS, "strategies": list(LADDER),
                "task_signature": signature, "declared": declared,
                "derivation": {"policy_version": POLICY_VERSION, "declared_risk": declared_risk,
                               "floor": floor, "effective_risk": tier},
                "intake": {"state": intake.state.value, "model_calls": 0,
                           "components_used": list(intake.metadata["components_used"])}}
    # audit 010 WF-015: whether the premortem's fixed templates applied, said in every standard
    standard["premortem"] = {**constructor(), "applied": bool(construction),
                             **({} if construction else {"said": NOT_CONSTRUCTED})}
    if construction:  # ``harness_intelligence``: the archive's key, kept for compatibility; a fixed template (WF-015)
        standard.update(harness_intelligence=construction, original_baseline_digest=before,
                        original_required_tests=original_tests, test_provenance="caller_and_task_derived")
    if clarification:
        standard["intake_clarification"] = clarification
        if clarification.get("cannot_check"):
            standard["cannot_check"] = {"requirement": clarification["requirement"], "why": clarification["cannot_check"]}
            wf.missions.record_unmet(mid, kind="cannot_check", subject=clarification["requirement"],
                                     why=clarification["cannot_check"])
    if access:  # before the risky record: a password or credential fix is risky to feasibility too (FEA-002)
        standard["derivation"]["consequence"] = {"source": "feasibility", "signals": access}
    elif consequence.classification == "risky":
        standard["derivation"]["consequence"] = {"source": "feasibility", "signals": {
            "classification": consequence.classification, "risk_level": consequence.risk_level,
            "recommended_next_step": consequence.recommended_next_step}}
    # E10b ruling E10b-R1: the derivation above is this template's standard; it is kept as given, not derived again
    compiled = acceptance.compile_contract(objective=objective, contract=intent, risk_tier=tier,
                                           budget_cents=budget_cents, template_derived=True)
    compiled = replace(compiled, metadata={**compiled.metadata, "coding": standard})
    wf.missions.accept(mid, compiled, actor="code", contract=intent)
    if wf.readiness and clarification and not clarification.get("not_applicable"):
        # COPY: This records accepted scope, not a claim that any check ran or any code was fixed.
        wf.missions.record_event(mid, "message", actor="code", detail={
            "from": "readiness", "to": "coding", "text": "The accepted interpretation and check scope are recorded.",
            "refs": {"readiness": {
                "acceptance_sha256": wf.missions.acceptance(mid).sha256,
                "understanding": readiness.assess(objective, repository).understanding,
                "interpretation": {"key": clarification["answer"]["replay_semantics"],
                    "source": "explicit_in_objective" if clarification["answer_actor"] == "objective"
                    else f"{clarification['answer_actor']}_answer"},   # "chat_answer": the chat's, never the user's
                "requirement": clarification["requirement"],
                "checks": {node: standard["test_digests"][node.split("::")[0]] for node in standard["required_tests"]},
                "guarantees": {node: (clarification["requirement"] if node not in standard.get("original_required_tests", standard["required_tests"]) else node)
                               for node in standard["required_tests"]}}}}, idem_key="coding:readiness-context")
    if construction:
        wf.missions.record_event(mid, "message", actor="code", detail={
            "from": "premortem", "to": "harness", "text": "Constructed an additive, nonindependent replay check.",
            "refs": {"harness_construction": {
                "standard_sha256": wf.missions.acceptance(mid).sha256, "invocation": construction["invocation"],
                "task_sha256": construction["task_sha256"],
                "checks": [{k: check[k] for k in ("predicate_id", "failure_id", "nodeid", "source_sha256")}
                           for check in construction["checks"]]}}}, idem_key="coding:harness-construction")
    from g6.workforce.objective import preflight

    preflight(wf, mid, "code_change")
    return advance(wf, mid)


def _reproduction(wf, mission_id: str) -> dict:
    for message in wf.missions.messages(mission_id, to="patch"):
        if "reproduction" in message["refs"]:
            return message["refs"]["reproduction"]
    return {}


def unrouted(found: dict) -> dict:
    """A reproduction (or a derived scope) as a status or a worker's brief shows it: each receipt without the route it
    names (``op``, lead ruling L-7). The mission's rows keep it for credit; a worker's brief names no capability."""
    if not (isinstance(found, dict) and found):
        return found
    plain = {**found, "receipts": [{k: v for k, v in r.items() if k != "op"} for r in found.get("receipts") or []]}
    if isinstance(found.get("unexecuted"), dict):
        plain["unexecuted"] = {k: v for k, v in found["unexecuted"].items() if k != "op"}
    return plain


def advance(wf, mission_id: str) -> dict:
    from g6.workforce import objective, service

    standard = frozen(wf, mission_id)
    if not standard or not wf.missions.confirmed(mission_id):
        return service.status(wf, mission_id)
    if standard["derivation"].get("consequence") and not any(
            m["refs"].get("accepted_risky_intent") for m in wf.missions.messages(mission_id)):
        intent = wf.missions.contract(mission_id)
        intake = run_intake(SimpleNamespace(goal=wf.missions.get(mission_id)["objective"],
            intent_answers={"why": intent.why, "success_criteria": standard["required_tests"],
                            "failure_criteria": ["frozen_checks_failed"], "non_goals": ["test_edits"],
                            "constraints": ["frozen_scope_tests_permissions_budget_attempts"],
                            "risk_tolerance": "medium"}, context_sources=[standard["repository"]],
            accepted_risky_intent=True), llm=None, clarification=True)
        # F-2: a password or credential fix proceeds only as the same terms frozen at entry, after the person's
        # confirmation, whatever state the re-intake ends in; any other route only if the re-intake does not block
        accepted, frozen_signals = access_terms(intake), standard["derivation"]["consequence"]["signals"]
        if isinstance(frozen_signals, list):
            allowed = bool(accepted) and accepted == frozen_signals and wf.missions.confirmed(mission_id)
        else:
            allowed = intake.state != StageState.BLOCK and not names_access(intake)
        if not allowed:
            raise ValueError(REFUSAL)
        wf.missions.record_event(mission_id, "message", actor="code", detail={
            "from": "code", "to": "intake", "text": "", "refs": {
                "accepted_risky_intent": True, "state": intake.state.value,
                "confirmation": wf.runtime.decisions.interventions(mission_id)[0].id}},
            idem_key="coding:risky-confirmed")
    advanced_at = time.perf_counter()
    snapshot = bench._repo(standard["snapshot"], wf.venture_id)
    if bench._digest(snapshot) != standard["baseline_digest"]:
        raise ValueError(REFUSAL)
    if not _reproduction(wf, mission_id):
        scope = standard.get("scope", {})
        decision = objective.preflight(wf, mission_id, "code_change")["answers"][6]["answer"]["reproduce"]
        op = decision["selected_option"]["required_capabilities"][0][1]
        # L-7: derive_scope ran the copy route, so its receipts stand only for a decision that chose that route
        if (not reuse_reference(wf.missions, mission_id) and scope.get("derived")
                and scope.get("baseline_digest") == bench._digest(snapshot) and op == "run_tests"):
            reproduction = {"reproduced": True, "tests": scope["results"], "receipts": scope["receipts"]}
        else:
            known = [d for d in reconfigure.changes(wf.missions, mission_id)
                     if d["stage"] == "reproduce" and d["trigger"] == "tool_unavailable"]
            if known:  # review M-4: a route this mission found unavailable is never run again
                if not known[-1]["changed"]["to"]:
                    return _stop(wf, mission_id, reconfigure.NO_CHECK)
                op = known[-1]["changed"]["to"].split(".", 1)[1]
            for _ in objective.STAGES["code_change"]["reproduce"]:  # E2E Build 6: each route is tried at most once
                if op == bench.IN_PLACE and not objective.in_place_eligible(wf, standard):
                    raise ValueError(REFUSAL)
                source = bench._repo(standard["repository"], wf.venture_id) if op == bench.IN_PLACE else snapshot
                reproduction = _reproduce(source, standard["required_tests"], wf.venture_id,
                                          baseline=standard["baseline_revision"], op=op)
                if "unexecuted" not in reproduction:
                    break
                op, why = reconfigure.reroute(wf, mission_id, "reproduce", failed=op, standard=standard,
                                              observation=reproduction["unexecuted"])
                if why:
                    return _stop(wf, mission_id, why)
                if not op:  # the route answers its probe: a test that did not run is not a route to change
                    raise ValueError(REFUSAL)
        wf.missions.record_event(mission_id, "message", actor="code",
                                  detail={"from": "code", "to": "patch", "text": OBJECTIVE_INSTRUCTIONS,
                                          "refs": {"reproduction": reproduction}}, idem_key="coding:reproduction")
    reproduced = _reproduction(wf, mission_id)
    # Q2: a disclosed missing checker need not invent a failing baseline to start bounded patch work.
    # Every existing check must still have executed; retain reproduced=False when they all passed.
    checked_baseline = (standard.get("cannot_check") and reproduced.get("tests")
                        and len(reproduced.get("receipts", [])) == len(standard["required_tests"])
                        and all(r["executed"] for r in reproduced["receipts"])
                        and set(reproduced["tests"]) == set(standard["required_tests"])
                        and all(reproduced["tests"].values()))
    if not reproduced["reproduced"] and not checked_baseline:
        raise ValueError(REFUSAL)
    if not wf.missions.get(mission_id)["plan_revision"]:
        plan = {"units": [
            {"key": "patch", "kind": "do", "goal": wf.missions.get(mission_id)["objective"],
             "output_schema": "files", "risk": standard["derivation"]["effective_risk"],
             "budget_cents": standard["budget_cents"], "max_attempts": standard["max_attempts"]},
            {"key": "check-patch", "kind": "verify", "verifies": "patch", "goal": STANDARD_EXPLANATION,
             "max_attempts": 1, "capabilities": [["workforce", "run_tests"]],
             "oracle": {"capability": ["workforce", "run_tests"],
                        "params": {"repo": standard["snapshot"], "tests": standard["required_tests"]},
                        "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True},
                        "kind": "test"}},
            {"key": "check-regression", "kind": "verify", "verifies": "patch", "goal": STANDARD_EXPLANATION,
             "max_attempts": 1, "capabilities": [["workforce", "run_tests"]],
             "oracle": {"capability": ["workforce", "run_tests"],
                        "params": {"repo": standard["snapshot"], "tests": standard["required_tests"],
                                   "regression": {**standard["task_signature"], "declared": standard.get("declared", [])}},
                        "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True},
                        "kind": "deterministic"}}], "edges": []}
        if standard["derivation"]["effective_risk"] == "R3":
            plan["edges"] = [[u["key"], "approve-patch"] for u in plan["units"] if u["kind"] == "verify"]
            # COPY: A high-consequence patch needs a person's approval after its checks.
            plan["units"].append({"key": "approve-patch", "kind": "gate", "inputs": ["patch"],
                                  "goal": "Human action required: approve the checked patch."})
        why = _unchecked(wf, mission_id, plan)
        if why:  # no worker is asked for a change nothing here can check (design R-2: no weaker check)
            return _stop(wf, mission_id, why)
        if wf.runtime.certificates:  # RVC R6.3: the baseline controls, after the freeze and before any patch exists
            from g6.workforce import coding_certificate

            coding_certificate.record_controls(wf, mission_id, standard)
        service.plan(wf, mission_id, plan, actor="code")
    service.run(wf, mission_id)
    wf.missions.record_event(mission_id, "message", actor="code", detail={
        "from": "code", "to": "learning", "text": "", "refs": {"coding_timing": {
            "seconds": time.perf_counter() - advanced_at, "cost": None}}})
    return service.status(wf, mission_id)


def _stop(wf, mission_id: str, why: str) -> dict:
    """Stop for a person: no route left that keeps the frozen standard (the mission ends blocked-escalated)."""
    from g6.workforce import service

    wf.missions.cancel(mission_id, reason=why, actor="code")
    return service.status(wf, mission_id)


def _unchecked(wf, mission_id: str, plan: dict) -> str:
    """Each check of ``plan`` whose route this mission found unavailable: a "no eligible alternative" decision (a check
    stage has one census-read route, design R-2); the words for the person, or "" when every check can run."""
    found = {d["changed"]["from"]: d for d in reconfigure.changes(wf.missions, mission_id)
             if d["trigger"] == "tool_unavailable"}
    why = ""
    for unit in plan["units"]:
        route = next((".".join(c) for c in unit.get("capabilities", []) if ".".join(c) in found), "")
        if unit["kind"] == "verify" and route:
            why = reconfigure.no_check(wf.missions, mission_id, [unit["key"]], route=tuple(route.split(".", 1)),
                                       observation={"probe_id": found[route]["observation"]["probe_id"]},
                                       why=found[route]["inadequate_because"])
    return why


def check_submission(standard: dict, artifact: dict) -> None:
    """All patch producers obey the same frozen scope; this never executes their files."""
    if (not isinstance(artifact, dict) or set(artifact) != {"files"}
            or not isinstance(artifact["files"], dict) or not artifact["files"]):
        raise ValueError("The patch must contain a nonempty files object under the frozen coding standard.")
    files = bench._files(artifact["files"])
    if not set(files) <= set(standard["permissions"]["files"]):
        raise ValueError("The patch must contain only source files in the frozen scope; tests cannot be changed.")


def repair_turn(missions, ledger, mission_id: str, producer) -> dict:
    """The repair ladder after the latest proposal failed or repeated (E2E Build 6, test I; review M-2, M-3, I-3):
    {"stop": words} to stop (a stop for a person, or a refused change, is recorded now); else {"strategy": the one the
    send-back uses, "change": the decision to record once the send-back went out (None when nothing changes)}."""

    standard = missions.acceptance(mission_id).metadata["coding"]
    artifacts = [(e["evidence_id"], e["detail"]) for e in missions.events(mission_id, kind="artifact")
                 if e["detail"]["unit"] == producer.key]
    verdicts = ledger.verdicts(mission_id, producer.key)
    checks = {u.key for u in missions.plan(mission_id)[1].units if u.verifies == producer.key}

    def score(artifact_id):
        return len({v["unit_key"] for v in verdicts if v["artifact_id"] == artifact_id
                    and v["executed"] and v["result"] == "pass"} & checks) / len(checks)

    judged = [a for a, _ in artifacts[:-1] if any(v["artifact_id"] == a for v in verdicts)]  # a repeat is not judged
    repeats = next((a for a, found in artifacts[:-1] if found["sha256"] == artifacts[-1][1]["sha256"]), "")
    tried = ledger.attempts(mission_id, producer.key)  # review M-10: an attempt that left no proposal still counts
    key = retry_stop(attempts=tried, limit=standard["max_attempts"], score=score(artifacts[-1][0]),
                     failure=True, previous_score=score(judged[-1]) if judged else None, repeated=bool(repeats))
    ladder = standard.get("strategies")
    if not ladder or key not in TRIGGER:
        stop = REPAIR_STOP.get(key, "")
        return {"stop": stop} if stop else {"strategy": reconfigure.strategy(missions, mission_id, ladder or LADDER)}
    latest, sha = artifacts[-1][0], artifacts[-1][1]["sha256"]
    done = [d for d in reconfigure.decided(missions, mission_id) if d["observation"].get("artifact_id") == latest]
    if done:  # a re-settle: the decision recorded for this proposal stands
        if done[0].get("refused"):
            return {"stop": reconfigure.REFUSED_STOP}
        to = done[0]["changed"]["to"]
        return {"stop": PERSON.format(why=REPAIR_STOP[key])} if to == ladder[-1] else {"strategy": to}
    current = reconfigure.strategy(missions, mission_id, ladder)
    following = ladder[min(ladder.index(current) + 1, len(ladder) - 1)]
    if key == "attempt_limit" or tried >= producer.max_attempts:
        following = ladder[-1]
    change = {"mission_id": mission_id, "stage": producer.key, "unit": producer.key, "trigger": TRIGGER[key],
              "observation": {"artifact_id": latest, "sha256": sha, "repeats": repeats, "verdicts": [
                  v["verdict_id"] for v in verdicts if v["artifact_id"] == latest and v["result"] == "fail"]},
              "inadequate_because": CAUSE[key], "changed": {"from": current, "to": following},
              "invariants_before": reconfigure.frozen(missions, mission_id),
              "invariants_after": reconfigure.in_force(missions, mission_id, max_attempts=producer.max_attempts)}
    idem = f"{producer.key}:{latest}"
    if change["invariants_before"] != change["invariants_after"]:  # refused: recorded, never raised (fails closed)
        reconfigure.decide(missions, change, idem=idem)
        return {"stop": reconfigure.REFUSED_STOP}
    if following == ladder[-1]:  # a stop for a person is final: recorded now
        reconfigure.decide(missions, change, idem=idem)
        return {"stop": PERSON.format(why=REPAIR_STOP[key])}
    return {"strategy": following, "change": change, "idem": idem}


def repair_stop(missions, ledger, mission_id: str, producer) -> str:
    """The ladder's stop words, or "" to go on; a change that goes on is recorded at once (for a caller with no veto)."""
    turn = repair_turn(missions, ledger, mission_id, producer)
    if turn.get("change"):
        reconfigure.decide(missions, turn["change"], idem=turn["idem"])
    return turn.get("stop", "")


def repeated(missions, ledger, mission_id: str, spec, row: dict) -> tuple[str, str] | None:
    """A proposal that repeats an earlier one byte for byte is recorded and uses an attempt, but is never judged again
    (E2E Build 6, test I): the ladder moves (the unit goes back to the worker with a focused brief) or stops for a
    person (the unit is held). None for any other settle."""
    standard = missions.acceptance(mission_id).metadata["coding"]
    if not standard.get("strategies") or row["state"] != "closed" or not row["artifact_id"]:
        return None
    shas = [(e["evidence_id"], e["detail"]["sha256"]) for e in missions.events(mission_id, kind="artifact")
            if e["detail"]["unit"] == spec.key]
    earlier = next((a for a, sha in shas[:-1] if sha == shas[-1][1]), "")
    if not earlier:
        return None
    stop = repair_stop(missions, ledger, mission_id, spec)
    if stop:
        missions.record_event(mission_id, "message", actor="code", detail={
            "from": "code", "to": spec.key, "text": stop,
            "refs": {"repair_stop": True, "run_id": row["run_id"], "artifact_id": row["artifact_id"]}},
            idem_key=f"repair-stop:{row['artifact_id']}")
        return "escalate", stop
    brief = focused(missions, ledger, mission_id, spec.key, earlier) or {"text": ASKED_AGAIN, "refs": {}}
    missions.record_event(mission_id, "message", actor="code", detail={
        "from": "code", "to": spec.key, "text": brief["text"],
        "refs": {**brief["refs"], "run_id": row["run_id"], "artifact_id": row["artifact_id"]}},
        idem_key=f"focused:{row['artifact_id']}")
    return "fail", ASKED_AGAIN


def focused(missions, ledger, mission_id: str, unit: str, artifact_id: str, *, strategy: str | None = None) -> dict | None:
    """The focused brief once the strategy (``strategy``, else the one recorded) is ``focused`` (None before): only the
    failing checks' evidence on the rejected proposal ``artifact_id`` (each failing test with its assertions, or the
    counterexample), that proposal's digest and difference from the baseline, and the rule that a repeat is not judged
    again."""
    standard = missions.acceptance(mission_id).metadata["coding"]
    if not standard.get("strategies") or (strategy or reconfigure.strategy(
            missions, mission_id, standard["strategies"])) != "focused":
        return None
    snapshot = bench._repo(standard["snapshot"], missions.get(mission_id)["venture_id"])
    failing = []
    for verdict in ledger.verdicts(mission_id, unit):
        if verdict["artifact_id"] != artifact_id or verdict["result"] != "fail" or not verdict["executed"]:
            continue
        reasons = json.loads(verdict["reasons_json"] or "[]")
        try:
            answered = json.loads(next((r[len(_ANSWERED):] for r in reasons if r.startswith(_ANSWERED)), "{}"))
        except ValueError:  # cut short in the record: the check's own words stand instead
            answered = {}
        # Partial checks have full observations in the existing run receipt; the
        # legacy displayed JSON may be truncated. Bind all four identities before
        # reading them and retain missing obligations beside the observed failure.
        partials = [record for event in missions.events(mission_id, kind="run.closed")
                    if event["detail"].get("run_id") == verdict["run_id"]
                    for record in event["detail"].get("verification", [])
                    if record.get("check") == verdict["unit_key"] and record.get("artifact_id") == artifact_id
                    and record.get("artifact_sha") == verdict["artifact_sha"] and record.get("interruption")]
        if partials:
            answered = partials[0]
        item = {"check": verdict["unit_key"]}
        tests = sorted(node for node, ok in (answered.get("tests") or {}).items() if ok is False)
        suite = answered.get("suite") if isinstance(answered.get("suite"), dict) else {}  # ECHA Stage A: whole-suite breaks
        tests = sorted({*tests, *(t for t in suite.get("tests") or [] if isinstance(t, str))})
        if tests:
            item["tests"] = [{"test": node, "assertions": _assertions(snapshot, node)} for node in tests]
        if answered.get("counterexamples"):
            item["counterexamples"] = answered["counterexamples"]
        if answered.get("interruption"):
            item.update({key: answered[key] for key in ("interruption", "not_run", "suite", "causes") if key in answered})
        if len(item) == 1:
            item["reason"] = "; ".join(reasons)[:600]
        failing.append(item)
    rejected = ledger.artifact(mission_id, artifact_id)
    diff = []
    for path, text in sorted(rejected["content"]["files"].items()):
        base = (snapshot / path).read_text(encoding="utf-8") if (snapshot / path).is_file() else ""
        lines = [line for line in difflib.unified_diff(base.splitlines(), text.splitlines(), lineterm="", n=0)
                 if line[:1] in "+-" and not line.startswith(("+++", "---"))]
        diff.append({"path": path, "added": sum(line.startswith("+") for line in lines),
                     "removed": sum(line.startswith("-") for line in lines), "lines": lines[:40]})
    return {"text": FOCUSED, "refs": {"strategy": "focused", "focused": {
        "failing": failing, "rejected": {"artifact_id": artifact_id, "sha256": rejected["sha256"], "diff": diff},
        "rule": REPEAT_RULE}}}


def _assertions(snapshot, node: str) -> list[str]:
    """A test's assert statements, as written. ``node`` is a pytest node id: ``file::test``, ``file::Class::test`` (to any
    depth) or either with a parametrize suffix (``test[1-3]``); a test that is not there reads as no assertions."""
    relative, *path = node.split("::")
    if not path:
        return []
    try:
        source = (snapshot / relative).read_text(encoding="utf-8")
        body = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return []
    path[-1] = path[-1].split("[", 1)[0]
    for part in path[:-1]:
        body = next((n for n in getattr(body, "body", []) if isinstance(n, ast.ClassDef) and n.name == part), None)
    test = next((n for n in getattr(body, "body", []) if isinstance(n, ast.FunctionDef) and n.name == path[-1]), None)
    return [ast.get_source_segment(source, n) for n in ast.walk(test) if isinstance(n, ast.Assert)] if test else []


def regression_replays(missions, mission_id: str) -> list[dict]:
    inputs = {}
    for event in missions.events(mission_id, kind="run.closed"):
        for check in event["detail"].get("verification", []):
            if check["check"] != "check-regression":
                continue
            for function in check.get("probe", {}).get("functions", []):
                if function["status"] in ("pass", "fail"):
                    inputs.setdefault((function["module"], function["function"]), set()).update(function["inputs"])
    return [{"module": module, "function": function, "inputs": sorted(values)}
            for (module, function), values in sorted(inputs.items())]


def repaired(wf, mission_id: str) -> bool:
    if not frozen(wf, mission_id):
        return False
    latest = wf.ledger.last_artifact(mission_id, "patch")
    checks = status(wf, mission_id, frozen(wf, mission_id))["checks"]
    return bool(latest and any(c.get("probe", {}).get("counterexamples") and c["result"] == "fail"
                              and c["artifact_id"] != latest["artifact_id"] for c in checks)
                and {c["check"] for c in checks if c["artifact_id"] == latest["artifact_id"]
                     and c["executed"] and c["result"] == "pass" and not Outcome.from_record(c.get("outcome")).degraded}
                == {u.key for u in wf.missions.plan(mission_id)[1].units if u.verifies == "patch"})


def status(wf, mission_id: str, standard: dict, *, include_pack: bool = False) -> dict:
    checks = [{**record, "run_id": event["detail"]["run_id"]}
              for event in wf.missions.events(mission_id, kind="run.closed")
              for record in event["detail"].get("verification", [])]
    stop = next((m["text"] for m in reversed(wf.missions.messages(mission_id)) if m["refs"].get("repair_stop")), "")
    if standard.get("scope"):
        standard = {**standard, "scope": unrouted(standard["scope"])}
    inherited = reuse_reference(wf.missions, mission_id)
    return {"standard": standard, "intake": {**standard["intake"],
            **({"source_mission_id": inherited["construction_mission_id"]} if inherited else {})},
            **({"harness_pack": harness_pack(wf, mission_id, standard, checks)}
               if include_pack and standard.get("harness_intelligence") else {}),
            **({"readiness": asdict(readiness.state(wf.missions, mission_id))} if wf.readiness and include_pack else {}),
            "reproduction": unrouted(_reproduction(wf, mission_id)), "checks": checks, "stop_reason": stop}


def harness_pack(wf, mission_id: str, standard: dict, checks: list[dict]) -> dict:
    """Owner-scoped projection of existing records, never a new verdict or store.

    The pack contains customer source and is for this mission's authorised reader,
    not general telemetry/learning export. Reuse references local owned records;
    arbitrary exported source and historical success claims are never imported.
    """
    from g6.workforce.missions import FINISHED

    accepted = wf.missions.acceptance(mission_id)
    mission = wf.missions.get(mission_id)
    artifact = wf.ledger.last_artifact(mission_id, "patch")
    construction = standard["harness_intelligence"]
    closed = wf.missions.events(mission_id, kind="run.closed")
    certified = next((e["detail"]["certificate"] for e in wf.missions.events(mission_id, kind="mission.closed")
                      if "certificate" in e["detail"]), None)  # RVC R6.5: kept on the close when certificates were on
    cost = wf.ledger.cost_total(mission_id)
    return {
        "artifact": artifact,  # Work product before supporting machinery.
        "version": 1, "mission_id": mission_id,
        "owner_scope": {"owner": wf.missions.owner, "venture_id": mission["venture_id"]},
        "standard": {"sha256": accepted.sha256, "revision": accepted.revision,
                     "objective": mission["objective"], "contract": accepted.as_dict()},
        "premortem": construction["hypotheses"], "construction": construction,
        "construction_receipts": [e for e in wf.missions.events(mission_id, kind="message")
                                  if e["detail"].get("refs", {}).get("harness_construction")],
        "reuse_provenance": reuse_reference(wf.missions, mission_id),
        "selection": wf.missions.events(mission_id, kind="preflight"),
        "execution": {
            "scope": ("pending_artifact" if artifact is None else "recorded_executor_call"
                      if artifact.get("provenance", {}).get("call") else "externally_supplied_artifact"),
            "generation_enforcement": "Only recorded G6 operations are observed; no claim over other producer actions.",
            "reproduction": _reproduction(wf, mission_id), "checks": checks,
            "runs": closed, "verdicts": wf.ledger.verdicts(mission_id),
            "cost": {**asdict(cost), "amount_minor_units": cost.amount_minor_units if cost.entries else None,
                     "scope": "Recorded execution rows only; energy, hardware and unobserved work remain unpriced."}},
        "assurance": {
            **({"readiness": asdict(readiness.state(wf.missions, mission_id))} if wf.readiness else {}),
            "completion_state": Outcome.from_record(mission["outcome"]).state if mission["status"] in FINISHED else "",
            "mission_status": mission["status"], "outcome": mission["outcome"],
            **({"certificate": certified} if certified is not None else {}),
            "independence": "nonindependent", "residual_uncertainty": [
                *construction["limits"],
                "Trusted same-user test process, not an OS sandbox or protected evaluator.",
                "Fixed guards may report not_applicable; read their actual receipts.",
                "Native-agent superiority, population effectiveness and fully costed value are unmeasured."],
            "next_action": "Inspect scoped checks and residual risks before using the artifact."},
        "reuse": {"tool": "mission_create", "version": 1,
                  "arguments": {"objective": mission["objective"], "risk_tier": mission["risk_tier"],
                                "budget_cents": mission["budget_cents"], "repo_json": json.dumps({"harness_pack": {
                                    "version": 1, "mission_id": mission_id, "standard_sha256": accepted.sha256,
                                    "reuse_id": "next-change"}}, sort_keys=True)},
                  "standard_sha256": accepted.sha256, "required_tests": standard["required_tests"],
                  "rule": "After completion, create a fresh mission with this reference; confirm it and submit new allowed source files. The same source mission and reuse_id return the same mission; choose another reuse_id for another change.",
                  "limit": "Same owner, venture and original frozen baseline only; no implicit rebase, cross-install import or transfer of approval or passing evidence."},
        "learning": {"promotion": "none", "record": "Existing task-local runs preserve failures and later checks.",
                     "effectiveness": "Unmeasured beyond these observations; an invocation is not a causal comparison."},
    }
