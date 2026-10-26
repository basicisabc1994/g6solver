# SPDX-License-Identifier: Apache-2.0
"""Study V2 population (VALIDATION_PLAN.md §0.1): every AgentDojo ground-truth tool call, one JSON line each.

    <agentdojo venv>/python -I validation/v2_action_gating/extract_calls.py --out CALLS.jsonl [--seed 20261018]

Run in a separate environment with ``agentdojo`` installed (pinned in ``docs/validation/REGISTRY.md``); G6 is not
imported. The dataset's authors wrote every task and its ground truth; this script only lists the calls. For each
suite of the benchmark version, the default environment is loaded with the default injection values, and each
task's ``ground_truth`` calls are written:

- a user task's calls are ``kind: user``, with that task's prompt as ``context``;
- an injection task's calls are ``kind: injection``, with its goal as ``goal``. Their ``context`` is the prompt of one
  user task of the same suite, drawn with ``random.Random(seed)``, because an injection arrives while the agent works
  on some user task.

Labels are assigned in ``run.py`` from ``kind`` and the frozen mapping, never here.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

BENCHMARK_VERSION = "v1.2.1"


def _plain(value):
    """A call argument as JSON: pydantic models and other objects become their dict or text."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    from agentdojo.task_suite.load_suites import get_suites

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261018)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    rows = []
    for suite_name, suite in sorted(get_suites(BENCHMARK_VERSION).items()):
        environment = suite.load_and_inject_default_environment(suite.get_injection_vector_defaults())
        users = dict(sorted(suite.user_tasks.items()))
        for task_id, task in users.items():
            for index, call in enumerate(task.ground_truth(environment.model_copy(deep=True))):
                rows.append({"suite": suite_name, "task_id": task_id, "kind": "user", "index": index,
                             "function": call.function, "args": _plain(dict(call.args)),
                             "context": task.PROMPT, "context_task": task_id})
        for task_id, task in sorted(suite.injection_tasks.items()):
            context_id = rng.choice(sorted(users))
            for index, call in enumerate(task.ground_truth(environment.model_copy(deep=True))):
                rows.append({"suite": suite_name, "task_id": task_id, "kind": "injection", "index": index,
                             "function": call.function, "args": _plain(dict(call.args)), "goal": task.GOAL,
                             "context": users[context_id].PROMPT, "context_task": context_id})
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"benchmark": BENCHMARK_VERSION, "seed": args.seed, "calls": len(rows),
                      "user_calls": sum(r["kind"] == "user" for r in rows),
                      "injection_calls": sum(r["kind"] == "injection" for r in rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
