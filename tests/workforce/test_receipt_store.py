# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Test receipts (plan Step 2, amendment A6): only G6's own confined test run writes one, into an append-only store
under G6_HOME, and ``lookup`` gives it back only for the artifact the run began on.

Pinned with real pytest and git subprocesses on temp repositories. Most runs here start through a stand-in boundary
(``fake_boundary``: a backend that starts the child as usual), because what is under test is the receipt's plumbing:
the operating-system boundary itself is pinned by the bubblewrap test, which runs where bubblewrap can confine a run
(CI's Linux jobs, or a Linux container). Offline.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib
import inspect
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core import confine
from g6.core.ledger import ChainLedger, digest
from g6.core.registry import get_registry
from g6.core.storage import state_path
from g6.workforce import repo_tests as rt
from g6.workforce import receipt_store

# The repository of tests/workforce/test_repo_tests_run.py: calc.py with a bug in add(), and its two tests.
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
CALC_TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
              "def test_sub():\n    assert sub(5, 3) == 2\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}
TESTS = ["tests/test_calc.py"]
IDS = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def _write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A G6_HOME of this test's own: the store starts empty."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "calc"
    (repo / "tests").mkdir(parents=True)
    _write(repo, "calc.py", BUGGY)
    _write(repo, "tests/test_calc.py", CALC_TESTS)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


class _Identity:
    def close(self) -> str:
        return ""


class _FakeBoundary:
    """A backend that 'confines' a run by starting it as usual: the receipt's plumbing is under test, not the boundary."""

    name = "fake-boundary"

    def ready(self, python, *, probe=True):
        return ""

    def started(self, python):
        return False

    def prepare(self, confinement, python):
        return _Identity(), ""

    def start(self, argv, identity, *, network=False, **options):
        return subprocess.Popen(argv, **options)


@pytest.fixture
def fake_boundary(monkeypatch):
    backend = _FakeBoundary()
    monkeypatch.setattr(confine, "_select", lambda: (backend, ""))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    return backend


def _store():
    return state_path("evidence", "test_receipts.jsonl")


def _entries() -> list[dict]:
    return ChainLedger(_store()).entries() if _store().exists() else []


def _a_receipt_binds_the_run(repo, backend: str) -> None:
    _write(repo, "calc.py", FIXED)
    artifact = digest(rt.snapshot(repo))   # the patch review's artifact_digest, computed the same way
    found = rt.run(repo, TESTS, receipt=True)
    assert (found["verdict"], found["isolation"]["backend"]) == (rt.PASSED, backend), found
    [entry] = _entries()
    body = entry["body"]
    assert entry["kind"] == "test-receipt" and entry["id"] == found["receipt"] == receipt_store.receipt_id(body)
    assert body["schema"] == "g6.test_receipt/1" and len(body["execution_id"]) == 32
    int(body["execution_id"], 16)
    assert body["g6_version"] == g6.__version__
    assert body["source_before"] == body["source_after"] == artifact
    assert body["base_commit"] == _git(repo, "rev-parse", "HEAD").strip()
    began, ended = datetime.fromisoformat(body["started_at"]), datetime.fromisoformat(body["finished_at"])
    assert began.utcoffset() == ended.utcoffset() == timedelta(0) and began <= ended
    with open(found["python"], "rb") as handle:
        sha = hashlib.sha256(handle.read()).hexdigest()
    assert body["executable"] == {"path": found["python"], "version": platform.python_version(), "sha256": sha}
    assert body["tests"] == {"requested": TESTS, "collected": IDS, "deselected": 0, "omitted": []}
    assert body["command"] == found["command"] and body["isolation"] == found["isolation"]
    assert body["result"] == {"verdict": rt.PASSED, "passed": 2, "failed": 0, "not_passed": 0, "total": 2,
                              "failed_ids": [], "completed": True, "degraded": False}
    assert body["ignored_inputs"] == {}
    # lookup gives it back for the tree the run began on, and for nothing else
    assert receipt_store.lookup(found["receipt"], artifact_digest=artifact) == body
    assert receipt_store.lookup(found["receipt"], artifact_digest="0" * 64) is None
    assert receipt_store.lookup(found["receipt"], artifact_digest="") is None


def test_a_confined_run_with_receipt_records_one_receipt_binding_the_run(repo, fake_boundary):
    # Removing the receipt branch from repo_tests._run, or a field from its body, fails here.
    _a_receipt_binds_the_run(repo, fake_boundary.name)


def test_a_run_confined_by_bubblewrap_records_its_receipt(repo, bwrap_backend):
    _a_receipt_binds_the_run(repo, confine.BUBBLEWRAP)


@pytest.mark.usefixtures("unconfined")
def test_an_unconfined_run_gets_no_receipt_even_where_the_person_allowed_it(repo):
    # Fails if a receipt is written for a run that only the person's opt-in let go ahead.
    found = rt.run(repo, TESTS, receipt=True)
    assert found["isolation"]["backend"] is None and found["isolation"]["allowed_by"]
    assert found["receipt"] == "" and _entries() == []


def test_a_run_that_could_not_start_gets_no_receipt(repo, fake_boundary, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no child starts in this test")

    monkeypatch.setattr(fake_boundary, "start", refuse)
    found = rt.run(repo, TESTS, receipt=True)
    assert found["verdict"] == "could_not_start" and found["receipt"] == "" and _entries() == []


def test_a_confined_run_without_receipt_records_nothing(repo, fake_boundary):
    found = rt.run(repo, TESTS)
    assert found["isolation"]["backend"] == fake_boundary.name
    assert found["receipt"] == "" and _entries() == []


def test_a_mutant_run_is_never_given_a_receipt(repo, fake_boundary):
    with pytest.raises(ValueError):
        rt.run(repo, TESTS, mutant=("calc.py", FIXED), receipt=True)
    assert _entries() == []


def test_a_failing_run_records_its_failure(repo, fake_boundary):
    found = rt.run(repo, TESTS, receipt=True)
    [entry] = _entries()
    assert found["verdict"] == rt.FAILED and entry["id"] == found["receipt"]
    assert entry["body"]["result"]["verdict"] == rt.FAILED
    assert entry["body"]["result"]["failed_ids"] == ["tests/test_calc.py::test_add"]


def test_an_edited_receipt_is_never_given_back(repo, fake_boundary):
    artifact = digest(rt.snapshot(repo))
    found = rt.run(repo, TESTS, receipt=True)   # the buggy calc.py: this run failed
    ident, text = found["receipt"], _store().read_text(encoding="utf-8")
    assert receipt_store.lookup(ident, artifact_digest=artifact)["result"]["verdict"] == rt.FAILED
    # 1. the body edited in place: the chain no longer checks out
    _store().write_text(text.replace('"verdict": "failed"', '"verdict": "passed"'), encoding="utf-8")
    assert _store().read_text(encoding="utf-8") != text
    assert receipt_store.lookup(ident, artifact_digest=artifact) is None
    # 2. the body edited and the chain's hashes redone to match: the body no longer hashes to its id
    entry = json.loads(text)
    entry["body"]["result"]["verdict"] = rt.PASSED
    entry["body_sha256"] = digest(entry["body"])
    entry["entry_sha256"] = digest({key: entry[key] for key in ("seq", "kind", "id", "body_sha256", "prev")})
    _store().write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    assert ChainLedger.audit(_store()) == ()
    assert receipt_store.lookup(ident, artifact_digest=artifact) is None


def test_lookup_answers_none_for_a_missing_or_unreadable_store():
    ident = "ab" * 32
    assert receipt_store.lookup(ident, artifact_digest="cd" * 32) is None   # no store at all
    assert receipt_store.check(ident, artifact_digest="cd" * 32)[0] == "unknown"
    assert not _store().exists()                                            # and asking made none
    _store().parent.mkdir(parents=True)
    _store().write_bytes(b"\xff not a ledger\n")
    assert receipt_store.lookup(ident, artifact_digest="cd" * 32) is None
    assert receipt_store.check(ident, artifact_digest="cd" * 32)[0] == "store_unreadable"


def test_a_torn_store_says_so_and_a_run_says_how_to_recover(repo, fake_boundary):
    """M1: a torn last line leaves the store unreadable: checks say "store_unreadable", and a run that cannot record
    its receipt says to move the file aside, after which its old receipts no longer count."""
    _write(repo, "calc.py", FIXED)
    artifact = digest(rt.snapshot(repo))
    first = rt.run(repo, TESTS, receipt=True)
    _store().write_text(_store().read_text(encoding="utf-8") + '{"seq": 1, "kind": "test-re', encoding="utf-8")
    assert receipt_store.check(first["receipt"], artifact_digest=artifact) == ("store_unreadable", None)
    second = rt.run(repo, TESTS, receipt=True)
    assert second["receipt"] == "" and "move it aside" in second["receipt_error"]


@pytest.mark.parametrize(("ignored", "files"), [
    ("tests/conftest.py", {"tests/conftest.py": "def pytest_runtest_setup(item):\n    pass\n"}),
    ("pytest.ini", {"pytest.ini": "[pytest]\naddopts = -q\n"}),
    (".pytest.ini", {".pytest.ini": "[pytest]\n"}),
    ("pytest.toml", {"pytest.toml": "[pytest]\n"}),   # review C1
    (".pytest.toml", {".pytest.toml": "\n"}),
    ("setup.cfg", {"setup.cfg": "[tool:pytest]\naddopts = -q\n"}),
    ("tox.ini", {"tox.ini": "[pytest]\n"}),
    ("pyproject.toml", {"pyproject.toml": "[tool.pytest.ini_options]\n"}),
    ("hook.pth", {"hook.pth": "import os\n"}),
    ("sitecustomize.py", {"sitecustomize.py": "import os\n"}),
    ("usercustomize.py", {"usercustomize.py": "import os\n"}),
    ("tests/test_extra.py", {"tests/test_extra.py": "def test_x():\n    assert True\n"}),   # test-named
    ("extra_test.py", {"extra_test.py": "def test_x():\n    assert True\n"}),
    ("tests/calc.py", {"tests/calc.py": "def add(a, b):\n    return 5\n"}),   # shadows the tracked calc.py
    ("vendor/", {"vendor/calc/__init__.py": "\n"}),                           # a package that shadows it
    ("hidden/", {"hidden/conftest.py": "\n"}),                                # inside an ignored folder
])
def test_a_run_a_git_ignored_file_could_change_gets_no_receipt(repo, fake_boundary, ignored, files):
    """I2: git's view leaves ignored files out, so the receipt's digest could not show a file pytest loaded."""
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(ignored + "\n", encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        _write(repo, rel, text)
    found = rt.run(repo, TESTS, receipt=True)
    assert found["receipt"] == "" and _entries() == []
    assert found["ignored_loadable"] == sorted(files) and sorted(files)[0] in found["receipt_withheld"]


@pytest.mark.parametrize("files", [
    {".venv/lib/conftest.py": "\n"}, {"build/conftest.py": "\n"}, {"dist/x.pth": "\n"},
    {"calc.egg-info/conftest.py": "\n"}, {"__pycache__/conftest.py": "\n"}, {"notes.txt": "\n"},
])
def test_ignored_environments_caches_and_other_files_do_not_withhold_the_receipt(repo, fake_boundary, files):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("\n".join({rel.split("/")[0] + ("/" if "/" in rel else "")
                                                              for rel in files}) + "\n", encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        _write(repo, rel, text)
    found = rt.run(repo, TESTS, receipt=True)
    assert found["receipt"] and found.get("ignored_loadable", []) == [], found.get("receipt_withheld")
    assert found["ignored_inputs"] == {} and _entries()[0]["body"]["ignored_inputs"] == {}


def test_an_ignored_python_file_g6_does_not_judge_is_bound_into_the_receipt(repo, fake_boundary):
    """A generated ``_version.py`` (setuptools-scm) beside tracked modules shadows none of them: the receipt is still
    written, and binds the file's bytes, so its id changes with them."""
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("_version.py\ngenerated/\n", encoding="utf-8")
    _write(repo, "_version.py", "version = '1.0'\n")
    (repo / "generated").mkdir()
    _write(repo, "generated/data.py", "X = 1\n")
    found = rt.run(repo, TESTS, receipt=True)
    bound = {rel: hashlib.sha256((repo / rel).read_bytes()).hexdigest() for rel in ("_version.py", "generated/data.py")}
    assert found["receipt"] and found["ignored_inputs"] == bound and "ignored_loadable" not in found
    [entry] = _entries()
    assert entry["body"]["ignored_inputs"] == bound and entry["id"] == receipt_store.receipt_id(entry["body"])


def test_an_ignored_file_scan_that_reaches_its_limit_withholds_the_receipt(repo, fake_boundary, monkeypatch):
    monkeypatch.setattr(rt, "IGNORED_SCAN_LIMIT", 2)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("logs/\n", encoding="utf-8")
    (repo / "logs").mkdir()
    for index in range(5):
        _write(repo, f"logs/run{index}.log", "x\n")
    scan = rt.ignored_inputs(repo)
    assert (scan.loadable, scan.other, scan.complete) == ((), {}, False)
    ran = rt.run(repo, TESTS, receipt=True)
    assert ran["receipt"] == "" and "limit" in ran["receipt_withheld"]


def test_the_receipt_store_the_gate_imports_is_never_taken_for_a_test_file():
    # Fails if the store module is named the way G6's own test rule or pytest's default discovery reads a test.
    from g6.coding_agent_gate import coding_agent_gate_block as gate

    [name] = set(re.findall(r"from g6\.workforce import (\w+)", inspect.getsource(gate)))
    module = importlib.import_module(f"g6.workforce.{name}")
    assert getattr(rt, name) is module   # repo_tests writes through the same module the gate reads
    filename = Path(module.__file__).name
    assert not rt.is_test_file(f"src/g6/workforce/{filename}")
    assert not fnmatch.fnmatch(filename, "test_*.py") and not fnmatch.fnmatch(filename, "*_test.py")


def test_a_receipt_id_is_the_sha256_of_the_bodys_canonical_json():
    assert receipt_store.receipt_id({"b": 1, "a": [1, "x"]}) == receipt_store.receipt_id({"a": [1, "x"], "b": 1})
    assert receipt_store.receipt_id({"b": 1, "a": [1, "x"]}) == hashlib.sha256(b'{"a":[1,"x"],"b":1}').hexdigest()


def test_the_gate_counts_a_real_receipt_for_the_tree_its_host_bound_it_to_as_g6_observed(repo, fake_boundary):
    from g6.coding_agent_gate import CodingAgentGateBlock, CodingAgentGateInput

    _write(repo, "calc.py", FIXED)
    artifact = digest(rt.snapshot(repo))
    found = rt.run(repo, TESTS, receipt=True)
    params = {"op": "verify_answer", "task": "Fix add()", "answer": "Fixed add(); all tests pass.",
              "task_contract": "add(2, 3) == 5 and the existing tests stay green",
              "evidence": [{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": found["receipt"]}]}

    def gate(bound: str) -> dict:
        return CodingAgentGateBlock(receipt_artifact_digest=bound).infer(CodingAgentGateInput(**params)).unwrap()

    seen = gate(artifact)
    assert (seen["verdict"], seen["evidence_basis"]) == ("pass", "g6-observed"), seen
    assert [r["receipt_status"] for r in seen["receipts"]] == ["matched"]
    _write(repo, "calc.py", FIXED + "\n# changed after the run\n")
    later = gate(digest(rt.snapshot(repo)))
    assert later["evidence_basis"] == "caller-declared" and later["verdict"] == "revise"
    assert later["receipts"] == [{"receipt": found["receipt"], "receipt_status": "artifact_mismatch"}]
    door = get_registry().invoke("coding_agent_gate", "verify_answer", {k: v for k, v in params.items()
                                                                         if k != "op"}).unwrap()
    assert door["receipts"] == [{"receipt": found["receipt"], "receipt_status": "not_honoured_at_door"}]
