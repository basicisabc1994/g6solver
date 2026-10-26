# SPDX-License-Identifier: Apache-2.0
"""Proposals that compound (frontier design §4.1, RC2 and RC3): each one edits a parent drawn from the Pareto pool,
so later cycles build on what earlier ones found instead of starting from the head again.

* ``ReflectiveStrategy``: a reflection model reads the parent's failing dev cases (input, expected answer, and, where
  the parent is the head, its output and the workflow's own feedback) and writes one change as JSON. Only the
  declared fields and ``prompt`` are kept; the rest is dropped, and the result is validated like any proposal. Every
  ``merge_every``-th proposal joins two frontier members instead.
* ``EditStrategy``: the same loop with a random neighbour instead of a reflected change (the study's random-edit arm).

Both see the dev split only, through the ``ProposerView`` and ``DevRunner`` every strategy gets. The reflection prompt
is fixed code: no method version edits it ("no level 3").
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

from g6.metaharness.pool import EditSpace, Pool
from g6.metaharness.propose import DevRunner, ProposerView
from g6.metaharness.schema import Profile
from g6.reasoning import first_json_object

REFLECT_SYSTEM = "You improve how an AI system is instructed to do one task. Reply with one JSON object and nothing else."
REFLECT_PROMPT = """The task each case is given:
{task}

The current settings (JSON):
{parent}

Fields you may change, with the values allowed for each (any other field is ignored):
{space}
You may also rewrite "prompt": extra instructions for the model that does the task (at most 1500 characters).

Development cases these settings get wrong, with the expected answer and what was observed:
{failures}

