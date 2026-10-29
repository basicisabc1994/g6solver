# SPDX-License-Identifier: Apache-2.0
"""V10's arms as the freeze file binds them, for the study's entry point (``study.py``) and the cost calibration
(``calibration.py``).

- **Arms C, T and E** are callables the freeze file names, ``module:function`` (``load``), with the driver's interface:
  C and T ``arm(patch, *, search, share_left, meter)``, given their search configuration (``driver.SEARCH``); E
  ``arm_e(patch, *, c_calls, t_calls, share_left, meter)``. Their callables into G6's verification search come with
  its proposer interface (VS-5). Until then the stand-ins (``STAND_INS``) take their place behind the same interface:
  each raises ``NotBuilt``, which the driver counts as a tool error. A study level refuses a stand-in.
- **Arm R** is never a callable the freeze file names: it is ``Sessions``, which reads the session record an approved
  launcher left for each patch, judges it by V9's arm 2 rule (``arm_r.disposition``), collects and judges the tests the
  session wrote in its copy (``arm_r.collect`` and ``judge``), prices its tokens into the meter (``arm_r.cost_cents``),
  and hands the driver its transcript for the gaming scan. It never starts a session. A patch with no session record is
  a counted tool error.

**A session record** is V9's arm 2 record (``self_review``: ``{task_id, patch_sha256, answer, ended, elapsed_s, tokens,
model_calls}``) with the model, its token counts by kind (``usage``) and the name of its transcript, a JSON-lines file
beside the record (``transcript``), in the sessions folder as ``<task id>.json`` (``session_file``).
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

from patch_review_search import arm_r

NOT_BUILT = ("verification search's proposer interface (VS-5) is not built at this commit, so this arm's callable is "
             "a stand-in")
STAND_INS = {"C": f"{__name__}:search_not_built", "T": f"{__name__}:search_not_built",
             "E": f"{__name__}:arm_e_not_built"}


class NotBuilt(RuntimeError):
    """An arm whose callable into G6 does not exist yet."""


def search_not_built(patch: dict, *, search: dict, share_left, meter) -> dict:
    raise NotBuilt(NOT_BUILT)


def arm_e_not_built(patch: dict, *, c_calls: int, t_calls: int, share_left, meter) -> dict:
    raise NotBuilt(NOT_BUILT)


def load(spec: str):
    """The callable ``module:function`` names."""
    module, _, name = str(spec).partition(":")
    if not module or not name:
        raise ValueError(f"an arm's binding is module:function, not {spec!r}")
    return getattr(importlib.import_module(module), name)


def session_file(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", task_id) + ".json"


class Sessions:
    """Arm R from the sessions the launcher ran (the module docstring). ``prepared``: each patch's prepared session
    (``arm_r.prepare``); ``copies``: each patch's copy the session reviewed in; ``trees``: each patch's patched and base
    trees; ``executor_for(task_id)``: V9's arm 1 executor for the task; ``runs`` and ``run_s``: arm E's n and each run's
    limit; ``prices``: the frozen price list."""

    def __init__(self, *, folder: Path, prepared: dict, copies: dict, trees: dict, executor_for, runs: int,
                 run_s: float, prices: dict):
        self.folder, self.prepared, self.copies, self.trees = Path(folder), prepared, copies, trees
        self.executor_for, self.runs, self.run_s, self.prices = executor_for, runs, run_s, prices

    def record(self, task_id: str) -> dict | None:
        path = self.folder / session_file(task_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def transcript(self, record: dict) -> str | None:
        name = str(record.get("transcript") or "")
        path = self.folder / name
        if not name or Path(name).name != name or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def __call__(self, patch: dict, *, share_left, meter) -> dict:
        task = patch["id"]
        record = self.record(task)
        disposition = arm_r.disposition(self.prepared[task], record)
        if record is None:
            return {"disposition": disposition, "findings": [], "session": None}
        cents = arm_r.cost_cents(record, self.prices)
        calls = int(record.get("model_calls") or 0)
        meter.add(calls=calls, cents=cents or 0.0, tokens=arm_r.tokens(record))
        patched, base = self.trees[task]
        judged = arm_r.judge(arm_r.collect(self.copies[task]), executor=self.executor_for(task),
                             patched_tree=Path(patched), base_tree=Path(base), runs=self.runs, run_s=self.run_s)
        return {"disposition": disposition, **judged, "transcript": self.transcript(record),
                "copy_root": str(self.copies[task]), "model_calls": calls, "spent_cents": cents,
                "session": {key: record.get(key) for key in ("ended", "elapsed_s", "tokens", "model", "usage")}}
