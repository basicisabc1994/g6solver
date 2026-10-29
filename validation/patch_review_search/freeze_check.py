# SPDX-License-Identifier: Apache-2.0
"""Freeze readiness: whether a G6 source tree has every feature V9's arms 3 and 4 and V10's arm E detect or mirror
from G6, in the shape the study's scripts read, and whether the parity fixtures of arms 3 and 4 pass against it. Its
report is what the freeze entry records.

    python freeze_check.py (--g6-src <folder holding src/g6> | --commit <commit> [--repository <git repository>])
                           [--require base,pool,search] --output <report.json>

Each feature belongs to a group:

- ``base``: what G6's frozen review at b3c342b gives arms 3 and 4 (``review_patch``, the runs, the helpers arm 4 calls,
  the reason codes the arms read);
- ``pool``: the pool-compatibility change (``import_roots``, ``G6_REPO_PYTHON``, ``G6_REPO_ENV`` and the names it
  refuses, ``repo_tests.shadowed`` and the ``changed_code_not_loaded`` code, the submodule folders and their reasons,
  ``_moved``'s reading of a folder not hashed);
- ``search``: verification search, whose rules arm E mirrors (the check file rule, the size and count limits, the
  placement rule and ``placements``, n and each run's own limit) and whose settings arms C and T are given (each
  configuration the driver gives, ``driver.SEARCH``, read back through G6's ``search_config``).

A feature is read by importing G6 from the tree in a separate interpreter and probing it: a value compared with the
study's own, a function called on fixed inputs, or a signature. The parity fixtures (``PARITY``, in
``tests/validation/test_v9_arms.py``) run with pytest against the tree. Every group is required unless ``--require``
names fewer. The check fails (exit code 1, every problem printed) when a feature of a required group is missing or has
changed shape, or a parity fixture of a required group fails, skips or is not collected. Groups not required are
reported, never a failure.

``freeze_readiness/`` holds the development runs' reports: the search branch (633f61b, every group), the
pool-compatibility fix pass (8b5364c, every group and base and pool only) and the frozen review (b3c342b, every
group and base only).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parent
REPOSITORY = VALIDATION.parent
GROUPS = ("base", "pool", "search")
_ARMS = "tests/validation/test_v9_arms.py"
#: The parity fixtures, by group: arm 3 (G6's review) and arm 4 (the direct checks) on the same fixture.
PARITY = {
    "base": [f"{_ARMS}::test_arm_4_runs_g6s_checks_and_differs_only_where_g6s_assessment_logic_decides",
             f"{_ARMS}::test_arm_4_mirrors_every_check_the_frozen_review_added"],
    "pool": [f"{_ARMS}::test_g6_and_the_direct_checks_read_the_code_a_run_loaded_alike",
             f"{_ARMS}::test_g6_and_the_direct_checks_read_a_submodule_folder_alike"],
}
#: Names a repository's test environment might hold, to compare G6's refusals with the study's.
_ENV_NAMES = ("QUTE_QT_WRAPPER", "QT_QPA_PLATFORM", "OL_CONFIG", "ANSIBLE_VERBOSITY", "PYTEST_ADDOPTS", "PYTHONPATH",
              "pythonpath", "LD_PRELOAD", "DYLD_LIBRARY_PATH", "COV_CORE_SOURCE", "G6_ALLOW_UNCONFINED", "PATH", "HOME",
              "USERPROFILE", "TMP", "TEMP", "TMPDIR", "PATHEXT", "HOMEPAGE", "TEMPLATE")
#: Check files to compare G6's file rule with arm E's.
_FILES = ("test_a.py", "tests/test_a.py", "tests/unit/test_a_1.py", "conftest.py", "tests/a_test.py", "tests/test-a.py",
          "../test_a.py", "/test_a.py", "tests\\test_a.py", "venv/test_a.py", ".hidden/test_a.py",
          "x.egg-info/test_a.py", "tests//test_a.py", "./test_a.py", "tests/__init__.py", "test_a.txt",
          "site-packages/test_a.py", "C:/test_a.py", "tests/__pycache__/test_a.py")


class Missing(Exception):
    """A feature that is not there, or not in the shape the study reads."""


def _source(module) -> str:
    """A G6 module's source text, as a probe reads it."""
    return inspect.getsource(module)


