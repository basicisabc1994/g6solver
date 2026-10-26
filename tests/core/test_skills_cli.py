# SPDX-License-Identifier: Apache-2.0
"""``g6 skills list`` and ``g6 skills install``: copy the using-g6 skill (by default), a component's skill, or the
vendored superpowers skills into an agent's skills folder, never over a skill already there unless asked."""
from __future__ import annotations

from pathlib import Path

import pytest

from g6.cli import main
from g6.core import skills


@pytest.fixture
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A project folder (the working directory) and a home folder, both empty."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", lambda: home)
    return project / ".claude" / "skills", home


def _same_tree(a: Path, b: Path) -> bool:
    files = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}  # noqa: E731
    return files(a) == files(b)


def test_list_names_every_skill_it_can_install(capsys) -> None:
    assert main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "using-g6" in out and "g6-coding-agent-gate" in out and "brainstorming" in out


def test_install_with_no_names_copies_only_the_using_g6_skill(where) -> None:
    dest, _ = where
    assert main(["skills", "install"]) == 0
    assert sorted(p.name for p in dest.iterdir()) == ["using-g6"]
    assert _same_tree(dest / "using-g6", skills.guide_dir())


def test_install_a_component_names_its_skill_g6_dash_component(where) -> None:
    dest, _ = where
    assert main(["skills", "install", "coding_agent_gate"]) == 0
    assert sorted(p.name for p in dest.iterdir()) == ["g6-coding-agent-gate"]
    assert _same_tree(dest / "g6-coding-agent-gate", skills.skill_dir("coding_agent_gate"))


def test_install_all_copies_the_guide_and_every_components_skill(where) -> None:
    dest, _ = where
    assert main(["skills", "install", "--all"]) == 0
    expected = {"using-g6", *(skills.skill_name(c) for c in skills.components_with_skills())}
    assert {p.name for p in dest.iterdir()} == expected and "g6-coding-agent-gate" in expected


def test_with_superpowers_also_copies_the_vendored_lifecycle_skills(where) -> None:
    dest, _ = where
    assert main(["skills", "install", "--with-superpowers"]) == 0
    assert {p.name for p in dest.iterdir()} == {"using-g6", *skills.vendored()}
    assert _same_tree(dest / "brainstorming", skills.vendored()["brainstorming"])


def test_the_vendored_copy_is_skipped_when_the_superpowers_plugin_is_installed(where, capsys) -> None:
    dest, home = where
    (home / ".claude" / "plugins" / "cache" / "claude-plugins-official" / "superpowers" / "7.0.0").mkdir(parents=True)
    assert main(["skills", "install", "--with-superpowers"]) == 0
    assert [p.name for p in dest.iterdir()] == ["using-g6"]
    assert "superpowers plugin is installed" in capsys.readouterr().out


def test_user_installs_into_the_home_folder(where) -> None:
    dest, home = where
    assert main(["skills", "install", "--user"]) == 0
    assert (home / ".claude" / "skills" / "using-g6" / "SKILL.md").is_file() and not dest.exists()


def test_a_skill_already_there_is_left_alone_unless_forced(where, capsys) -> None:
    dest, _ = where
    (dest / "using-g6").mkdir(parents=True)
    (dest / "using-g6" / "SKILL.md").write_text("mine", encoding="utf-8")
    assert main(["skills", "install"]) == 0
    assert (dest / "using-g6" / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert "left as is" in capsys.readouterr().out
    assert main(["skills", "install", "--force"]) == 0
    assert _same_tree(dest / "using-g6", skills.guide_dir())


def test_an_unknown_name_is_refused_and_nothing_is_copied(where, capsys) -> None:
    dest, _ = where
    assert main(["skills", "install", "no_such_component"]) == 2
    assert "no_such_component" in capsys.readouterr().err and not dest.exists()
