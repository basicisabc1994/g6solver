# SPDX-License-Identifier: Apache-2.0
"""V9's pinned G6 toolchain (``validation/patch_review_utility/environment``): the manifest of sources, fetching
into an empty folder with every file verified before use, the tree digest, and the builder commands.

No network and no container here: downloads are replaced by a function, and the commands are only read. The files
fetched are made-up bytes, never real sources."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from patch_review_utility.environment import toolchain

MANIFEST = toolchain.MANIFEST


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake(files: dict[str, bytes]) -> dict:
    """A manifest with the real one's shape whose sources are ``files``."""
    real = toolchain.load(MANIFEST)
    made = copy.deepcopy(real)
    entries = toolchain.sources(made)
    for entry, (name, data) in zip(entries, files.items()):
        entry.update(file=name, url=f"https://example.invalid/{name}", sha256=sha(data), size=len(data))
    return made


def test_the_committed_manifest_pins_every_source_by_hash_and_size():
    manifest = toolchain.load(MANIFEST)
    entries = toolchain.sources(manifest)
    assert {e["role"] for e in entries} >= {"python", "git", "bubblewrap", "wheel"}
    assert all(e["url"].startswith("https://") and len(e["sha256"]) == 64 and e["size"] > 0 for e in entries)
    assert manifest["python"]["sha256"] == "5ff9f0602ce964a529ed1e8555d3abab6341926d724402ba9a15e9e877191124"
    assert all("@sha256:" in image for image in manifest["builders"].values())
    git = tuple(int(part) for part in manifest["git"]["version"].split("."))
    assert git >= (2, 31), "G6 refuses git older than 2.31"


@pytest.mark.parametrize("damage", [
    lambda m: m["git"].update(sha256="not-a-hash"),
    lambda m: m["git"].update(url="http://example.invalid/git.tar.xz"),
    lambda m: m.pop("bubblewrap"),
    lambda m: m["wheels"].append({"file": "x.whl", "url": "https://example.invalid/x.whl", "sha256": "0" * 64}),
    lambda m: m["builders"].update(static="alpine:latest"),
])
def test_a_manifest_that_does_not_pin_a_source_is_refused(tmp_path, damage):
    manifest = json.loads(Path(MANIFEST).read_text(encoding="utf-8"))
    damage(manifest)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        toolchain.load(path)


def test_fetching_fills_an_empty_folder_and_verifies_every_file(tmp_path):
    files = {f"source-{i}.bin": f"bytes {i}".encode() for i in range(len(toolchain.sources(toolchain.load(MANIFEST))))}
    manifest = fake(files)
    asked = []
    got = toolchain.fetch(manifest, tmp_path / "downloads", download=lambda url: asked.append(url) or files[
        url.rsplit("/", 1)[1]])
    assert sorted(p.name for p in (tmp_path / "downloads").iterdir()) == sorted(files)
    assert [r["sha256"] for r in got] == [sha(files[r["file"]]) for r in got] and len(asked) == len(files)
    assert toolchain.verify(manifest, tmp_path / "downloads") == []


def test_a_cached_copy_is_used_only_after_its_hash_is_checked(tmp_path):
    files = {f"source-{i}.bin": f"bytes {i}".encode() for i in range(len(toolchain.sources(toolchain.load(MANIFEST))))}
    manifest = fake(files)
    cache = tmp_path / "cache"
    cache.mkdir()
    first = next(iter(files))
    (cache / first).write_bytes(files[first])
    asked = []
    toolchain.fetch(manifest, tmp_path / "d", cache=cache, download=lambda url: asked.append(url) or files[
        url.rsplit("/", 1)[1]])
    assert f"https://example.invalid/{first}" not in asked and len(asked) == len(files) - 1


def test_a_folder_that_is_not_empty_or_a_file_that_does_not_match_is_refused(tmp_path):
    files = {f"source-{i}.bin": f"bytes {i}".encode() for i in range(len(toolchain.sources(toolchain.load(MANIFEST))))}
    manifest = fake(files)
    used = tmp_path / "used"
    used.mkdir()
    (used / "left.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        toolchain.fetch(manifest, used, download=lambda url: b"")
    with pytest.raises(ValueError, match="SHA-256"):
        toolchain.fetch(manifest, tmp_path / "bad", download=lambda url: b"tampered")
    assert not any((tmp_path / "bad").iterdir())                        # nothing unverified is left to be used
    (tmp_path / "ok").mkdir()
    for name, data in files.items():
        (tmp_path / "ok" / name).write_bytes(data)
    (tmp_path / "ok" / next(iter(files))).write_bytes(b"changed later")
    assert len(toolchain.verify(manifest, tmp_path / "ok")) == 1


def test_the_tree_digest_covers_names_contents_links_and_the_executable_bit(tmp_path):
    root = tmp_path / "t"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "tool").write_bytes(b"#!/bin/sh\n")
    (root / "lib.txt").write_bytes(b"data")
    first = toolchain.tree_digest(root)
    assert first == toolchain.tree_digest(root) and len(first) == 64
    (root / "lib.txt").write_bytes(b"data!")
    assert toolchain.tree_digest(root) != first
    (root / "lib.txt").write_bytes(b"data")
    assert toolchain.tree_digest(root) == first
    if os.name != "nt":
        os.chmod(root / "bin" / "tool", 0o755)
        assert toolchain.tree_digest(root) != first
        os.symlink("tool", root / "bin" / "alias")
        assert toolchain.tree_digest(root) != first


