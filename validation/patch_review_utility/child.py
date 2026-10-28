# SPDX-License-Identifier: Apache-2.0
"""One arm-3 or arm-4 review in its own interpreter (``arms.in_child`` starts it).

    python -I validation/patch_review_utility/child.py <job.json> <reply.json>

Isolated mode (``-I``) ignores the environment's Python variables and leaves this folder off the import path, so the
job names the folders to import from (``roots``). The reply is written only when the review returns: a process killed
at its limit leaves none.
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as handle:
        job = json.load(handle)
    sys.path[:0] = [root for root in job["roots"] if root not in sys.path]
    from patch_review_utility import arms

    found = arms.run_job(job)
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(found, handle, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
