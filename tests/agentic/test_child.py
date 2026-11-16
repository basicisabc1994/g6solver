# SPDX-License-Identifier: Apache-2.0
"""The kernel's child (``g6._agentic._child``): its replay channel, its entry point, and its launch, which is confined
by an operating-system backend or refused, never run unconfined (design 5.3, 10.2, 10.11)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from g6._agentic import _child, kernel
from g6.cegis import runner
from g6.core import component_process, confine
from g6.core.storage import g6_home
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, FLOAT_SKETCH, Model

RUN = {"kind": "run", "component": "cegis", "op": "synthesize", "params": FLOAT_PARAMS,
       "functions": ["g6.cegis.hole:float_grid"], "answers": []}


def _inside(path: Path, folder: str) -> bool:
    try:
        return path.resolve().is_relative_to(Path(folder).resolve())
    except OSError:
        return False


def test_the_first_unanswered_call_ends_the_run_with_an_ask_and_g6s_function_is_put_back():
    import g6.cegis.hole as hole

    original = hole.float_grid
    said = _child.serve(RUN)
    assert set(said) == {"ask"} and said["ask"]["index"] == 0
    assert said["ask"]["call"] == {"function": "g6.cegis.hole:float_grid", "args": [0, -32, 32, FLOAT_SKETCH],
                                   "kwargs": {}}
    assert said["ask"]["digest"] == _child.call_digest(said["ask"]["call"]) and hole.float_grid is original


def test_an_answer_for_another_call_does_not_replay(monkeypatch):
    monkeypatch.setattr(runner, "_INSIDE", None)
    said = _child.serve({**RUN, "answers": [{"call": "0" * 64, "value": [0.5]}]})
    assert said["refused"].startswith("[G6_E_AGENTIC_NOT_PURE]")


def test_a_part_outside_the_components_package_is_never_resolved():
    with pytest.raises(ValueError, match="own package"):
        _child.resolve("g6.datalog.ops:evaluate", "g6.cegis")
    with pytest.raises(ValueError, match="plain name"):
        _child.resolve("g6.cegis.hole:float_grid.__globals__", "g6.cegis")
    with pytest.raises(ValueError, match="not defined in"):
        _child.resolve("g6.cegis.loop:check_candidate", "g6.cegis")   # imported there from g6.cegis.verify
    with pytest.raises(ValueError, match="own package"):
        _child.serve({**RUN, "functions": ["os:system"]})


def test_the_entry_point_reports_its_own_failure_as_one_line(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({**RUN, "functions": ["os:system"]})))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert _child.main() == 0
    reply = json.loads(out.getvalue().strip().splitlines()[-1])
    assert reply["refused"].startswith("[G6_E_AGENTIC_CHILD_FAILED]")


def test_the_entry_point_sets_the_runner_seam_and_runs_the_declared_checker(monkeypatch):
    monkeypatch.setattr(runner, "_INSIDE", None)   # restored after the test: this process is not a kernel child
    result = {"control": {"0": 0.5}, "program": "def f(x):\n    return x * 0.5\n"}
    request = {"kind": "check", "component": "cegis", "checker": "g6.cegis.block:recheck", "params": FLOAT_PARAMS,
               "result": result, "inside": "g6.cegis.runner:inside_kernel_child", "isolation": {"backend": "x"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert _child.main() == 0
    reply = json.loads(out.getvalue().strip())
    assert reply["done"]["ok"]["passed"] is True and reply["done"]["ok"]["compared"] == 3


def test_the_child_is_confined_or_the_stage_is_refused_never_run_unconfined(monkeypatch, tmp_path):
    """Where a backend can confine it the child has no network, writes only its own folder and reads neither the key
    file nor a sign-in; where none can, nothing starts and no model is asked."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    plans, contained = [], []
    real_plan, real_run = confine.plan, component_process.run_contained

    def plan(confinement, python=None):
        made = real_plan(confinement, python)
        plans.append((confinement, made))
        return made

    def run_contained(argv, **options):
        contained.append(options)
        return real_run(argv, **options)

    monkeypatch.setattr(confine, "plan", plan)
    monkeypatch.setattr(component_process, "run_contained", run_contained)
    model = Model([0.25, 0.5, 1.0])
    reply = kernel.run("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=model.llm)
    confinement, made = plans[0]
    assert confinement.network is False and len(confinement.read_write) == 1
    assert Path(confinement.read_write[0]).name.startswith("g6_agentic_")
    assert confinement.read_only == confine.interpreter_paths(sys.executable)
    for secret in (g6_home(), g6_home() / ".env", Path.home() / ".claude", Path.home() / ".codex"):
        assert not any(_inside(secret, folder) for folder in (*confinement.read_only, *confinement.read_write)), secret
    if made.backend is None:
        assert reply["agentic"]["outcome"] == "degraded" and reply["agentic"]["reason"] == "isolation_unavailable"
        assert "G6_E_ISOLATION_UNAVAILABLE" in reply["agentic"]["detail"] and contained == [] and model.prompts == []
        assert reply["agentic"]["isolation"]["backend"] is None and reply["agentic"]["isolation"]["why"] == made.why
        assert reply["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"
        return
    assert reply["agentic"]["outcome"] == "substituted", reply["agentic"]
    assert reply["agentic"]["isolation"]["backend"] == made.backend and reply["agentic"]["isolation"]["network"] is False
    assert all(options["confined"].backend for options in contained) and len(contained) == 3
    for options in contained:
        assert not [k for k in options["env"] if k.startswith(("G6_", "ANTHROPIC", "OPENAI", "OPENROUTER", "CLAUDE"))]
