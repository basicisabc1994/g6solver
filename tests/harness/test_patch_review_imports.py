# SPDX-License-Identifier: Apache-2.0
"""Changed code the tests never loaded. The ansible image installs ``ansible-core`` (not editable) and keeps the
repository in a ``lib/`` layout, so from the repository's top ``import ansible`` loads the installed copy of the base
code: the tests pass whatever the patch did to ``lib/ansible``.

Pinned: G6's bootstrap records every module the run loaded from outside the tree under test (and outside the
standard library), at most ``repo_tests.LOADED_LIMIT``; a changed module loaded from there instead ("shadowed") makes
the evidence of that run unusable: at most ``insufficient_evidence``, ``changed_code_not_loaded``, the requirement not
established, an unknown naming the module, where it came from and what to do; a list cut at its limit is the same, never
a pass; a changed module never loaded, or a standard-library module named like a changed file, changes nothing.
``import_roots`` (at most 8 folders inside the repository, no links) go first on the import path of every run of the
review, are recorded in its scope, and with ``["lib"]`` the defect is found. Development fixtures only; the
interpreter is a throwaway virtual environment in the test's own folder (``G6_REPO_PYTHON``); the review's own runs are
confined (bubblewrap; Docker on Windows) or recorded."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_review
from g6.harness.patch_review import review_patch
from g6.workforce import repo_tests as rt

BASE = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b):\n    return a - b\n"   # the defective patch
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
CHECK = "tests/test_calc.py::test_add"
REQUIREMENTS = [{"id": "R1", "text": "add returns the sum", "checks": [CHECK]}]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A repository in a lib/ layout (lib/calc), committed, with ``calc`` also installed, a copy of the base code, in a
    virtual environment outside the repository that G6_REPO_PYTHON names; then the defective patch to lib/calc."""
    repo = tmp_path / "app"
    (repo / "lib" / "calc").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "lib" / "calc" / "__init__.py").write_text(BASE, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    env = tmp_path / "installed-env"
    # --copies: an environment linked to G6's own Python is refused (L1); the person makes G6's own with --copies
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", "--copies", str(env)],
                   check=True,
                   capture_output=True, timeout=180)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               check=True, capture_output=True, text=True).stdout.strip())
    (site / "calc").mkdir(parents=True)
    (site / "calc" / "__init__.py").write_text(BASE, encoding="utf-8")
    # pytest for the environment: where G6 itself runs from a virtual environment, its base Python has none
    (site / "g6_test_pytest.pth").write_text(str(Path(pytest.__file__).resolve().parents[1]) + "\n", encoding="utf-8")
    monkeypatch.setenv(rt.REPO_PYTHON, str(python))
    (repo / "lib" / "calc" / "__init__.py").write_text(BROKEN, encoding="utf-8")
    return repo, site / "calc" / "__init__.py"


# --- the record of what a run loaded (repo_tests) --------------------------------------------------------------------


@pytest.mark.usefixtures("unconfined")
def test_a_run_records_a_changed_module_it_loaded_from_outside_the_tree(installed):
    repo, copy = installed
    found = rt.run(repo, ["tests/test_calc.py"])
    assert found["verdict"] == rt.PASSED, found["output"]   # the installed base code passed: the danger
    assert found["loaded_complete"] is True
    assert "calc" in found["loaded_outside"], (sorted(found["loaded_outside"])[:40], found["output"][-800:])
    assert Path(found["loaded_outside"]["calc"]).resolve() == copy.resolve()
    assert rt.shadowed(rt.change(repo).sources, found) == {
        "lib/calc/__init__.py": {"module": "calc", "file": found["loaded_outside"]["calc"]}}
    rooted = rt.run(repo, ["tests/test_calc.py"], roots=("lib",))   # the import root: the patched code runs
    assert rooted["verdict"] == rt.FAILED and rt.shadowed(rt.change(repo).sources, rooted) == {}


@pytest.mark.usefixtures("unconfined")
def test_a_standard_library_module_named_like_a_changed_file_is_not_shadowing(tmp_path, monkeypatch):
    monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    repo = tmp_path / "app"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "json.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "test_j.py").write_text("import json\n\n\ndef test_j():\n    assert json.dumps(1) == '1'\n",
                                    encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "tools" / "json.py").write_text("X = 2\n", encoding="utf-8")
    found = rt.run(repo, ["test_j.py"])
    assert found["verdict"] == rt.PASSED and "json" not in found["loaded_outside"]
    assert rt.shadowed(rt.change(repo).sources, found) == {}


