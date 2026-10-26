# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Workers: bounded execution policies narrowed under the mission's ceiling (workforce OS plan, P0 T5).

Pinned: every ``RuntimePolicy`` field has a narrowing rule, and a worker may only be tighter than its
ceiling on each (a planted widening of any field is refused, and a new field without a rule fails
here); the allowlist is never empty (an empty ``allowed_tools`` means allow-any to the policy
engine); a read-only template takes no writing op; a unit cannot name a capability its template does
not allow; templates are strict files; a spec's digest names exactly what it permits. Offline.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import capabilities as wcap
from g6.workforce import plans as wp
from g6.workforce import workers as ww

CEILING = RuntimePolicy(max_steps=10, max_tool_calls=20, max_shell_commands=2, max_files_modified=3,
                        max_lines_changed=100, max_retries=2, max_runtime_seconds=300, max_cost_cents=50,
                        max_privilege_level=1, allowed_tools=frozenset({"solver.solve", "ledger.read"}),
                        forbidden_paths=frozenset({"/secrets"}), require_tests_before_final=True,
                        require_quorum=True, quorum_n=4, quorum_f=1, quorum_required_accepts=3)
FIELDS = [f.name for f in dataclasses.fields(RuntimePolicy)]


def _contract(component: str, op: str, side_effect: str = "read") -> wcap.ParamContract:
    return wcap.ParamContract.from_dict({
        "component": component, "op": op, "required": {"x": "int"}, "probe_params": {"x": 1},
        "expect_field": "y", "side_effect": side_effect, "check": "input_model"})


CONTRACTS = {("solver", "solve"): _contract("solver", "solve"), ("ledger", "read"): _contract("ledger", "read"),
             ("ledger", "post"): _contract("ledger", "post", "local_write")}


def _lookup(component: str, op: str):
    return CONTRACTS.get((component, op))


def _wider(name: str):
    value = getattr(CEILING, name)
    if name == "allowed_tools":
        return value | {"mailer.send"}
    if name == "forbidden_paths":
        return frozenset()
    if isinstance(value, bool):
        return not value
    if name.startswith("quorum_"):
        return value - 1
    return value + 1


def _tighter(name: str):
    value = getattr(CEILING, name)
    if name == "allowed_tools":
        return frozenset({"solver.solve"})
    if name == "forbidden_paths":
        return value | {"/etc"}
    if isinstance(value, bool):
        return value
    if name.startswith("quorum_"):
        return value + 1
    return value - 1


def test_every_policy_field_has_a_narrowing_rule():
    """A field added to RuntimePolicy without a rule here would pass through unchecked: it fails instead."""
    assert set(ww.NARROWING) == set(FIELDS)


@pytest.mark.parametrize("name", FIELDS)
def test_a_planted_widening_of_any_field_is_refused(name):
    with pytest.raises(ww.WideningRefused) as refused:
        ww.narrow(CEILING, **{name: _wider(name)})
    assert name in str(refused.value)


@pytest.mark.parametrize("name", FIELDS)
def test_a_tighter_value_of_any_field_is_kept(name):
    policy = ww.narrow(CEILING, **{name: _tighter(name)})
    assert getattr(policy, name) == _tighter(name)
    assert all(getattr(policy, other) == getattr(CEILING, other) for other in FIELDS if other != name)


def test_an_empty_allowlist_is_refused_even_under_an_allow_any_ceiling():
    with pytest.raises(ww.WideningRefused, match="allowed_tools"):
        ww.narrow(CEILING, allowed_tools=frozenset())
    with pytest.raises(ww.WideningRefused, match="allowed_tools"):
        ww.narrow(RuntimePolicy(), allowed_tools=frozenset())
    assert ww.narrow(RuntimePolicy(), allowed_tools=frozenset({"a.b"})).allowed_tools == {"a.b"}


def test_an_unknown_policy_field_is_refused():
    with pytest.raises(ww.WideningRefused, match="max_happiness"):
        ww.narrow(CEILING, max_happiness=3)


# --- forming a worker -----------------------------------------------------------------------------


def _unit(*caps: tuple[str, str], kind: str = "do", budget: int = 10) -> wp.UnitSpec:
    return wp.UnitSpec(key="u", kind=kind, goal="g", capabilities=tuple(caps), budget_cents=budget)


