# SPDX-License-Identifier: Apache-2.0
"""How an executor uses a profile (META_HARNESS_PLAN.md 4.7; AD A3.1's host factory).

``ExecutorFactory = Callable[[ProfileContext], Callable[[task_contract], str]]``. The context hands the factory one
``MeteredCaller`` per step, already capped by the profile's budget and the declared model's cap, the hint text, the
profile's prompt and its retry count. A factory that ignores a setting simply shows no measured difference: the report
says "no measured effect" and never assumes the setting was applied (the archive's ``ConfigOverride``, LL-009).

``model_call_factory`` is the default for a workflow that is one model call per task, so a user with no agent can
start at once.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from g6.learning import TheoryStore, render_addendum
from g6.learning.addendum import with_addendum
from g6.learning.schema import PromptAddendum
from g6.metaharness.schema import Profile, Workflow
from g6.metaharness.measurement import TraceCaller, trace_for
from g6.reasoning import Budget, MeteredCaller

DEFAULT_SYSTEM = "Do the task. Reply with the answer only."


@dataclass
class ProfileContext:
    profile: Profile
    callers: dict[str, MeteredCaller]
    hint_text: str = ""
    hint_ids: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    pipeline: tuple[Any, ...] = ()                  # the workflow's PipelineSteps the profile names, in order
    distillates: dict[str, str] = field(default_factory=dict)   # step -> admitted source

    def trace(self, case_id: str, result: dict[str, Any], oracle: str):
        return trace_for(self.callers, case_id, self.profile.digest(), result, oracle)

    def caller(self, step: str) -> MeteredCaller:
        return self.callers[step]

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.callers.values())


ExecutorFactory = Callable[[ProfileContext], Callable[[Any], str]]


def context_for(workflow: Workflow, profile: Profile, *, theories: TheoryStore | None = None,
                clock: Callable[[], float] = time.monotonic, artifacts: Any = None) -> ProfileContext:
    """A fresh context for one run: a new meter per step, so every run's calls are counted from zero. An adapter is
    loaded only for a profile that names one (an unapproved one runs only inside an evaluation, never live)."""
    adapter_dir = artifacts.adapter_dir(profile.adapter) if profile.adapter and artifacts is not None else None
    callers = {}
    order: list[str] = []
    for step in workflow.steps:
        model = workflow.models[profile.routing[step]]
        cap = min(profile.budgets.max_model_calls, model.max_calls_per_run)
        budget = Budget(max_model_calls=cap, max_output_chars=profile.budgets.max_output_chars,
                        max_wall_clock_s=profile.budgets.max_wall_clock_s)
        inner = model.adapter_loader(adapter_dir) if adapter_dir is not None else model.caller
        callers[step] = TraceCaller(inner, budget, clock=clock, step=step, model=profile.routing[step], usage=model.usage, order=order)
    hint_text, hint_ids = "", ()
    if profile.hints and theories is not None:
        found = [t for t in (theories.get(h) for h in profile.hints) if t is not None]
        addendum = render_addendum(found)
        hint_text, hint_ids = addendum.text, addendum.theory_ids
    distillates = ({step: artifacts.distillate(sha) for step, sha in profile.distillates.items()}
                   if profile.distillates and artifacts is not None else {})
    extra = {"exemplars": _bank(workflow.examples or workflow.suite.dev, workflow.task)} if profile.exemplars else {}
    return ProfileContext(profile, callers, hint_text, tuple(hint_ids), extra=extra,
                          pipeline=tuple(workflow.pipeline_steps[n] for n in profile.pipeline),
                          distillates=distillates)


_BANKS: dict[tuple[int, str], tuple[Any, Any]] = {}


def _bank(cases: tuple[Any, ...], template: str) -> Any:
    """One index per example bank and task template (the tuple is kept, so its id cannot be reused while cached)."""
    from g6.metaharness.exemplars import Bank

    found = _BANKS.get((id(cases), template))
    if found is None or found[0] is not cases:
        found = _BANKS[(id(cases), template)] = (cases, Bank(cases, template))
    return found[1]


def with_exemplars(ctx: ProfileContext, task: str) -> str:
    """The task with the profile's nearest labelled examples in front of it (``Profile.exemplars``), or as it is."""
    bank = ctx.extra.get("exemplars")
    shown = bank.render(task, ctx.profile.exemplars, ctx.profile.context_budget_chars) if bank is not None else ""
    return f"{shown}\n\n{task}" if shown else task


