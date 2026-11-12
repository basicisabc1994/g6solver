"""Versioned full-byte capture precedes review execution; sparse identities keep their meaning."""
import hashlib

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


def test_legacy_snapshot_cannot_supply_a_full_byte_binding(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(OSError, match="execution-artifact"):
        vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60, artefact=repo_tests.snapshot(root))

