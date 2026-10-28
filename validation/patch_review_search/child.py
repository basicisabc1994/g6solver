# SPDX-License-Identifier: Apache-2.0
"""One run of arm C or T on one patch in its own interpreter (``driver.ChildArm`` starts it).

    python -I validation/patch_review_search/child.py <job.json> <reply.json>

Isolated mode (``-I``) ignores the environment's Python variables and leaves this folder off the import path, so the
job names the folders to import from (``roots``). The arm (``spec``, ``module:function``) gets the patch, its search
configuration, its meter (``metering.FileMeter``, whose file the driver reads whatever happens here) and ``share_left``,
which counts what the meter has counted against what was left of the arm's share when the run began. The reply is
written only when the arm returns or raises: a process killed at its limit leaves none.
"""
from __future__ import annotations

import importlib
import json
import sys


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as handle:
        job = json.load(handle)
    sys.path[:0] = [root for root in job["roots"] if root not in sys.path]
    from patch_review_search.metering import FileMeter, InfrastructureFailure

    meter = FileMeter(job["meter"])
    module, _, name = job["spec"].partition(":")
    try:
        arm = getattr(importlib.import_module(module), name)
        found = {"reply": arm(job["patch"], search=job["search"], meter=meter,
                              share_left=lambda: meter.reading()["cents"] < job["share_cents"])}
    except InfrastructureFailure as exc:
        found = {"infrastructure": str(exc)}
    except Exception as exc:  # the driver counts it as a tool error
        found = {"error": f"{type(exc).__name__}: {exc}"}
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(found, handle, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
