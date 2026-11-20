"""Owner-only A2 check: emit an overlap count and pool repository IDs to exclude.

This command is never called by the miner or inventory. Only the owner supplies
the sealed cohort path. Successful stdout is one integer and a newline; failures
have a fixed message that contains no cohort data or paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ._mine_io import atomic_json, cohort_ids, in_cohort, local_path, pool_ids, read_json


_FORMAT_REASONS = {"cohort repository list missing", "expected repository list", "cohort bug id refused",
                   "repository id refused"}


_PATH_REASONS = {"linked path refused", "sealed path refused"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--final-cohort", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, required=True)
    args = parser.parse_args(argv)
    # A refusal names its stage and, for the cohort's format, one of these fixed reasons; never cohort content.
    stage = "paths"
    try:
        paths = []
        for stage, value, sealed_ok in (("pool path", args.pool, False), ("final cohort path", args.final_cohort, True),
                                        ("exclude path", args.exclude, False)):
            try:
                paths.append(local_path(value, sealed=sealed_ok))
            except ValueError as error:
                stage += ": " + (str(error) if str(error) in _PATH_REASONS else "unrecognised")
                raise
        pool, sealed, output = paths
        stage = "paths: overlapping"
        if len({pool, sealed, output}) != 3:
            raise ValueError("overlapping paths")
        stage = "pool"
        repositories = pool_ids(read_json(pool))
        # Keep this read entirely inside the owner's command; never return sealed IDs.
        stage = "final cohort read"
        text = sealed.read_text(encoding="utf-8")
        stage = "final cohort json"
        data = json.loads(text)
        stage = "final cohort format"
        try:
            cohort = cohort_ids(data)
        except ValueError as error:
            stage += ": " + (str(error) if str(error) in _FORMAT_REASONS else "unrecognised")
            raise
        except (KeyError, TypeError, AttributeError):
            stage += ": row shape"
            raise
        overlap = sorted(repository for repository in repositories if in_cohort(repository, cohort))
        stage = "exclude write"
        atomic_json(output, overlap)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        parser.exit(2, f"A2 input or output refused at {stage}\n")
    print(len(overlap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