def need(condition, why: str) -> None:
    if not condition:
        raise Missing(why)


# --- the probes, run in a separate interpreter with G6 imported from the tree under check ---------------------------------

def _probes() -> list[tuple[str, str, object]]:
    """Each feature: (name, group, probe). A probe returns what it read, or raises ``Missing``."""
    from patch_review_search import arm_e
    from patch_review_utility import containers

    def g6(module: str):
        import importlib
        return importlib.import_module(module)

    def review_patch():
        params = inspect.signature(g6("g6.harness.patch_review").review_patch).parameters
        need({"repo", "task", "requirements", "timeout_s"} <= set(params), "review_patch lacks a parameter arm 3 passes")
        return sorted(params)

    def untrusted():
        found = g6("g6.harness.patch_review").UNTRUSTED
        need({"inconsistent_report", "incomplete"} <= set(found), "UNTRUSTED lacks a verdict arm 4 reads")
        return sorted(found)

    def mirrored():
        review = g6("g6.harness.patch_review")
        names = ("_in_repository", "_runner_changes", "_collection_errors", "_test_diff")
        need(all(callable(getattr(review, name, None)) for name in names), "a helper arm 4 calls is gone")
        return list(names)

    def deadline_codes():
        source = inspect.getsource(g6("g6.harness.patch_review"))
        need('"run_timed_out"' in source and '"timed_out"' in source, "the codes arm 3 reads for a deadline are gone")
        return ["run_timed_out", "timed_out"]

    def runner():
        tests = g6("g6.workforce.repo_tests")
        names = ("checked_repo", "change", "snapshot", "ignored_inputs", "related_texts", "derive_tests", "diff_text",
                 "inside", "run", "run_before")
        need(all(callable(getattr(tests, name, None)) for name in names), "a runner function arm 4 calls is gone")
        need(set(tests.RAN) == {"passed", "failed"}, "RAN is not the two verdicts that ran")
        return list(names)

    def moved():
        tests = g6("g6.workforce.repo_tests")
        need(tests._moved({"a": "1"}, {"a": "2"}) and tests._moved({"a": "1"}, {"a": "1"}) == [],
             "_moved does not list what changed between two snapshots")
        return "lists the keys whose values differ"

    def arms_import():
        from patch_review_utility import arms
        return sorted(arms.DIRECT_CHECKS)

    def import_roots():
        need("import_roots" in inspect.signature(g6("g6.harness.patch_review").review_patch).parameters,
             "review_patch takes no import_roots")
        return "review_patch(import_roots=...)"

    def roots():
        tests = g6("g6.workforce.repo_tests")
        need(all("roots" in inspect.signature(fn).parameters for fn in (tests.run, tests.run_before)),
             "run or run_before takes no roots")
        return "run(roots=...), run_before(roots=...)"

    def repo_python():
        tests = g6("g6.workforce.repo_tests")
        need(getattr(tests, "REPO_PYTHON", None) == "G6_REPO_PYTHON", "G6 does not read G6_REPO_PYTHON")
        return tests.REPO_PYTHON

    def repo_env():
        tests = g6("g6.workforce.repo_tests")
        need(getattr(tests, "REPO_ENV", None) == "G6_REPO_ENV" and callable(getattr(tests, "_repo_env", None)),
             "G6 does not read G6_REPO_ENV")
        sample = {"QT_QPA_PLATFORM": "offscreen", "OL_CONFIG": "/app/conf/openlibrary.yml", "SPACED": "a b  c"}
        before = os.environ.get(tests.REPO_ENV)
        os.environ[tests.REPO_ENV] = containers.repo_env_flag(sample).partition("=")[2]
        try:
            need(tests._repo_env() == sample, "G6 reads the study's G6_REPO_ENV encoding differently")
        finally:
            if before is None:
                os.environ.pop(tests.REPO_ENV, None)
            else:
                os.environ[tests.REPO_ENV] = before
        return "one JSON object of names to text"

    def refused_names():
        pattern = getattr(g6("g6.workforce.repo_tests"), "_RUNNER_NAMES", None)
        need(pattern is not None, "G6 has no list of names G6_REPO_ENV may not set")
        differ = [name for name in _ENV_NAMES
                  if bool(pattern.fullmatch(name)) != bool(containers.test_env_problems({name: "x"}))]
        need(not differ, "G6 and the study refuse different names: " + ", ".join(differ))
        return pattern.pattern

    def shadowed():
        tests = g6("g6.workforce.repo_tests")
        need(callable(getattr(tests, "shadowed", None)), "G6 does not read which code a run loaded")
        ran = {"verdict": "passed", "loaded_outside": {"pkg.mod": "/site/pkg/mod.py"}, "loaded_complete": True}
        need(tests.shadowed(("src/pkg/mod.py",), ran) == {"src/pkg/mod.py": {"module": "pkg.mod",
                                                                             "file": "/site/pkg/mod.py"}},
             "shadowed does not name the module and file of a changed module loaded from elsewhere")
        need(tests.shadowed(("src/pkg/mod.py",), {**ran, "loaded_complete": False}) is None,
             "shadowed does not say when it cannot tell")
        need(tests.shadowed(("src/pkg/mod.py",), {"verdict": "passed"}) == {}, "shadowed reads a run that records none")
        return "shadowed(sources, ran) -> {path: {module, file}} | None"

    def not_loaded_code():
        need(getattr(g6("g6.harness.patch_review"), "NOT_LOADED", None) == "changed_code_not_loaded",
             "the reason code changed_code_not_loaded is gone")
        return "changed_code_not_loaded"

    def not_hashed():
        tests = g6("g6.workforce.repo_tests")
        need(callable(getattr(tests, "submodules_not_hashed", None)), "G6 does not read submodule folders")
        key = tests._SUBMODULE + "vendor/lib"
        need(tests.submodules_not_hashed({key: tests.NOT_HASHED + "too many files"}) == {"vendor/lib": "too many files"},
             "submodules_not_hashed does not name a folder and why")
        return "submodules_not_hashed(snapshot) -> {path: why}"

    def moved_not_hashed():
        tests = g6("g6.workforce.repo_tests")
        key = tests._SUBMODULE + "vendor/lib"
        need(tests._moved({key: tests.NOT_HASHED + "x"}, {key: "0" * 64}) == [],
             "a folder not hashed on one side reads as changed (the meaning before the fix pass, 8b5364c)")
        return "not hashed on either side is never changed"

    def change_fields():
        names = {field.name for field in dataclasses.fields(g6("g6.workforce.repo_tests").Change)}
        need({"sources", "submodules", "submodule_changes"} <= names, "Change lacks a field arm 4 reads")
        return sorted(names & {"sources", "submodules", "submodule_changes"})

    def submodule_codes():
        source = inspect.getsource(g6("g6.harness.patch_review"))
        need('"change_inside_submodule"' in source and '"submodule_not_hashed"' in source,
             "a submodule reason code arm 4 mirrors is gone")
        return ["change_inside_submodule", "submodule_not_hashed"]

    def search():
        return g6("g6.harness.verification_search").__name__

    def limits():
        vs = g6("g6.harness.verification_search")
        need(vs.MAX_CHECKS == arm_e.MAX_TESTS, f"G6's limit on checks is {vs.MAX_CHECKS}, arm E's {arm_e.MAX_TESTS}")
        need(vs.MAX_SOURCE_BYTES == arm_e.MAX_SOURCE_BYTES, "G6's limit on a check's text differs from arm E's")
        for text in ("x" * arm_e.MAX_SOURCE_BYTES, "x" * (arm_e.MAX_SOURCE_BYTES + 1), "\ud800"):
            need(bool(vs._source_problem(text)) == bool(arm_e.source_problem(text)),
                 "G6 and arm E judge a check's text differently")
        return {"checks": vs.MAX_CHECKS, "bytes": vs.MAX_SOURCE_BYTES}

    def file_rule():
        vs = g6("g6.harness.verification_search")
        need((vs._NAME.pattern, vs._PART.pattern, set(vs._NOT_A_PLACE), tuple(vs._METADATA), vs.FOLDER)
             == (arm_e._NAME.pattern, arm_e._PART.pattern, set(arm_e._NOT_A_PLACE), tuple(arm_e._METADATA),
                 arm_e.FOLDER), "G6's check file rule's parts differ from arm E's")
        differ = [file for file in _FILES if (vs._file_problem(file) == "") != (arm_e.file_problem(file) == "")]
        need(not differ, "G6 and arm E judge these check files differently: " + ", ".join(differ))
        return {"name": vs._NAME.pattern, "folder": vs.FOLDER}

    def placement():
        vs = g6("g6.harness.verification_search")
        for file, text in (("tests/test_a.py", "def test_a():\n    pass\n"), ("test_b.py", "x = 1\n")):
            check = vs.ProposedCheck(id="c1", requirement_id="R1", intent="change", file=file, source=text)
            need(vs.placement(check) == arm_e.placement(file, text), "G6 and arm E place a check differently")
        return "folder/g6_check_<12 hexadecimal digits of the text's SHA-256>/name"

    def placements():
        vs = g6("g6.harness.verification_search")
        text = "def test_a():\n    pass\n"
        check = vs.ProposedCheck(id="c1", requirement_id="R1", intent="change", file="lib/test_a.py", source=text)
        cases = {"clean": lambda root: None, "file in the way": lambda root: (root / "lib").write_text("x\n"),
                 "folder exists": lambda root: (root / arm_e.placement("lib/test_a.py", text).rpartition("/")[0])
                 .mkdir(parents=True)}
        read = []
        with tempfile.TemporaryDirectory(prefix="v10-freeze-") as scratch:
            for name, make in cases.items():
                root = Path(scratch) / name.replace(" ", "_")
                root.mkdir()
                make(root)
                try:
                    vs.placements(root, [check])
                    refused = False
                except ValueError:
                    refused = True
                need(refused == bool(arm_e._place_problem(root, arm_e.placement("lib/test_a.py", text))),
                     f"G6 and arm E judge a placement differently ({name})")
                read.append(name)
        return read

    def parameters():
        return arm_e.g6_parameters(g6("g6.harness.verification_search"))

    def settings():
        from patch_review_search import driver
        vs = g6("g6.harness.verification_search")
        need("templates" in vs.SEARCH_SETTINGS, "G6's search has no templates setting")
        for arm, config in driver.SEARCH.items():
            try:
                got = vs.search_config(dict(config))
            except ValueError as exc:
                raise Missing(f"G6's search does not take arm {arm}'s configuration {config}: {exc}") from exc
            need(all(got.get(key) == value for key, value in config.items()),
                 f"G6's search reads arm {arm}'s configuration {config} as {got}")
        return {"settings": sorted(vs.SEARCH_SETTINGS), "arms": driver.SEARCH}

    def search_parameter():
        need("search" in inspect.signature(g6("g6.harness.patch_review").review_patch).parameters,
             "review_patch takes no search setting")
        return "review_patch(search=...)"

    def check_observer():
        # rehearsal.observed_checks keeps the text of every check verification search is given (item 81's ruling)
        vs = g6("g6.harness.verification_search")
        shapes = {"search": ["change", "checks", "placed"], "not_run": ["checks", "placed"]}
        found = {}
        for name, wanted in shapes.items():
            found[name] = list(inspect.signature(getattr(vs, name)).parameters)[:len(wanted)]
            need(found[name] == wanted, f"verification_search.{name} takes {found[name]}, not {wanted} first")
        need({"id", "requirement_id", "source"} <= set(getattr(vs.ProposedCheck, "model_fields", {})),
             "a proposed check has no id, requirement_id or source")
        source = _source(g6("g6.harness.patch_review"))
        need("verification_search.search" in source and "verification_search.not_run(" in source,
             "the review no longer reaches search and not_run through the module, so the observer cannot see them")
        return found

    def limit_words():
        from patch_review_search import secondary
        envelope = g6("g6.model_checking.envelope")
        codes = {envelope.INPUT_TOO_LARGE, envelope.OUTPUT_TOO_LARGE, envelope.TIME_LIMIT_TOO_LONG}
        need(codes == set(secondary.OVER_LIMIT_CODES), f"model_checking's limit codes are {sorted(codes)}")
        source = _source(g6("g6.harness.verification_templates"))
        missing = [words for words in secondary.G6_LIMIT_WORDS if words not in source]
        need(not missing, "the formal path's limit words changed: " + "; ".join(missing))
        return {"codes": sorted(codes), "words": len(secondary.G6_LIMIT_WORDS)}

    return [("review_patch", "base", review_patch), ("UNTRUSTED", "base", untrusted),
            ("arm 4's review helpers", "base", mirrored), ("deadline reason codes", "base", deadline_codes),
            ("runner functions", "base", runner), ("_moved", "base", moved), ("the arms import", "base", arms_import),
            ("roots on every run", "base", roots), ("import_roots", "pool", import_roots),
            ("G6_REPO_PYTHON", "pool", repo_python), ("G6_REPO_ENV encoding", "pool", repo_env),
            ("G6_REPO_ENV refused names", "pool", refused_names), ("shadowed", "pool", shadowed),
            ("changed_code_not_loaded", "pool", not_loaded_code), ("submodules_not_hashed", "pool", not_hashed),
            ("_moved and a folder not hashed", "pool", moved_not_hashed), ("Change's submodule fields", "pool",
                                                                             change_fields),
            ("submodule reason codes", "pool", submodule_codes),
            ("verification_search", "search", search), ("check limits", "search", limits),
            ("check file rule", "search", file_rule), ("placement", "search", placement),
            ("placements", "search", placements), ("n and each run's limit", "search", parameters),
            ("search settings", "search", settings), ("review_patch's search setting", "search", search_parameter),
            ("the search arms' check observer", "search", check_observer),
            ("the formal path's limit words and codes", "search", limit_words)]


