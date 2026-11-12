"""Frozen db65ca1 reading; stdlib and sibling fixtures only.

Copied source; receipt version/executable/digest metadata use fixed test adapters.
"""
from __future__ import annotations

import re
import time
from collections.abc import Collection, Sequence
from types import SimpleNamespace as Requirement
from . import repo_tests
ISOLATION_UNAVAILABLE = "G6_E_ISOLATION_UNAVAILABLE"
_execute = None  # tests inject transport of reports made by the real producer


PASSED_ON_CHECKS, FAILED, NOT_ESTABLISHED = "passed_on_stated_checks", "failed", "not_established"


NOT_READ = "not_read"


ERROR = "error"


UNTRUSTED = frozenset({repo_tests.CHANGED, "inconsistent_report", "incomplete"})


NOT_COMPARED = "derived_test_not_compared"


DEFECTS = frozenset({repo_tests.FAILED})


ENVIRONMENT = "could not be collected in this environment"


_COLLECTING = re.compile(r"ERROR (?:collecting )?([^\s:]+\.py)(?=\s|$)", re.MULTILINE)


BOUNDARY_HELP = ("Allowing unconfined runs (g6 setup or G6_ALLOW_UNCONFINED) does not change this: a patch review "
                 "uses only confined runs, the only ones that get test receipts. To get a boundary, install bubblewrap "
                 "on Linux, or on Windows make the one-time access grant g6 doctor prints; macOS has none yet.")


STATED, DERIVED, STRUCTURAL = "stated", "derived", "structural"


NOT_LOADED = "changed_code_not_loaded"


class _Refused(repo_tests.RepoRefused):
    """A refusal of the review's own inputs; ``help``, when set, is what the refusal's warning card suggests doing (for
    a template instance's bindings, the template's bindings and one valid instance: worked examples, W4)."""
    help = ""


def _rooted(roots: tuple[str, ...]) -> dict:
    """The keyword that passes the review's ``import_roots`` to every run, when it names any."""
    return {"roots": roots} if roots else {}


def _confined(ran: dict) -> dict:
    """``ran``, or a refusal when no operating-system boundary confined it: its results are never assessed."""
    isolation = ran.get("isolation") or {}
    if "isolation" in ran and not isolation.get("backend"):
        raise _Refused(ISOLATION_UNAVAILABLE, "G6 assesses a patch only from test runs inside an operating-system "
                       f"boundary, and none confined this one ({isolation.get('why') or 'no backend'}), so its results "
                       f"are not used. {BOUNDARY_HELP} Run g6 doctor: it says what this computer needs.")
    return ran


def _collection_errors(output: str) -> set[str]:
    """The files pytest said it could not collect (it names them relative to its root folder)."""
    return set(_COLLECTING.findall(output.replace("\\", "/")))


def _names(check: str, files: set[str]) -> bool:
    head = check.partition("::")[0].rstrip("/")
    return any(path == head or path.startswith(head + "/") for path in files)


def _covers(check: str, node: str) -> bool:
    check = check.rstrip("/")
    return node == check or node.startswith((check + "::", check + "[", check + "/"))


LISTINGS = {"test_outcomes": dict, "failed_ids": (list, tuple), "setup_errors": dict, "omitted_ids": (list, tuple),
            "collected_ids": (list, tuple)}


FAILURE_LISTINGS = ("failed_ids", "setup_errors")


PHASES, OUTCOMES = ("setup", "call", "teardown"), frozenset({"passed", "failed", "skipped"})


def _listing(ran: dict, key: str) -> tuple[list[str], bool]:
    """The ids the listing ``key`` of ``ran`` names (``LISTINGS``), relative to the repository's top, and whether it reads
    as one. None, and readable, when the report has none (absent, or None as the runner gives one its plugin never
    wrote); none, and unreadable, when it is another container or names a test by anything but text."""
    value = ran.get(key)
    if value is None:
        return [], True
    if not isinstance(value, LISTINGS[key]) or not all(isinstance(node, str) for node in value):
        return [], False
    full = _in_repository(ran)
    return [full(node) for node in value], True


def _failed_ids(ran: dict) -> set[str]:
    """The tests the runner's own report lists as failed in ``ran`` (``FAILURE_LISTINGS``), by their ids relative to
    the repository's top; none from a listing that does not read as one."""
    return {*_listing(ran, "failed_ids")[0], *_listing(ran, "setup_errors")[0]}


