# SPDX-License-Identifier: Apache-2.0
"""SWI-Prolog, Lean, TLC and SageMath run the caller's program, goal, proof or spec, so each goes through
``g6.core.confine`` like every other run of untrusted code: planned with the program's own folders read only
(``confine.program_paths``, with ELAN_HOME, the JDK's root or SAGE_ROOT), its run folder writable, its record kept in
the result's evidence; and refused, before the program starts, as the envelope's ``G6_E_ISOLATION_UNAVAILABLE`` where
isolation is required and no backend can give it. Offline: the programs are stand-ins and nothing is started."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_ACTION, ISOLATION_UNAVAILABLE

OFF = "unavailable: switched off by this test"


@pytest.fixture
def started(monkeypatch) -> list[dict[str, Any]]:
    """No backend, and every start the runners ask for recorded (the program is never really started: exit 0)."""
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    calls: list[dict[str, Any]] = []

    def run_process(argv: list[str], **options: Any) -> int:
        plan = options.get("confined")
        calls.append({"argv": argv, **options, "writes_owned": plan is not None and all(
            confine.owned(path) for path in plan.confinement.read_write)})   # while the run's folder is still there
        return 0

    monkeypatch.setattr(component_process, "run_process", run_process)
    return calls


def _program(tmp_path: Path, name: str) -> Path:
    exe = tmp_path / name / "bin" / (f"{name}.exe" if os.name == "nt" else name)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    exe.chmod(0o755)
    return exe


def _planned(call: dict[str, Any], program: Path, *extra: Path) -> None:
    plan = call["confined"]
    # fails if the runner starts its program without a confine plan, or the plan misses the program's folders
    assert isinstance(plan, confine.Plan) and plan.backend is None and plan.why == OFF
    assert set(confine.program_paths(str(program), *map(str, extra))) <= set(plan.confinement.read_only)
    assert plan.confinement.read_write and call["writes_owned"] and plan.confinement.network is False


def test_swi_prolog_runs_through_a_plan_with_its_folders(started, tmp_path) -> None:
    from g6.prolog import runner

    swipl, record = _program(tmp_path, "swipl"), {}
    runner.run(str(swipl), runner.driver_arguments(256), files={"program.pl": "a."}, seconds=5, memory_mb=256,
               cap=1000, record=record)
    _planned(started[0], swipl)
    assert record["isolation"]["why"] == OFF and record["isolation"]["warning"] == confine.UNCONFINED_WARNING


def test_lean_runs_through_a_plan_with_its_folders_and_elan_home(started, tmp_path, monkeypatch) -> None:
    from g6.lean import runner

    lean, elan, record = _program(tmp_path, "lean"), tmp_path / "elan-home", {}
    elan.mkdir()
    monkeypatch.setenv("ELAN_HOME", str(elan))
    runner.run(str(lean), ["--json", "Proof.lean"], file_text="theorem t : True := trivial", seconds=5,
               memory_mb=256, cap=1000, record=record)
    _planned(started[0], lean, elan)
    assert record["isolation"]["why"] == OFF


def test_sage_runs_through_a_plan_with_its_folders_and_sage_root(started, tmp_path, monkeypatch) -> None:
    from g6.algebra import runner

    sage, root, record = _program(tmp_path, "sage"), tmp_path / "sage-root", {}
    root.mkdir()
    monkeypatch.setenv("SAGE_ROOT", str(root))
    runner.run_sage(str(sage), ["g6_expression.sage"], file_name="g6_expression.sage", file_text="print(1)",
                    seconds=5, memory_mb=512, cap=1000, record=record)
    _planned(started[0], sage, root)
    assert record["isolation"]["why"] == OFF


def _jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jar = tmp_path / "tla2tools.jar"
    jar.write_bytes(b"not really a jar")
    monkeypatch.setenv("G6_TLA2TOOLS_JAR", str(jar))
    monkeypatch.setenv("G6_TLA2TOOLS_SHA256", hashlib.sha256(jar.read_bytes()).hexdigest())


def test_tlc_runs_through_a_plan_with_javas_folders_and_the_jdk(started, tmp_path, monkeypatch) -> None:
    from g6.model_checking import tlc

    java, jdk = _program(tmp_path, "java"), tmp_path / "jdk"
    jdk.mkdir()
    monkeypatch.setenv("G6_JAVA", str(java))
    monkeypatch.setenv("JAVA_HOME", str(jdk))
    _jar(tmp_path, monkeypatch)
    result = tlc.sany_parse("---- MODULE Spec ----\nVARIABLE x\n====\n")
    _planned(started[0], java, jdk)
    # the record reaches the result's evidence
    assert result.evidence[0]["isolation"]["why"] == OFF


SPEC = "---- MODULE Spec ----\nVARIABLE x\n====\n"


@pytest.mark.parametrize("why", ["not allowed", "required"])
@pytest.mark.parametrize("which", ["prolog", "lean", "tlc", "sage"])
def test_with_no_backend_each_is_refused_before_it_starts_unless_allowed(which, why, started, tmp_path,
                                                                         monkeypatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))   # no choice recorded
    if why == "not allowed":
        monkeypatch.delenv(confine.ALLOW, raising=False)
    else:
        monkeypatch.setenv(confine.REQUIRE, "1")               # whatever was allowed
    if which == "prolog":
        from g6.prolog import query

        monkeypatch.setenv("G6_SWIPL", str(_program(tmp_path, "swipl")))
        result = query("parent(tom, bob).", "parent(tom, X)")
    elif which == "lean":
        from g6.lean import check_proof

        monkeypatch.setenv("G6_LEAN", str(_program(tmp_path, "lean")))
        result = check_proof("theorem t : True := trivial", "t")
    elif which == "tlc":
        from g6.model_checking import tlc

        monkeypatch.setenv("G6_JAVA", str(_program(tmp_path, "java")))
        _jar(tmp_path, monkeypatch)
        result = tlc.sany_parse(SPEC)
    else:
        from g6.algebra import sage_evaluate

        monkeypatch.setenv("G6_SAGE", str(_program(tmp_path, "sage")))
        result = sage_evaluate("x + 1", symbols=["x"])
    # fails if the program is started unconfined where isolation is required, or the refusal is not the envelope's
    # own, with its code and what to do
    assert started == [], started
    assert result.completion_state == "blocked-escalated" and result.code == ISOLATION_UNAVAILABLE
    assert result.reason.startswith(f"[{ISOLATION_UNAVAILABLE}]") and OFF in result.reason
    assert result.warning_card["code"] == ISOLATION_UNAVAILABLE
    assert result.warning_card["suggested_action"] == ISOLATION_ACTION


def test_an_installation_that_keeps_part_of_itself_elsewhere_is_read_there_too(tmp_path) -> None:
    java = _program(tmp_path, "jdk")
    elsewhere = tmp_path / "etc" / "java-21"
    (elsewhere / "security").mkdir(parents=True)
    (elsewhere / "security" / "java.security").write_text("securerandom.source=file:/dev/random\n", encoding="utf-8")
    (elsewhere / "jvm.cfg").write_text("-server KNOWN\n", encoding="utf-8")
    (tmp_path / "jdk" / "lib").mkdir()
    (tmp_path / "jdk" / "conf" / "security").mkdir(parents=True)
    try:
        (tmp_path / "jdk" / "conf" / "security" / "java.security").symlink_to(elsewhere / "security" / "java.security")
        (tmp_path / "jdk" / "lib" / "jvm.cfg").symlink_to(elsewhere / "jvm.cfg")
    except OSError as exc:   # Windows without the right to make links
        pytest.skip(f"cannot make a symbolic link here: {exc}")
    paths = confine.program_paths(str(java))
    # fails if a JDK whose files link into /etc (Debian's: conf/security/java.security, lib/jvm.cfg) is planned
    # without where they lead: Java then cannot load its java.security file inside the boundary
    assert os.path.realpath(elsewhere) in paths
    assert os.path.realpath(elsewhere / "security") not in paths   # inside a folder already named


def test_a_version_run_is_not_planned_it_runs_the_persons_own_program_on_nothing_of_the_callers(started,
                                                                                            tmp_path) -> None:
    from g6.prolog import runner

    record: dict[str, Any] = {}
    runner.run(str(_program(tmp_path, "swipl")), ["--version"], files={}, seconds=5, memory_mb=256, cap=1000,
               record=record)
    # fails if `swipl --version` (no program, goal or file of the caller's) is refused where isolation is required
    assert started[0].get("confined") is None and "isolation" not in record


@pytest.mark.skipif(shutil.which("swipl") is None, reason="SWI-Prolog (swipl) is not on PATH")
def test_swi_prolog_runs_inside_bubblewrap_on_linux(bwrap_backend) -> None:
    from g6.prolog import query

    result = query("parent(tom, bob).\nparent(bob, ann).", "parent(tom, X)")
    # fails if SWI-Prolog's run (and its re-check) is not confined where bubblewrap is ready, or no longer works there
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.evidence[0]["search"]["isolation"]["backend"] == confine.BUBBLEWRAP
    assert result.evidence[0]["recheck_run"]["isolation"]["backend"] == confine.BUBBLEWRAP
