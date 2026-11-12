# SPDX-License-Identifier: Apache-2.0
"""Measuring the 16 seed strategies as fixed-strategy pairs (0.3 design 14.2; README.md beside this file).

Each seed is a candidate against no strategy, on the same cases, agent, information and budget. The policy in
``policy.json`` is the frozen one (``g6.learning.worth.DEFAULT_POLICY``): a policy file with other content is refused
before anything runs, the runner takes no policy from its caller, and the policy's digest is written into the receipt
ledger for the cohort before the first trial. Worth is read from the ledger and what the evaluator kept beside it.
Every model call passes G6's consent check; without the person's grant an arm is ``refused`` and stays in its
receipt. Nothing here promotes a strategy.

    python validation/strategies/run.py --cohort <cohort.json> --replay <replay id> --agent-id <name of the model>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
POLICY = HERE / "policy.json"
BUDGET = {"max_calls": 100_000, "max_seconds": 600.0, "max_usd": 5.0}


def load_policy() -> Any:
    """The policy ``policy.json`` holds, when it is the frozen one; ``G6_E_STRATEGY_POLICY`` when the file was
    changed. The path is this folder's own: a caller names no other."""
    from g6.learning.strategy import StrategyError
    from g6.learning.worth import DEFAULT_POLICY, POLICY as REFUSED, WorthPolicy

    policy = WorthPolicy(**json.loads(POLICY.read_text(encoding="utf-8")))
    if policy.digest != DEFAULT_POLICY.digest:
        raise StrategyError(REFUSED, f"policy.json has digest {policy.digest}, and the frozen policy is "
                                     f"{DEFAULT_POLICY.digest}: a bar is not changed in place")
    return policy


def load_cohort(path: Path) -> tuple[str, str, list[Any]]:
    """``(cohort id, split, instances)`` from a cohort file. The split is held-out or shadow: a training split is
    never measured for worth."""
    from g6.learning.schema import TaskInstance

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(data) != {"cohort_id", "split", "instances"} or data["split"] not in ("heldout", "shadow"):
        raise ValueError("a cohort file is {cohort_id, split, instances}, its split heldout or shadow")
    instances = [TaskInstance(input=row["input"], expected_output=row.get("expected_output", ""),
                              instance_id=row["instance_id"], metadata=dict(row.get("metadata", {})))
                 for row in data["instances"]]
    if not instances or len({i.instance_id for i in instances}) != len(instances):
        raise ValueError("a cohort has at least one instance, each id once")
    return str(data["cohort_id"]), data["split"], instances


def run(cohort_path: Path, out_dir: Path | None = None, *, replay_id: str, agent_id: str, agent: Any = None,
        judge: Any = None, seed: int = 0, budget: Mapping[str, float] | None = None,
        clock: Callable[[], float] | None = None) -> dict[str, dict[str, Any]]:
    """Run every seed against no strategy on the cohort and answer each seed's worth, by seed name. ``agent`` stands
    in for the model in tests; the default is the person's own model, through the consent-guarded caller."""
    import g6
    from g6.core.consent_callers import provider_from_env as guarded_provider
    from g6.core.ledger import ChainLedger
    from g6.core.storage import g6_home
    from g6.learning.paired_eval import PairedEvaluator
    from g6.learning.strategy import StrategyStore, seed_artefacts
    from g6.learning.worth import bind_policy, worth

    policy = load_policy()
    cohort_id, split, instances = load_cohort(cohort_path)
    out_dir = Path(out_dir) if out_dir is not None else g6_home() / "strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    store, ledger = StrategyStore(out_dir), ChainLedger(out_dir / "receipts.jsonl")
    bind_policy(ledger, cohort_id, policy)                    # before the first trial; a changed policy is refused
    agent = agent if agent is not None else guarded_provider()
    environment = hashlib.sha256(json.dumps([g6.__version__, platform.python_version(), platform.system(), agent_id],
                                            sort_keys=True).encode("utf-8")).hexdigest()
    evaluator = PairedEvaluator(store=store, ledger=ledger, agent=agent, agent_id=agent_id, cohort_id=cohort_id,
                                split=split, seed=seed, budget=dict(budget or BUDGET), judge=judge,
                                environment_digest=environment,
                                evaluator_ref=f"validation/strategies/run.py@g6-{g6.__version__}",
                                **({"clock": clock} if clock is not None else {}))
    seeds = seed_artefacts()
    for name in sorted(seeds):
        evaluator.run(instances, candidate=store.put(seeds[name]), replay_id=replay_id)
    report = {name: asdict(worth(ledger, store, seeds[name].content_digest, splits=(split,)))
              for name in sorted(seeds)}
    target = out_dir / f"worth-{hashlib.sha256(cohort_id.encode('utf-8')).hexdigest()[:12]}-{replay_id}.json"
    target.write_text(json.dumps({"cohort_id": cohort_id, "replay_id": replay_id, "policy_digest": policy.digest,
                                  "seeds": report}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--replay", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run(args.cohort, args.out, replay_id=args.replay, agent_id=args.agent_id, seed=args.seed)
    print(json.dumps({name: {"status": row["status"], "wins": row["wins"], "losses": row["losses"],
                             "incomplete_pairs": row["incomplete_pairs"], "meets_bar": row["meets_bar"]}
                      for name, row in report.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