class Commands:
    """Stands in for every command the build runs; records them and makes the files a real one would."""

    def __init__(self, volume_exists=False):
        self.volume_exists, self.seen = volume_exists, []

    def __call__(self, command, check=False, capture_output=False, text=False):
        import io
        import tarfile
        import types
        self.seen.append(command)
        out = ""
        if command[:3] == ["docker", "volume", "inspect"]:
            return types.SimpleNamespace(returncode=0 if self.volume_exists else 1, stdout="")
        if command[:1] == ["git"]:
            with tarfile.open(command[command.index("-o") + 1], "w") as handle:
                data = b"[project]\nname = 'g6'\n"
                info = tarfile.TarInfo("pyproject.toml")
                info.size = len(data)
                handle.addfile(info, io.BytesIO(data))
        elif command[:2] == ["uv", "build"]:
            out_dir = Path(command[command.index("--out-dir") + 1])
            (out_dir / "g6-0.1.1-py3-none-any.whl").write_bytes(b"a wheel")
        elif "digest" in command:
            out = "ab" * 32 + "\n"
        elif command[-2:-1] == ["-c"] and "git --version" in command[-1]:
            out = "git version 2.56.0\nbubblewrap 0.13.1\n3.12.15\n0.1.1\n"
        return types.SimpleNamespace(returncode=0, stdout=out)


def test_the_build_records_its_sources_scripts_wheel_versions_and_tree_digest(tmp_path):
    files = {e["file"]: f"bytes {i}".encode() for i, e in enumerate(toolchain.sources(toolchain.load(MANIFEST)))}
    manifest = fake(files)
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    commands = Commands()
    lock = toolchain.build(manifest, downloads=tmp_path, commit="b" * 40, volume="g6-v9-tc", lock=tmp_path / "l.json",
                           repository=tmp_path, run=commands)
    assert lock == json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))
    assert lock["g6_wheel"] == {"file": "g6-0.1.1-py3-none-any.whl", "sha256": sha(b"a wheel")}
    assert lock["tree_sha256"] == "ab" * 32 and lock["versions"]["git"] == "git version 2.56.0"
    assert set(lock["scripts_sha256"]) == {"build_static.sh", "install_python.sh", "toolchain.py"}
    assert ["git", "-C", str(tmp_path), "archive", "--format=tar"] == commands.seen[1][:5]   # the commit, not a tree
    with pytest.raises(ValueError, match="exists"):
        toolchain.build(manifest, downloads=tmp_path, commit="b" * 40, volume="g6-v9-tc", lock=tmp_path / "m.json",
                        repository=tmp_path, run=Commands(volume_exists=True))
    (tmp_path / next(iter(files))).write_bytes(b"changed")
    with pytest.raises(ValueError, match="do not match"):
        toolchain.build(manifest, downloads=tmp_path, commit="b" * 40, volume="v2", lock=tmp_path / "n.json",
                        repository=tmp_path, run=Commands())


def test_the_builders_run_from_pinned_images_with_the_sources_read_only_and_never_privileged():
    manifest = toolchain.load(MANIFEST)
    commands = toolchain.builder_commands(manifest, sources=Path("/src"), wheels=Path("/whl"), volume="g6-v9-tc",
                                          scripts=Path("/scripts"))
    assert len(commands) == 2                                              # static tools, then Python and G6
    assert "--network" not in commands[0] and commands[1][commands[1].index("--network") + 1] == "none"
    for command in commands:
        assert command[:3] == ["docker", "run", "--rm"] and "--privileged" not in command
        image = next(word for word in command if "@sha256:" in word)
        assert image in manifest["builders"].values()
        assert any(word.endswith(":/sources:ro") for word in command)
        assert "g6-v9-tc:/opt/g6" in command
