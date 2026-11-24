# SPDX-License-Identifier: Apache-2.0
"""Folder identities with source access; ambiguous names without source access."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

_shared = importlib.util.spec_from_file_location(
    "folder_identity_archive", Path(__file__).with_name("test_we_replay_saved_trust.py"))
trust = importlib.util.module_from_spec(_shared)
_shared.loader.exec_module(trust)
batch = trust.batch


def test_missing_origin_folder_is_unbound(tmp_path):
    assert rc._origin_run_identity("run/synthetic/absent", tmp_path) is None


def test_file_identity_survives_folder_rename(tmp_path):
    original = tmp_path / "run/synthetic/original"
    original.mkdir(parents=True)
    before = rc._origin_run_identity("run/synthetic/original", tmp_path)
    assert before is not None
    original.rename(original.with_name("renamed"))
    after = rc._origin_run_identity("run/synthetic/renamed", tmp_path)
    assert after is not None
    assert before == after


def test_replaced_folder_has_new_identity(tmp_path):
    original = tmp_path / "run/synthetic/original"
    original.mkdir(parents=True)
    before = rc._origin_run_identity("run/synthetic/original", tmp_path)
    assert before is not None
    retained = original.with_name("retained")
    original.rename(retained)
    original.mkdir()
    assert not original.samefile(retained)
    after = rc._origin_run_identity("run/synthetic/original", tmp_path)
    assert after is not None
    assert before != after


def test_distinct_case_folders_keep_separate_patch_bindings(tmp_path):
    names = ["run/synthetic/MixedCase", "run/synthetic/mixedcase"]
    for name in names:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    if (tmp_path / names[0]).samefile(tmp_path / names[1]):
        pytest.skip("requires an already case-sensitive folder; no directory flags changed")
    cases = [SimpleNamespace(origins=[{"run": name}], digest=digest)
             for name, digest in zip(names, ["a" * 64, "b" * 64])]
    assert rc._unique_origin_digests(cases, source=tmp_path)


@pytest.mark.parametrize("names", [
    ("run/synthetic/MixedCase", "run/synthetic/mixedcase"),
    ("run/synthetic/LONGNA~1", "run/synthetic/LongName"),
    ("run/synthetic/LongName", "run/synthetic/LONGNA~1"),
], ids=["case-alias", "short-first", "short-second"])
def test_source_free_possible_aliases_are_incomplete(names, monkeypatch):
    def unexpected_open(*args, **kwargs):
        pytest.fail("source-free scoring opened an origin named by saved data")

    monkeypatch.setattr(rc, "_open_handle", unexpected_open)
    # Even equal digests cannot turn ambiguous names into a decided identity.
    cases = [SimpleNamespace(origins=[{"run": name}], digest="a" * 64) for name in names]
    assert not rc._unique_origin_digests(cases)


@pytest.mark.parametrize("names", [
    ("run/LONGPA~1/first", "run/LongParent/first"),
    ("run/synthetic/LO1234~1", "run/synthetic/LongName"),
    ("run/synthetic/LONGNA~1.DIR", "run/synthetic/LongName.Directory"),
], ids=["parent-alias", "hashed-alias", "extension-alias"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_source_free_short_aliases_at_each_depth_are_incomplete(names, reverse):
    cases = [SimpleNamespace(origins=[{"run": name}], digest="a" * 64)
             for name in (names[::-1] if reverse else names)]
    assert not rc._unique_origin_digests(cases)


@pytest.mark.parametrize("names", [
    ("run/synthetic/first", "run/synthetic/second"),
    ("run/first/LONGNA~1", "run/second/LongName"),
    ("run/synthetic/LongName", "round2/run/synthetic/LongName"),
    ("run/synthetic/\u00e9", "run/synthetic/e\u0301"),
], ids=["ordinary", "separate-parents", "separate-rounds", "unicode-forms"])
def test_source_free_noncolliding_names_remain_distinct(names, monkeypatch):
    def unexpected_open(*args, **kwargs):
        pytest.fail("source-free comparison opened an origin")

    monkeypatch.setattr(rc, "_open_handle", unexpected_open)
    cases = [SimpleNamespace(origins=[{"run": name}], digest=digest)
             for name, digest in zip(names, ["a" * 64, "b" * 64])]
    assert rc._unique_origin_digests(cases)


@pytest.mark.parametrize("conflict", [False, True], ids=["same-digest", "different-digest"])
def test_repeated_exact_name_retains_one_digest_binding(conflict):
    cases = [SimpleNamespace(origins=[{"run": "run/synthetic/exact"}], digest=digest)
             for digest in ["a" * 64, "b" * 64 if conflict else "a" * 64]]
    assert rc._unique_origin_digests(cases) is (not conflict)


def test_regular_file_is_not_a_folder_identity(tmp_path):
    path = tmp_path / "run/synthetic/file"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic")
    assert rc._origin_run_identity("run/synthetic/file", tmp_path) is None


def test_unreadable_folder_identity_is_unbound(tmp_path, monkeypatch):
    path = tmp_path / "run/synthetic/folder"
    path.mkdir(parents=True)

    def unreadable(*args, **kwargs):
        raise PermissionError("synthetic unreadable folder")

    if os.name == "nt":
        monkeypatch.setattr(rc._WindowsAPI, "file_identity", unreadable, raising=False)
        assert rc.Source(tmp_path, required=True).root == tmp_path
    else:
        monkeypatch.setattr(rc.os, "fstat", unreadable)
    cases = [SimpleNamespace(origins=[{"run": "run/synthetic/folder"}], digest="a" * 64)]
    assert not rc._unique_origin_digests(cases, source=tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX device and inode")
def test_posix_identity_uses_device_and_inode(tmp_path):
    path = tmp_path / "run/synthetic/folder"
    path.mkdir(parents=True)
    info = path.stat()
    assert rc._origin_run_identity("run/synthetic/folder", tmp_path) == (info.st_dev, info.st_ino)


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
def test_real_short_alias_has_the_same_file_identity(tmp_path):
    import ctypes

    folder = tmp_path / "run/synthetic/LongFolderName"
    folder.mkdir(parents=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetShortPathNameW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetShortPathNameW(str(folder), buffer, len(buffer))
    assert 0 < size < len(buffer), ctypes.get_last_error()
    short_name = Path(buffer.value).name
    if "~" not in short_name:
        pytest.skip("volume has no short-name alias")
    alias = "run/synthetic/" + short_name
    assert folder.samefile(tmp_path / alias)
    identity = rc._origin_run_identity("run/synthetic/LongFolderName", tmp_path)
    assert identity is not None and identity == rc._origin_run_identity(alias, tmp_path)


@pytest.mark.parametrize("alias", ["case", "short"])
def test_ambiguous_saved_names_take_incomplete_route(batch, capsys, alias):
    right = next(record for record in batch.saved["records"]
                 if record["mode"] == "agent" and record["label"] == "right")
    wrong = next(record for record in batch.saved["records"]
                 if record["mode"] == "agent" and record["label"] == "wrong")
    name = right["origin_run"]
    name = name.swapcase() if alias == "case" else name.rsplit("/", 1)[0] + "/LONGNA~1"
    case = next(case for case in batch.saved["plan"]["cases"] if case["digest"] == wrong["digest"])
    origin = next(origin for origin in case["origins"] if origin["run"] == wrong["origin_run"])
    origin["run"] = wrong["origin_run"] = name
    status, result = trust.score(batch, capsys)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        2, "unavailable", "incomplete")


def test_explicit_source_missing_origin_takes_incomplete_route(batch, tmp_path, capsys):
    record = next(record for record in batch.saved["records"] if record["mode"] == "agent")
    folder = batch.source / record["origin_run"]
    folder.rename(tmp_path / "removed-origin")
    status, result = trust.score(batch, capsys, source=True)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        2, "unavailable", "incomplete")


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_honest_distinct_short_and_long_names_score_met(batch, capsys, source):
    right_names = iter(("RIGHTI", "RIGHTII"))
    replacements = {}
    for case in batch.saved["plan"]["cases"]:
        for origin in case["origins"]:
            old = origin["run"]
            leaf = next(right_names) if case["label"] == "right" else "WRONGORIGIN"
            new = old.rsplit("/", 1)[0] + "/" + leaf
            (batch.source / old).rename(batch.source / new)
            replacements[old] = new
            origin["run"] = new
    for record in batch.saved["records"]:
        if record["mode"] == "agent":
            record["origin_run"] = replacements[record["origin_run"]]
    right = batch.source / next(name for name in replacements.values() if name.endswith("/RIGHTI"))
    wrong = batch.source / next(name for name in replacements.values() if name.endswith("/WRONGORIGIN"))
    assert not right.samefile(wrong)
    assert right.joinpath("final.patch").read_bytes() != wrong.joinpath("final.patch").read_bytes()
    status, result = trust.score(batch, capsys, source=source)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        0, "batch", "met")


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
@pytest.mark.parametrize("conflict", [False, True], ids=["same-digest", "different-digest"])
def test_exact_name_digest_binding_survives_replacement(tmp_path, monkeypatch, source, conflict):
    name = "run/synthetic/origin"
    folder = tmp_path / name
    folder.mkdir(parents=True)
    original = rc._origin_run_identity
    identities = []

    def identify(run, root=None):
        identity = original(run, root)
        identities.append(identity)
        if root is not None and len(identities) == 1:
            folder.rename(tmp_path / "retained-origin")
            folder.mkdir()
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    cases = [SimpleNamespace(origins=[{"run": name}], digest=digest)
             for digest in ["a" * 64, "b" * 64 if conflict else "a" * 64]]
    assert rc._unique_origin_digests(cases, source=tmp_path if source else None) is (not conflict)
    assert len(identities) == 2
    if source:
        assert None not in identities and identities[0] != identities[1]
