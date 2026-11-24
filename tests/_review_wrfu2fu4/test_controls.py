"""Attacked paths that held; all source and output fixtures are synthetic."""
import importlib.util
import math
import os
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("review4_controls", Path(__file__).with_name("test_review.py"))
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)
build, rc, shared, tmp_path = review.build, review.rc, review.shared, review.tmp_path


@pytest.mark.parametrize("value", [1, 1.0, 5e-324, -5e-324, math.nextafter(1.0, 2.0),
                                  1.7976931348623157e308])
@pytest.mark.parametrize("source", [False, True], ids=["batch", "source-refresh"])
def test_finite_numbers_keep_the_writer_value(build, capsys, value, source):
    batch = build(shared.arguments(value))
    sent = shared.arguments(value)
    for record in batch.saved["records"]:
        assert rc._json_equal(record["arguments"]["template_instances"], sent["template_instances"])
    if source:
        batch.saved["evidence_rule_version"] = 0
    status, result = shared.score(batch, capsys, source)
    assert (status, result["predictions"]["P2"]["status"]) == (0, "met")


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source-refresh"])
def test_neighbouring_subnormal_mismatch_is_incomplete(build, capsys, source):
    batch = build(shared.arguments(5e-324))
    record = batch.saved["records"][0]
    record["arguments"]["template_instances"][0]["bindings"]["cases"][0]["args"][0] = 1e-323
    if source:
        batch.saved["evidence_rule_version"] = 0
    _, result = shared.score(batch, capsys, source)
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source-refresh"])
def test_real_reviewer_float_timeout_refusal_never_scores_met(build, capsys, source):
    request = shared.arguments()
    request["timeout_s"] = 600.0
    batch = build(request)
    assert batch.live["predictions"]["P2"]["status"] == "incomplete"
    assert all(r["status"] == "failed" and r["result"] == "no review"
               and r["review"]["ok"] is False for r in batch.saved["records"])
    if source:
        batch.saved["evidence_rule_version"] = 0
    _, result = shared.score(batch, capsys, source)
    assert result["predictions"]["P2"]["status"] == "incomplete"


@pytest.mark.parametrize("name", ["../run/x", "run/../x", "run/x/./y", "run//x/y",
                                  "run\\x\\y", "run/x/y.", "run/x/y "])
def test_unsafe_relative_names_have_no_identity(name):
    assert rc._origin_run_identity(name) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_junction_is_refused_with_source(build, capsys):
    batch = build()
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    folder = batch.source / wrong["origin_run"]
    alias = folder.with_name("JunctionAlias")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(folder)],
                          capture_output=True, check=False)
    assert made.returncode == 0, made.stderr
    assert alias.samefile(folder)
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    case["origins"][0]["run"] = right["origin_run"] = alias.relative_to(batch.source).as_posix()
    _, result = shared.score(batch, capsys, source=True)
    assert result["predictions"]["P2"]["status"] == "incomplete"


def test_source_refresh_requires_the_original_archive_root(build, tmp_path, capsys):
    batch = build()
    batch.saved["evidence_rule_version"] = 0
    copied = tmp_path / "different-source"
    # Copy only fixture bytes, without reading any real archive or dataset.
    for path in batch.source.rglob("*"):
        target = copied / path.relative_to(batch.source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    batch.source = copied
    status, result = shared.score(batch, capsys, source=True)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        2, "unavailable", "incomplete")


@pytest.mark.parametrize("source", [False, True])
def test_unicode_normalisation_forms_remain_distinct(tmp_path, source):
    names = ["run/synthetic/\u00e9", "run/synthetic/e\u0301"]
    for name in names:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    assert not (tmp_path / names[0]).samefile(tmp_path / names[1])
    identities = [rc._origin_run_identity(name, tmp_path if source else None) for name in names]
    assert None not in identities and identities[0] != identities[1]


@pytest.mark.parametrize("refresh", [False, True], ids=["saved-batch", "source-refresh"])
def test_recreated_folder_keeps_batch_provenance_or_refuses_changed_source(build, tmp_path, capsys, refresh):
    batch = build()
    folder = batch.runs[0].path
    old_id = folder.stat().st_ino
    removed = tmp_path / "old-folder"
    assert folder.resolve().is_relative_to(tmp_path.resolve())
    folder.rename(removed)
    folder.mkdir()
    assert folder.stat().st_ino != old_id
    for path in removed.rglob("*"):
        target = folder / path.relative_to(removed)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    (folder / "final.patch").write_bytes(b"replacement patch")
    if refresh:
        batch.saved["evidence_rule_version"] = 0
    status, result = shared.score(batch, capsys, source=True)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    assert observed == ((2, "unavailable", "incomplete") if refresh else (0, "batch", "met"))
