# SPDX-License-Identifier: Apache-2.0
"""Start from a shipped bundle and tune it on your own labelled examples (META_HARNESS_PLAN.md 4.5 item 4).

``bundle_workflow`` turns a bundle into a workflow whose executor is the bundle's own ``run``, given the profile's
metered model caller. Each case's input fills one of the bundle's fields; ``extra_inputs`` fill the rest. The output a
check and the oracle see is the bundle's result as JSON (``state``, ``summary``, ``rows``). The standard check is that
the bundle released a result; the oracle, which you write, reads your labelled answer. A bundle that ignores a profile
setting (most bundles take no prompt) shows no measured difference, and the report says so: nothing assumes a setting
was applied.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from g6.harness import Check
from g6.metaharness.executor import ExecutorFactory, ProfileContext
from g6.metaharness.schema import Model, Suite, Workflow


def _released(out: str) -> bool:
    return json.loads(out).get("state") != "blocked-escalated"


RELEASED = Check("the bundle released a result", _released, check_id="bundle-released",
                 known_bad=('{"state": "blocked-escalated"}', "not json"), known_good='{"state": "qualified-draft"}')


def bundle_factory(bundle: Any, field: str, *, extra_inputs: Mapping[str, str] | None = None,
                   step: str = "answer") -> ExecutorFactory:
    extra = dict(extra_inputs or {})

    def factory(ctx: ProfileContext) -> Callable[[Any], str]:
        def execute(contract: Any) -> str:
            result = bundle.run({**extra, field: contract.task}, ctx.caller(step))
            return json.dumps({"state": result.completion_state, "summary": result.summary, "rows": result.rows},
                              sort_keys=True, default=str)

        return execute

    return factory


def bundle_workflow(bundle_id: str, *, field: str, suite: Suite, models: Mapping[str, Model],
                    oracle: Callable[[str, Any], bool], name: str | None = None,
                    extra_inputs: Mapping[str, str] | None = None, **kw: Any) -> tuple[Workflow, ExecutorFactory]:
    from g6.bundles import catalog

    bundles = catalog()
    if bundle_id not in bundles:
        raise KeyError(f"no shipped bundle {bundle_id!r}; shipped: {sorted(bundles)}")
    bundle = bundles[bundle_id]
    if field not in {f.name for f in bundle.fields}:
        raise KeyError(f"bundle {bundle_id!r} has no field {field!r}")
    workflow = Workflow(name=name or bundle_id, task="{input}", checks=(RELEASED,), oracle=oracle, suite=suite,
                        models=dict(models), **kw)
    return workflow, bundle_factory(bundle, field, extra_inputs=extra_inputs)
