# SPDX-License-Identifier: Apache-2.0
"""g6.integrity (audit 020 IS-003, IS-004): a match is verified only against a pinned manifest. Offline."""
from __future__ import annotations

from g6.integrity import (build_manifest, manifest_sha256, read_manifest, verify_installed, verify_tree,
                          write_manifest)


def _tree(tmp_path):
    root = tmp_path / "pkg"
    (root / "sub").mkdir(parents=True)
    (root / "a.py").write_text("A = 1\n")
    (root / "sub" / "b.py").write_text("B = 2\n")
    return root


def test_a_pinned_matching_manifest_is_verified_and_an_unpinned_one_is_a_draft(tmp_path) -> None:
    root = _tree(tmp_path)
    manifest = build_manifest(root)
    pin = write_manifest(manifest, tmp_path / "MANIFEST")
    assert read_manifest(tmp_path / "MANIFEST") == manifest and pin == manifest_sha256(manifest)
    assert verify_tree(root, manifest, expected_manifest_sha256=pin).reliability_label == "verified"
    unpinned = verify_tree(root, manifest)
    assert unpinned.matched and unpinned.reliability_label == "qualified-draft" and "not pinned" in unpinned.problems[0]


def test_is004_no_manifest_is_never_integrity_verified(tmp_path) -> None:
    root = _tree(tmp_path)
    result = verify_tree(root, read_manifest(tmp_path / "absent"))
    assert result.manifest_present is False and result.matched is False
    assert result.reliability_label == "blocked-escalated"


def test_is003_a_regenerated_manifest_does_not_match_the_pin(tmp_path) -> None:
    """Whoever edits the code can rebuild the manifest beside it; the pin kept elsewhere catches that."""
    root = _tree(tmp_path)
    pin = manifest_sha256(build_manifest(root))
    (root / "a.py").write_text("A = 'tampered'\n")
    regenerated = build_manifest(root)
    result = verify_tree(root, regenerated, expected_manifest_sha256=pin)
    assert result.pinned is False and result.reliability_label == "blocked-escalated"


def test_changed_missing_and_extra_files_are_named(tmp_path) -> None:
    root = _tree(tmp_path)
    manifest = build_manifest(root)
    (root / "a.py").write_text("A = 3\n")
    (root / "sub" / "b.py").unlink()
    (root / "c.py").write_text("C = 4\n")
    result = verify_tree(root, manifest)
    assert (result.modified, result.missing, result.unexpected) == (("a.py",), ("sub/b.py",), ("c.py",))
    assert result.reliability_label == "blocked-escalated"


def test_the_running_package_matches_its_own_freshly_built_manifest() -> None:
    import g6
    from pathlib import Path

    manifest = build_manifest(Path(g6.__file__).resolve().parent)
    result = verify_installed(manifest, expected_manifest_sha256=manifest_sha256(manifest))
    assert result.matched and result.reliability_label == "verified"
