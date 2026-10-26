# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The test bench, ``workforce.run_tests`` (workforce OS plan, V1b): the oracle for "make the failing
test pass".

Pinned: the bench copies a repository from under the bound workspace root into a scratch directory,
applies a patch's files there (never in the owner's repository), refuses a patch that edits a test file
or leaves the copy, and runs each named test through the market's sealed runner (``venture.truth``);
``passed`` only when every named test passed. It is a native capability: its ParamContract is shipped,
it climbs the readiness ladder without the registry, its probe needs no repository of the owner's,
and the single door routes it to the bench, never to the registry. Offline, temp dirs; each named
test is a real pytest subprocess on a two-function synthetic repository.
"""
from __future__ import annotations

import pytest
from g6.core import confine
from g6.workforce import store
from g6.workforce import bench, service
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import workers as ww
from g6.workforce.runtime import ceiling_for

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
BREAKS_SUB = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n"
TESTS = "from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n"
NODES = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"]


def make_repo(root, name: str = "calc", venture: str = "v1") -> None:
    """A two-function repository with one failing test, under ``<root>/repos/<venture>/<name>``."""
    repo = root / bench.REPOS / venture / name
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    make_repo(tmp_path)
    return tmp_path


def _run(**params) -> dict:
    return bench.run("run_tests", {"repo": "calc", "tests": NODES, **params}, venture="v1")


def test_the_unpatched_repository_fails_its_named_test(root):
    out = _run()
    assert out["result"]["passed"] is False
    assert out["result"]["tests"] == {NODES[0]: False, NODES[1]: True}


def test_a_patch_that_fixes_the_code_passes_every_named_test(root):
    out = _run(files={"calc.py": FIXED})
    assert {"backend", "why"} <= set(out["result"].pop("isolation"))  # how the tests ran (g6.core.confine)
    assert out["result"] == {"passed": True, "tests": {NODES[0]: True, NODES[1]: True}, "applied": ["calc.py"],
                             "integrity": {"test_files_unchanged": True, "test_files": 1},
                             # ECHA Stage A: the patch is also compared with the whole suite (here: test_sub passed before)
                             "suite": {"status": "pass", "compared": 1, "broken": 0, "tests": []}}


def test_a_patch_that_breaks_another_test_does_not_pass(root):
    out = _run(files={"calc.py": BREAKS_SUB})
    assert out["result"]["passed"] is False and out["result"]["tests"][NODES[1]] is False


def test_the_owners_repository_is_never_changed(root):
    _run(files={"calc.py": FIXED})
    assert (root / bench.REPOS / "v1" / "calc" / "calc.py").read_text(encoding="utf-8") == BUGGY


@pytest.mark.parametrize(("params", "why"), [
    ({"files": {"tests/test_calc.py": "def test_add():\n    pass\n"}}, "test file"),
    ({"files": {"conftest.py": "import pytest\n"}}, "test file"),
    ({"files": {"../escape.py": "x = 1\n"}}, "inside the repository"),
    ({"files": {"/abs.py": "x = 1\n"}}, "inside the repository"),
    ({"files": {"calc.py": 3}}, "text"),
    ({"repo": "../calc"}, "repository name"),
    ({"repo": "missing"}, "no repository"),
    ({"tests": []}, "at least one test"),
    ({"tests": ["../outside.py::test_x"]}, "inside the repository"),
    # Codex review of V1: nothing in a patch may replace or configure the runner, or alias a test file.
    ({"files": {"pytest.py": "print('1 passed')\nraise SystemExit(0)\n"}}, "runner"),
    ({"files": {"_pytest/__init__.py": "x = 1\n"}}, "runner"),
    ({"files": {"os.py": "x = 1\n"}}, "runner"),
    ({"files": {"pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-p evil\"\n"}}, "runner"),
    ({"files": {"setup.cfg": "[tool:pytest]\n"}}, "runner"),
    ({"files": {"sitecustomize.py": "x = 1\n"}}, "runner"),
    ({"files": {"evil.pth": "import os\n"}}, "runner"),
    ({"files": {"TESTS/TEST_CALC.PY": "def test_add():\n    pass\n"}}, "test file"),
    ({"files": {"Conftest.py": "x = 1\n"}}, "test file"),
])
def test_a_patch_or_request_that_cannot_be_run_safely_is_refused(root, params, why):
    out = _run(**params)
    assert out["ok"] is False and why in out["error"]
    assert (root / bench.REPOS / "v1" / "calc" / "calc.py").read_text(encoding="utf-8") == BUGGY


def test_without_a_bound_workspace_root_only_the_probe_runs(monkeypatch):
    monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
    assert "workspace" in _run()["error"]
    assert bench.run("run_tests", {"repo": bench.PROBE, "tests": [bench.PROBE_TEST]})["result"]["passed"] is True
    assert "venture" in bench.run("run_tests", {"repo": "calc", "tests": NODES})["error"]  # no venture: probe only


def test_an_unknown_op_is_refused():
    assert bench.run("delete_repo", {})["ok"] is False


def test_pytest_is_declared_as_a_program_the_person_installs_in_the_python_running_g6():
    import sys

    import g6.workforce
    from g6.core.outside import detect

    found, = [p for p in g6.workforce.__g6_outside__ if p.name == "pytest"]
    assert found.command == sys.executable and "import pytest" in " ".join(found.version_args)   # imports, runs none
    assert detect(found).found is bench.ready()   # the declaration finds what the bench itself needs


# --- the native capability --------------------------------------------------------------------------


@pytest.fixture
def caps(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    return wcap.Capabilities(vstore.for_owner("alice"), contracts=wcap.load_contracts(), registry=_NoRegistry(),
                             authority=service._census_authority)  # the library's static census table


class _NoRegistry:
    """The bench is not in the registry: nothing here may be asked about it."""

    def list_all(self):
        return []

    def get_class(self, name):
        raise AssertionError("the registry was asked about a native capability")

    def describe(self, name):
        raise AssertionError("the registry was asked about a native capability")


def test_the_bench_is_a_declared_model_free_read_capability():
    contract = wcap.load_contracts()[(bench.COMPONENT, "run_tests")]
    assert contract.model_free is True and contract.side_effect == "read"
    assert contract.expect_field == "passed"


def test_the_bench_climbs_the_ladder_without_the_registry_and_probes_through_the_door(caps):
    assert caps.readiness(bench.COMPONENT, "run_tests").level == "deps_installed"
    found = caps.probe(bench.COMPONENT, "run_tests", wcall.prober(caps).reply, actor="code")
    assert found.level == "probe_passed", found.reason


def test_the_door_routes_the_bench_and_never_the_registry(root, monkeypatch):
    assert not hasattr(wcall, "invoke_component")  # the library has no registry door to call
    contracts = wcap.load_contracts()

    def contract(component, op):
        return contracts.get((component, op))

    worker = ww.form_worker("verifier", ceiling=ceiling_for({"budget_cents": 0}), contracts=contract,
                            allowed=((bench.COMPONENT, "run_tests"),))
    door = wcall.Door(worker, contracts=contract, run_id="r1", invoke=wcall.venture_invoke("v1"))
    result = door.call(bench.COMPONENT, "run_tests", {"repo": "calc", "tests": NODES, "files": {"calc.py": FIXED}})
    assert result.outcome.state == wc.QUALIFIED and result.model_calls == 0
    assert wc.answer(result.reply)["passed"] is True


# --- Codex review of V1: the runner, the environment, the original and the owner --------------------


def test_a_pytest_module_in_the_repository_cannot_stand_in_for_the_runner(root):
    """The owner's repository (or anything copied with it) holding a pytest.py that prints a pass: the bench
    imports the real pytest before the repository is on the path, so the failing test still fails."""
    forged = "print('1 passed')\nraise SystemExit(0)\n"
    (root / bench.REPOS / "v1" / "calc" / "pytest.py").write_text(forged, encoding="utf-8")
    assert _run()["result"]["tests"][NODES[0]] is False


def test_the_tests_run_without_the_servers_environment(root):
    # Only the paid-model block is G6's in the child: no workspace root, nothing else of the server's. A confined
    # child also carries G6's own confinement marker (confine.CONFINED_ENV), which is not the server's environment.
    probe = ("import os\nassert {k for k in os.environ if k.startswith('G6_')} <= "
             f"{{'G6_LLM_BACKEND_OVERRIDE', {confine.CONFINED_ENV!r}}}, sorted(os.environ)\n")
    out = _run(files={"calc.py": probe + FIXED})
    assert out["result"]["passed"] is True, out


def test_patched_code_that_changes_the_original_repository_is_caught(root, monkeypatch):
    # Unconfined, so the write lands and the bench's comparison after the run is what catches it; confined, the
    # operating system refuses the write (test_workforce_isolation).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    original = root / bench.REPOS / "v1" / "calc" / "calc.py"
    attack = f"from pathlib import Path\nPath({str(original)!r}).write_text('tampered', encoding='utf-8')\n"
    out = _run(files={"calc.py": attack + FIXED})
    assert out["ok"] is False and "original repository changed" in out["error"]


def test_another_ventures_repository_is_not_found(root):
    out = bench.run("run_tests", {"repo": "calc", "tests": NODES}, venture="v2")
    assert out["ok"] is False and "no repository" in out["error"]


# --- Gate W's authority ladder (plan 2026-10-05-gate-w-authority-ladder, Task 10): a property over a family -------
INC = "def inc(x):\n    return x + {k}\n"


def _inc_repo(root, k: int) -> str:
    """A one-function repository whose test expects inc(2) == 2 + k, with the code off by one."""
    name = f"inc{k}"
    repo = root / bench.REPOS / "v1" / name
    (repo / "tests").mkdir(parents=True)
    (repo / "inc.py").write_text(INC.format(k=k + 1), encoding="utf-8")
    (repo / "tests" / "test_inc.py").write_text(f"from inc import inc\n\n\ndef test_inc():\n    assert inc(2) == {2 + k}\n",
                                                encoding="utf-8")
    return name


@pytest.mark.parametrize("k", [0, 1, 7])
def test_run_tests_property_the_right_patch_passes_and_an_off_by_one_never_does(root, k):
    name, node = _inc_repo(root, k), ["tests/test_inc.py::test_inc"]

    def run(files=None) -> bool:
        params = {"repo": name, "tests": node, **({"files": files} if files else {})}
        return bench.run("run_tests", params, venture="v1")["result"]["passed"]

    assert (run(), run({"inc.py": INC.format(k=k)}), run({"inc.py": INC.format(k=k - 1)})) == (False, True, False)