def test_a_worker_is_formed_from_a_template_narrowed_to_its_unit():
    spec = ww.form_worker("doer", ceiling=CEILING, unit=_unit(("solver", "solve")), contracts=_lookup)
    assert spec.allowed == (("solver", "solve"),) and spec.policy.allowed_tools == {"solver.solve"}
    assert spec.rung == "deterministic" and spec.template == "doer" and not spec.read_only
    assert spec.policy.max_cost_cents == 10  # the unit's budget, under the ceiling's 50
    assert spec.policy.forbidden_paths >= CEILING.forbidden_paths


def test_a_unit_that_names_no_capability_forms_no_worker():
    with pytest.raises(ValueError, match="allowlist"):
        ww.form_worker("doer", ceiling=CEILING, unit=_unit(), contracts=_lookup)


def test_a_read_only_template_takes_no_writing_op():
    with pytest.raises(ValueError, match="local_write"):
        ww.form_worker("verifier", ceiling=CEILING, unit=_unit(("ledger", "post"), kind="verify"),
                       contracts=_lookup)
    spec = ww.form_worker("verifier", ceiling=CEILING, unit=_unit(("ledger", "read"), kind="verify"),
                          contracts=_lookup)
    assert spec.read_only and spec.rung == "verify"
    assert (spec.policy.max_files_modified, spec.policy.max_lines_changed, spec.policy.max_shell_commands) == (0, 0, 0)


def test_a_capability_without_a_contract_forms_no_worker():
    with pytest.raises(ValueError, match="ParamContract"):
        ww.form_worker("doer", ceiling=CEILING, unit=_unit(("mailer", "send")), contracts=_lookup)


def test_a_unit_budget_above_the_ceiling_is_refused():
    with pytest.raises(ww.WideningRefused, match="max_cost_cents"):
        ww.form_worker("doer", ceiling=CEILING, unit=_unit(("solver", "solve"), budget=51), contracts=_lookup)


def test_a_worker_spec_must_permit_exactly_its_allowlist():
    policy = ww.narrow(CEILING, allowed_tools=frozenset({"solver.solve", "ledger.read"}))
    with pytest.raises(ValueError):
        ww.WorkerSpec(template="doer", purpose="p", rung="deterministic", allowed=(("solver", "solve"),),
                      policy=policy, read_only=False)
    with pytest.raises(ValueError):
        ww.WorkerSpec(template="doer", purpose="p", rung="deterministic", allowed=(), policy=policy, read_only=False)
    with pytest.raises(ValueError):
        ww.WorkerSpec(template="doer", purpose="p", rung="telepathy", allowed=(("solver", "solve"),),
                      policy=ww.narrow(CEILING, allowed_tools=frozenset({"solver.solve"})), read_only=False)


def test_the_digest_names_what_the_spec_permits():
    a = ww.form_worker("doer", ceiling=CEILING, unit=_unit(("solver", "solve")), contracts=_lookup)
    b = ww.form_worker("doer", ceiling=CEILING, unit=_unit(("solver", "solve")), contracts=_lookup)
    c = ww.form_worker("doer", ceiling=CEILING, unit=_unit(("solver", "solve"), budget=5), contracts=_lookup)
    assert a.sha256 == b.sha256 != c.sha256 and len(a.sha256) == 64


# --- templates are strict files -------------------------------------------------------------------


def test_the_shipped_templates_load():
    templates = ww.load_templates()
    assert {"doer", "verifier", "prober"} <= set(templates)
    assert templates["verifier"].read_only and templates["prober"].read_only


@pytest.mark.parametrize("over", [
    {"rung": "telepathy"}, {"mood": "brave"}, {"policy": {"allowed_tools": ["a.b"]}},
    {"policy": {"max_steps": "many"}}, {"allowed": [["solver"]]}, {"allowed": []}, {"read_only": "yes"},
])
def test_a_malformed_template_is_refused(tmp_path, over):
    body = {"name": "x", "purpose": "p", "rung": "deterministic", "read_only": False, "allowed": "from_unit",
            "policy": {"max_steps": 5}}
    body.update(over)
    (tmp_path / "x.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError):
        ww.load_templates(tmp_path)
