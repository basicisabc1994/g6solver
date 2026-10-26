# SPDX-License-Identifier: Apache-2.0
"""g6.csf composition and rollback (audit 012: CSF-010, CSF-016, CSF-011), with survival controls for every gate.

The archive cannot run here; each regression test says in its docstring what the archive did. Each ``test_null_*``
puts that behaviour back in memory (monkeypatching only) and shows the regression test fails. Git runs only inside a
fresh repository each test creates under ``tmp_path``.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from g6.csf import composition, rollback
from g6.csf.rollback import GIT_FAILED, GIT_OUTSIDE_ROOT, GIT_RESET_DISABLED, GIT_UNAVAILABLE, GitCheckpoint
from g6.csf.schema import EPSILON_INVALID, INPUT_INVALID, CSFError, CSFInputError
from g6.invariants import check_envelope


def _fails(test, *args) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


# --- CSF-010: Fréchet by default, independence only when asserted -----------------------------------------------------

def _archive_theorem1(eps1, eps2, *, assume_independent=False, rho=0.0):
    """The archive's Theorem 1 (composition.py:66-77): out-of-range epsilon only noted, rho clamped to [0, 1]."""
    rho = max(0.0, min(1.0, rho))
    success = max(0.0, min(1.0, (1.0 - eps1) * (1.0 - eps2) - rho * min(eps1, eps2)))
    return 1.0 - success


def test_csf010_disjoint_failures_are_within_the_default_bound() -> None:
    """Archive: e1 = e2 = 0.1, rho = 0, target 0.19 gave composed epsilon 0.19 and PASS; with disjoint failures the
    true failure probability is 0.2."""
    bound = composition.compose_bound(0.1, 0.1)
    assert bound == pytest.approx(0.2) and not bound <= 0.19
    # Any joint law of two failure events with P(F1) <= e1 and P(F2) <= e2: P(F1 or F2) never exceeds the bound.
    rng = random.Random(5)
    for _ in range(2000):
        e1, e2 = rng.random(), rng.random()
        p1, p2 = rng.uniform(0, e1), rng.uniform(0, e2)
        both = rng.uniform(max(0.0, p1 + p2 - 1.0), min(p1, p2))
        assert p1 + p2 - both <= composition.compose_bound(e1, e2) + 1e-12


def test_csf010_out_of_range_epsilon_is_refused() -> None:
    """Archive: e1 = -1 clamped the success bound to 1.0, so the composition read epsilon 0 and passed."""
    for bad in (-1.0, 1.5, float("nan"), True, "0.1", None):
        with pytest.raises(CSFInputError) as caught:
            composition.compose_bound(bad, 0.1)
        assert caught.value.code == EPSILON_INVALID
    with pytest.raises(CSFInputError):
        composition.compose_bound(0.1, 0.1, assume_independent="yes")  # type: ignore[arg-type]


def test_null_csf010(monkeypatch) -> None:
    monkeypatch.setattr(composition, "compose_bound", _archive_theorem1)
    _fails(test_csf010_disjoint_failures_are_within_the_default_bound)
    _fails(test_csf010_out_of_range_epsilon_is_refused)


def test_independence_is_used_only_when_asserted_and_is_exact_then() -> None:
    """Survival control: with independence asserted the value is 1 - (1 - e1)(1 - e2), exact for independent events."""
    assert composition.compose_bound(0.1, 0.1, assume_independent=True) == pytest.approx(0.19)
    assert composition.compose_bound(0.0, 0.0) == 0.0 and composition.compose_bound(0.7, 0.6) == 1.0


# --- CSF-016: one operation vocabulary, unknown fails closed, no constant delay ---------------------------------------

_ARCHIVE_IRREVERSIBLE = frozenset({"external_api_call", "email_send", "payment_process", "database_drop",
                                   "network_broadcast", "process_kill"})


def _archive_classify(op: str) -> str:
    """The archive's classification (rollback.py:27-34, 206-214): irreversible only if in IRREVERSIBLE_OPS."""
    return "irreversible" if op in _ARCHIVE_IRREVERSIBLE else "reversible"


