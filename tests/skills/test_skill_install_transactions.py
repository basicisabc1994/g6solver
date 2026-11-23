# SPDX-License-Identifier: Apache-2.0
"""State-held authority and interrupted writes apply to every shared installer call."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import skills
from g6.cli import main, setup
from g6.skills import install

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("transaction_sync", ROOT / "tools/sync_plugin_skill.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


@pytest.fixture
def payload(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    source = tmp_path / "source.md"
    source.write_bytes(b"old instructions\n")
    return {"SKILL.md": source}, tmp_path / "installed"


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() if p.is_file() else None
            for p in folder.rglob("*")}


@pytest.fixture(params=["cli", "setup", "plugin-guide", "plugin-bundle"])
def writer(request, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "source/bundle"
    source = bundle / "bundle-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"bundled instructions\n")
    (bundle / "LICENSE").write_bytes(b"licence\n")
    (bundle / "VENDORED.md").write_bytes(b"source record\n")
    monkeypatch.setattr(skills, "vendor_sources", lambda: {"bundle": bundle})
    monkeypatch.setattr(skills, "vendor_dir", lambda: tmp_path / "unused-superpowers")
    monkeypatch.setattr(skills, "components_with_skills", lambda: [])
    monkeypatch.setattr(skills, "guide_dir", lambda: source)
    monkeypatch.setattr(setup, "STEPS", ())
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: "fake-client")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
    target = tmp_path / ".claude/skills/bundle-skill"
    if request.param == "cli":
        return lambda: main(["skills", "install", "bundle-skill"]), target
    if request.param == "setup":
        return lambda: main(["setup", "--client", "claude", "--refuse-unconfined"]), target
    target = tmp_path / "plugin"
    if request.param == "plugin-guide":
        return lambda: sync.write(source, target), target
    return lambda: sync.write(bundle, target, whole=True), target


@pytest.mark.parametrize("origin", ["forged", "copied", "state-lost"])
def test_every_writer_requires_its_own_destination_record(writer, tmp_path, origin):
    write, target = writer
    if origin == "state-lost":
        write()
        shutil.rmtree(tmp_path / "state")
    elif origin == "copied":
        source = tmp_path / "other.md"
        source.write_bytes(b"instructions installed elsewhere\n")
        other = tmp_path / "other-install"
        assert install.install_files({"SKILL.md": source}, other) == install.INSTALLED
        shutil.copytree(other, target)
    else:
        target.mkdir(parents=True)
        personal = b"personal instructions\n"
        (target / "SKILL.md").write_bytes(personal)
        (target / install.MARKER).write_text(json.dumps({
            "owner": "g6", "version": 1, "tree": {"SKILL.md": hashlib.sha256(personal).hexdigest()},
        }, sort_keys=True) + "\n", encoding="utf-8")
    before = snapshot(target)
    write()
    assert snapshot(target) == before, "a writer treated destination data as ownership authority"


def test_matching_destination_receipt_never_grants_authority(payload):
    files, target = payload
    target.mkdir()
    personal = b"personal instructions\n"
    (target / "SKILL.md").write_bytes(personal)
    (target / install.MARKER).write_text(json.dumps({
        "owner": "g6", "version": 1, "tree": {"SKILL.md": hashlib.sha256(personal).hexdigest()},
    }, sort_keys=True) + "\n", encoding="utf-8")
    before = snapshot(target)
    assert install.install_files(files, target) == install.KEPT
    assert snapshot(target) == before


@pytest.mark.parametrize("change", ["copy", "missing-state", "bad-state", "another-path", "another-id"])
def test_authority_is_bound_to_g6_state_destination_and_identity(payload, tmp_path, change):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    state = tmp_path / "state"
    if change == "copy":
        copied = tmp_path / "copied"
        shutil.copytree(target, copied)
        target = copied
    elif change == "missing-state":
        shutil.rmtree(state)
    elif change in ("bad-state", "another-path"):
        record, = (state / "skill-installs").glob("*.json")
        if change == "bad-state":
            record.write_bytes(b"{}")
        else:
            value = json.loads(record.read_bytes())
            value["destination"] = str(tmp_path / "somewhere-else")
            record.write_text(json.dumps(value), encoding="utf-8")
    before = snapshot(target)
    files["SKILL.md"].write_bytes(b"new instructions\n")
    kwargs = {"skill_id": "other-bundle/installed"} if change == "another-id" else {}
    assert install.install_files(files, target, **kwargs) == install.KEPT
    assert snapshot(target) == before


def test_state_records_exact_files_including_provenance(payload, tmp_path):
    files, target = payload
    assert install.install_files(files, target, skill_id="bundle/skill") == install.INSTALLED
    record, = (tmp_path / "state/skill-installs").glob("*.json")
    value = json.loads(record.read_bytes())
    assert value["destination"] == os.path.normcase(str(target.resolve()))
    assert value["skill_id"] == "bundle/skill"
    assert value["tree"] == {name: hashlib.sha256(data).hexdigest() for name, data in snapshot(target).items()}
    files["SKILL.md"].write_bytes(b"new instructions\n")
    assert install.install_files(files, target, skill_id="bundle/skill") == install.UPDATED
    assert (target / "SKILL.md").read_bytes() == b"new instructions\n"


@pytest.mark.parametrize("edge", ["backup", "publish", "record"])
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, SystemExit])
def test_every_replacement_failure_restores_old_tree_and_authority(payload, monkeypatch, edge, failure):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    before = snapshot(target)
    files["SKILL.md"].write_bytes(b"new instructions\n")
    rename, replace = Path.rename, os.replace
    fired = False

    def fail():
        nonlocal fired
        if not fired:
            fired = True
            raise failure("interrupted after filesystem mutation")

    def renamed(path, destination):
        result = rename(path, destination)
        if (edge == "backup" and path == target) or (edge == "publish" and path.name == "new"):
            fail()
        return result

    def replaced(source, destination):
        result = replace(source, destination)
        if edge == "record" and Path(destination).suffix == ".json":
            fail()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", renamed)
        patch.setattr(os, "replace", replaced)
        with pytest.raises(failure):
            install.install_files(files, target)
    assert snapshot(target) == before
    assert install.install_files(files, target) == install.UPDATED


@pytest.mark.parametrize("edge", ["backup", "publish", "record"])
def test_next_run_recovers_after_process_death(payload, edge):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    files["SKILL.md"].write_bytes(b"new instructions\n")
    script = '''
import os
import sys
from pathlib import Path
from g6.skills import install
source, target, edge = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
rename, replace = Path.rename, os.replace
def renamed(path, destination):
    result = rename(path, destination)
    if (edge == "backup" and path == target) or (edge == "publish" and path.name == "new"):
        os._exit(73)
    return result
def replaced(source, destination):
    result = replace(source, destination)
    if edge == "record" and Path(destination).suffix == ".json":
        os._exit(73)
    return result
Path.rename, os.replace = renamed, replaced
install.install_files({"SKILL.md": source}, target)
'''
    result = subprocess.run([sys.executable, "-c", script, str(files["SKILL.md"]), str(target), edge],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    assert install.install_files(files, target) == install.UPDATED
    assert (target / "SKILL.md").read_bytes() == b"new instructions\n"
    assert not list(target.parent.glob(".g6-install-*"))


def test_edit_during_staging_is_preserved(payload, monkeypatch):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    original = Path.read_bytes

    def read(path):
        if path == files["SKILL.md"]:
            (target / "SKILL.md").write_bytes(b"person edited while staging\n")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert install.install_files(files, target) == install.KEPT
    assert (target / "SKILL.md").read_bytes() == b"person edited while staging\n"


def test_edit_after_backup_move_is_restored_without_deletion(payload, monkeypatch):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    rename = Path.rename

    def renamed(path, destination):
        result = rename(path, destination)
        if path == target:
            (Path(destination) / "SKILL.md").write_bytes(b"personal edit during swap\n")
        return result

    monkeypatch.setattr(Path, "rename", renamed)
    assert install.install_files(files, target) == install.KEPT
    assert (target / "SKILL.md").read_bytes() == b"personal edit during swap\n"


def test_changed_backup_is_retained_after_publication(payload, monkeypatch):
    files, target = payload
    assert install.install_files(files, target) == install.INSTALLED
    files["SKILL.md"].write_bytes(b"new instructions\n")
    rename = Path.rename

    def renamed(path, destination):
        result = rename(path, destination)
        if path.name == "new":
            (path.parent / "old/personal.txt").write_bytes(b"keep this personal addition\n")
        return result

    monkeypatch.setattr(Path, "rename", renamed)
    with pytest.raises(OSError, match="G6_SKILL_RECOVERY_REQUIRED"):
        install.install_files(files, target)
    backup, = target.parent.glob(".g6-install-*/old")
    assert (backup / "personal.txt").read_bytes() == b"keep this personal addition\n"
    assert (target / "SKILL.md").read_bytes() == b"new instructions\n"


@pytest.mark.parametrize("edge", ["adjacency", "exception-backup", "exception-publish", "exception-record",
                                 "abandoned-backup", "abandoned-publish", "abandoned-record"])
def test_destination_stays_complete_at_every_rename_boundary(writer, tmp_path, monkeypatch, edge):
    """Only adjacent renames may leave a gap; failures restore and every writer recovers first."""
    write, target = writer
    write()
    before = snapshot(target)
    records = tmp_path / "state/skill-installs"
    state, = (p for p in records.glob("*.json")
              if json.loads(p.read_bytes())["destination"] == os.path.normcase(str(target.resolve())))
    old_record = json.loads(state.read_bytes())
    instruction, = (tmp_path / "source").rglob("SKILL.md")
    instruction.write_bytes(b"new instructions\n")
    rename, replace = Path.rename, os.replace

    if edge == "adjacency":
        # Enforce adjacent statements as well as runtime boundaries: even a flush,
        # check, assignment or other third statement in the gap violates the rule.
        tree = ast.parse(inspect.getsource(install._install))
        pair = [ast.dump(ast.parse(line).body[0], include_attributes=False) for line in
                ("target.rename(backup)", "incoming.rename(target)")]
        adjacent = any(
            [ast.dump(a, include_attributes=False), ast.dump(b, include_attributes=False)] == pair
            for node in ast.walk(tree) for body in (getattr(node, "body", []),)
            if isinstance(body, list) for a, b in zip(body, body[1:])
        )
        assert adjacent, "a third operation separates live-to-backup and staged-to-live renames"
        observations = []

        def renamed(path, destination):
            if observations and observations[-1][2] is False:
                assert path.name == "new" and Path(destination) == target
            result = rename(path, destination)
            if path != target and Path(destination) != target:
                return result
            complete = target.is_dir() and snapshot(target) in (before, after)
            observations.append((path, Path(destination), complete))
            return result

        # The expected published tree includes the regenerated provenance receipt.
        staged = tmp_path / "expected"
        shutil.copytree(target, staged)
        relative, = (name for name in before if name.endswith("SKILL.md"))
        (staged / relative).write_bytes(b"new instructions\n")
        (staged / install.MARKER).write_bytes(install._receipt(staged))
        after = snapshot(staged)
        monkeypatch.setattr(Path, "rename", renamed)
        write()
        assert [(p == target, d == target, complete) for p, d, complete in observations] == [
            (True, False, False), (False, True, True),
        ]
        assert snapshot(target) == after
    elif edge.startswith("exception-"):
        fired = False

        def fail():
            nonlocal fired
            if not fired:
                fired = True
                raise KeyboardInterrupt("interrupted after filesystem mutation")

        def renamed(path, destination):
            result = rename(path, destination)
            if (edge == "exception-backup" and path == target
                    or edge == "exception-publish" and path.name == "new" and Path(destination) == target):
                fail()
            return result

        def replaced(source, destination):
            result = replace(source, destination)
            if edge == "exception-record" and Path(destination) == state:
                fail()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(Path, "rename", renamed)
            patch.setattr(os, "replace", replaced)
            with pytest.raises(KeyboardInterrupt):
                write()
        assert fired
        assert target.is_dir() and snapshot(target) == before
        assert json.loads(state.read_bytes()) == old_record
        write()
        assert snapshot(target) != before
    else:
        # Abandon genuine on-disk transaction state, without allowing rollback.
        staging = target.parent / ".g6-install-abandoned"
        incoming, backup = staging / "new", staging / "old"
        shutil.copytree(target, incoming)
        relative, = (name for name in before if name.endswith("SKILL.md"))
        (incoming / relative).write_bytes(b"new instructions\n")
        (incoming / install.MARKER).write_bytes(install._receipt(incoming))
        after = snapshot(incoming)
        new_record = {**old_record, "tree": install._tree(incoming)}
        key = hashlib.sha256(old_record["destination"].encode("utf-8")).hexdigest()
        pending = records / f"{key}.pending"
        install._save(pending, {"before": old_record, "after": new_record, "staging": staging.name})
        target.rename(backup)
        if edge != "abandoned-backup":
            incoming.rename(target)
        if edge == "abandoned-record":
            install._save(state, new_record)
        expected = before if edge == "abandoned-backup" else after

        def source_barrier(*args, **kwargs):
            assert target.is_dir() and snapshot(target) == expected, "writer skipped recovery before other work"
            assert not pending.exists() and not staging.exists()
            raise RuntimeError("source deliberately unavailable after recovery")

        # Stop at each path's first non-recovery operation, before discovery,
        # setup choices/registration, or source enumeration can defer recovery.
        monkeypatch.setattr(skills, "components_with_skills", source_barrier)
        monkeypatch.setattr(setup, "STEPS", (source_barrier,))
        monkeypatch.setattr(sync, "_files", source_barrier)
        with pytest.raises(RuntimeError, match="source deliberately unavailable after recovery"):
            write()
        assert snapshot(target) == expected


def test_case_only_duplicate_bundle_names_are_refused_before_any_write(tmp_path, monkeypatch):
    roots = {}
    for bundle, name in (("first", "Same-Skill"), ("second", "same-skill")):
        root = tmp_path / bundle
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_bytes(b"instructions\n")
        roots[bundle] = root
    monkeypatch.setattr(skills, "vendor_sources", lambda: roots)
    with pytest.raises(ValueError, match="G6_SKILL_DUPLICATE_DESTINATION"):
        skills.vendored()