Write one change that would fix these failures without breaking cases that already pass. Reply with a JSON object of
only the fields to change, for example {{"prompt": "..."}}."""
MAX_FAILURES = 8


def seed_base(pool: Pool, view: ProposerView) -> list[str] | None:
    """The dev case ids, with the head's results from the view added to the pool (no second run). ``None`` when the
    view did not run every dev case: results on fewer cases are not comparable."""
    ids = [d["case_id"] for d in view.dev]
    if len(view.ran) < len(ids):
        return None
    failed = {f.case_id for f in view.failures}
    pool.add(Profile.model_validate(view.base), {c: c not in failed for c in ids})
    return ids


def run_on_dev(runner: DevRunner, profile: Profile, ids: list[str]) -> dict[str, bool] | None:
    """The profile's dev results, or ``None`` when it could not run every case (the budget ran out, or the profile
    names something the workflow does not declare)."""
    try:
        results = runner(profile, ids)
    except Exception:  # noqa: BLE001 - one bad candidate must not end the strategy (and drop the good ones)
        return None
    return None if getattr(runner, "last_skipped", 0) else dict(zip(ids, results))


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=list)


@dataclass
class _PoolStrategy:
    space: EditSpace
    pool: Pool = field(default_factory=Pool)
    calls: int = 3
    merge_every: int = 0
    seed: int = 0
    made: int = 0                                         # changes attempted
    duplicates: int = 0                                   # attempts that repeated a profile already in the pool

    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False)

    def _change(self, view: ProposerView, parent: Profile, rng: random.Random) -> dict[str, Any] | None:
        raise NotImplementedError

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        ids = seed_base(self.pool, view)
        if ids is None:
            return []                                     # the view itself was cut short: no fair dev comparison
        base = Profile.model_validate(view.base)
        found: list[tuple[int, int, Profile]] = []
        for _ in range(self.calls):
            self.made += 1
            rng = random.Random(f"{self.seed}:{self.made}")
            parent, front = self.pool.parent(rng), self.pool.frontier()
            parents = (parent.digest(),)
            if self.merge_every and self.made % self.merge_every == 0 and len(front) >= 2:
                first, second = rng.sample(front, 2)
                data = self.space.merge(first, second, base)
                parents = (first.digest(), second.digest())
            else:
                data = self._change(view, parent, rng)
            try:
                profile = Profile.model_validate(data) if data is not None else None
            except ValueError:
                profile = None
            if profile is None:
                continue
            if self.pool.seen(profile):
                self.duplicates += 1
                continue
            results = run_on_dev(runner, profile, ids)
            if results is None:
                if getattr(runner, "last_skipped", 0):
                    break                                 # the dev budget is spent: stop, store nothing cut short
                continue                                  # this profile cannot run (an undeclared model or check)
            self.pool.add(profile, results)
            self.lineage[profile.digest()] = parents
            score = sum(results.values())
            if score >= sum(self.pool.results(parent).values()):   # a tie on a small dev split: racing decides
                found.append((score, -self.made, profile))
        found.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [p.model_dump(mode="json") for _, _, p in found]


class ReflectiveStrategy(_PoolStrategy):
    def __init__(self, reflector: Any, space: EditSpace, pool: Pool | None = None, *, calls: int = 3,
                 merge_every: int = 3, seed: int = 0, name: str = "reflect") -> None:
        super().__init__(space, pool if pool is not None else Pool(), calls, merge_every, seed)
        self.reflector, self.name = reflector, name     # an LLMCaller; it may be larger than the task model

    def _change(self, view: ProposerView, parent: Profile, rng: random.Random) -> dict[str, Any] | None:
        failing = {c for c, ok in self.pool.results(parent).items() if not ok}
        observed = {f.case_id: f for f in view.failures} if parent.digest() == Profile.model_validate(view.base).digest() \
            else {}
        rows = []
        for case in view.dev:
            if case["case_id"] not in failing or len(rows) >= MAX_FAILURES:
                continue
            row = {"input": case["input"], "expected": case["expected"]}
            seen = observed.get(case["case_id"])
            if seen is not None:
                row |= {"output": seen.output, **({"feedback": seen.feedback} if seen.feedback else {})}
            rows.append(row)
        if not rows:
            return None
        prompt = REFLECT_PROMPT.format(task=view.task, parent=json.dumps(parent.model_dump(mode="json"), sort_keys=True),
                                       space=json.dumps({k: list(v) for k, v in self.space.space.items()}, default=str),
                                       failures=json.dumps(rows, ensure_ascii=False, indent=1))
        try:
            change = first_json_object(self.reflector.complete(REFLECT_SYSTEM, prompt))
        except Exception:  # noqa: BLE001 - a failed or unparsable reflection proposes nothing; it never raises through
            return None
        # only declared fields, each with a declared value (prompt is free text): the same space every arm searches
        kept = {k: v for k, v in change.items()
                if k == "prompt" or (k in self.space.space
                                     and _json_key(v) in {_json_key(x) for x in self.space.space[k]})}
        if isinstance(kept.get("prompt"), str):
            kept["prompt"] = kept["prompt"][:1500]
        return {**parent.model_dump(mode="json"), **kept} if kept else None


class Pooled:
    """Any strategy whose candidates join the shared pool, with their dev results, so reflections and merges can
    build on them: recovery's remedies seed the pool this way (frontier design §4.1)."""

    def __init__(self, inner: Any, pool: Pool) -> None:
        self.inner, self.pool, self.name = inner, pool, inner.name

    @property
    def lineage(self) -> dict[str, tuple[str, ...]]:
        return getattr(self.inner, "lineage", {})

    def propose(self, view: ProposerView, runner: DevRunner) -> list[Any]:
        out = list(self.inner.propose(view, runner))
        ids = seed_base(self.pool, view)
        for item in out if ids is not None else ():
            try:
                profile = Profile.model_validate(item)
            except ValueError:
                continue
            if self.pool.seen(profile):
                continue
            results = run_on_dev(runner, profile, ids)
            if results is None:
                if getattr(runner, "last_skipped", 0):
                    break
                continue
            self.pool.add(profile, results)
        return out


class EditStrategy(_PoolStrategy):
    def __init__(self, space: EditSpace, pool: Pool | None = None, *, calls: int = 3, merge_every: int = 0,
                 seed: int = 0, name: str = "edit") -> None:
        super().__init__(space, pool if pool is not None else Pool(), calls, merge_every, seed)
        self.name = name

    def _change(self, view: ProposerView, parent: Profile, rng: random.Random) -> dict[str, Any] | None:
        options = self.space.neighbours(parent)
        return rng.choice(options) if options else None
