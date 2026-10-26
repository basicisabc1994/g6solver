# SPDX-License-Identifier: Apache-2.0
"""A coding mission's snapshot copies never follow a link (``g6.workforce.coding``): a repository, or a copy G6 made of
one, that holds a symbolic link or a Windows junction is refused before anything is copied
(``G6_E_WORKFORCE_REPOSITORY_LINKED``), whichever of the four copies would have made it, and the copy itself leaves out
a link that appears while it runs. Each copy path is pinned by a junction to a folder outside the repository, planted
after the checks that come before that copy, so the copy's own guard is what is tested. Every link and file here is made
inside the test's own temporary folder; nothing is run but the repository's own tests, as the rig always runs them."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6.workforce import bench, coding, service, truth

_objective_spec = importlib.util.spec_from_file_location("snapshot_links_objective_fixture",
                                                         Path(__file__).with_name("test_coding_objective.py"))
_objective = importlib.util.module_from_spec(_objective_spec)
_objective_spec.loader.exec_module(_objective)
objective_rig = _objective.objective_rig

LINKED = "G6_E_WORKFORCE_REPOSITORY_LINKED"
SECRET = b"a file outside the repository"
REPOSITORY = {"repo": "sample", "module": "policy.py", "function": "label"}
POPULATION = Path(__file__).resolve().parent / "data" / "hic05-dev-v1"
REPLAY_OBJECTIVE = "Make this Python inventory event handler idempotent."
REPLAY_REPOSITORY = {"repo": "sample", "module": "inventory.py", "function": "apply_event"}
ANSWER = {"replay_semantics": "identical_nonadjacent_in_memory"}


@pytest.fixture(autouse=True)
def _bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig's own settings: certificates off (as the rig's tests run it) and each check held to 30 seconds."""
    monkeypatch.setenv("G6_WORKFORCE_CERTIFICATES", "0")
    monkeypatch.setattr(truth, "TIMEOUT_S", 30)
    monkeypatch.setattr(truth, "SUITE_TIMEOUT_S", 30)


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _reaching_out(root: Path) -> list[str]:
    """What a copy holds that reaches outside the repository: a link, or a copy of the outside file (found without
    following any link)."""
    found, folders = [], [root]
    while folders:
        for path in folders.pop().iterdir():
            if _linked(path):
                found.append(f"{path.relative_to(root).as_posix()} (a link)")
            elif path.is_dir():
                folders.append(path)
            elif path.read_bytes() == SECRET:
                found.append(path.relative_to(root).as_posix())
    return found


@pytest.fixture
def copies(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str]]]:
    """Every folder ``shutil.copytree`` copies during the test, with what it holds that reaches outside."""
    made: list[tuple[str, list[str]]] = []
    real = shutil.copytree

    def spy(src, dst, *args, **kwargs):
        done = real(src, dst, *args, **kwargs)
        made.append((str(dst), _reaching_out(Path(dst))))
        return done

    monkeypatch.setattr(shutil, "copytree", spy)
    return made


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(SECRET)
    return outside


def _junction(link: Path, target: Path) -> None:
    """A directory junction from ``link`` to ``target`` (Windows makes one without any privilege)."""
    if sys.platform != "win32":
        pytest.skip("a directory junction is a Windows feature; the symbolic-link test covers this path elsewhere")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _planted_on(monkeypatch: pytest.MonkeyPatch, name: str, link_at, outside: Path) -> list[Path]:
    """Wrap ``coding.<name>`` so that its first call plants a junction at ``link_at(*args)`` to ``outside`` first: a
    link that appears after the checks before it, so the copy that follows meets it."""
    original, planted = getattr(coding, name), []

    def wrapper(*args, **kwargs):
        if not planted:
            link = Path(link_at(*args, **kwargs))
            _junction(link, outside)
            planted.append(link)
        return original(*args, **kwargs)

    monkeypatch.setattr(coding, name, wrapper)
    return planted


