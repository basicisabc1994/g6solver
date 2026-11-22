# SPDX-License-Identifier: Apache-2.0
"""The Claude Code plugin ships the ``using-g6`` skill (plan revision 8, A): ``plugins/g6/skills/using-g6/`` holds
``SKILL.md`` and ``references/`` byte for byte as ``src/g6/skills/using-g6/`` has them, kept so by
``tools/sync_plugin_skill.py`` (``--check`` / ``--write``)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sync_plugin_skill", ROOT / "tools" / "sync_plugin_skill.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def test_the_plugin_ships_the_skill_byte_for_byte():
    source, shipped = ROOT / "src" / "g6" / "skills" / "using-g6", ROOT / "plugins" / "g6" / "skills" / "using-g6"
    names = sorted(p.relative_to(source).as_posix() for p in [source / "SKILL.md", *(source / "references").rglob("*")]
                   if p.is_file())
    assert names and "SKILL.md" in names
    assert sorted(p.relative_to(shipped).as_posix() for p in shipped.rglob("*") if p.is_file()) == names
    for name in names:
        assert (shipped / name).read_bytes() == (source / name).read_bytes(), name
    assert sync.main(["--check"]) == 0


def test_the_check_names_a_drift_and_write_repairs_it(tmp_path):
    source, shipped = tmp_path / "src", tmp_path / "plugin"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"---\nname: using-g6\n---\nbody\n")
    (source / "references" / "a.md").write_bytes(b"a\n")
    (source / "routing.json").write_bytes(b"{}")          # not shipped: the plugin needs only the skill and its references
    assert sync.problems(source, shipped) == ["SKILL.md is missing", "references/a.md is missing"]
    sync.write(source, shipped)
    assert sync.problems(source, shipped) == [] and not (shipped / "routing.json").exists()
    (shipped / "references" / "a.md").write_bytes(b"edited\n")
    (shipped / "references" / "stale.md").write_bytes(b"old\n")
    assert sync.problems(source, shipped) == ["references/a.md differs", "references/stale.md is not in the source"]
    sync.write(source, shipped)
    assert sync.problems(source, shipped) == []


def test_the_plugin_skill_is_discoverable_like_its_completion_loop_skill():
    text = (ROOT / "plugins" / "g6" / "skills" / "using-g6" / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---", 2)[1]
    assert "name: using-g6" in head and "description: " in head


def test_full_bundle_sync_preserves_examples_licence_and_provenance(tmp_path):
    source, shipped = tmp_path / "source", tmp_path / "plugin"
    for name, data in {"LICENSE": b"licence\n", "VENDORED.md": b"record\n",
                       "langextract-usage/SKILL.md": b"skill\n",
                       "langextract-usage/examples/sample.py": b"example\n"}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert "LICENSE is missing" in sync.problems(source, shipped, whole=True)
    sync.write(source, shipped, whole=True)
    assert sync.problems(source, shipped, whole=True) == []
    assert (shipped / "langextract-usage/examples/sample.py").read_bytes() == b"example\n"
    (shipped / "LICENSE").write_bytes(b"changed\n")
    assert sync.problems(source, shipped, whole=True) == ["LICENSE differs"]


def test_full_bundle_sync_refuses_a_missing_source_before_removing_the_copy(tmp_path):
    import pytest

    source, shipped = tmp_path / "missing", tmp_path / "plugin"
    shipped.mkdir()
    (shipped / "LICENSE").write_bytes(b"kept\n")
    assert sync.problems(source, shipped, whole=True) == ["source bundle is missing or empty"]
    with pytest.raises(FileNotFoundError):
        sync.write(source, shipped, whole=True)
    assert (shipped / "LICENSE").read_bytes() == b"kept\n"


def test_full_bundle_sync_does_not_report_two_absent_bundles_as_equal(tmp_path):
    assert sync.problems(tmp_path / "missing", tmp_path / "absent", whole=True) == [
        "source bundle is missing or empty"]
