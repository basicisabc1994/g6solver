# SPDX-License-Identifier: Apache-2.0
"""Installed vendored items retain their source's legal records on every write path."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cli import main, setup
from g6.core import skills

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("records_sync", ROOT / "tools/sync_plugin_skill.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
VENDORED = list(skills.vendored())


@pytest.fixture
def environment(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    for name, source in skills.vendor_sources().items():
        shutil.copytree(source, sources / name)
    monkeypatch.setattr(skills, "vendor_sources", lambda: {p.name: p for p in sources.iterdir()})
    monkeypatch.setattr(skills, "vendor_dir", lambda: sources / "superpowers")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(setup, "STEPS", ())
    monkeypatch.setattr(setup, "codex_agents", lambda *a: None)
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: "fake-client")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
    return tmp_path


@pytest.mark.parametrize("name", VENDORED)
@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("scope", ["project", "user"])
def test_cli_records_are_byte_equal_to_the_source(environment, name, client, scope):
    command = ["skills", "install", name, "--client", client]
    if scope == "user":
        command.append("--user")
    assert main(command) == 0
    source = skills.vendored()[name].parent
    for record in ("LICENSE", "VENDORED.md"):
        assert (environment / f".{client}/skills" / name / record).read_bytes() == (source / record).read_bytes()


@pytest.mark.parametrize("entry", ["cli", "setup", "plugin"])
@pytest.mark.parametrize("bundle", ["superpowers", "langextract"])
@pytest.mark.parametrize("record", ["LICENSE", "VENDORED.md"])
@pytest.mark.parametrize("existing", [False, True])
def test_missing_records_fail_closed_before_changing_the_destination(environment, capsys, entry, bundle, record, existing):
    source = skills.vendor_sources()[bundle]
    name = next(iter(skills.vendored(bundle)))
    target = environment / ".claude/skills" / name
    if entry == "plugin":
        target = environment / "plugin" / bundle
        if existing:
            sync.write(source, target, whole=True)
    elif existing:
        assert main(["skills", "install", name]) == 0
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    (source / record).unlink()
    capsys.readouterr()
    if entry == "plugin":
        with pytest.raises(FileNotFoundError, match="G6_SKILL_SOURCE_RECORD_MISSING"):
            sync.write(source, target, whole=True)
    elif entry == "setup" and bundle == "superpowers":
        # Setup deliberately does not select Superpowers; it must not touch it.
        assert main(["setup", "--client", "claude", "--refuse-unconfined"]) == 0
    else:
        command = (["setup", "--client", "claude", "--refuse-unconfined"] if entry == "setup"
                   else ["skills", "install", name, "--force"])
        assert main(command) != 0
        output = capsys.readouterr()
        assert "G6_SKILL_SOURCE_RECORD_MISSING" in output.out + output.err
    assert {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()} == before
    if not existing:
        assert not target.exists()


@pytest.mark.parametrize("bundle", ["superpowers", "langextract"])
def test_plugin_carries_each_records_original_bytes(environment, bundle):
    source = skills.vendor_sources()[bundle]
    target = environment / "plugin" / bundle
    sync.write(source, target, whole=True)
    for record in ("LICENSE", "VENDORED.md"):
        assert (target / record).read_bytes() == (source / record).read_bytes()


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_setup_installs_the_selected_bundle_with_records(environment, client):
    assert main(["setup", "--client", client, "--refuse-unconfined"]) == 0
    source = skills.vendor_sources()["langextract"]
    for record in ("LICENSE", "VENDORED.md"):
        assert (environment / f".{client}/skills/langextract-usage" / record).read_bytes() == (
            source / record).read_bytes()