@pytest.mark.usefixtures("unconfined")
def test_a_list_cut_at_its_limit_cannot_say_and_a_recorded_run_without_one_says_nothing(installed, monkeypatch):
    repo, _ = installed
    monkeypatch.setattr(rt, "LOADED_LIMIT", 0)
    found = rt.run(repo, ["tests/test_calc.py"])
    assert found["loaded_complete"] is False and rt.shadowed(rt.change(repo).sources, found) is None
    assert rt.shadowed(("lib/calc/__init__.py",), {"verdict": "passed"}) == {}


# --- the review (recorded runs) --------------------------------------------------------------------------------------


def _result(passed=(), failed=(), **extra) -> dict:
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
            "not_passed": 0, "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.1,
            "python": sys.executable, "interpreter_source": "setting", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": "", **extra}


def _recorded(monkeypatch, ran: dict) -> list:
    calls: list = []

    def execute(root, ids, *, timeout_s, change=None, roots=()):
        calls.append(("base" if change is not None else "in_place", tuple(ids), tuple(roots)))
        return {rel: ran for rel in ids} if change is not None else ran

    def execute_at_base(change, ids, *, timeout_s, roots=()):
        calls.append(("at_base", tuple(ids), tuple(roots)))
        return {"verdict": "not_run", "said": "no run was recorded"}

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)
    return calls


SHADOW = {"loaded_outside": {"calc": "/usr/local/lib/python3.9/dist-packages/calc/__init__.py"},
          "loaded_complete": True}


@pytest.mark.parametrize("failing", [False, True], ids=["passing", "failing"])
def test_a_run_that_loaded_the_changed_module_from_elsewhere_is_no_evidence_either_way(installed, monkeypatch,
                                                                                         failing):
    repo, _ = installed
    ran = _result(failed=[CHECK]) if failing else _result([CHECK])
    _recorded(monkeypatch, {**ran, **SHADOW})   # a failure of the installed copy is no defect of the patch either
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment
    assert ("evidence", "changed_code_not_loaded") in {(r["stage"], r["code"]) for r in assessment["reasons"]}
    assert not any(r["stage"] == "integrity" for r in assessment["reasons"])
    [requirement] = assessment["requirements"]
    assert requirement["status"] == "not_established"
    [unknown] = [u for u in assessment["unknowns"] if "lib/calc/__init__.py" in u]
    assert "/usr/local/lib/python3.9/dist-packages/calc/__init__.py" in unknown and "import_roots" in unknown


def test_a_run_whose_list_was_cut_is_no_evidence_and_one_that_never_loaded_the_module_keeps_its_outcome(
        installed, monkeypatch):
    repo, _ = installed
    _recorded(monkeypatch, _result([CHECK]) | {"loaded_outside": {}, "loaded_complete": False})
    cut = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)["assessment"]
    assert cut["outcome"] == "insufficient_evidence" and "changed_code_not_loaded" in {r["code"] for r in
                                                                                      cut["reasons"]}
    _recorded(monkeypatch, _result([CHECK]) | {"loaded_outside": {"other": "/x/other.py"}, "loaded_complete": True})
    kept = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)["assessment"]
    assert kept["outcome"] == "supported_within_scope", kept


def test_import_roots_reach_every_run_and_the_scope(installed, monkeypatch):
    repo, _ = installed
    calls = _recorded(monkeypatch, _result([CHECK]) | {"loaded_outside": {}, "loaded_complete": True})
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS, import_roots=["lib"])
    assert got["assessment"]["scope"]["import_roots"] == ["lib"]
    assert calls and all(roots == ("lib",) for _, _, roots in calls), calls


@pytest.mark.parametrize("roots", [["../outside"], ["/abs"], ["C:/abs"], ["missing"], ["tests/test_calc.py"],
                                   [f"r{index}" for index in range(9)], "lib", [3], ["."]])
def test_import_roots_outside_the_repository_or_not_folders_are_refused(installed, monkeypatch, roots):
    repo, _ = installed
    for index in range(9):
        (repo / f"r{index}").mkdir()
    _recorded(monkeypatch, _result([CHECK]))
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS, import_roots=roots)
    assert got["completion_state"] == "blocked-escalated" and "G6_E_INPUT_VALIDATION" in str(got), got


def test_an_import_root_that_is_a_link_is_refused(installed, monkeypatch, tmp_path):
    repo, _ = installed
    try:
        os.symlink(repo / "lib", repo / "linked", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this system cannot make a symbolic link ({exc})")
    _recorded(monkeypatch, _result([CHECK]))
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS, import_roots=["linked"])
    assert got["completion_state"] == "blocked-escalated" and "G6_E_INPUT_VALIDATION" in str(got), got


