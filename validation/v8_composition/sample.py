# SPDX-License-Identifier: Apache-2.0
"""Draw a new Python-only split from pinned V2 metadata; never load task text or patches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
SEED = 20261027


def draw(rows: list[dict], v7: dict, *, dev_count: int = 8, scored_count: int = 48) -> dict:
    python = {row["instance_id"]: row["repo"] for row in rows if row["repo_language"].lower() == "python"}
    if len({row["instance_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate dataset IDs")
    reused = [ident for ident in v7["pilot"] if ident in python and python[ident] == "internetarchive/openlibrary"]
    if len(reused) != 2:
        raise ValueError("expected exactly two reusable OpenLibrary pilot tasks")
    if dev_count < len(reused) or scored_count < 1:
        raise ValueError("invalid split sizes")
    excluded = set(v7["dev"])
    pool = sorted(set(python) - excluded)
    random.Random(f"V8:{SEED}:python").shuffle(pool)
    need = dev_count - len(reused)
    if len(pool) < need + scored_count:
        raise ValueError("insufficient Python-only tasks")
    dev, scored = reused + pool[:need], pool[need:need + scored_count]
    return {"study": "V8", "status": "draft-not-frozen", "seed": SEED,
            "dataset": v7["dataset"], "rule": "sample.py: sorted Python IDs, seeded shuffle, disjoint slices",
            "dev": dev, "reused_pilots": reused, "excluded_seen": sorted(excluded - set(reused)),
            "scored": scored, "repositories": {ident: python[ident] for ident in dev + scored},
            "note": "Task-disjoint, not repository-disjoint. IDs only; no task content inspected."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    v7 = json.loads((ROOT / "docs/validation/results/V7/splits.json").read_text())
    if hashlib.sha256(args.parquet.read_bytes()).hexdigest() != v7["dataset"]["sha256"]:
        raise ValueError("dataset does not match the pinned V2 SHA-256")
    import pyarrow.parquet as pq
    rows = pq.read_table(args.parquet, columns=["instance_id", "repo", "repo_language"]).to_pylist()
    split = draw(rows, v7)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(split, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(split['dev'])} development and {len(split['scored'])} scored IDs; draft, not frozen.")


if __name__ == "__main__":
    main()