def _held(copies: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    return [(where, found) for where, found in copies if found]


def _git_repo(repo: Path) -> Path:
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                                 encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "base")):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                       cwd=repo, check=True, capture_output=True)
    return repo


def _ask_replay(repo: Path) -> None:
    coding._ask_replay(None, source=repo, baseline="", question={}, objective="Fix add", repository={"repo": "calc"},
                       declared_risk="R1", budget_cents=0)


# --- the four copies -----------------------------------------------------------------------------------------------------


def test_the_replay_intake_copy_refuses_a_junction_and_copies_nothing(tmp_path, copies) -> None:
    outside = _outside(tmp_path)
    repo = _git_repo(tmp_path / "repos" / "v1" / "calc")
    _junction(repo / "joined", outside)
    with pytest.raises(ValueError) as refused:
        _ask_replay(repo)
    # fails if coding._ask_replay copies with shutil.copytree's default, which follows the junction (coding.py:572)
    assert _held(copies) == [] and not list(repo.parent.glob("coding-intake-*")), _held(copies)
    assert str(refused.value).startswith(f"[{LINKED}]") and "joined" in str(refused.value), str(refused.value)
    assert (outside / "secret.txt").read_bytes() == SECRET


def test_the_replay_intake_copy_refuses_a_symbolic_link_and_copies_nothing(tmp_path, copies) -> None:
    outside = _outside(tmp_path)
    repo = _git_repo(tmp_path / "repos" / "v1" / "calc")
    try:
        os.symlink(outside, repo / "linked", target_is_directory=True)
    except OSError as exc:   # Windows makes one only with Developer Mode or the privilege to create symbolic links
        pytest.skip(f"this machine cannot make a symbolic link: {exc}")
    with pytest.raises(ValueError) as refused:
        _ask_replay(repo)
    # fails if coding._ask_replay copies before refusing a source that holds a symbolic link (coding.py:572)
    assert _held(copies) == [] and str(refused.value).startswith(f"[{LINKED}]"), str(refused.value)


def test_the_clarified_copy_refuses_a_junction_that_appears_after_the_snapshot_check(objective_rig, tmp_path,
                                                                                       monkeypatch, copies) -> None:
    rig = objective_rig
    outside = _outside(tmp_path)
    _replay_prepare(rig)
    made = rig.call("mission_create", objective=REPLAY_OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPLAY_REPOSITORY))
    assert made["ok"] and made.get("interaction_state") == "awaiting_input", made
    snapshot = _retained(made["mission_id"])
    planted = _planted_on(monkeypatch, "execution_allowed", lambda *args: snapshot / "joined", outside)
    copies.clear()
    reply = rig.call("mission_accept", mission_id=made["mission_id"], contract_json=json.dumps(ANSWER))
    assert planted == [snapshot / "joined"]
    # fails if coding.clarify copies the retained snapshot with shutil.copytree's default (coding.py:672)
    assert _held(copies) == [], _held(copies)
    assert LINKED in json.dumps(reply) and service.bind().missions.get(made["mission_id"])["acceptance"] is None, reply
    assert (outside / "secret.txt").read_bytes() == SECRET