def model_call_factory(system: str = DEFAULT_SYSTEM, *, step: str = "answer") -> ExecutorFactory:
    """One model call per task (plus the profile's retries on an empty answer), with the hints and the profile's
    prompt added. The model's reply is the output."""

    def factory(ctx: ProfileContext) -> Callable[[Any], str]:
        prompt = f"{system}\n\n{ctx.profile.prompt}".strip() if ctx.profile.prompt else system
        if ctx.profile.skill_text:
            prompt = f"{prompt}\n\n{ctx.profile.skill_text}"
        stable = ctx.profile.prompt_layout == "stable_prefix"
        if stable and ctx.hint_text:   # R1: the hints join the fixed system prompt, so a prefix cache can reuse it
            prompt = f"{prompt}\n\n{ctx.hint_text}"

        def execute(contract: Any) -> str:
            if step in ctx.distillates:   # R2: an admitted distillate replaces the model call, out of process
                from g6.distillation import run_sandboxed

                ran = run_sandboxed(ctx.distillates[step], contract.task)
                answer = str(ran.value) if ran.ok and ran.value is not None else ""
            else:
                task = with_exemplars(ctx, contract.task)
                user = task if stable else with_addendum(_addendum(ctx), task)
                answer = ""
                for _ in range(1 + ctx.profile.retries):
                    answer = ctx.caller(step).complete(prompt, user)
                    if answer.strip():
                        break
            for pipeline_step in ctx.pipeline:
                answer = pipeline_step.run(answer, contract, ctx)
            return answer

        return execute

    return factory


def _addendum(ctx: ProfileContext) -> PromptAddendum:
    return PromptAddendum(ctx.hint_text, ctx.hint_ids)


CODE_RUNNER = """import json, sys
sys.path.insert(0, ".")
import main
task = open("_g6_task.txt", encoding="utf-8").read()
out = main.solve(task)
print(json.dumps({"output": out if isinstance(out, str) else str(out)}))
"""


def code_factory(workflow: Workflow, artifacts: Any, *, timeout_s: float = 30.0, mem_mb: int = 512) -> ExecutorFactory:
    """For a workflow with a ``code_root``: its files, with the profile's code overlay, copied into a fresh directory
    and run out of process by ``g6.core.sandbox`` (``main.solve(task)``). The user's own folder is never written. A run
    no operating-system boundary could confine, where isolation is required, raises ``confine.IsolationRefused``: it is
    no answer, so it is never scored as an empty one."""
    import json
    from pathlib import Path

    from g6.core.confine import IsolationRefused
    from g6.core.sandbox import isolation_refused, run_script

    root = Path(workflow.code_root)

    def factory(ctx: ProfileContext) -> Callable[[Any], str]:
        def execute(contract: Any) -> str:
            files = {p.name: p.read_text(encoding="utf-8") for p in root.iterdir() if p.is_file() and p.suffix == ".py"}
            files.update({name: artifacts.blob(sha) for name, sha in ctx.profile.code.items()})
            files["_g6_task.txt"] = contract.task
            ran = run_script(CODE_RUNNER, files, timeout_s=timeout_s, mem_mb=mem_mb)
            if isolation_refused(ran):
                raise IsolationRefused(ran.stderr, ran.isolation)
            if ran.status != "ok":
                return ""
            try:
                return str(json.loads(ran.stdout.strip().splitlines()[-1])["output"])
            except (ValueError, KeyError, IndexError):
                return ""

        return execute

    return factory


def null_factory(ctx: ProfileContext) -> Callable[[Any], str]:
    """The negative control: an executor that answers nothing and calls no model. A suite whose checks cannot tell this
    from the base profile cannot discriminate (the role of ``distillation.admission``'s negative control)."""
    return lambda contract: ""
