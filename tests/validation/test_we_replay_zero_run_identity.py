# SPDX-License-Identifier: Apache-2.0
"""Repair 3: signed JSON zero and archive run folder identity."""
from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

_shared = importlib.util.spec_from_file_location(
    "repair3_shared_archive", Path(__file__).with_name("test_we_replay_saved_trust.py"))
trust = importlib.util.module_from_spec(_shared)
_shared.loader.exec_module(trust)
batch = trust.batch
score = trust.score


@pytest.mark.parametrize("left,right,expected", [
    (0.0, -0.0, False), (-0.0, 0.0, False),
    (0.0, 0.0, True), (-0.0, -0.0, True),
    (1.25, 1.25, True), (-1.25, -1.25, True), (1.25, -1.25, False),
    (float("nan"), float("nan"), False),
    (float("inf"), float("inf"), False),
    (float("-inf"), float("-inf"), False),
])
def test_float_value_and_sign_match_at_every_json_depth(left, right, expected):
    for wrap in (lambda value: value, lambda value: [value],
                 lambda value: {"bindings": [{"args": [value]}]}):
        assert rc._json_equal(wrap(left), wrap(right)) is expected


@pytest.mark.parametrize("alias", [False, True], ids=["exact", "case-alias"])
@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
def test_alias_conflict_uses_incomplete_route(batch, capsys, alias, source):
    right = next(r for r in batch.saved["records"] if r["mode"] == "agent" and r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["mode"] == "agent" and r["label"] == "wrong")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    origin = next(o for o in case["origins"] if o["run"] == right["origin_run"])
    run = wrong["origin_run"]
    if alias:
        if os.name != "nt":
            pytest.skip("requires Windows case aliases")
        prefix, name = run.rsplit("/", 1)
        run = prefix + "/" + name.swapcase()
        assert run != wrong["origin_run"]
        assert (batch.source / run).samefile(batch.source / wrong["origin_run"])
    origin["run"] = right["origin_run"] = run
    origin["arguments"] = copy.deepcopy(wrong["arguments"])
    right["arguments"] = copy.deepcopy(wrong["arguments"])
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    assert status == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("present", [False, True], ids=["absent", "on-disk"])
@pytest.mark.parametrize("conflict", [False, True], ids=["same-digest", "different-digest"])
def test_folder_identity_binds_one_digest(tmp_path, present, conflict):
    if os.name != "nt":
        pytest.skip("requires Windows case aliases")
    run = "run/synthetic/MixedCase"
    if present:
        (tmp_path / run).mkdir(parents=True)
        assert (tmp_path / run).samefile(tmp_path / run.swapcase())
    cases = [SimpleNamespace(origins=[{"run": run}], digest="a"),
             SimpleNamespace(origins=[{"run": run.swapcase()}], digest="b" if conflict else "a")]
    assert rc._unique_origin_digests(cases, source=tmp_path) is (present and not conflict)


def test_source_refresh_rejects_case_aliased_origin_conflict(batch, monkeypatch, capsys):
    if os.name != "nt":
        pytest.skip("requires Windows case aliases")
    real_enumerate = rc.enumerate_cases

    def conflicting(*args, **kwargs):
        cases, excluded = real_enumerate(*args, **kwargs)
        assert len(cases) == 2 and cases[0].digest != cases[1].digest
        run = cases[0].origins[0]["run"]
        prefix, name = run.rsplit("/", 1)
        alias = prefix + "/" + name.swapcase()
        assert run != alias and (batch.source / run).samefile(batch.source / alias)
        cases[1].origins[0]["run"] = alias
        return cases, excluded

    batch.saved["evidence_rule_version"] = 0
    monkeypatch.setattr(rc, "enumerate_cases", conflicting)
    status, result = score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("run", [None, "", "../escape", "/absolute", "//host/share/run", "run/sealed/x"])
def test_unbound_or_unsafe_origin_never_gets_a_distinct_identity(run):
    cases = [SimpleNamespace(origins=[{"run": run}], digest="a")]
    assert not rc._unique_origin_digests(cases)


def test_unknown_folder_identity_is_unbound(tmp_path, monkeypatch):
    real_path = rc.Source.path

    def unavailable(source, run):
        if str(run) == "run/synthetic/unknown":
            raise OSError("synthetic identity unavailable")
        return real_path(source, run)

    monkeypatch.setattr(rc.Source, "path", unavailable)
    cases = [SimpleNamespace(origins=[{"run": "run/synthetic/unknown"}], digest="a")]
    assert not rc._unique_origin_digests(cases, source=tmp_path)


def test_source_free_identity_does_not_open_batch_named_paths(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("source-free identity opened a batch-named folder")

    monkeypatch.setattr(rc, "_open_handle", unexpected)
    cases = [SimpleNamespace(origins=[{"run": "run/synthetic/absent"}], digest="a")]
    assert rc._unique_origin_digests(cases)


def test_current_score_with_explicit_source_requires_decidable_identity(batch, monkeypatch, capsys):
    run = batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    real_path = rc.Source.path

    def unavailable(archive, relative):
        if archive.root == batch.source and str(relative) == run:
            raise OSError("synthetic identity unavailable")
        return real_path(archive, relative)

    monkeypatch.setattr(rc.Source, "path", unavailable)
    status, result = score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_nonfolder_origin_is_unbound(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run/not-a-folder").write_text("synthetic", encoding="utf-8")
    cases = [SimpleNamespace(origins=[{"run": "run/not-a-folder"}], digest="a")]
    assert not rc._unique_origin_digests(cases, source=tmp_path)
