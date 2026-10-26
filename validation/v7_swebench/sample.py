# SPDX-License-Identifier: Apache-2.0
"""V7 split draw (PROTOCOL.md §2): 10 development and 20 evaluation task ids, disjoint by repository.

It reads three columns of the dataset file, ``instance_id``, ``repo`` and ``repo_language``, and nothing else: no
problem statement, patch, test or requirement is read, here or anywhere a split is drawn. The rule:

1. each task's language class is ``go``, ``python`` or ``javascript`` (the dataset's ``js`` and ``ts``);
2. the development repositories are one repository per class, the first of that class's sorted repositories shuffled
   by ``random.Random("V7:20261023:repos:<class>")``; every other repository is an evaluation repository;
3. each repository's sorted ids are shuffled by ``random.Random("V7:20261023:ids:<repo>")``;
4. development ids are taken round-robin over the development repositories (in the order given by
   ``random.Random("V7:20261023:dev-order")``) until 10, and evaluation ids round-robin over the evaluation repositories
   (``"V7:20261023:eval-order"``) until 20, a repository whose ids run out being skipped;
5. the dry run uses the first two development ids in draw order, and the pilot the first four;
6. the scored order (design D, lead 2026-10-23) is the 20 evaluation ids, in their order, then the same round-robin
   continued from where it stopped, now over every repository (the evaluation repositories in their order, then the
   development ones in theirs), each repository's shuffled ids less those already taken and less the 10 development
   ids, until 60.

    python -I validation/v7_swebench/sample.py --data <folder with the parquet file> --out <results folder>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import v7common as c  # noqa: E402

CLASSES = {"go": "go", "python": "python", "js": "javascript", "ts": "javascript"}


def language_class(language: str) -> str:
    try:
        return CLASSES[language]
    except KeyError:
        raise ValueError(f"unknown repo_language {language!r}: the draw rule names go, python, js and ts") from None


def _round_robin(order: list[str], pools: Mapping[str, list[str]], size: int, start: int = 0) -> list[str]:
    """``size`` ids, one per repository in turn from position ``start`` of ``order``, skipping empty repositories."""
    queues = {repo: list(pools[repo]) for repo in order}
    out: list[str] = []
    at = start
    while len(out) < size and any(queues.values()):
        repo = order[at % len(order)]
        if queues[repo]:
            out.append(queues[repo].pop(0))
        at += 1
    if len(out) < size:
        raise ValueError(f"only {len(out)} ids available for a split of {size}")
    return out


def _next_position(order: list[str], picks: list[str], repo_of: Mapping[str, str]) -> int:
    """Where a round-robin over ``order`` that made ``picks`` would go next: the position after its last pick."""
    return (order.index(repo_of[picks[-1]]) + 1) if picks else 0


def draw(rows: Iterable[Mapping[str, str]], *, seed: int = c.SEED, dev_size: int = c.DEV_SIZE,
         eval_size: int = c.EVAL_SIZE, scored: int = c.SCORED_SIZE) -> dict:
    """The split from ``(instance_id, repo, repo_language)`` rows only."""
    by_repo: dict[str, list[str]] = {}
    repo_class: dict[str, str] = {}
    for row in rows:
        repo, cls = row["repo"], language_class(row["repo_language"])
        if repo_class.setdefault(repo, cls) != cls:
            raise ValueError(f"{repo} has tasks in two language classes")
        by_repo.setdefault(repo, []).append(row["instance_id"])
    ids = [i for v in by_repo.values() for i in v]
    if len(set(ids)) != len(ids):
        raise ValueError("instance ids are not unique")
    dev_repos = []
    for cls in sorted(set(repo_class.values())):
        repos = sorted(r for r, k in repo_class.items() if k == cls)
        random.Random(f"V7:{seed}:repos:{cls}").shuffle(repos)
        dev_repos.append(repos[0])
    eval_repos = sorted(r for r in by_repo if r not in dev_repos)
    pools = {}
    for repo, found in by_repo.items():
        pool = sorted(found)
        random.Random(f"V7:{seed}:ids:{repo}").shuffle(pool)
        pools[repo] = pool
    dev_order, eval_order = sorted(dev_repos), list(eval_repos)
    random.Random(f"V7:{seed}:dev-order").shuffle(dev_order)
    random.Random(f"V7:{seed}:eval-order").shuffle(eval_order)
    dev = _round_robin(dev_order, pools, dev_size)
    evaluation = _round_robin(eval_order, pools, eval_size)
    taken = set(dev) | set(evaluation)
    rest = {repo: [i for i in pools[repo] if i not in taken] for repo in pools}
    repo_of = {i: r for r, v in by_repo.items() for i in v}
    rotation = eval_order + dev_order   # the development repositories join the turn after the evaluation ones
    order = evaluation + _round_robin(rotation, rest, max(0, scored - len(evaluation)),
                                      start=_next_position(eval_order, evaluation, repo_of))
    return {"seed": seed, "dev": dev, "eval": evaluation, "scored": order, "dry_run": dev[:2],
            "pilot": dev[:c.PILOT_SIZE], "dev_repos": dev_order, "eval_repos": eval_order,
            "repo_of": {i: repo_of[i] for i in (*dev, *order)},
            "pool": {"tasks": len(ids), "repositories": len(by_repo)}}


def check_disjoint(split: Mapping) -> list[str]:
    """Problems with a split: shared ids, or a repository on both sides (rule 3)."""
    problems = []
    dev, evaluation, repo_of = split["dev"], split["eval"], split["repo_of"]
    if set(dev) & set(evaluation):
        problems.append("an id is in both splits")
    shared = {repo_of[i] for i in dev} & {repo_of[i] for i in evaluation}
    if shared:
        problems.append(f"repositories on both sides: {sorted(shared)}")
    if len(set(dev)) != len(dev) or len(set(evaluation)) != len(evaluation):
        problems.append("an id appears twice in one split")
    scored = split.get("scored", evaluation)
    if scored[:len(evaluation)] != list(evaluation) or len(set(scored)) != len(scored) or set(scored) & set(dev):
        problems.append("the scored order does not start with the evaluation ids, repeats an id or holds a dev id")
    return problems


def read_id_columns(parquet: Path) -> list[dict]:
    """The three id columns of the dataset file, after checking its hash against the registered one."""
    import pyarrow.parquet as pq   # the study's own virtual environment, never G6's

    found = hashlib.sha256(parquet.read_bytes()).hexdigest()
    if found != c.DATASET["sha256"]:
        raise SystemExit(f"{parquet.name}: SHA-256 {found} is not the registered {c.DATASET['sha256']}")
    table = pq.read_table(parquet, columns=["instance_id", "repo", "repo_language"])
    return table.to_pylist()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, type=Path, help="folder holding the dataset's parquet file")
    ap.add_argument("--out", required=True, type=Path, help="results folder (splits.json is written there)")
    args = ap.parse_args(argv)
    split = draw(read_id_columns(args.data / c.DATASET["local_name"]))
    problems = check_disjoint(split)
    if problems:
        raise SystemExit("; ".join(problems))
    record = {"study": c.STUDY, "dataset": {k: v for k, v in c.DATASET.items() if k != "local_name"},
              "rule": "sample.py docstring", **split}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "splits.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                                          newline="\n")
    print(json.dumps({"dev": split["dev"], "eval": split["eval"], "dev_repos": split["dev_repos"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