def probe(src: str) -> list[dict]:
    """Every feature of G6 at ``src`` (the folder holding ``g6``): in this interpreter, which must be a fresh one."""
    sys.path[:0] = [src, str(VALIDATION)]
    found = []
    for name, group, check in _probes():
        try:
            found.append({"name": name, "group": group, "present": True, "read": check()})
        except Missing as exc:
            found.append({"name": name, "group": group, "present": False, "problem": str(exc)})
        except Exception as exc:  # a module or name that is not there is a missing feature, said, never raised
            found.append({"name": name, "group": group, "present": False, "problem": f"{type(exc).__name__}: {exc}"})
    return found


# --- the check, in the study's interpreter -------------------------------------------------------------------------------

def features(src: Path) -> list[dict]:
    done = subprocess.run([sys.executable, "-I", str(Path(__file__)), "--probe", str(src)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise SystemExit(f"the probe of G6 at {src.name} failed: {done.stderr[-2000:]}")
    return json.loads(done.stdout)


def parity(src: Path, groups: list[str]) -> dict[str, dict]:
    """The parity fixtures of ``groups`` run with pytest against G6 at ``src``: each fixture's passed, failed and skipped
    counts."""
    wanted = [node for group in groups for node in PARITY.get(group, [])]
    found = {node: {"group": group, "passed": 0, "failed": 0, "skipped": 0} for group in groups
             for node in PARITY.get(group, [])}
    if not wanted:
        return found
    with tempfile.TemporaryDirectory(prefix="v10-parity-") as scratch:
        report = Path(scratch) / "parity.xml"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(src), os.environ.get("PYTHONPATH")])),
               "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
                        *wanted], cwd=REPOSITORY, env=env, capture_output=True, text=True)
        cases = ElementTree.parse(report).getroot().iter("testcase") if report.is_file() else []
        for case in cases:
            node = f"{_ARMS}::{case.get('name', '').partition('[')[0]}"
            if node not in found:
                continue
            state = ("skipped" if case.find("skipped") is not None else
                     "failed" if case.find("failure") is not None or case.find("error") is not None else "passed")
            found[node][state] += 1
    return found


