# SPDX-License-Identifier: Apache-2.0
"""Dev trace strategies. Every result is a profile candidate; the meta-gate alone judges it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from g6.llm_router import advice
from g6.metaharness.propose import DevRunner, ProposerView
from g6.metaharness.trace import MeasuredStep


def step_cost(step: MeasuredStep) -> tuple[float | None, dict[str, Any]]:
    if step.cost_usd is not None:
        return step.cost_usd, {"basis": "measured", "cost_usd": step.cost_usd}
    if step.input_tokens is None or step.output_tokens is None:
        return None, {"basis": "unknown"}
    result = advice.estimate_cost(step.model, step.input_tokens, step.output_tokens)
    cost = result.estimated_usd
    return cost, {"basis": "dated-price", "cost_usd": cost,
                  "prices_as_of": getattr(result, "prices_as_of", None),
                  "stale_price": getattr(result, "stale_price", True)}


@dataclass
class CostStrategy:
    task: str
    providers: tuple[str, ...] = ("ollama",)
    name: str = "trace_cost"
    evidence: list[dict[str, Any]] = field(default_factory=list, init=False)

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        self.evidence.clear()
        ranked = []
        for trace in view.traces:
            for step in trace.steps:
                cost, basis = step_cost(step)
                if cost is not None:
                    ranked.append((cost, step, basis))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        route = advice.route(self.task, providers=self.providers, fallbacks=max(1, len(view.models)))
        out, seen = [], set()
        for current, step, basis in ranked:
            if step.name not in view.steps or step.input_tokens is None or step.output_tokens is None:
                continue
            for model in route.chain:
                if model not in view.models or model == view.base.get("routing", {}).get(step.name):
                    continue
                cost = advice.estimate_cost(model, step.input_tokens, step.output_tokens).estimated_usd
                if cost is None:
                    continue
                if cost >= current or (step.name, model) in seen:
                    continue
                seen.add((step.name, model))
                self.evidence.append({"step": step.name, **basis, "candidate_model": model,
                                      "note": "routing hypothesis only; no saving established"})
                out.append({**view.base, "routing": {**view.base.get("routing", {}), step.name: model}})
        return out


@dataclass
class SpeedStrategy:
    """A person supplies alternative models; 'faster' is a hypothesis until evaluated."""

    faster_models: dict[str, str]
    name: str = "trace_speed"
    max_candidates: int = 3
    evidence: list[dict[str, Any]] = field(default_factory=list, init=False)

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        self.evidence.clear()
        out, seen = [], set()
        if self.max_candidates <= 0:
            return out
        for trace in view.traces:
            path = list(trace.critical_path())
            critical = set(path)
            for step in sorted(trace.steps, key=lambda s: (-s.latency_s, s.name)):
                if step.name not in critical:
                    continue
                model = self.faster_models.get(step.name)
                if (step.name in seen or model not in view.models or step.name not in view.steps
                        or model == view.base.get("routing", {}).get(step.name)):
                    continue
                seen.add(step.name)
                self.evidence.append({"step": step.name, "latency_s": step.latency_s,
                                      "critical_path": path})
                out.append({**view.base, "routing": {**view.base.get("routing", {}), step.name: model}})
                if len(out) >= self.max_candidates:
                    return out
        return out


@dataclass
class QualityStrategy:
    """Step -> key of an optional Check, or a declared repair/second-look PipelineStep."""

    checks: dict[str, str] = field(default_factory=dict)
    pipeline_steps: dict[str, str] = field(default_factory=dict)
    name: str = "trace_quality"

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        out = []
        for trace in view.traces:
            for step in trace.steps:
                if step.passed is True and step.checked_by:
                    continue
                check, repair = self.checks.get(step.name), self.pipeline_steps.get(step.name)
                extra, pipeline = list(view.base.get("extra_checks", ())), list(view.base.get("pipeline", ()))
                if check in view.optional_checks and check not in extra:
                    candidate = {**view.base, "extra_checks": [*extra, check]}
                    if candidate not in out:
                        out.append(candidate)
                if view.pipeline_ops.get(repair) in ("test_repair", "second_look") and repair not in pipeline:
                    candidate = {**view.base, "pipeline": [*pipeline, repair]}
                    if candidate not in out:
                        out.append(candidate)
        return out
