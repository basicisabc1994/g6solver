# SPDX-License-Identifier: Apache-2.0
"""The earlier version's served workforce tools, as plain local calls for the ported
tests. The library ships no MCP server; this keeps each tool's answer shape (JSON, ``ok`` true with the trimmed
status, or ``ok`` false with the refusal's words) so the archive's mission tests read the same answers."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from g6.workforce import objective as compiler
from g6.workforce import plans, service
from g6.workforce.store import NOT_FOUND


class Refusal(Exception):
    def __init__(self, error: str, **extra: Any) -> None:
        super().__init__(error)
        self.extra = extra


def _json_object(text: str, what: str) -> dict:
    try:
        data = json.loads(text or "")
    except (TypeError, ValueError) as exc:
        raise Refusal(f"{what} is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Refusal(f"{what} must be a JSON object")
    return data


def _workforce(fn: Callable, *args: Any, **kwargs: Any) -> dict:
    try:
        return fn(service.bind(), *args, **kwargs)
    except plans.PlanRejected as exc:
        raise Refusal("the plan was refused; fix every problem and call mission_plan (or mission_delegate) again",
                      problems=list(exc.problems)) from None
    except service.WorkforceUnbound as exc:
        raise Refusal(str(exc)) from None
    except (ValueError, KeyError) as exc:
        raise Refusal(str(exc)) from None
    except LookupError as exc:
        if str(exc) == NOT_FOUND:
            raise
        raise Refusal(str(exc)) from None


def _summary(found: dict) -> dict:
    return service.trim(found)


def mission_create(objective: str, risk_tier: str, budget_cents: int, repo_json: str = "",
                   materials_json: str = "") -> dict:
    repository = _json_object(repo_json, "repo_json") if str(repo_json or "").strip() else None
    materials = _json_object(materials_json, "materials_json") if str(materials_json or "").strip() else None
    return _summary(_workforce(compiler.compile, objective=str(objective), declared_risk=str(risk_tier),
                               budget_cents=budget_cents, repository=repository, materials=materials))




def mission_accept(mission_id: str, contract_json: str = "") -> dict:
    contract = _json_object(contract_json, "contract_json") if str(contract_json or "").strip() else None
    return _summary(_workforce(service.accept, str(mission_id), contract))


def mission_plan(mission_id: str, plan_json: str = "") -> dict:
    if not str(plan_json or "").strip():
        raise Refusal("local plan generation is not part of the g6 library; submit plan_json")
    return _summary(_workforce(service.plan, str(mission_id), _json_object(plan_json, "plan_json")))


def mission_run(mission_id: str, max_steps: int = 50) -> dict:
    return _summary(_workforce(service.run, str(mission_id), max_steps=max(1, min(int(max_steps), 200))))


def mission_status(mission_id: str, detail: str = "summary") -> dict:
    if str(detail) not in ("summary", "full"):
        raise Refusal('detail is "summary" (the default) or "full"')
    full = str(detail) == "full"
    found = _workforce(service.status, str(mission_id), stages=full)
    return found if full else _summary(found)


def mission_submit(mission_id: str, unit_key: str, artifact_json: str) -> dict:
    return _summary(_workforce(service.submit, str(mission_id), str(unit_key),
                               _json_object(artifact_json, "artifact_json")))


def workforce_capabilities() -> dict:
    return _workforce(service.capabilities)


def workforce_probe(component: str, op: str) -> dict:
    return _workforce(service.probe, str(component), str(op))


TOOLS = {f.__name__: f for f in (mission_create, mission_accept, mission_plan, mission_run, mission_status,
                                 mission_submit, workforce_capabilities, workforce_probe)}


def call(name: str, **arguments: Any) -> dict:
    """One tool call, answered as the served tool answered: JSON round-tripped, never raised."""
    try:
        found = {"ok": True, **TOOLS[name](**arguments)}
    except Refusal as exc:
        found = {"ok": False, "error": str(exc), **exc.extra}
    except LookupError as exc:
        if isinstance(exc, KeyError | IndexError) or str(exc) != NOT_FOUND:
            raise
        found = {"ok": False, "error": NOT_FOUND}
    except Exception as exc:  # noqa: BLE001 - the archive's MCP transport returned a raising tool as an error result
        found = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return json.loads(json.dumps(found, default=str))
