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


# --- G6's own re-check at the door (plan A1): the label is re-derived from the files, never taken from the fields ---

def test_a_hand_built_pinned_result_is_not_verified() -> None:
    """Anyone can construct an IntegrityResult that says matched and pinned; with no tree to re-check it is not
    verified."""
    from g6.integrity import IntegrityResult

    forged = IntegrityResult(manifest_present=True, matched=True, pinned=True, missing=(), modified=(), unexpected=(),
                             problems=())
    assert forged.reliability_label == "qualified-draft"


def test_a_tree_changed_after_the_check_is_not_verified(tmp_path) -> None:
    root = _tree(tmp_path)
    manifest = build_manifest(root)
    result = verify_tree(root, manifest, expected_manifest_sha256=manifest_sha256(manifest))
    assert result.reliability_label == "verified"
    (root / "a.py").write_text("A = 'edited after the check'\n")
    assert result.reliability_label == "blocked-escalated"


def test_a_result_claiming_a_pin_it_does_not_have_is_not_verified(tmp_path) -> None:
    from dataclasses import replace

    root = _tree(tmp_path)
    manifest = build_manifest(root)
    honest = verify_tree(root, manifest)                 # unpinned: a qualified draft
    assert replace(honest, pinned=True).reliability_label != "verified"


def test_the_recheck_plants_a_control_and_a_comparison_that_misses_it_vouches_for_nothing(tmp_path,
                                                                                         monkeypatch) -> None:
    from g6.integrity import tree

    root = _tree(tmp_path)
    manifest = build_manifest(root)
    result = verify_tree(root, manifest, expected_manifest_sha256=manifest_sha256(manifest))
    monkeypatch.setattr(tree, "_differences", lambda actual, listed: ((), (), ()))   # a comparison that sees nothing
    assert result.reliability_label != "verified"


def test_g6s_integrity_recheck_is_what_promotes_at_the_core_door(tmp_path) -> None:
    from dataclasses import asdict

    from g6.core.reliability_envelope import door_witness, envelope_fields

    root = _tree(tmp_path)
    manifest = build_manifest(root)
    value = asdict(verify_tree(root, manifest, expected_manifest_sha256=manifest_sha256(manifest)))
    witness = door_witness("integrity", value)
    claimed = {"reliability_label": "verified", "evidence": [{"kind": "integrity"}], "promotion_witness": witness}
    assert envelope_fields(**claimed)["completion_state"] == "verified"
    (root / "sub" / "b.py").write_text("B = 'changed after the check'\n")
    assert envelope_fields(**claimed)["completion_state"] == "qualified-draft"
    assert door_witness("integrity", {"completion_state": "verified", "matched": True, "pinned": True}) is None