def test_csf016_external_api_is_irreversible_and_unknown_operations_fail_closed() -> None:
    """Archive: IRREVERSIBLE_OPS said ``external_api_call`` and the hazard tables said ``external_api``, so
    ``external_api`` was reversible; any name it did not know (``delete_production_db``) was reversible too."""
    assert rollback.classify_operation("external_api") == "irreversible"
    result = rollback.rollback_feasibility(["file_write", "external_api", "delete_production_db"], 3)
    assert result.feasible is False and result.completion_state == "blocked-escalated"
    assert result.irreversible_ops == ("external_api",) and result.unknown_ops == ("delete_production_db",)
    assert result.reversible_steps == (0,)


def test_csf016_feasibility_has_no_constant_rollback_delay() -> None:
    """Archive: ``rollback_delay = interval * 0.1`` ("assume ~0.1 s per step"), never measured, decided feasibility."""
    result = rollback.rollback_feasibility(["file_write"], 1)
    assert not any(hasattr(result, name) for name in ("rollback_delay_sec", "max_delay_sec"))


def test_null_csf016(monkeypatch) -> None:
    monkeypatch.setattr(rollback, "classify_operation", _archive_classify)
    _fails(test_csf016_external_api_is_irreversible_and_unknown_operations_fail_closed)
    monkeypatch.setattr(rollback.RollbackFeasibility, "rollback_delay_sec", 0.1, raising=False)
    _fails(test_csf016_feasibility_has_no_constant_rollback_delay)


def test_a_reversible_plan_is_still_feasible() -> None:
    """Survival control: a gate that refused every plan would fail this."""
    result = rollback.rollback_feasibility(["file_read", "file_write", "compute", "llm_call"], 20, request_id="r")
    assert result.feasible is True and result.completion_state == "qualified-draft"
    assert result.reversible_steps == tuple(range(20))
    assert result.snapshot_interval == 2 and result.snapshot_steps == (*range(0, 20, 2), 19)
    envelope = result.to_envelope()
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == "G6_E_CSF_STATIC_CLASSIFICATION"
    assert envelope["request_id"] == "r" and envelope["evidence"][0]["measured"] is False


def test_infeasible_plans_are_valid_blocked_envelopes_and_bad_input_is_refused() -> None:
    envelope = rollback.rollback_feasibility(["email_send"], 1).to_envelope()
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == "G6_E_CSF_ROLLBACK_INFEASIBLE"
    for ops, horizon in ((["a"], 0), (["a"], True), (["a", "b"], 1), ("file_write", 10), ([""], 1), ([3], 1)):
        with pytest.raises(CSFInputError) as caught:
            rollback.rollback_feasibility(ops, horizon)  # type: ignore[arg-type]
        assert caught.value.code == INPUT_INVALID


# --- CSF-011: GitCheckpoint on tmp_path repositories only -------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _make_repo(base: Path) -> Path:
    """A fresh repository at ``base / "repo"`` with one commit of ``a.txt`` ("one"), isolated from global hooks and
    signing."""
    root = base / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    for key, value in (("user.name", "g6 test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"),
                       ("core.hooksPath", str(base / "no-hooks")), ("core.autocrlf", "false")):
        _git(root, "config", key, value)
    (root / "a.txt").write_text("one", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "first")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path)


@needs_git
def test_csf011_reset_needs_allow_reset_on_the_object(repo: Path) -> None:
    """Archive: ``GitRollbackManager.rollback`` ran ``git reset --hard`` for any in-process caller; the break-glass
    flag was checked only in one MCP wrapper (csf_mcp/server.py:34-36)."""
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two", encoding="utf-8")
    second = GitCheckpoint(repo).snapshot("second")
    with pytest.raises(CSFError) as caught:
        GitCheckpoint(repo).reset(first)
    assert caught.value.code == GIT_RESET_DISABLED
    assert _git(repo, "rev-parse", "HEAD") == second and (repo / "a.txt").read_text(encoding="utf-8") == "two"
    with pytest.raises(CSFInputError):
        GitCheckpoint(repo, allow_reset="yes")  # type: ignore[arg-type]


