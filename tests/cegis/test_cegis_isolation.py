# SPDX-License-Identifier: Apache-2.0
"""Every CEGIS candidate runs through ``g6.core.confine``, like every other run of untrusted code: its child is
planned, confined where a backend can, and the result says how (``boundary["isolation"]``); a run that isolation is
required for and no backend can give is refused, and no candidate runs. And an outside program's plan
(``confine.program_paths``) reads the program's folders and starts no interpreter. Offline."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from g6.cegis import InputDomain, Sketch, cegis
from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.invariants import check_envelope

LINEAR = "def f(x):\n    return x * ?? + ??\n"
X = InputDomain.ints(["x"], -3, 3)
OFF = "unavailable: switched off by this test"


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))


def test_a_candidates_run_is_planned_by_confine_and_the_result_says_how_it_ran(monkeypatch) -> None:
    _off(monkeypatch)
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X)
    # fails if SubprocessRunner starts its child without a confine plan, or the result drops the plan's record
    assert result.status == "no_counterexample_in_domain"
    assert result.boundary["isolation"]["backend"] is None and result.boundary["isolation"]["why"] == OFF
    assert result.boundary["isolation"]["warning"] == confine.UNCONFINED_WARNING
    assert result.to_envelope()["evidence"][0]["isolation"]["why"] == OFF


def test_with_isolation_required_and_no_backend_cegis_is_refused_and_no_candidate_runs(monkeypatch) -> None:
    _off(monkeypatch)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    started: list[Any] = []
    monkeypatch.setattr(component_process, "run_contained", lambda *a, **k: started.append(a))
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X)
    # fails if a candidate is run unconfined when isolation is required, or the refusal is reported as a search result
    assert started == [] and result.status == "refused" and result.control is None
    assert result.completion_state == "blocked-escalated"
    assert result.reason.startswith(f"[{ISOLATION_UNAVAILABLE}]") and OFF in result.reason
    envelope = result.to_envelope(request_id="q", run_id="r")
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == ISOLATION_UNAVAILABLE


def test_a_cegis_candidate_runs_inside_bubblewrap_on_linux(bwrap_backend) -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X)
    # fails if CEGIS's child runs outside the operating-system boundary where one is ready
    assert result.status == "no_counterexample_in_domain"
    assert (result.boundary["isolation"]["backend"], result.boundary["isolation"]["why"]) == (confine.BUBBLEWRAP, "")


def _program(tmp_path: Path) -> tuple[Path, Path]:
    prefix = tmp_path / "opt" / "tool"
    exe = prefix / "bin" / ("tool.exe" if os.name == "nt" else "tool")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    exe.chmod(0o755)
    return prefix, exe


def test_an_outside_programs_paths_are_its_folder_its_installation_and_the_extra_ones_that_exist(tmp_path) -> None:
    prefix, exe = _program(tmp_path)
    home = tmp_path / "elan"
    home.mkdir()
    # fails if program_paths misses the installation above a bin folder (its lib and share), or keeps a path that
    # does not exist
    assert confine.program_paths(str(exe), str(home), str(tmp_path / "absent")) == (
        os.path.realpath(exe.parent), os.path.realpath(prefix), os.path.realpath(home))
    loose = tmp_path / "loose" / "tool"
    loose.parent.mkdir()
    loose.write_bytes(b"")
    assert confine.program_paths(str(loose)) == (os.path.realpath(loose.parent),)


class _Fake:
    name = "fake"

    def __init__(self) -> None:
        self.prepared: list[tuple[confine.Confinement, str | None]] = []

    def ready(self, python: str, *, probe: bool = True) -> str:
        return ""

    def started(self, python: str) -> bool:
        return False

    def prepare(self, confinement: confine.Confinement, python: str | None) -> tuple[Any, str]:
        self.prepared.append((confinement, python))
        return object(), ""

    def start(self, argv: list[str], identity: Any, *, network: bool = False, **options: Any) -> Any:
        raise AssertionError("not started here")


def test_a_plan_for_an_outside_program_names_no_interpreter(monkeypatch, tmp_path) -> None:
    fake = _Fake()
    monkeypatch.setattr(confine, "_select", lambda: (fake, ""))
    _, exe = _program(tmp_path)
    with confine.run_folder("g6_test_program_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,), read_only=confine.program_paths(str(exe))))
    # fails if a program's plan needs an interpreter (the backend would add and test-start one it does not run)
    assert plan.backend == "fake" and fake.prepared[0][1] is None
    assert fake.prepared[0][0].read_only == confine.program_paths(str(exe))
