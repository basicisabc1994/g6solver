"""Focused safety and end-to-end controls, using only synthetic archives."""
import importlib.util
import json
from pathlib import Path

import pytest
from worked_examples import replay_rc as rc

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


synthetic = load("wrfu4_3_controls", "_review_wrfu3_2/test_review.py")
review2 = load("wrfu4_3_previous", "_review_wrfu4_2/test_review.py")
fu4 = load("wrfu4_3_builder", "validation/test_we_replay_fu4.py")
batch, tmp_path = synthetic.batch, synthetic.tmp_path

# Reuse previous tests' assertions, with this file's real basetemp fixture.
test_roundtrip = synthetic.test_honest_replay_save_and_rescore
test_malformed = synthetic.test_malformed_origin_is_unbound
test_one_digest = synthetic.test_reused_exact_origin_one_patch_across_bugs
test_ancestor_access = review2.test_traverse_ancestors_need_no_listing
test_alias_pins = review2.test_aliases_keep_shared_pins_alive
test_rename_pins = review2.test_pins_block_rename_until_comparison_finishes
test_descriptor_cleanup = review2.test_content_descriptor_exhaustion_closes_native_handles
test_all_cleanup = review2.test_exhaustion_releases_every_handle
test_shared_budget = fu4.test_shared_ancestors_fit_a_folder_budget


@pytest.mark.parametrize("batch", [["agent"], ["hook"]], indirect=True, ids=["agent", "hook"])
@pytest.mark.parametrize("mutation", ["none", "source", "manifest", "arguments", "response", "newer"])
def test_fresh_scoring_keeps_evidence_bound(batch, capsys, mutation):
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    if mutation == "source":
        (batch.source / batch.names[0] / "final.patch").write_bytes(b"new synthetic bytes")
    elif mutation == "manifest":
        batch.saved["plan"]["source_snapshot"]["identity_sha256"] = "0" * 64
    elif mutation == "arguments":
        batch.saved["records"][0]["arguments"]["task"] = "unreviewed synthetic task"
    elif mutation == "response":
        batch.saved["records"][0]["review"] = {"ok": False}
    elif mutation == "newer":
        batch.saved["evidence_rule_version"] = 2
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
    captured = capsys.readouterr()
    assert captured.out, captured.err
    result = json.loads(captured.out)
    prediction = result["predictions"][batch.prediction]
    if mutation == "none":
        assert (status, result["evidence_source"], prediction["status"]) == (0, "source", "met")
    else:
        assert prediction["status"] == "incomplete", result
        assert prediction["prediction_met"] is None
        assert status == (0 if mutation in {"arguments", "response"} else 2)


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("name", [None, "../outside", "run/x/.", "run/x ", "run/x:stream",
                                  "//no-network.invalid/share", r"\\?\C:\outside"])
def test_invalid_origin_does_not_open_outside(batch, monkeypatch, capsys, name):
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = name
    real, calls = rc._WindowsAPI._open, []

    def opening(api, path, access):
        assert not str(path).startswith("\\\\"), "network or namespace open"
        calls.append(path)
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    assert synthetic.score(batch, capsys) == (0, "source", "met")
    assert calls

review1 = load("wrfu4_3_first", "_review_wrfu4_1/test_review.py")
parent4 = load("wrfu4_3_parent_four", "_review_wrfu3_4/test_review.py")
test_interrupt_cleanup = review1.test_every_pin_failure_closes_handles_control
test_rule_versions = review1.test_replay_save_score_controls
test_junction_refusal = parent4.test_junction_alias_is_refused_control
test_parent_swap = synthetic.test_parent_swap_after_open_never_uses_redirected_identity

closure = load("wrfu4_3_closure", "validation/test_we_replay_review4_closure.py")
test_rule_digest = closure.test_evidence_rule_code_requires_a_version_change


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("code,reason", [(32, "the origin folder is in use by another program"),
                                         (5, "access denied"), (4, "too many open files")])
def test_unrecorded_existing_folder_failure_stays_terminal(batch, capsys, monkeypatch, code, reason):
    name = "run/black-5634/extra"
    folder = batch.source / name
    folder.mkdir()
    assert name + "/final.patch" not in batch.saved["plan"]["source_snapshot"]["files"]
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = name
    real = rc._WindowsAPI._open

    def opening(api, path, access):
        if path == folder:
            raise rc._WindowsAPI().ctypes.WinError(code)
        return real(api, path, access)

    def unexpected(*args, **kwargs):
        pytest.fail("a real folder-hold failure fell through to fresh scoring")

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    monkeypatch.setattr(rc, "enumerate_cases", unexpected)
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
    captured = capsys.readouterr()
    assert captured.out, captured.err
    result = json.loads(captured.out)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == "source folders could not be held: " + reason