def evaluate(found: list[dict], fixtures: dict[str, dict], required: list[str]) -> list[str]:
    """Every problem that stops the freeze: a feature of a required group missing or changed, or a parity fixture of
    a required group that failed, skipped or was not collected."""
    problems = [f"{item['group']}: {item['name']}: {item['problem']}" for item in found
                if item["group"] in required and not item["present"]]
    for node, counts in fixtures.items():
        if counts["group"] not in required:
            continue
        name = node.rpartition("::")[2]
        if counts["failed"] or counts["skipped"]:
            problems.append(f"{counts['group']}: parity fixture {name}: {counts['failed']} failed, "
                            f"{counts['skipped']} skipped")
        elif not counts["passed"]:
            problems.append(f"{counts['group']}: parity fixture {name}: not collected")
    return problems


def _tree_digest(src: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((src / "g6").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(src).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def check(src: Path, *, required: list[str], source: dict) -> dict:
    found = features(src)
    fixtures = parity(src, list(GROUPS))
    problems = evaluate(found, fixtures, required)
    return {"study": "V9 and V10", "kind": "freeze readiness", "g6": {**source, "tree_sha256": _tree_digest(src)},
            "required": required, "ready": not problems, "problems": problems, "features": found,
            "parity": fixtures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--g6-src", help="a folder holding src/g6")
    where.add_argument("--commit", help="a G6 commit, read with git archive")
    where.add_argument("--probe", help=argparse.SUPPRESS)
    parser.add_argument("--repository", default=str(REPOSITORY), help="the git repository holding --commit")
    parser.add_argument("--require", default=",".join(GROUPS), help="the groups the frozen commit must have")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.probe:
        print(json.dumps(probe(args.probe)))
        return 0
    required = [group for group in args.require.split(",") if group]
    unknown = [group for group in required if group not in GROUPS]
    if unknown:
        parser.error("unknown group(s): " + ", ".join(unknown))
    with tempfile.TemporaryDirectory(prefix="v10-freeze-src-") as scratch:
        if args.commit:
            commit = subprocess.run(["git", "-C", args.repository, "rev-parse", "--verify", args.commit + "^{commit}"],
                                    capture_output=True, text=True, check=True).stdout.strip()
            archive = Path(scratch) / "src.tar"
            subprocess.run(["git", "-C", args.repository, "archive", "--format=tar", "-o", str(archive), commit, "src"],
                           check=True)
            subprocess.run(["tar", "-xf", str(archive), "-C", scratch], check=True)
            src, source = Path(scratch) / "src", {"commit": commit}
        else:
            src, source = Path(args.g6_src) / "src", {"source": "a folder"}
        report = check(src, required=required, source=source)
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    for problem in report["problems"]:
        print("NOT READY: " + problem, file=sys.stderr)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
