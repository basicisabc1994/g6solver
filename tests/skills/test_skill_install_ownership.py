# SPDX-License-Identifier: Apache-2.0
"""Every writer preserves personal trees, including edits to a prior G6 install."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cli import main, setup
from g6.core import skills

ROOT = Path(__file__).resolve().parents[2]
NAMES = [skills.GUIDE, "g6-coding-agent-gate", *skills.vendored()]
SPEC = importlib.util.spec_from_file_location("ownership_sync", ROOT / "tools/sync_plugin_skill.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(setup, "STEPS", ())
    monkeypatch.setattr(setup, "codex_agents", lambda *args: None)
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: "fake-client")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
    sources = tmp_path / "sources"
    for name, source in skills.vendor_sources().items():
        shutil.copytree(source, sources / name)
    monkeypatch.setattr(skills, "vendor_sources", lambda: {p.name: p for p in sources.iterdir()})
    monkeypatch.setattr(skills, "vendor_dir", lambda: sources / "superpowers")
    return home, project


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() if p.is_file() else None
            for p in folder.rglob("*")}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("scope", ["project", "user"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("state", ["absent", "own", "personal", "edited"])
def test_cli_installs_updates_or_preserves_every_skill(environment, capsys, name, client, scope, force, state):
    home, project = environment
    target = (home if scope == "user" else project) / f".{client}/skills" / name
    command = ["skills", "install", name, "--client", client]
    if force:
        command.append("--force")
    if scope == "user":
        command.append("--user")
    if state in ("own", "edited"):
        assert main(command) == 0
    elif state == "personal":
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"personal instructions\n")
    if state == "edited":
        (target / "SKILL.md").write_bytes(b"personal instructions\n")
    if state == "own" and name in skills.vendored():
        source = skills.vendored()[name] / "SKILL.md"
        source.write_bytes(source.read_bytes() + b"\nUpdated source.\n")
    before = tree(target)
    capsys.readouterr()
    assert main(command) == 0
    output = capsys.readouterr().out
    if state in ("personal", "edited"):
        assert tree(target) == before, "installer changed a personal tree"
        assert "G6_SKILL_KEPT_PERSONAL" in output
    else:
        assert (target / "SKILL.md").is_file()
        receipt = json.loads((target / ".g6-installed.json").read_text(encoding="utf-8"))
        assert receipt["owner"] == "g6" and receipt["version"] == 1
        assert ("G6_SKILL_UPDATED" if state == "own" else "G6_SKILL_INSTALLED") in output
        if name in skills.vendored():
            assert (target / "SKILL.md").read_bytes() == (skills.vendored()[name] / "SKILL.md").read_bytes()


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("name", [skills.GUIDE, "langextract-usage"])
@pytest.mark.parametrize("state", ["absent", "own", "personal", "edited"])
def test_setup_uses_the_same_ownership_rule(environment, capsys, client, name, state):
    home, _ = environment
    target = home / f".{client}/skills" / name
    if state in ("own", "edited"):
        assert main(["skills", "install", name, "--user", "--client", client]) == 0
    if state == "personal":
        target.mkdir(parents=True)
    if state in ("personal", "edited"):
        (target / "SKILL.md").write_bytes(b"personal\n")
    before = tree(target)
    capsys.readouterr()
    assert main(["setup", "--client", client, "--refuse-unconfined"]) == 0
    output = capsys.readouterr().out
    if state in ("personal", "edited"):
        assert tree(target) == before
        assert "G6_SKILL_KEPT_PERSONAL" in output
        assert "Replaced the earlier copy" not in output
    else:
        assert (target / ".g6-installed.json").is_file()
    assert (home / f".{client}/skills/langextract-usage/SKILL.md").is_file()


@pytest.mark.parametrize("mutation", ["add", "delete", "directory", "missing-marker", "bad-marker", "reformatted-marker"])
def test_receipt_covers_the_complete_tree(environment, capsys, mutation):
    _, project = environment
    assert main(["skills", "install"]) == 0
    target = project / ".claude/skills/using-g6"
    if mutation == "add":
        (target / "personal.txt").write_bytes(b"mine")
    elif mutation == "delete":
        (target / "SKILL.md").unlink()
    elif mutation == "directory":
        (target / "personal-empty-directory").mkdir()
    elif mutation == "missing-marker":
        (target / ".g6-installed.json").unlink()
    elif mutation == "reformatted-marker":
        marker = target / ".g6-installed.json"
        marker.write_bytes(json.dumps(json.loads(marker.read_bytes()), indent=2).encode("utf-8"))
    else:
        (target / ".g6-installed.json").write_bytes(b"{}")
    before = tree(target)
    capsys.readouterr()
    assert main(["skills", "install", "--force"]) == 0
    assert tree(target) == before
    assert "G6_SKILL_KEPT_PERSONAL" in capsys.readouterr().out


@pytest.mark.parametrize("name", list(skills.vendored()))
@pytest.mark.parametrize("state", ["absent", "own", "personal", "edited"])
def test_plugin_bundle_writer_uses_the_same_rule(environment, tmp_path, capsys, name, state):
    source = skills.vendored()[name].parent
    target = tmp_path / "plugin" / source.name
    if state in ("own", "edited"):
        sync.write(source, target, whole=True)
    if state == "personal":
        (target / name).mkdir(parents=True)
    if state in ("personal", "edited"):
        (target / name / "SKILL.md").write_bytes(b"mine")
    before = tree(target)
    if state == "own":
        (source / name / "SKILL.md").write_bytes(b"new upstream instructions\n")
    capsys.readouterr()
    sync.write(source, target, whole=True)
    if state in ("personal", "edited"):
        assert tree(target) == before
        assert "G6_SKILL_KEPT_PERSONAL" in capsys.readouterr().out
    else:
        assert sync.problems(source, target, whole=True) == []
        assert (target / ".g6-installed.json").is_file()


@pytest.mark.parametrize("state", ["absent", "own", "personal", "edited"])
def test_plugin_guide_writer_preserves_personal_trees(tmp_path, capsys, state):
    source, target = tmp_path / "source", tmp_path / "plugin"
    source.mkdir()
    (source / "SKILL.md").write_bytes(b"guide\n")
    if state in ("own", "edited"):
        sync.write(source, target)
    if state == "personal":
        target.mkdir()
    if state in ("personal", "edited"):
        (target / "SKILL.md").write_bytes(b"mine\n")
    before = tree(target)
    (source / "SKILL.md").write_bytes(b"updated guide\n")
    capsys.readouterr()
    sync.write(source, target)
    if state in ("personal", "edited"):
        assert tree(target) == before
        assert "G6_SKILL_KEPT_PERSONAL" in capsys.readouterr().out
    else:
        assert (target / "SKILL.md").read_bytes() == b"updated guide\n"


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("name", list(skills.vendored("superpowers")))
def test_setup_keeps_its_existing_superpowers_selection(environment, client, name):
    home, _ = environment
    target = home / f".{client}/skills" / name
    assert main(["setup", "--client", client, "--refuse-unconfined"]) == 0
    assert not target.exists()
    target.mkdir()
    (target / "SKILL.md").write_bytes(b"mine")
    assert main(["setup", "--client", client, "--refuse-unconfined"]) == 0
    assert (target / "SKILL.md").read_bytes() == b"mine"


def test_personal_collision_does_not_block_other_skills(environment, capsys):
    _, project = environment
    target = project / ".claude/skills/brainstorming"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"mine")
    assert main(["skills", "install", "--with-superpowers", "--force"]) == 0
    assert (target / "SKILL.md").read_bytes() == b"mine"
    assert (target.parent / "using-superpowers/SKILL.md").is_file()
    assert "G6_SKILL_KEPT_PERSONAL" in capsys.readouterr().out


def test_plugin_command_continues_after_a_personal_guide(environment, tmp_path, monkeypatch, capsys):
    guide = tmp_path / "guide-source"
    guide.mkdir()
    (guide / "SKILL.md").write_bytes(b"guide")
    shipped = tmp_path / "plugin-guide"
    shipped.mkdir()
    (shipped / "SKILL.md").write_bytes(b"mine")
    bundle = tmp_path / "plugin-bundle"
    monkeypatch.setattr(sync, "SOURCE", guide)
    monkeypatch.setattr(sync, "SHIPPED", shipped)
    monkeypatch.setattr(sync, "BUNDLE_SOURCE", skills.vendor_sources()["langextract"])
    monkeypatch.setattr(sync, "BUNDLE_SHIPPED", bundle)
    assert sync.main(["--write"]) == 0
    assert (shipped / "SKILL.md").read_bytes() == b"mine"
    assert (bundle / "langextract-usage/SKILL.md").is_file()
    assert "G6_SKILL_KEPT_PERSONAL" in capsys.readouterr().out


def test_a_source_read_failure_keeps_the_previous_install(environment, monkeypatch, capsys):
    _, project = environment
    assert main(["skills", "install"]) == 0
    target = project / ".claude/skills/using-g6"
    before = tree(target)
    source = skills.guide_dir() / "SKILL.md"
    original = Path.read_bytes

    def read(path):
        if path == source:
            raise OSError("source read failed")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    capsys.readouterr()
    assert main(["skills", "install", "--force"]) == 1
    assert tree(target) == before
    assert "G6_SKILL_INSTALL_FAILED" in capsys.readouterr().err
    assert not list(target.parent.glob(".g6-install-*"))
