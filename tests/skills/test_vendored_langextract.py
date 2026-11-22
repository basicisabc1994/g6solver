# SPDX-License-Identifier: Apache-2.0
"""Google's library guide ships unchanged; G6 discovers and distributes it alongside other bundles."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from g6.core import skills

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "src/g6/skills/vendor/langextract"
COMMIT = "0e441bf77ddc3119dd0aa413322959234dd84ee5"
HASHES = {
    "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "langextract-usage/README.md": "daed6cca515323a4aeaa4eb97023bfc68c851e784202aaf2ebc3dfa452667358",
    "langextract-usage/SKILL.md": "d753e3ad9d7cc703d1c9030e399e146f134b44d0cbc7db2294e15463382fc248",
    "langextract-usage/examples/basic_extraction.py": "67217ed49270b3f2ec2cfe9dfa5469e52728b6c60b09f7fa27932926fc259de9",
    "langextract-usage/examples/multiple_documents.py": "914d59edd05ec1b72ade56103d30a50523c8b5c611d8f21d7f2e2b9b5ca48d80",
    "langextract-usage/examples/relationship_extraction.py": "fe8aa22bfbcda8f4f5ce67018c05dd851db15b7f99b85683f81d8643596930d6",
    "langextract-usage/references/prompt-validation.md": "5309a79c3a1a93ec8854ad5f8876614eef51a3700d268686aebab675fe15b9a5",
    "langextract-usage/references/providers.md": "447c12266d9ea5415f937a0bd4ed63c6709f1d7b1c2a2c1a070504918474dd7b",
    "langextract-usage/references/resolver-params.md": "f916c63aa7f9c58df73ca9b86cd08d81cc5c416c4f8650f7392e8a2d94e1f258",
}


def test_every_file_is_upstreams_byte_for_byte():
    record = (VENDOR / "VENDORED.md").read_text(encoding="utf-8")
    listed = dict(re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", record, re.MULTILINE))
    assert listed == HASHES
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*")
               if p.is_file() and p.name != "VENDORED.md"}
    assert on_disk == set(HASHES)
    for rel, digest in HASHES.items():
        data = (VENDOR / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, rel
        assert b"\r" not in data, rel


def test_the_record_and_licence_travel_with_the_bundle():
    record = (VENDOR / "VENDORED.md").read_text(encoding="utf-8")
    assert "https://github.com/google/langextract" in record and COMMIT in record
    assert "skills/langextract-usage" in record and "2026-11-10" in record
    assert "Apache-2.0" in record and "**Changes:** none" in record
    assert not re.search(r"[A-Za-z]:[\\/]+Users|/home/|/Users/", record)
    assert "Apache License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert not (VENDOR / "NOTICE").exists()
    assert "langextract" in (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_loader_finds_all_sources_and_preserves_the_superpowers_directory():
    assert skills.vendor_dir() == ROOT / "src/g6/skills/vendor/superpowers"
    assert skills.vendor_sources()["langextract"] == VENDOR
    assert skills.vendored()["langextract-usage"] == VENDOR / "langextract-usage"
    assert set(skills.vendored("langextract")) == {"langextract-usage"}
    assert "langextract-usage" not in skills.vendored("superpowers")


def test_loader_discovers_a_new_source_without_importing_its_examples(tmp_path, monkeypatch):
    directory = tmp_path / "vendor/another-source/another-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: another-skill\n---\n", encoding="utf-8")
    (directory / "example.py").write_text("raise RuntimeError('do not import')\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_skills_root", lambda: tmp_path)
    assert skills.vendored() == {"another-skill": directory}


def test_loader_refuses_duplicate_names_across_sources(tmp_path, monkeypatch):
    for source in ("first", "second"):
        directory = tmp_path / "vendor" / source / "same-name"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: same-name\n---\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_skills_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="duplicate vendored skill"):
        skills.vendored()


def test_loader_refuses_an_unknown_source():
    with pytest.raises(KeyError):
        skills.vendored("missing-source")


def test_plugin_discovers_the_library_guide_with_its_original_bytes_and_licence():
    plugin = ROOT / "plugins/g6"
    manifest = json.loads((plugin / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert set(manifest["skills"]) == {"./skills/using-g6", "./skills/completion-loop",
                                      "./vendor/langextract/langextract-usage"}
    shipped = plugin / "vendor/langextract"
    for rel in [*HASHES, "VENDORED.md"]:
        assert (shipped / rel).read_bytes() == (VENDOR / rel).read_bytes(), rel


def test_wheel_package_selection_covers_the_entire_bundle():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "src/g6" in wheel["packages"]
    assert not wheel.get("only-packages", False)
    assert not wheel.get("exclude")
    assert all((ROOT / "src/g6/skills/vendor/langextract" / rel).is_file() for rel in [*HASHES, "VENDORED.md"])


def test_git_preserves_both_copies_bytes():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "src/g6/skills/vendor/** -text" in attributes
    assert "plugins/g6/vendor/** -text" in attributes
