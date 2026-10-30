"""Run the synthetic patch-review engineering trial without model calls (development fixtures, never V9 data)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

from g6.harness.patch_review import review_patch


GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"
TEST_ADD = "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
TEST = TEST_ADD + "def test_negative():\n    assert add(-2, -3) == -5\n"
#: Ground truth comes from the arithmetic specification: the sum for positive and negative integers.
POSITIVE = {"id": "R1", "text": "add returns the sum of two positive integers", "checks": ["test_calc.py::test_add"]}
NEGATIVE = {"id": "R2", "text": "add returns the sum of two negative integers",
            "checks": ["test_calc.py::test_negative"]}
CHECKED = [POSITIVE, NEGATIVE]
UNCHECKED = [POSITIVE, {**NEGATIVE, "checks": []}]
#: name, code, test, runner changed, stale digest, acceptable by the fixture's specification, requirements, expected
CASES = (
    ("correct", GOOD, TEST, False, False, True, CHECKED, "supported_within_scope"),
    ("unfixed", "def add(a, b):\n    return b - a\n", TEST, False, False, False, CHECKED, "defect_detected"),
    ("weakened-test", BAD, "def test_add():\n    assert True\n", False, False, False, CHECKED,
     "test_integrity_compromised"),
    ("runner-changed", GOOD, TEST, True, False, False, CHECKED, "test_integrity_compromised"),
    ("unchecked-requirement", "def add(a, b):\n    return abs(a + b)\n", TEST_ADD, False, False, False, UNCHECKED,
     "insufficient_evidence"),
    ("stale-artifact", GOOD, TEST, False, True, False, CHECKED, None),
)
ACCEPTED = "supported_within_scope"
DETECTED = ("defect_detected", "test_integrity_compromised")


def row(name: str, acceptable: bool, result: dict | None, elapsed: float, *, error: str = "",
        expected: str | None = None) -> dict:
    """One trial row from G6's reply: the outcome, the per-stage reason codes, the checks run and their cost. A
    refusal has no outcome; a tool error has no reply."""
    assessment = (result or {}).get("assessment") or {}
    scope = assessment.get("scope") or {}
    runs = scope.get("checks_run") or []
    first = next((run for run in runs if run.get("tier") == 1), None)
    return {"case": name, "acceptable_by_fixture_spec": acceptable, "expected_outcome": expected,
            "baseline_accept": None if first is None else bool(first["verdict"] == "passed" and first["passed"] > 0),
            "g6_outcome": assessment.get("outcome"),
            "g6_accept": assessment.get("outcome") == ACCEPTED and all(
                r["status"] == "passed_on_stated_checks" for r in assessment.get("requirements", [])),
            "refused": result is not None and not assessment,
            "tool_error": error or ("" if result is not None else "no reply"),
            "completion_state": (result or {}).get("completion_state"),
            "warning_code": ((result or {}).get("warning_card") or {}).get("code"),
            "reason_codes": [f"{r['stage']}:{r['code']}" for r in assessment.get("reasons", [])],
            "requirements": {r["id"]: r["status"] for r in assessment.get("requirements", [])},
            "checks_run": [{key: run.get(key) for key in ("tier", "check", "command", "verdict", "seconds")}
                           for run in runs],
            "checks_skipped": scope.get("checks_skipped", []),
            "isolation": (scope.get("environment") or {}).get("isolation"),
            "cost": {"seconds": round(elapsed, 3), "checks": len(runs)}}


def summarise(rows: list[dict]) -> dict:
    """The counts: acceptance is ``supported_within_scope`` with every requirement passed on its stated checks;
    refusals and tool errors are counted apart and are never detections."""
    paired = [r for r in rows if r["baseline_accept"] is not None]
    return {"paired_cases": len(paired),
            "baseline_false_acceptances": sum(r["baseline_accept"] and not r["acceptable_by_fixture_spec"]
                                              for r in paired),
            "g6_acceptances": sum(r["g6_accept"] for r in rows),
            "g6_correct_acceptances": sum(r["g6_accept"] and r["acceptable_by_fixture_spec"] for r in rows),
            "g6_false_acceptances": sum(r["g6_accept"] and not r["acceptable_by_fixture_spec"] for r in rows),
            "g6_detections": sum(r["g6_outcome"] in DETECTED for r in rows),
            "g6_insufficient_evidence": sum(r["g6_outcome"] == "insufficient_evidence" for r in rows),
            "g6_refusals": sum(r["refused"] for r in rows),
            "g6_tool_errors": sum(bool(r["tool_error"]) for r in rows),
            "outcomes_as_expected": sum(r["g6_outcome"] == r["expected_outcome"] for r in rows)}


def run() -> dict:
    rows = []
    for name, code, test, runner_changed, stale, acceptable, requirements, expected in CASES:
        with tempfile.TemporaryDirectory(prefix="g6-patch-trial-") as folder:
            root = Path(folder)
            (root / "calc.py").write_text(BAD, encoding="utf-8")
            (root / "test_calc.py").write_text(TEST_ADD if test == TEST_ADD else TEST, encoding="utf-8")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline"]):
                subprocess.run(["git", "-C", folder, *args], check=True, capture_output=True)
            (root / "calc.py").write_text(code, encoding="utf-8")
            (root / "test_calc.py").write_text(test, encoding="utf-8")
            if runner_changed:
                (root / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
            start = time.monotonic()
            result, error = None, ""
            try:
                result = review_patch(root, task="Return the sum for positive and negative integers.",
                                      requirements=requirements, claim="Addition returns the sum.",
                                      sources={"spec": "Addition returns the sum."},
                                      expected_artifact_digest="outdated" if stale else "")
            except Exception as exc:  # a tool error is counted, never dropped
                error = f"{type(exc).__name__}: {exc}"
            found = row(name, acceptable, result, time.monotonic() - start, error=error, expected=expected)
            if found["isolation"] is not None and not found["isolation"].get("backend"):
                raise RuntimeError("Actual confinement required; unconfined runs are not trial evidence")
            rows.append(found)
    return {"study": "synthetic-engineering-only", "model_calls": 0, "cases": rows,
            "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (Path(__file__), Path(__file__).with_name("PROTOCOL.md"))},
            **summarise(rows),
            "interpretation": "Development fixtures only; no utility claim. The unchecked-requirement patch passes "
                              "its one stated check and fails the negative-input requirement, which states no "
                              "check: G6 neither accepts nor detects it, and says the requirement is not established."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), indent=2) + "\n", encoding="utf-8")
