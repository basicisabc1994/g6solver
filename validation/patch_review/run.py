"""Run the frozen synthetic patch-review engineering trial without model calls."""
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
TEST = "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
CASES = (
    ("correct", GOOD, TEST, False, False, True),
    ("unfixed", BAD, TEST, False, False, False),
    ("weakened-test", BAD, "def test_add():\n    assert True\n", False, False, False),
    ("runner-changed", GOOD, TEST, True, False, False),
    ("unselected-regression", "def add(a, b):\n    return abs(a + b)\n", TEST, False, False, False),
    ("stale-artifact", GOOD, TEST, False, True, False),
)


def run() -> dict:
    rows = []
    for name, code, test, runner_changed, stale, acceptable in CASES:
        with tempfile.TemporaryDirectory(prefix="g6-patch-trial-") as folder:
            root = Path(folder)
            (root / "calc.py").write_text(BAD, encoding="utf-8")
            (root / "test_calc.py").write_text(TEST, encoding="utf-8")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline"]):
                subprocess.run(["git", "-C", folder, *args], check=True, capture_output=True)
            (root / "calc.py").write_text(code, encoding="utf-8")
            (root / "test_calc.py").write_text(test, encoding="utf-8")
            if runner_changed:
                (root / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
            start = time.monotonic()
            result = review_patch(root, task="Return the sum for positive and negative integers.",
                                  claim="Addition returns the sum.",
                                  sources={"spec": "Addition returns the sum."}, tests=["test_calc.py"],
                                  expected_artifact_digest="outdated" if stale else "")
            elapsed = time.monotonic() - start
            ran = result.get("tests")
            if ran and not ran["isolation"].get("backend"):
                raise RuntimeError("Actual confinement required; unconfined runs are not trial evidence")
            if not stale and ran is None:
                raise RuntimeError(f"{name}: test execution unavailable: {result}")
            baseline_accept = bool(ran and ran["verdict"] == "passed" and ran["passed"] > 0)
            rows.append({"case": name, "acceptable_by_fixture_spec": acceptable,
                         "baseline_accept": baseline_accept if ran else None,
                         "g6_decision": result.get("decision", "blocked"),
                         "completion_state": result["completion_state"], "seconds": round(elapsed, 3),
                         "test_result": ran, "unresolved": result.get("unresolved", []),
                         "warning_card": result.get("warning_card"),
                         "executed_checks": result.get("executed_checks", []),
                         "unselected_requirement": "add(-2, -3) == -5" if name == "unselected-regression" else None})
    paired = [r for r in rows if r["baseline_accept"] is not None]
    return {"study": "synthetic-engineering-only", "model_calls": 0, "cases": rows,
            "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (Path(__file__), Path(__file__).with_name("PROTOCOL.md"))},
            "paired_cases": len(paired),
            "baseline_false_acceptances": sum(r["baseline_accept"] and not r["acceptable_by_fixture_spec"]
                                              for r in paired),
            "g6_acceptances": sum(r["g6_decision"] == "allow" for r in rows),
            "g6_correct_patches_blocked": sum(r["acceptable_by_fixture_spec"] and r["g6_decision"] == "blocked"
                                              for r in rows),
            "interpretation": "No utility claim: blocking every patch prevents acceptance but also prevents useful acceptance. "
                              "Selected test receipts cannot detect the unselected arithmetic regression."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), indent=2) + "\n", encoding="utf-8")
