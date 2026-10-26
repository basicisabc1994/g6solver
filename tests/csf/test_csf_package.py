# SPDX-License-Identifier: Apache-2.0
"""g6.csf as a package (audit 012: CSF-003, CSF-009, CSF-017, CSF-019, and the §11 port notes): what was dropped is
absent, every file carries the library's licence header, the component metadata is declared, and running it writes
nothing outside the caller's own paths."""
from __future__ import annotations

import importlib
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import g6.csf
from g6.csf import rollback

_SRC = Path(g6.csf.__file__).parent


def _fails(test, *args) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


def _modules() -> list[object]:
    names = [m.name for m in pkgutil.walk_packages(g6.csf.__path__, "g6.csf.")]
    return [g6.csf, *(importlib.import_module(n) for n in names)]


def _absent(names: tuple[str, ...]) -> None:
    found = [f"{m.__name__}.{n}" for m in _modules() for n in names if hasattr(m, n)]
    assert not found, found


def test_csf003_no_hazard_table_check_safety_or_verify_bounds() -> None:
    """Archive: ``check_safety`` summed asserted per-operation constants (``code_execute`` 0.10, ...) into a
    "probability"; ``verify_bounds`` checked a caller's number against the same table (CSF-019). The operation
    vocabulary kept here holds classes, not numbers."""
    _absent(("check_safety", "verify_bounds", "get_hazard_info", "_G6_HAZARDS", "G6_SAFETY_SIGNATURE"))
    assert set(rollback.OPERATIONS.values()) == {"reversible", "irreversible"}


def test_csf009_no_nusmv_smv_lean_or_game_bridge() -> None:
    """Archive: the NuSMV bridge was called without an ``ltl_spec`` and never ran; SMV and Lean encoded a
    most-probable-successor abstraction; the safety game sampled the most probable branch (CSF-013)."""
    _absent(("check_via_nusmv", "agent_to_smv", "check_via_lean", "agent_to_lean", "verify_all_backends",
             "solve_game", "formulate_game", "worst_case", "SafetyVerifier", "CSFBlock"))


def test_null_csf003_csf009(monkeypatch) -> None:
    monkeypatch.setattr(g6.csf, "check_safety", lambda operations: {"meets_bound": True}, raising=False)
    _fails(test_csf003_no_hazard_table_check_safety_or_verify_bounds)
    monkeypatch.setattr(g6.csf, "check_via_nusmv", lambda agent: None, raising=False)
    _fails(test_csf009_no_nusmv_smv_lean_or_game_bridge)


_SPDX = "# SPDX-License-Identifier: Apache-2.0"
_ARCHIVE_HEADER = "# Proprietary and confidential. See LICENSE for terms.\n"  # the archive's second header line


def _header_ok(text: str) -> bool:
    head = "\n".join(text.splitlines()[:5])
    return text.startswith(_SPDX + "\n") and not any(w in head for w in ("Proprietary", "All rights reserved",
                                                                          "Copyright"))


def test_csf017_every_file_carries_the_library_licence_header() -> None:
    """Archive: every file said "Proprietary and confidential" while skill/plugin.json said MIT."""
    files = sorted(_SRC.rglob("*.py"))
    assert files and all(_header_ok(f.read_text(encoding="utf-8")) for f in files), [
        f.name for f in files if not _header_ok(f.read_text(encoding="utf-8"))]


def test_null_csf017() -> None:
    assert not _header_ok(_ARCHIVE_HEADER + '"""csf"""\n')


def test_component_metadata_is_declared() -> None:
    assert g6.csf.__g6_component__ is True and g6.csf.__g6_maturity__ == "beta"
    assert g6.csf.__g6_requires__ == ("formal",)
    doc = g6.csf.__doc__ or ""
    assert "(audit 012 §11)" in doc and "Capability label: ``implemented``" in doc
    offered, not_ported = doc.split("Not ported from the archive", 1)
    assert "reachability_bound" in offered and "worst_case" in not_ported
    assert all(hasattr(g6.csf, name) for name in g6.csf.__all__)


_RUN = r"""
import socket
def _no_net(*a, **k):
    raise AssertionError("network access")
socket.socket.connect = _no_net
from g6.csf import BoundedAgent, ResourceTriple, compose_bound, reachability_bound, rollback_feasibility
from g6.csf.smt import check_z3
rows = [{"from": "a", "to": "bad", "probability": 0.5}, {"from": "a", "to": "a", "probability": 0.5},
        {"from": "bad", "to": "bad", "probability": 1.0}]
agent = BoundedAgent(["a", "bad"], rows, "a", ["bad"], ResourceTriple(N=3))
reachability_bound(agent, 0.2).to_envelope()
check_z3(agent, "G(!bad)").to_envelope()
compose_bound(0.1, 0.2)
rollback_feasibility(["file_write"], 2).to_envelope()
"""


def test_running_the_package_writes_nothing_and_uses_no_network(tmp_path: Path) -> None:
    """Audit 012 §11: the archive kept SQLite ledgers at ``~/.csf/csf.db`` and ``~/.csf_formal/csf_formal.db``."""
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "G6_HOME": str(home / ".g6")}
    subprocess.run([sys.executable, "-c", _RUN], cwd=work, env=env, check=True)
    assert list(home.iterdir()) == [] and list(work.iterdir()) == []
