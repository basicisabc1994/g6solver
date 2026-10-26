# SPDX-License-Identifier: Apache-2.0
"""g6.code.swe_diagnostics: reads of a repository for a coding agent over folders the person declares, Python by
syntax tree and JavaScript, TypeScript and Go by pattern (marked so), capped and said; and patch_check through
g6.workforce.repo_tests on a local git repository made here. Nothing reaches the network."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.code.swe_diagnostics as package
from g6.code import _roots
from g6.code.swe_diagnostics import SweDiagnosticsBlock, SweDiagnosticsInput, read
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply

CART = "class Cart:\n    def __init__(self):\n        self.items = []\n\n    def total(self):\n        return sum(self.items)\n"
TESTS = '''import pytest
from shop.cart import Cart


@pytest.fixture
def cart():
    return Cart()


@pytest.mark.parametrize("n", [1, 2])
def test_total_starts_at_zero(cart, n):
    """A new cart is empty."""
    assert cart.total() == 0


class TestCart:
    def setup_method(self):
        self.cart = Cart()

    def test_items(self):
        self.assertEqual(self.cart.items, [])
'''
JS = "describe('cart', () => {\n  it('totals', () => {\n    expect(total([1, 2])).toBe(3);\n  });\n});\n"
GO = "package cart\n\nfunc TestTotal(t *testing.T) {\n\tassert.Equal(t, 3, Total())\n}\n"


def make_repo(root: Path) -> Path:
    repo = root / "shop"
    (repo / "src" / "shop").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "web").mkdir()
    (repo / "node_modules" / "left").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "shop"\nrequires-python = ">=3.11"\n\n'
                                         '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (repo / "README.md").write_text("# Shop\n", encoding="utf-8")
    (repo / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "shop" / "cart.py").write_text(CART, encoding="utf-8")
    (repo / "tests" / "test_cart.py").write_text(TESTS, encoding="utf-8")
    (repo / "web" / "cart.test.js").write_text(JS, encoding="utf-8")
    (repo / "node_modules" / "left" / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    return repo


def block(root: Path | None) -> SweDiagnosticsBlock:
    return SweDiagnosticsBlock(roots=_roots.Roots((root.resolve(),), "test") if root else _roots.Roots((), "none"))


def run(op: str, root: Path | None = None, **params) -> dict:
    return block(root).infer(SweDiagnosticsInput(op=op, **params)).unwrap()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=G6 test", "-c", "user.email=test@example.org", "-c", "core.hooksPath=",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True, timeout=60)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed here")


# --- D1: an agent learns how an unfamiliar repository builds, tests and what its change touches -----------------------

def test_d1_the_test_runner_a_repository_declares_only_in_pyproject_is_found(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    found = run("detect_build_env", tmp_path, path=str(repo))
    assert found["test_runner"] == "pytest" and found["test_command"] == "python -m pytest"
    assert found["language_versions"] == {"python": ">=3.11"} and found["detected_files"] == ["pyproject.toml"]
    assert found["completion_state"] == "qualified-draft"


@needs_git
def test_d1_patch_check_names_changed_code_that_does_not_parse_and_a_touched_runner_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    shutil.rmtree(repo / "node_modules")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    (repo / "src" / "shop" / "cart.py").write_text(CART.replace("def total(self):", "def total(self)"),
                                                   encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("collect_ignore = ['test_cart.py']\n", encoding="utf-8")
    out = run("patch_check", tmp_path, repo=str(repo))
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["severity"] == "red"
    assert [b["file"] for b in out["does_not_parse"]] == ["src/shop/cart.py"]
    assert out["runner_files"] == ["tests/conftest.py"] and "tests/test_cart.py" in out["tests_to_run"]
    assert out["evidence"][0]["ran_tests"] is False


# --- the archive's findings -------------------------------------------------------------------------------------------

@needs_git
def test_swe_diagnostics_01_no_op_marks_an_observation_verified(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    replies = [run("repo_map", tmp_path, path=str(repo)), run("detect_build_env", tmp_path, path=str(repo)),
               run("test_analyze", tmp_path, path=str(repo / "tests" / "test_cart.py")),
               run("code_summarize", tmp_path, path=str(repo / "src" / "shop" / "cart.py")),
               run("patch_check", tmp_path, repo=str(repo))]
    assert {r["completion_state"] for r in replies} == {"qualified-draft"}
    assert all(r["warning_card"]["verified"] is False and "not a checked claim" in r["warning_card"]["message"]
               for r in replies[:4])


def test_swe_diagnostics_02_no_outside_module_runs_whatever_g6_project_root_says(tmp_path: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    tools = tmp_path / "planted" / "benchmarks" / "swe_bench_pro" / "tools"
    tools.mkdir(parents=True)
    (tools / "repo_map.py").write_text("open(__file__ + '.RAN', 'w').close()\n", encoding="utf-8")
    monkeypatch.setenv("G6_PROJECT_ROOT", str(tmp_path / "planted"))
    repo = make_repo(tmp_path)
    run("repo_map", tmp_path, path=str(repo))
    assert not (tools / "repo_map.py.RAN").exists()
    source = "".join(p.read_text(encoding="utf-8") for p in Path(package.__file__).parent.glob("*.py"))
    assert "exec_module" not in source and "G6_PROJECT_ROOT" not in source


@needs_git
def test_swe_diagnostics_03_patch_check_takes_an_explicit_repository_under_a_root(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "allowed")
    git(repo, "init", "-q")
    assert run("patch_check", tmp_path / "allowed")["code"] == "G6_E_REPO_INPUT"
    assert run("patch_check", tmp_path / "elsewhere_root", repo=str(repo))["code"] == "G6_E_CODE_OUTSIDE_ROOT"
    said = str(get_registry().invoke("swe_diagnostics", "patch_check", {"repo": str(repo)},
                                     block_sensitive=True).failure())
    assert said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_swe_diagnostics_04_no_benchmark_material_ships() -> None:
    folder = Path(package.__file__).parent
    assert {p.suffix for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts} <= {".py", ".md"}
    assert not [p for p in folder.rglob("*") if "swe_bench" in p.name or "benchmarks" in p.parts]


# --- the reads --------------------------------------------------------------------------------------------------------

def test_repo_map_skips_installed_code_and_says_what_it_found(tmp_path: Path) -> None:
    out = run("repo_map", tmp_path, path=str(make_repo(tmp_path)))
    assert out["languages"] == {"python": 3, "javascript": 1} and out["build_systems"] == ["python-pyproject"]
    assert out["key_files"] == ["README.md", "pyproject.toml"] and "node_modules" not in json.dumps(out["folders"])
    assert out["read"]["files"] == 6 and out["read"]["truncated"] is False


def test_repo_map_says_when_its_cap_stopped_the_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read, "MAX_FILES", 3)
    out = run("repo_map", tmp_path, path=str(make_repo(tmp_path)))
    assert out["read"]["files"] == 3 and out["read"]["truncated"] is True
    assert out["read"]["truncated_reason"] == "file cap"
    assert "3 file(s)" in out["warning_card"]["message"]
    assert "truncated (file cap)" in out["warning_card"]["message"]


def test_python_tests_are_read_from_the_tree_and_other_languages_by_marked_pattern(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    python = run("test_analyze", tmp_path, path=str(repo / "tests" / "test_cart.py"))
    assert python["method"] == "ast" and python["total_tests"] == 2 and python["fixtures"][0]["name"] == "cart"
    assert python["tests"][0]["decorators"] == ["pytest.mark.parametrize"]
    assert python["tests"][0]["assertions"][0]["test"] == "cart.total() == 0" and "docstring" not in python["tests"][0]
    assert python["classes"][0]["setup"] == ["setup_method"]
    js = run("test_analyze", source=JS, name="cart.test.js")
    go = run("test_analyze", source=GO, name="cart_test.go")
    assert js["method"] == go["method"] == "pattern" and "not parsed" in js["note"]
    assert js["total_tests"] == 1 and js["assertions"][0]["matcher"] == "toBe" and go["tests"][0]["name"] == "TestTotal"


def test_code_summarize_reads_python_by_tree_and_go_by_pattern() -> None:
    py = run("code_summarize", source=CART, name="cart.py")
    assert py["classes"][0]["methods"] == ["__init__", "total"] and py["method"] == "ast"
    go = run("code_summarize", source=GO, name="cart.go")
    assert go["method"] == "pattern" and go["symbols"][0]["name"] == "TestTotal"
    broken = run("code_summarize", source="def f(:\n", name="f.py")
    assert broken["parses"] is False and broken["completion_state"] == "qualified-draft"


def test_a_link_out_of_the_root_is_not_followed(tmp_path: Path) -> None:
    allowed, secret = tmp_path / "allowed", tmp_path / "secret"
    repo = make_repo(allowed)
    secret.mkdir()
    (secret / "keys.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
    try:
        (repo / "linked").symlink_to(secret, target_is_directory=True)
    except OSError:
        try:
            import _winapi

            _winapi.CreateJunction(str(secret), str(repo / "linked"))
        except (ImportError, OSError):
            pytest.skip("this machine lets a test make neither a symbolic link nor a junction")
    out = run("repo_map", allowed, path=str(repo))
    assert "linked" not in out["folders"] and out["read"]["files"] == 6
    assert run("code_summarize", allowed, path=str(repo / "linked" / "keys.py"))["code"] == "G6_E_CODE_OUTSIDE_ROOT"


# --- the gates --------------------------------------------------------------------------------------------------------

def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for field in ("approved", "verified", "reviewed", "trusted"):
        with pytest.raises(ValidationError):
            SweDiagnosticsInput(op="repo_map", path="C:/x", **{field: True})


def test_g4_what_cannot_be_read_is_said_never_skipped(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "package.json").write_text("{not json", encoding="utf-8")
    found = run("detect_build_env", tmp_path, path=str(repo))
    assert found["unreadable"] == ["package.json"] and "Could not read: package.json" in found["warning_card"]["message"]
    assert run("repo_map", path=str(repo))["code"] == "G6_E_CODE_NO_ROOT"
    assert run("test_analyze", tmp_path, path=str(repo / "README.md"))["completion_state"] == "blocked-escalated"


def test_g5_patch_check_runs_git_so_it_alone_is_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ("patch_check",)
    assert [p.name for p in package.__g6_outside__] == ["git"]


# --- D2, D6 -----------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("blind_repo_change",)
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.code.swe_diagnostics" not in FailureModeRegistry.planned()


def test_d6_code_summarize_round_trips_over_mcp_and_describe_serves_the_skill() -> None:
    reply = invoke_reply("swe_diagnostics", "code_summarize", json.dumps({"source": CART, "name": "cart.py"}))
    assert reply["ok"] and reply["result"]["classes"][0]["name"] == "Cart"
    assert describe_reply("swe_diagnostics")["skill"].startswith("---\nname: g6-swe-diagnostics")
