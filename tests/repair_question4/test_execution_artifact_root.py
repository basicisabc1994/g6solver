"""Versioned full-byte capture precedes review execution; sparse identities keep their meaning."""
import hashlib
import os
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.harness import verification_search as vs
from g6.workforce import repo_tests

from tests.repair_question2.helpers import repo


def test_capture_keeps_raw_normalised_bytes_and_unchanged_dependencies(tmp_path):
    root = repo(tmp_path, extra={"helper.py": "VALUE = 1\n"})
    change = repo_tests.change(root)
    sparse = repo_tests.snapshot(root)
    captured = vs.bind_execution(change, sparse, deadline=vs._clock() + 60)
    assert captured == sparse and digest(captured) == digest(sparse)
    assert captured.execution["version"] == "execution-artifact.v1"
    assert captured.execution["files"]["helper.py"] == hashlib.sha256((root / "helper.py").read_bytes()).hexdigest()
    (root / "helper.py").write_text("VALUE = 2\n")
    assert vs._copy_manifest(change, deadline=vs._clock() + 60, artefact=captured) == captured.execution["files"]


@pytest.mark.parametrize("damage", ["dependency", "extra", "missing", "unreadable"])
def test_manifest_comparison_covers_every_copied_file(tmp_path, damage):
    root = repo(tmp_path, extra={"helper.py": "VALUE = 1\n"})
    expected = vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60)
    copied = {**expected, "test_g6.py": "check"}
    if damage == "extra":
        copied["extra.py"] = "x" * 64
    elif damage == "missing":
        del copied["helper.py"]
    elif damage == "unreadable":
        expected["helper.py"] = "unreadable"
    else:
        copied["helper.py"] = "a" * 64
    assert not vs._same_copy(copied, expected, "test_g6.py")


@pytest.mark.skipif(os.name != "nt", reason="Win32 rewrites a path before it opens it")
def test_a_listed_name_windows_reads_as_another_files_is_never_recorded_with_that_files_bytes(tmp_path):
    """git lists ``dir./x.py`` (stored by its literal name); a plain open of that name reaches ``dir/x.py``. The capture
    records under a listed name only what the disk rule read by that name (``repo_tests._disk``), as the copy does."""
    root = repo(tmp_path, extra={"dir/x.py": "OTHER = 'what a plain open of the listed name reaches'\n"})
    other, listed = (root / "dir" / "x.py").read_bytes(), b"LISTED = 'what is stored under the listed name'\n"
    folder = Path("\\\\?\\" + str(root / "dir."))
    folder.mkdir()
    (folder / "x.py").write_bytes(listed)
    try:
        assert (folder / "x.py").read_bytes() == listed and (root / "dir" / "x.py").read_bytes() == other   # two files
        change = repo_tests.change(root)
        assert "dir./x.py" in map(repo_tests.shown_name, change.files)      # git lists it
        try:
            files = vs._copy_manifest(change, deadline=vs._clock() + 60)
        except (repo_tests.RepoRefused, OSError):
            return      # refused: nothing is recorded under the listed name
    finally:
        os.unlink(folder / "x.py")
        os.rmdir(folder)
    recorded = {repo_tests.shown_name(name): value for name, value in files.items()}
    assert recorded["dir/x.py"] == hashlib.sha256(other).hexdigest()
    assert recorded.get("dir./x.py") in (None, hashlib.sha256(listed).hexdigest()), (
        "the execution manifest records dir./x.py with dir/x.py's bytes; the file stored under that name was never read")


def test_legacy_snapshot_cannot_supply_a_full_byte_binding(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(OSError, match="execution-artifact"):
        vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60, artefact=repo_tests.snapshot(root))