def test_the_mcp_tool_takes_import_roots():
    pytest.importorskip("fastmcp")
    import asyncio

    from g6.mcp import build_server

    tool = asyncio.run(build_server().get_tools())["g6_review_patch"]
    assert "import_roots" in tool.parameters["properties"]


# --- the review's fix pass (2026-10-29): the reviewer's probes P1, P3, P3b, M1 and M4 --------------------------------

#: A committed conftest that registers an optional dependency lazily (importlib.util.LazyLoader, as lazy_loader's
#: load() does), never used by the tests, whose import would fail (the reviewer's P3b).
LAZY_CONFTEST = '''import importlib.util, sys
from pathlib import Path


def _lazy(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    spec.loader = importlib.util.LazyLoader(spec.loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


_lazy("optional_dep", Path(__file__).with_name("optional_dep.py"))
del _lazy
'''
#: An entry of sys.modules whose __dict__ cannot be read: the list is incomplete, the run never ends on it.
ODD_CONFTEST = ("import sys\n\n\nclass _Odd:\n    @property\n    def __dict__(self):\n"
                "        raise RuntimeError('no dict here')\n\n\nsys.modules['odd_entry'] = _Odd()\n")


def _lazy_repo(tmp_path: Path, conftest: str) -> Path:
    """calc.py (add wrong) and its test, with ``conftest`` and a module whose import fails, committed; then the
    correct patch."""
    repo = tmp_path / "app"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BROKEN, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TEST, encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    (repo / "tests" / "optional_dep.py").write_text("raise ImportError('optional dependency missing')\n",
                                                   encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "calc.py").write_text(BASE, encoding="utf-8")   # the correct patch
    return repo


@pytest.mark.usefixtures("unconfined")
def test_reading_the_module_list_never_loads_a_lazy_module_nor_ends_the_run(tmp_path, monkeypatch):
    """Review C1 (Critical): reading a lazily loaded module's __file__ ran its failing import, so pytest exited 1."""
    monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    lazy = rt.run(_lazy_repo(tmp_path / "lazy", LAZY_CONFTEST), ["tests/test_calc.py"])
    assert (lazy["verdict"], lazy["loaded_complete"]) == (rt.PASSED, True), lazy["output"][-1200:]
    odd = rt.run(_lazy_repo(tmp_path / "odd", ODD_CONFTEST), ["tests/test_calc.py"])
    assert (odd["verdict"], odd["loaded_complete"]) == (rt.PASSED, False), odd["output"][-1200:]


@pytest.mark.usefixtures("unconfined")
def test_import_roots_come_before_the_repositorys_top(tmp_path, monkeypatch):
    """Review M4: a package both at the top and in a root comes from the root. (pytest itself puts a test file's own
    folder first, so a test file at the top would still see the top's: the tests here are in tests/, as usual.)"""
    monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    repo = tmp_path / "app"
    for folder in (repo / "calc", repo / "lib" / "calc", repo / "tests"):
        folder.mkdir(parents=True)
    for folder in (repo / "calc", repo / "lib" / "calc"):
        (folder / "__init__.py").write_text(BASE, encoding="utf-8")
    (repo / "tests" / "test_where.py").write_text(
        "import calc\n\n\ndef test_where():\n"
        "    assert calc.__file__.replace('\\\\', '/').endswith('lib/calc/__init__.py'), calc.__file__\n",
        encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    found = rt.run(repo, ["tests/test_where.py"], roots=("lib",))
    assert found["verdict"] == rt.PASSED, found["output"][-800:]


def test_the_runs_records_keep_what_the_run_left_out_or_could_not_see(installed, monkeypatch):
    """Review M1: the reply's run records carry the copy's links, the modules loaded from outside the tree, and the
    submodule folders not hashed."""
    repo, _ = installed
    extra = {"links_left_out": {"pkg/up": rt.LINK_OUTSIDE}, "links_materialised": {"lib2": "lib"},
             "loaded_outside": {"other": "/x/other.py"}, "loaded_complete": True,
             "submodules_not_hashed": {"vendor/x": "it holds more than 1 files, the most G6 hashes"}}
    _recorded(monkeypatch, _result([CHECK]) | extra)
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)
    [first, *_] = got["assessment"]["scope"]["checks_run"]
    for key in ("links_left_out", "links_materialised", "loaded_outside", "submodules_not_hashed"):
        assert first[key] == extra[key], key