def test_the_scope_copy_refuses_a_junction_that_appears_after_the_link_check(objective_rig, tmp_path, monkeypatch,
                                                                             copies) -> None:
    rig = objective_rig
    outside = _outside(tmp_path)
    planted = _planted_on(monkeypatch, "execution_allowed", lambda *args: rig.repo / "joined", outside)
    made = rig.call("mission_create", objective=_objective.OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    assert planted == [rig.repo / "joined"]
    # fails if coding.start copies the repository for its scope with shutil.copytree's default (coding.py:790)
    assert _held(copies) == [], _held(copies)
    assert made["ok"] is False and made["error"].startswith(f"[{LINKED}]"), made
    assert (outside / "secret.txt").read_bytes() == SECRET


def test_the_frozen_snapshot_copy_refuses_a_junction_that_appears_in_g6s_own_copy(objective_rig, tmp_path,
                                                                                  monkeypatch, copies) -> None:
    rig = objective_rig
    outside = _outside(tmp_path)
    planted = _planted_on(monkeypatch, "_declared", lambda source, *args: Path(source) / "joined", outside)
    made = rig.call("mission_create", objective=_objective.OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    assert len(planted) == 1 and planted[0].parent != rig.repo   # planted in the scope copy, not the repository
    # fails if coding._freeze copies the frozen snapshot with shutil.copytree's default (coding.py:929)
    assert _held(copies) == [] and not list(rig.repo.parent.glob("coding-*")), _held(copies)
    assert made["ok"] is False and made["error"].startswith(f"[{LINKED}]"), made
    assert (outside / "secret.txt").read_bytes() == SECRET


def test_a_link_that_appears_after_the_refusal_check_is_left_out_of_the_copy_never_followed(tmp_path, monkeypatch,
                                                                                             copies) -> None:
    outside = _outside(tmp_path)
    repo = _git_repo(tmp_path / "repos" / "v1" / "calc")
    _junction(repo / "tests" / "joined", outside)
    monkeypatch.setattr(coding, "_link_in", lambda root: "")   # as if the junction appeared just after the check
    coding._snapshot(repo, tmp_path / "copy")
    # fails if coding._snapshot's ignore stops leaving out links (shutil.copytree's default follows a junction)
    assert _held(copies) == [] and (tmp_path / "copy" / "tests" / "test_calc.py").is_file(), _held(copies)
    assert not (tmp_path / "copy" / ".git").exists()             # the snapshot's own patterns still apply


# --- the checks before them ----------------------------------------------------------------------------------------------


def test_a_repository_holding_a_junction_is_refused_before_anything_is_copied(objective_rig, tmp_path,
                                                                               copies) -> None:
    rig = objective_rig
    outside = _outside(tmp_path)
    _junction(rig.repo / "joined", outside)
    # fails if coding.execution_terms still looks for links with Path.is_symlink, which misses a junction (coding.py:719)
    with pytest.raises(ValueError) as refused:
        coding.execution_terms(service.bind(), "sample")
    assert str(refused.value).startswith(f"[{LINKED}]"), str(refused.value)
    made = rig.call("mission_create", objective=_objective.OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    # fails if coding.start still looks for links with Path.is_symlink (coding.py:772)
    assert _held(copies) == [] and made["ok"] is False and made["error"].startswith(f"[{LINKED}]"), (made, copies)


def test_a_retained_snapshot_holding_a_junction_is_refused_before_preparation(objective_rig, tmp_path,
                                                                              copies) -> None:
    rig = objective_rig
    outside = _outside(tmp_path)
    _replay_prepare(rig)
    made = rig.call("mission_create", objective=REPLAY_OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPLAY_REPOSITORY))
    _junction(_retained(made["mission_id"]) / "joined", outside)
    copies.clear()
    reply = rig.call("mission_accept", mission_id=made["mission_id"], contract_json=json.dumps(ANSWER))
    # fails if coding.clarify still looks for links with Path.is_symlink (coding.py:630)
    assert _held(copies) == [] and reply["ok"] is False and reply["error"].startswith(f"[{LINKED}]"), reply


# --- helpers that need the rig ------------------------------------------------------------------------------------------


def _replay_prepare(rig) -> None:
    """The replay population on the rig's repository, committed (as ``test_hic_clarification.prepare``)."""
    (rig.repo / "policy.py").write_text(_objective.AFTER, encoding="utf-8")
    for name in ("inventory.py", "tests/test_inventory.py"):
        (rig.repo / name).write_bytes((POPULATION / name).read_bytes())
    for args in (("add", "."), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "replay population")):
        subprocess.run(["git", *args], cwd=rig.repo, check=True, capture_output=True)


def _retained(mission_id: str) -> Path:
    wf = service.bind()
    request = wf.missions.events(mission_id, kind="mission.created")[0]["detail"]["materials"]["coding_intake"]
    return bench._repo(request["snapshot"], wf.venture_id)