def phases_of(ran: dict) -> dict[str, dict[str, str]]:
    """The one reading of a run's per-test report (G6 0.3, review 4's finding 1): every test the runner's report names
    as run or failed (``test_outcomes``, ``FAILURE_LISTINGS``), with its outcome per phase as the report gives it
    (``repo_tests``' plugin), by the test's id relative to the repository's top. A test listed only as failed is there
    too (review 1 of slice 2's follow-up, finding 2); ``_verdicts`` reads those only collected or left out.

    What the report does not say is marked, never filled in (findings 1 and 5), and ``body_of`` reads every mark as
    unknown: a test's phases that do not read as a mapping of names to outcomes are none; a test the runner lists as
    failed whose phases show no failure failed in a phase it did not name (``unnamed``), and one whose phases show a
    failure no listing of failures names has a failure the runner did not list (``unlisted``); and when a listing, or
    the report's root folder, does not read as one, every test's ``report`` is ``unreadable``."""
    outcomes, read = ran.get("test_outcomes"), {key: _listing(ran, key)[1] for key in LISTINGS}
    failures_read = all(read[key] for key in FAILURE_LISTINGS)
    whole = all(read.values()) and isinstance(ran.get("rootdir") or "", str)
    full, listed = _in_repository(ran), _failed_ids(ran)
    found: dict[str, dict[str, str]] = {}
    for node, phases in (outcomes.items() if read["test_outcomes"] and outcomes else ()):
        named = isinstance(phases, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in phases.items())
        found[full(node)] = dict(phases) if named else {}
    for node in sorted(listed):
        found.setdefault(node, {})
    for node, mine in found.items():
        shows = "failed" in mine.values()
        if node in listed and not shows:
            mine["unnamed"] = "failed"
        elif shows and node not in listed and failures_read:
            mine["unlisted"] = "failed"
        if not whole:
            mine["report"] = "unreadable"
    return found


def _whole(phases: dict[str, str]) -> bool:
    """Whether ``phases`` hold every phase a test needs, each one pytest names and each with an outcome pytest gives:
    its set-up and teardown, and its call after a set-up that passed; or a module's skipped collection alone."""
    if phases == {"collection": "skipped"}:
        return True
    return (set(phases) <= set(PHASES) and set(phases.values()) <= OUTCOMES and "setup" in phases
            and "teardown" in phases and (phases["setup"] != "passed" or "call" in phases))


def body_of(phases: dict[str, str]) -> str:
    """What one test's phases (``phases_of``) show of its body: ``failed`` only when every phase it needs is there and
    one pytest names (``_whole``), its set-up passed and its call failed; ``ERROR`` when anything else failed (a set-up
    or teardown error, a failure the runner named in no phase): unknown, never a failure of the body and never a pass;
    ``skipped`` when a phase was skipped (an xfail too: pytest reports its call skipped); ``passed`` only when every
    phase it needs is there and passed. Anything partial, unrecognised, contradictory or unreadable (review 1 of slice 2's
    follow-up, findings 1 and 5) is ``ERROR`` where a failure shows and ``not_run`` where none does: unknown either way."""
    states = set(phases.values())
    if not _whole(phases):
        return ERROR if "failed" in states else "not_run"
    if phases.get("setup") == "passed" and phases.get("call") == "failed":
        return FAILED
    if "failed" in states:
        return ERROR
    return "skipped" if "skipped" in states else "passed"


def observation(found: dict) -> str:
    """What one check's verdict entry (``_verdicts``) tells the issuer: its verdict, or ``ERROR`` wherever the entry
    names an observation. Only ``_verdicts`` names one, ``ERROR``, where the review's verdict is ``failed`` and no
    test's body failed; whatever value one holds, it can only make the result unknown, never a failure or a pass. Every
    reader of a run of a stated check for the issuer reads it through here."""
    return ERROR if found.get("observation") is not None else str(found.get("verdict"))


def _not_found(check: str, output: str) -> bool:
    """Whether pytest said it found no test named ``check`` (it prints the path it was given, or the full path)."""
    for line in output.replace("\\", "/").splitlines():
        _, sep, rest = line.partition("not found: ")
        if sep and (rest.strip() == check or rest.strip().endswith("/" + check)):
            return True
    return False


def _in_repository(ran: dict):
    """A function from a test id or file as pytest named it in ``ran``, relative to its root folder, to the same id
    relative to the repository's top, as checks name them (review I9: a pytest config below the top moves the root). A
    root folder that is not text places nothing (``phases_of`` reads every test of such a report as unknown)."""
    root = ran.get("rootdir") or ""
    prefix = root.strip("/") if isinstance(root, str) else ""
    return (lambda node: f"{prefix}/{node}") if prefix else (lambda node: node)


def _observed(checks: Sequence[str], ran: dict) -> dict[str, str]:
    """Each of ``checks`` that ``ran`` collected (a test of it is among the run's per-test outcomes, its failures or
    its set-up errors), with its verdict in that run (``_verdicts``). A check the run did not collect has none: the
    run did not observe it. G6 0.3: every run of a stated check on the patch reaches the issuer this way."""
    nodes = set(phases_of(ran))
    collected = [check for check in dict.fromkeys(checks) if any(_covers(check, node) for node in nodes)]
    return {check: observation(found) for check, found in _verdicts(collected, ran).items()}


