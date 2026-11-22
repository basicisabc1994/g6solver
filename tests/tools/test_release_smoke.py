# SPDX-License-Identifier: Apache-2.0
"""The release smoke must require a completed, confined fixture review."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_smoke", ROOT / "tools/mcp_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def answer(backend="bubblewrap"):
    return {"ok": True, "assessment": {"outcome": "supported_within_scope", "requirements": [
        {"id": "R1", "status": "passed_on_stated_checks"}], "scope": {
            "environment": {"isolation": {"backend": backend}},
            "checks_run": [{"tier": 1, "verdict": "passed", "passed": 1}]}}}


@pytest.mark.parametrize("backend", ["bubblewrap", "appcontainer"])
def test_patch_journey_requires_a_confined_completed_review(backend):
    calls = []

    def review(repo, **kwargs):
        calls.append((repo, kwargs))
        return answer(backend)

    assert smoke.check_patch_review("fixture", review=review, expected_backend=backend) == []
    assert len(calls) == 1
    request = calls[0][1]
    assert request["requirements"][0]["checks"] == ["test_calc.py::test_add"]
    assert request["search"] == {"propose": False}


@pytest.mark.parametrize("bad", [
    {"ok": False, "warning_card": {"code": "G6_E_ISOLATION_UNAVAILABLE"}},
    {"job_id": "pending", "status": "running"},
    {"ok": True, "assessment": {"outcome": "insufficient_evidence"}},
    answer(None), answer("synthetic-unit-fixture"),
])
def test_patch_journey_does_not_count_a_refusal_or_pending_job_as_success(bad):
    assert smoke.check_patch_review("fixture", review=lambda *a, **k: copy.deepcopy(bad),
                                   expected_backend="bubblewrap")


def test_ci_smoke_builds_wheel_and_uses_a_clean_venv_with_both_boundaries():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = text.split("  mcp-smoke:", 1)[1].split("  scan:", 1)[0]
    assert "os: [ubuntu-latest, windows-latest]" in job
    assert "python -m build --wheel" in job
    assert "-m venv" in job and ".whl" in job
    assert "tools/ci_bubblewrap.sh" in job
    # Owner ruling F2 (11-22): Windows runners have no AppContainer, so the confined patch review runs on Linux only
    # and the Windows job runs the handshake and consent journey; the limit is stated in the release notes.
    assert "icacls" not in job
    step = job.split("- name: Installed MCP handshake", 1)[1]
    windows, other = step.split("if ($IsWindows) {", 1)[1].split("} else {", 1)
    assert "tools/mcp_smoke.py" in windows and "--patch-review" not in windows
    assert "--patch-review" in other.split("}", 1)[0]
    notes = (ROOT / "docs/releases/v0.3.0.md").read_text(encoding="utf-8")
    assert "Windows CI runners" in notes and "AppContainer" in notes
    assert "G6_ALLOW_UNCONFINED" not in job
    assert "PYTHONPATH" in job  # explicitly removed before the installed-wheel journey
