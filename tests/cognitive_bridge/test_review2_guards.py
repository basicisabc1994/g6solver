# SPDX-License-Identifier: Apache-2.0
"""Review 2, findings 3, 4 and 5 (guard_covers_less_than_it_guards, met in both reviews): the adaptation gate admits a
changed path only when it is one of the files the bridge declares adaptable, compared by file identity however the
path is spelled or linked, and refuses everything else: a hard link to a judge, the statistic's export module, a judge
reached through a folder link, and any path whose lookup fails."""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

import g6.reasoning
from g6.cognitive_bridge import adaptation
from g6.cognitive_bridge.adaptation import protected_paths

ROOT = Path(__file__).resolve().parents[2]
JUDGE = ROOT / "src" / "g6" / "cognitive_bridge" / "assurance.py"
GUARD = "src/g6/cognitive_bridge/guard.py"


@pytest.fixture
def inside():
    """A scratch folder inside the checkout (a changed path is named relative to it), removed afterwards."""
    folder = Path(tempfile.mkdtemp(prefix=".review2-", dir=ROOT))
    try:
        yield folder
    finally:
        for link in folder.iterdir():
            if link.is_symlink() or link.is_junction():  # remove a folder link itself, never what it points to
                try:
                    os.unlink(link)
                except OSError:
                    os.rmdir(link)
        shutil.rmtree(folder)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _hard_link(target: Path, alias: Path) -> None:
    try:
        os.link(target, alias)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this file system makes no hard link here: {exc}")


def _folder_link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError) as exc:
        pytest.skip(f"no folder link can be made here: {exc}")


def test_finding_3_a_hard_link_to_a_judge_is_refused(inside):
    alias = inside / "ordinary_alias_3.py"
    _hard_link(JUDGE, alias)
    assert alias.samefile(JUDGE)
    assert protected_paths([_relative(alias)]) == [_relative(alias)], "hardlink to assurance.py was accepted"


def test_a_declared_name_made_a_hard_link_to_a_judge_admits_nothing():
    declared = ROOT / GUARD
    assert not declared.exists()
    _hard_link(JUDGE, declared)
    try:
        assert declared.samefile(JUDGE)
        assert protected_paths([GUARD]) == [GUARD]
    finally:
        declared.unlink()


def test_finding_4_the_statistics_export_module_is_refused(passing_adaptation):
    gate, change, pairs, controls = passing_adaptation
    path = "src/g6/reasoning/__init__.py"
    # adaptation.py imports the statistic from this package, whose export can be changed.
    assert "from g6.reasoning.compare import Arm, Case, Verdict, compare, sign_test_p" in open(
        g6.reasoning.__file__, encoding="utf-8").read()
    result = gate.assess(replace(change, changed_paths=(path,)), pairs, controls,
                         detected_failures=("false_confidence",))
    assert result["completion_state"] == "blocked-escalated" and protected_paths([path]) == [path], (
        f"statistic export module accepted: {result['recommendation']}")


def test_finding_5_a_path_resolution_error_refuses_the_path(monkeypatch):
    path = "src/g6/cognitive_bridge/method_adapter.py"
    assert protected_paths([path]) == []
    real = adaptation.os.path.realpath

    def cannot_resolve(value, *args, **kwargs):
        if str(value).endswith("method_adapter.py"):
            raise OSError("simulated file-system lookup failure")
        return real(value, *args, **kwargs)

    monkeypatch.setattr(adaptation.os.path, "realpath", cannot_resolve)
    assert adaptation.protected_paths([path]) == [path], "resolution error accepted the candidate path"


def test_a_lookup_error_on_the_candidate_alone_refuses_it(monkeypatch):
    # The same declared file in another spelling: admitted, until its own lookup fails (the declaration still resolves).
    spelling = "src/g6/cognitive_bridge/../cognitive_bridge/method_adapter.py"
    assert protected_paths([spelling]) == []
    real = adaptation.os.path.realpath

    def cannot_resolve(value, *args, **kwargs):
        if ".." in str(value):
            raise OSError("simulated file-system lookup failure")
        return real(value, *args, **kwargs)

    monkeypatch.setattr(adaptation.os.path, "realpath", cannot_resolve)
    assert protected_paths([spelling, adaptation.ADAPTABLE[0]]) == [spelling]


def test_an_error_in_the_declaration_refuses_every_path(monkeypatch):
    def broken():
        raise OSError("simulated listing failure")

    monkeypatch.setattr(adaptation, "judged_files", broken)
    assert protected_paths([GUARD]) == [GUARD]


def test_a_declaration_that_names_a_judge_admits_nothing(monkeypatch):
    monkeypatch.setattr(adaptation, "ADAPTABLE", (*adaptation.ADAPTABLE, _relative(JUDGE)))
    assert protected_paths([GUARD, _relative(JUDGE)]) == sorted([GUARD, _relative(JUDGE)])


def test_only_the_declared_files_are_admitted():
    for name in adaptation.ADAPTABLE:
        assert protected_paths([name]) == [], name
    for other in ("src/g6/cognitive_bridge/xai.py", "src/g6/cognitive_bridge/new_module.py", "README.md",
                  "src/g6/cognitive_bridge", "src/g6/core/door.py", _relative(JUDGE)):
        assert protected_paths([other]) == [other], other


def test_another_spelling_of_a_declared_file_is_that_file_and_of_a_judge_is_that_judge():
    judge = _relative(JUDGE)
    for spelling in (f"src/./g6/cognitive_bridge/{Path(GUARD).name}", GUARD.replace("/", os.sep),
                     f"src/g6/../g6/cognitive_bridge/{Path(GUARD).name}"):
        assert protected_paths([spelling]) == [], spelling
    for spelling in (f"src/./g6/cognitive_bridge/{JUDGE.name}", judge.replace("/", os.sep), judge.upper(),
                     f"src/g6/../g6/cognitive_bridge/{JUDGE.name}", f"src/g6/../{Path(GUARD).name}",
                     str(ROOT / GUARD)):  # an absolute path is refused, whatever it names
        assert protected_paths([spelling]) == [spelling], spelling
    if (ROOT / GUARD.upper()).parent.exists() and os.path.normcase("A") == "a":  # a file system that folds case
        assert protected_paths([GUARD.upper()]) == []


def test_a_judge_reached_through_a_link_to_its_folder_is_refused(inside):
    link = inside / "linked_bridge"
    _folder_link(JUDGE.parent, link)
    through = _relative(link / JUDGE.name)
    assert (ROOT / through).samefile(JUDGE)
    assert protected_paths([through]) == [through], "a judge reached through a folder link was accepted"
    # the declared file, reached the same way, is the same file
    assert protected_paths([_relative(link / Path(GUARD).name)]) == []
