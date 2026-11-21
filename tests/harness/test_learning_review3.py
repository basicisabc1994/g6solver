"""Review 3 of the learning loop: a template finding's replay keeps its template, and g6_invoke never blocks the MCP
event loop."""
import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import resolution, verification_search as vs, verification_templates as vt
from tests.harness.test_proposed_checks import result

#: Every template whose instances compile into a check search runs (the structural ones run no check).
COMPILED = sorted(name for name, definition in vt.REGISTRY.items() if definition.text or definition.formal)


def _replay(origin, one, on_patch):
    done = vs.Search()
    vs._found(done, one, vs._activity(one, vs.placement(one), origin), {**on_patch, "tree_digest": "t"},
              {"result": "passed", "verdict": "passed", "detail": "d", "tree_digest": "t"},
              {"result": "counterexample", "detail": "d"}, "a" * 64, "b" * 40, origin)
    assessment = {"requirements": [], "completion": {"defects_reproduced": {"proposed_checks": done.defects}}}
    return resolution.build(assessment, repo="fixture", task="keep")[0]["cheapest_check"]["arguments"]


@pytest.mark.parametrize("template", COMPILED)
def test_every_compiled_template_finding_replays_its_own_instance(template):
    bindings = {"stands": "for the template's own bindings"}
    instance = vt.TemplateInstance(id="i", template=template, requirement_id="R", intent="preserve", bindings=bindings)
    origin = vs.Origin(template=template, instance={"template": template, "requirement_id": "R",
                                                    "intent": "preserve", "bindings": bindings})
    one = vs.ProposedCheck(id="i", requirement_id="R", intent="preserve", file="test_i.py",
                           source="def test_i():\n    assert False\n")
    args = _replay(origin, one, {"result": "failed", "verdict": "failed", "detail": "d"})
    assert "proposed_checks" not in args
    assert args["template_instances"] == [{"id": "settle-reproduction", "template": instance.template,
                                           "requirement_id": "R", "intent": "preserve", "bindings": bindings}]


def test_compiled_check_names_its_instance_but_never_a_persons_confirmed_one(monkeypatch):
    monkeypatch.setattr(vt, "compile_text", lambda definition, data: "def test_x():\n    pass\n")
    monkeypatch.setattr(vs, "placements", lambda root, checks: {c.id: c.file for c in checks})
    stated = vt.TemplateInstance(id="e", template="example.v1", requirement_id="R", intent="preserve",
                                 bindings={"b": 1})
    _, _, origin = vt.compiled_check(Path.cwd(), stated, {}, [])
    assert origin.instance == {"template": "example.v1", "requirement_id": "R", "intent": "preserve",
                               "bindings": {"b": 1}}
    confirmed = stated.model_copy(update={"provenance": "person_confirmed"})
    _, _, origin = vt.compiled_check(Path.cwd(), confirmed, {}, [])
    assert origin.instance is None


def test_a_free_form_finding_still_replays_as_its_proposed_check_text():
    one = vs.ProposedCheck(id="f", requirement_id="R", intent="preserve", file="test_f.py",
                           source="def test_f():\n    assert False\n", test="test_f")
    args = _replay(vs.FREE_FORM, one, {"result": "failed", "verdict": "failed", "detail": "d"})
    assert "template_instances" not in args
    assert args["proposed_checks"][0]["test"] == "test_f"


def test_resolution_replay_preserves_differential_counterexample(monkeypatch):
    data = {"function": ["module", "calc", "edge"], "mode": "outputs",
            "calls": [{"args": [1], "kwargs": {}}]}
    instance = vt.TemplateInstance(id="keep-edge", requirement_id="R", intent="preserve",
        template="differential.v1",
        bindings={"function": "calc.edge", "mode": "outputs", "calls": [{"args": [1]}]})
    one, placed, origin = vt.compiled_check(Path.cwd(), instance, data, [])
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(), base_commit="b" * 40)

    def run(change, placed, source, node, *, at_base, **kwargs):
        observation = {"input": {"args": [1], "kwargs": {}}, "output": 1 if at_base else 2}
        from g6.harness.behaviour_runner import ExecutableSource
        if type(source) is ExecutableSource:
            return {"verdict": "passed", "said": "", "seconds": 0.0, "source_before": "a" * 64,
                    "g6_observation": {"result": "passed", "detail": "observed",
                                      "observations": [observation]}}
        return {**result([node], output="G6-OBSERVATION " + json.dumps(observation)), "completed": True}

    monkeypatch.setattr(vs, "_run_check", run)
    original = vs.search(change, [one], {one.id: placed},
        deadline=vs._clock() + 60, artifact="a" * 64, origins={one.id: origin})
    assert original.activities[0]["result"] == "counterexample"
    assessment = {"requirements": [{"id": "R", "text": "Preserve edge behavior", "checks": [],
        "status": "passed_on_stated_checks"}],
        "completion": {"defects_reproduced": {"proposed_checks": original.defects}}}
    call = resolution.build(assessment, repo=str(change.repo), task="Preserve edge")[0]["cheapest_check"]
    # Repair 3: the replay names the template instance itself, which the review validates and compiles from the same
    # tree data (``verification_templates.prepare``), so it keeps its template's identity and semantics.
    assert "proposed_checks" not in call["arguments"], call
    replayed = vt.template_instances(call["arguments"]["template_instances"], {"R"})
    assert [(r.template, r.intent, r.bindings) for r in replayed] == [
        (instance.template, instance.intent, instance.bindings)]
    again, again_placed, again_origin = vt.compiled_check(Path.cwd(), replayed[0], data, [])
    repeated = vs.search(change, [again], {again.id: again_placed},
                        deadline=vs._clock() + 60, artifact="a" * 64, origins={again.id: again_origin})
    observed = repeated.activities[0]["result"]
    assert observed == "counterexample", observed


def test_registered_invoke_does_not_block_mcp_event_loop(monkeypatch):
    import g6.mcp as mcp
    from g6.mcp.registrations import _listed

    heartbeat = threading.Event()

    def synchronous_component(*args, **kwargs):
        return {"ok": True, "heartbeat_ran_during_component": heartbeat.wait(0.25)}

    monkeypatch.setattr(mcp, "invoke_reply", synchronous_component)
    tool = dict(_listed(mcp.build_server()))["g6_invoke"]

    async def run():
        async def other_request():
            await asyncio.sleep(0.01)
            heartbeat.set()
        reply, _ = await asyncio.gather(
            tool.fn(component="learning_history", op="list", params_json='{"repo":"fixture"}', ctx=None),
            other_request())
        return reply

    reply = asyncio.run(run())
    assert reply["heartbeat_ran_during_component"], reply
