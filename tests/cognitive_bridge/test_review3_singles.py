# SPDX-License-Identifier: Apache-2.0
"""Review 3, findings 5 and 6. A path lookup decides by the lookup itself: only "no such file" in a folder that can be
listed is a file not made yet, and any other lookup error refuses the path (failure_read_as_success). Feature names are
compared as the tree will use them, as strings, and a collision is refused (identity_from_rendering)."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import adaptation, distill_blackbox, extract_tree

ROOT = Path(__file__).resolve().parents[2]


def _deny(monkeypatch, target: Path, error: OSError, name: str = "lstat"):
    real = getattr(os, name)
    denied = []

    def no_access(path, *args, **kwargs):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(target)):
            denied.append(str(path))
            raise error
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, name, no_access)
    return denied


@pytest.mark.parametrize("error", [PermissionError("review: metadata lookup denied"),
                                   OSError(errno.EIO, "simulated input/output error")], ids=["permission", "io"])
def test_finding_5_a_denied_lookup_cannot_admit_an_adaptable_path(monkeypatch, error):
    path = adaptation.ADAPTABLE[0]
    assert adaptation.protected_paths([path]) == []
    denied = _deny(monkeypatch, ROOT / path, error)
    result = adaptation.protected_paths([path])
    assert denied, "lookup interleaving did not run"
    assert result == [path], f"lookup failure was treated as an allowed new file: {result}"


def test_a_file_not_made_yet_needs_a_folder_that_can_be_listed(monkeypatch):
    path = adaptation.ADAPTABLE[0]
    denied = _deny(monkeypatch, (ROOT / path).parent, PermissionError("review: folder not listable"), "listdir")
    assert adaptation.protected_paths([path]) == [path] and denied


def test_finding_6_distinct_feature_names_that_render_identically_cannot_change_prediction():
    # Minimal fitted regression-tree protocol; no optional sklearn import or process.
    estimator = SimpleNamespace(
        n_features_in_=2, n_outputs_=1,
        tree_=SimpleNamespace(children_left=[1, -1, -1], children_right=[2, -1, -1],
                              feature=[0, -2, -2], threshold=[.5, -2., -2.],
                              value=[[[0.]], [[10.]], [[20.]]]))
    try:
        tree = extract_tree(estimator, [1, "1"])
    except (TypeError, ValueError):
        return
    row = {1: 0., "1": 1.}
    # Column 0 is 0, so the original tree follows its left branch.
    assert tree.predict([row]) == [10.], (
        f"feature identities collapsed to {tree.features}: prediction={tree.predict([row])}")


class _Named:
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


@pytest.mark.parametrize("names", [[1, "1"], [True, "True"], [_Named("age"), "age"]], ids=["int", "bool", "str-like"])
def test_names_that_read_alike_as_strings_are_refused_in_export_and_surrogate(names):
    # since follow-up 2 of PR #8 an object that only reads as text is refused where it enters, as not JSON data
    refused = "not JSON data" if isinstance(names[0], _Named) else "unique"
    estimator = SimpleNamespace(n_features_in_=2, n_outputs_=1, tree_=SimpleNamespace())
    with pytest.raises(ValueError, match=refused):
        extract_tree(estimator, names)
    rows = [[float(i), float(i % 3)] for i in range(20)]
    with pytest.raises(ValueError, match=refused):
        distill_blackbox(None, rows[:12], rows[12:], names)


def test_distinct_names_are_kept_as_the_strings_the_tree_reads():
    estimator = SimpleNamespace(
        n_features_in_=2, n_outputs_=1,
        tree_=SimpleNamespace(children_left=[1, -1, -1], children_right=[2, -1, -1], feature=[0, -2, -2],
                              threshold=[.5, -2., -2.], value=[[[0.]], [[10.]], [[20.]]]))
    tree = extract_tree(estimator, [1, 2])
    assert tree.features == ("1", "2") and tree.predict([{"1": 0., "2": 1.}]) == [10.]
