"""Owner-only A2 check: emit an overlap count and pool repository IDs to exclude.

This command is never called by the miner or inventory. Only the owner supplies
the sealed cohort path. Successful stdout is one integer and a newline; failures
have a fixed message that contains no cohort data or paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ._mine_io import atomic_json, cohort_ids, local_path, pool_ids, read_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--final-cohort", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pool, sealed, output = local_path(args.pool), local_path(args.final_cohort, sealed=True), local_path(args.exclude)
        if len({pool, sealed, output}) != 3:
            raise ValueError("overlapping paths")
        # Keep this read entirely inside the owner's command; never return sealed IDs.
        overlap = sorted(pool_ids(read_json(pool)) & cohort_ids(json.loads(sealed.read_text(encoding="utf-8"))))
        atomic_json(output, overlap)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        parser.exit(2, "A2 input or output refused\n")
    print(len(overlap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
