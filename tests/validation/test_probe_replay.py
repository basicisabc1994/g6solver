# SPDX-License-Identifier: Apache-2.0
"""The replay of archived probes reads pytest's answer rightly: a broken instrument must not look like a clean pass."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import probe_replay as pr  # noqa: E402


def test_a_clean_run_passes():
    cell = pr.summary("........ [100%]\n8 passed in 0.40s\n")
    assert cell["passed"] == 8 and pr.verdict(cell) == "."


def test_a_failing_run_names_its_tests():
    cell = pr.summary("F.\nFAILED ../../tmp/p-L2-1/test_eof.py::test_cross[a-b] - assert 1\n1 failed, 1 passed in 0.4s\n")
    assert cell["ids"] == ["test_eof.py::test_cross[a-b]"] and pr.verdict(cell) == "X"


def test_a_collection_error_is_not_a_pass():
    cell = pr.summary("ERROR ../../tmp/p/test_eof.py\n1 error in 0.41s\n")
    assert pr.verdict(cell) == "?"


def test_a_summary_the_replay_cannot_read_is_not_a_pass():
    assert pr.verdict(pr.summary("Results (2.29s):\n       426 failed\n       200 passed\n        36 skipped\n")) == "?"
    assert "-p no:pretty" in pr.RUN


def test_no_output_is_not_a_pass():
    assert pr.verdict(pr.summary("")) == "?"


def test_failures_with_a_collection_error_still_say_could_not_run():
    assert pr.verdict(pr.summary("3 failed, 2 passed, 1 error in 1.0s\n")) == "?"


def test_the_table_marks_accepted_and_rejected_patches():
    cell = {"ran": True, "passed": 1, "failed": 0, "errors": 0, "ids": []}
    text = pr.table({"task": "t", "sets": ["L3-1"], "patches": {"A-1": True, "A-2": False},
                     "matrix": {"base": {"L3-1": cell}, "A-1": {"L3-1": cell}, "A-2": {"L3-1": cell},
                                "E-1": {"unapplied": True}}})
    assert "B base" in text and "+ A-1" in text and "- A-2" in text and "E-1" not in text


def test_held_out_bugs_are_refused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe_replay", pr.we.R3_HELD[0]])
    with pytest.raises(SystemExit, match="held-out"):
        pr.main()