@needs_git
def test_an_allowed_reset_still_restores_the_checkpoint(repo: Path) -> None:
    """Survival control: with ``allow_reset=True`` the reset happens."""
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two", encoding="utf-8")
    checkpoint = GitCheckpoint(repo, allow_reset=True)
    checkpoint.snapshot()
    assert checkpoint.reset(first[:12]) == first == checkpoint.head()
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one"
    for bad in ("--hard", "HEAD~1", "deadbeef", 7):
        with pytest.raises(CSFInputError):
            checkpoint.reset(bad)  # type: ignore[arg-type]


@needs_git
def test_csf011_snapshot_commits_tracked_files_only(repo: Path) -> None:
    """Archive: ``snapshot`` ran ``git add -A``, committing every untracked file, an unignored ``.env`` included."""
    (repo / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (repo / "a.txt").write_text("two", encoding="utf-8")
    sha = GitCheckpoint(repo).snapshot("tracked only")
    assert _git(repo, "ls-tree", "-r", "--name-only", sha).split() == ["a.txt"]
    assert _git(repo, "show", f"{sha}:a.txt") == "two" and (repo / ".env").exists()
    assert GitCheckpoint(repo).snapshot() == sha  # nothing tracked changed: HEAD, no new commit


def _archive_stage(self) -> None:
    self._git("add", "-A")


@needs_git
def test_csf011_a_root_that_is_not_the_repository_top_level_is_refused(repo: Path) -> None:
    """Archive: the handlers took any ``repo_path`` an MCP client sent. Here a root inside another repository would act
    on that whole repository, so it is refused and nothing in the enclosing repository changes."""
    head = _git(repo, "rev-parse", "HEAD")
    sub = repo / "sub"
    sub.mkdir()
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    for call in (lambda: GitCheckpoint(sub).snapshot(), lambda: GitCheckpoint(sub, allow_reset=True).reset(head)):
        with pytest.raises(CSFError) as caught:
            call()
        assert caught.value.code == GIT_OUTSIDE_ROOT
    assert _git(repo, "rev-parse", "HEAD") == head and (repo / "a.txt").read_text(encoding="utf-8") == "changed"
    plain = repo.parent / "plain"
    plain.mkdir()
    with pytest.raises(CSFError) as caught:
        GitCheckpoint(plain).head()
    assert caught.value.code == GIT_OUTSIDE_ROOT


def _archive_git(self, *args: str, check: bool = True):
    """``git -C <path>`` with no boundary, as GitPython's ``Repo(path)`` callers in the archive got any repository."""
    done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False,
                          env=dict(os.environ))
    if check and done.returncode != 0:
        raise CSFError(GIT_FAILED, done.stderr)
    return done


@needs_git
def test_null_csf011(monkeypatch, tmp_path: Path) -> None:
    roots = {}
    for name in ("gate", "stage", "root"):
        base = tmp_path / name
        base.mkdir()
        roots[name] = _make_repo(base)  # a fresh repository for each null check
    monkeypatch.setattr(GitCheckpoint, "_require_reset_allowed", lambda self: None)
    _fails(test_csf011_reset_needs_allow_reset_on_the_object, roots["gate"])
    monkeypatch.setattr(GitCheckpoint, "_stage", _archive_stage)
    _fails(test_csf011_snapshot_commits_tracked_files_only, roots["stage"])
    monkeypatch.setattr(GitCheckpoint, "_require_root_is_toplevel", lambda self: None)
    monkeypatch.setattr(GitCheckpoint, "_git", _archive_git)
    _fails(test_csf011_a_root_that_is_not_the_repository_top_level_is_refused, roots["root"])


@needs_git
def test_missing_git_is_an_honest_refusal(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(rollback, "find_program", lambda name: None)
    with pytest.raises(CSFError) as caught:
        GitCheckpoint(repo).snapshot()
    assert caught.value.code == GIT_UNAVAILABLE


def test_a_root_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CSFInputError):
        GitCheckpoint(tmp_path / "missing")
