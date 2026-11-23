# SPDX-License-Identifier: Apache-2.0
"""The public review lists selection rules and withholds no-load files from change evidence."""
import pytest

from g6.harness import patch_review as pr
from tests.harness.test_patch_review import GOOD, Recorded, repo, result, review  # noqa: F401, F811

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")


def test_review_reports_a_selected_file_that_did_not_load_change(repo, monkeypatch):  # noqa: F811
    (repo / "calc.py").write_text(GOOD)
    node = "test_calc.py::test_add"
    recorded = result([node])
    recorded.update(loaded_inside={}, loaded_outside={}, loaded_complete=True)
    Recorded(monkeypatch, default=recorded)
    got = review(repo, requirements=[])
    assessment = got["assessment"]
    assert "1 selected files did not load the changed code: test_calc.py" in assessment["unknowns"]
    run = next(run for run in assessment["scope"]["checks_run"] if run["tier"] == 2)
    assert run["tested_change"] is False
    assert run["selection"] == [{"rule": "imports", "distance": 1}, {"rule": "names"}]
    assert "test_calc.py (imports distance 1, names)" in assessment["report"]


def test_review_names_all_files_after_time_budget(repo, monkeypatch):  # noqa: F811
    (repo / "calc.py").write_text(GOOD)
    # No new test files: adding tests would correctly trigger other integrity rules.
    monkeypatch.setattr(pr, "_left", lambda deadline: 0)
    got = review(repo, requirements=[])
    skipped = got["assessment"]["scope"]["checks_skipped"]
    assert any(row["check"] == "test_calc.py" and row["reason"] == "time budget" for row in skipped)
