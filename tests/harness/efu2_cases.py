"""Local review fixtures; runner calls are fakes and start no processes."""
import time
from pathlib import Path
from types import SimpleNamespace

from g6.harness import patch_review as review
from g6.harness import requirement_evidence as evidence

NODE = "test_calc.py::test_add"
CHECK = NODE
PASSED = {"setup": "passed", "call": "passed", "teardown": "passed"}
FAILED = {"setup": "passed", "call": "failed", "teardown": "passed"}


def report(phases, **over):
    failed = [NODE] if "failed" in phases.values() else []
    return {
        "verdict": "failed" if failed else "passed", "said": "", "output": "",
        "rootdir": "", "test_outcomes": {NODE: dict(phases)}, "failed_ids": failed,
        "setup_errors": {}, "collected_ids": [NODE], "omitted_ids": [],
        "isolation": {"backend": "recorded-review-fixture"}, **over,
    }


def assess_runs(monkeypatch, base, patch, observed=()):
    monkeypatch.setattr(review, "_execute_at_base", lambda *a, **k: base)
    monkeypatch.setattr(review, "_execute", lambda *a, **k: patch)
    req = review.Requirement(id="R1", text="add returns a sum", checks=[CHECK])
    change = SimpleNamespace(files=("calc.py",), sources=("calc.py",), base_commit="0" * 40)
    verdicts = review._verdicts([CHECK], patch)
    runs = []
    deadline = time.monotonic() + 30
    review._at_base(change, [req], {CHECK: CHECK}, verdicts, deadline, [], runs, [], [])
    again = review._run_again(Path.cwd(), change, [req], {CHECK: CHECK}, verdicts,
                              runs, deadline, "a" * 64)
    return evidence.assess(
        [review._requirement(req, verdicts, {CHECK: CHECK})], runs,
        artifact="a" * 64, base_commit=change.base_commit,
        check_sha={CHECK: "c" * 64}, written=lambda _: False,
        environment={}, again=again, observed=observed,
    )