def test_the_runs_records_say_which_generated_files_the_copy_took_or_left_out(installed, monkeypatch):
    """The review of 286c173: a git-ignored file a copy takes is trusted code in both copies, so the reply names it,
    with its SHA-256, and names each one it left out, with why."""
    repo, _ = installed
    extra = {"generated_copied": {"src/_pkg_version.py": "0" * 64},
             "generated_left_out": {"src/_other_version.py": "it holds more than literal data"}}
    _recorded(monkeypatch, _result([CHECK]) | extra)
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)
    [first, *_] = got["assessment"]["scope"]["checks_run"]
    for key in extra:
        assert first[key] == extra[key], key


def _venv_in(folder: Path) -> tuple[Path, Path]:
    # --copies: an environment linked to G6's own Python is refused (L1)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", "--copies", str(folder)],
                   check=True,
                   capture_output=True, timeout=180)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               check=True, capture_output=True, text=True).stdout.strip())
    (site / "g6_test_pytest.pth").write_text(str(Path(pytest.__file__).resolve().parents[1]) + "\n", encoding="utf-8")
    return python, site


# --- confined (Linux bubblewrap; Docker on Windows) ------------------------------------------------------------------


@pytest.mark.parametrize("folder", [".venv", ".tox/py312"])
def test_confined_an_installed_copy_inside_the_repository_is_not_the_tree(tmp_path, bwrap_backend, monkeypatch,
                                                                         folder):
    """Review I1 (the reviewer's P1): an environment inside the repository (.venv, chosen by G6 itself, or .tox through
    G6_REPO_PYTHON) holds an installed copy of the base code; the defective patch to lib/calc."""
    repo = tmp_path / "app"
    (repo / "lib" / "calc").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "lib" / "calc" / "__init__.py").write_text(BASE, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TEST, encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n.tox/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    python, site = _venv_in(repo / folder)
    (site / "calc").mkdir()
    (site / "calc" / "__init__.py").write_text(BASE, encoding="utf-8")
    if folder == ".venv":
        monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    else:
        monkeypatch.setenv(rt.REPO_PYTHON, str(python))
    (repo / "lib" / "calc" / "__init__.py").write_text(BROKEN, encoding="utf-8")
    got = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)["assessment"]
    assert got["outcome"] == "insufficient_evidence", got
    assert "changed_code_not_loaded" in {r["code"] for r in got["reasons"]}


def test_confined_a_correct_patch_beside_a_lazily_loaded_failing_module_keeps_its_outcome(tmp_path, bwrap_backend,
                                                                                         monkeypatch):
    """Review C1 (the reviewer's P3b): at b3c342b this read supported_within_scope; the module list made it
    test_integrity_compromised."""
    monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    got = review_patch(_lazy_repo(tmp_path, LAZY_CONFTEST), task="Repair addition.",
                       requirements=REQUIREMENTS)["assessment"]
    assert got["outcome"] == "supported_within_scope", got


def test_confined_a_receipt_never_vouches_for_a_run_that_loaded_tree_code_from_elsewhere(installed, bwrap_backend,
                                                                                         monkeypatch):
    """Review M1: receipts are reusable evidence, so a run that loaded a module of the tree from outside it, or whose
    list is incomplete, gets none."""
    repo, _ = installed
    shadowed = rt.run(repo, ["tests/test_calc.py"], receipt=True)
    assert shadowed["receipt"] == "" and "calc" in shadowed["receipt_withheld"], shadowed
    rooted = rt.run(repo, ["tests/test_calc.py"], roots=("lib",), receipt=True)
    assert rooted["receipt"], rooted   # survival: the same run loading the tree's own code gets one
    monkeypatch.setattr(rt, "LOADED_LIMIT", 0)
    cut = rt.run(repo, ["tests/test_calc.py"], roots=("lib",), receipt=True)
    assert cut["receipt"] == "" and cut["receipt_withheld"], cut


def test_confined_a_defective_patch_the_installed_copy_hides_is_not_supported_and_import_roots_find_it(
        installed, bwrap_backend):
    repo, copy = installed
    hidden = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS)["assessment"]
    assert hidden["outcome"] == "insufficient_evidence", hidden
    assert "changed_code_not_loaded" in {r["code"] for r in hidden["reasons"]}
    assert any("installed-env" in u and "import_roots" in u for u in hidden["unknowns"]), hidden["unknowns"]
    found = review_patch(repo, task="Repair addition.", requirements=REQUIREMENTS, import_roots=["lib"])["assessment"]
    assert found["outcome"] == "defect_detected", found
    assert found["scope"]["import_roots"] == ["lib"]