def _verdicts(ids: list[str], ran: dict) -> dict[str, dict]:
    """Each check's own verdict in one run, read from pytest's per-test outcomes by phase (``phases_of``, ``body_of``).
    The review's verdict counts a test that failed outside its body (``ERROR``) as ``failed``, as it always has, so its
    status and outcome keep their meaning; where that is all a ``failed`` verdict rests on, the entry says so
    (``observation``: ``ERROR``), and the issuer is told that (``observation``). A test of a check that started which
    pytest collected and neither ran to a reported phase nor left out never finished (review 1 of slice 2's follow-up,
    finding 2): the check did not pass."""
    run, said, output = ran["verdict"], ran.get("said") or "", ran.get("output") or ""
    full = _in_repository(ran)
    tests = phases_of(ran)
    omitted = set(_listing(ran, "omitted_ids")[0])
    unfinished = [node for node in _listing(ran, "collected_ids")[0] if node not in tests and node not in omitted]
    missing = {check for check in ids if _not_found(check, output)}
    broken = {full(rel) for rel in _collection_errors(output)}
    found: dict[str, dict] = {}
    for check in ids:
        bodies = {node: body_of(phases) for node, phases in tests.items() if _covers(check, node)}
        if bodies:   # it started: one of its tests that never reported a phase never finished
            bodies.update({node: "not_run" for node in unfinished if _covers(check, node)})
        nodes = {node: FAILED if said_of == ERROR else said_of for node, said_of in bodies.items()}
        dropped = [node for node in omitted if _covers(check, node)]
        states = set(nodes.values())
        if run in UNTRUSTED:
            verdict, detail = run, said or "the run's results are not believed"
        elif run == repo_tests.RUN_INCONSISTENT:   # the owner's ruling (2026-10-29): no check passes on such a run
            verdict, detail = run, said or repo_tests.SAID[run]
        elif "failed" in states:
            verdict, detail = FAILED, "failed: " + ", ".join(sorted(n for n, s in nodes.items() if s == "failed")[:5])
        elif run == repo_tests.CRASHED:   # after the line above: a crash withholds a pass, and never erases a failure on the record
            verdict, detail = run, said or repo_tests.SAID[run].format(where="")
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
        if verdict == FAILED and FAILED not in bodies.values():   # no test's body failed: set-up, teardown, collection
            found[check]["observation"] = ERROR
    return found


_SEEN_KEYS = ("links_left_out", "links_materialised", "loaded_outside", "submodules_not_hashed", "generated_copied",
              "generated_left_out")


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


def _left(deadline: float) -> int:
    return int(deadline - time.monotonic())


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


def _not_loaded(found: dict[str, dict], ran: dict) -> list[str]:
    """The test files pytest could not load in ``ran``, the run against the base commit's code (one that imports a
    name the patch adds, say), each holding a check of ``found``: none of those checks ran there, so each becomes
    ``not_run``, never a failure at the base (the owner's pilot, 2026-10-30). Returns those files."""
    full = _in_repository(ran)
    broken = sorted({full(rel) for rel in _collection_errors(ran.get("output") or "")})
    files = []
    for check, verdict in found.items():
        head = check.partition("::")[0].rstrip("/")
        mine = [path for path in broken if path == head or path.startswith(head + "/")]
        if verdict["verdict"] != "passed" and mine:
            found[check] = {"verdict": "not_run", "detail": "its test file could not be loaded against the base "
                                                            "commit's code"}
            files += mine
    return list(dict.fromkeys(files))


def _on_base(root, change, derived, second, deadline, runs, unknowns, reason, roots=(), *, stated: Sequence[str],
             observed: list[dict]) -> None:
    """Replay each failing derived test file, and each one pytest could not collect, at the base commit: a defect only
    when it passed there. Where one fails on the patch and G6 cannot say it passed at the base commit and fails on the
    patch, an evidence reason of its own (``derived_test_not_compared``; the launch review's I4), never support; one
    that did not pass at the base commit either stays an unknown (the patch did not cause it).

    G6 0.3: a replay that ran the file on the patch, in a copy, observed each ``stated`` check in it; each one's verdict
    there is added to ``observed`` in the order it ran (``_observed``), ``NOT_READ`` when the replay kept no per-test
    result, and ``NOT_LOADED`` when it ran code from outside the copy."""
    def uncompared(text: str) -> None:
        unknowns.append(text)
        reason("evidence", NOT_COMPARED, text)

    full = _in_repository(second)
    failing = sorted(({node.partition("::")[0] for node in _failed_ids(second)}
                      | {full(rel) for rel in _collection_errors(second.get("output") or "")}) & set(derived))
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
        mine = [check for check in stated if _names(check, {rel})]
        unkept = out.get("baseline") == "passed" and out.get("patched", "not_run") != "not_run"   # ran, results not kept
        seen = _observed(mine, out) if phases_of(out) else dict.fromkeys(mine if unkept else (), NOT_READ)
        if seen:   # it ran on the patch: each stated check it ran, as it ran there
            observed.append({"run": ["replay", rel], "verdicts": dict.fromkeys(seen, NOT_LOADED) if elsewhere
                             else seen})
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
