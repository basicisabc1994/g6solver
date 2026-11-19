# SPDX-License-Identifier: Apache-2.0
"""Forty synthetic families, real mutation accounting, fake executions only."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from g6.workforce import mutation_check

from .runbook import ISSUE_TEMPLATE


class SimulatedClock:
    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds

    def advance(self, seconds):
        if seconds < 0:
            raise ValueError("clock cannot go backwards")
        self.seconds += seconds


def dry_run(*, survive=False, episode_seconds=60, actor_text=""):
    """Fault switches exercise bars; no generated source is executed."""
    if type(episode_seconds) not in (int, float) or episode_seconds < 6:
        raise ValueError("episode must cover the six simulated mutant runs")
    clock = SimulatedClock()
    families = []
    for index in range(40):
        started = clock()
        code = f"def boundary_{index}(value):\n    return value < {index + 1}\n"
        execution_count = 0

        def run(_changed_source, _timeout):
            nonlocal execution_count
            execution_count += 1
            clock.advance(1)
            return SimpleNamespace(execution_id=f"fake-{index}-{execution_count}",
                                   passed=1 if survive else 0, failed=0 if survive else 1, reason="")

        result = mutation_check.check(code, run, budget_s=1200, base_duration_s=1, clock=clock)
        clock.advance(max(0, episode_seconds - (clock() - started)))
        families.append({"id": f"fake-boundary-{index:02d}", "source_sha256": hashlib.sha256(code.encode()).hexdigest(),
                         "issue_template": ISSUE_TEMPLATE, "actor_text": actor_text,
                         "episode_seconds": clock() - started, "mutation_check": result})
    # Explicitly synthetic manifest evidence: this dry run has no model directory.
    marker = hashlib.sha256(b"S0 fake base; no model opened").hexdigest()
    return {"schema": 1, "step": "P0-9", "status": "ok", "mode": "fake",
            "manifest": {"model_directory": "fake://no-model", "sha256": marker,
                         "sha256_after": marker, "expected": {"fake-base": marker},
                         "before": {"fake-base": marker}, "after": {"fake-base": marker}},
            "metrics": {"families": families}}
