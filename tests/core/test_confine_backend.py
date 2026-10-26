# SPDX-License-Identifier: Apache-2.0
"""The backend interface of ``g6.core.confine``: a backend says whether it is ``ready``, ``prepare``s one run's
identity and ``start``s the child; nothing in :func:`confine.plan` or :func:`confine.popen` knows more of it. And the
process tree starts a confined child on POSIX too, leading a new session. Offline; nothing here starts a process."""
from __future__ import annotations

import sys
from typing import Any

import pytest

from g6.core import confine, process_tree


class _Fake:
    """A backend with only the interface: ready, started, prepare, start."""

    name = "fake"

    def __init__(self, why: str = "") -> None:
        self.why = why
        self.prepared: list[tuple[confine.Confinement, str]] = []
        self.started_with: list[tuple[list[str], Any, dict[str, Any]]] = []

    def ready(self, python: str, *, probe: bool = True) -> str:
        return ""

    def started(self, python: str) -> bool:
        return False

    def prepare(self, confinement: confine.Confinement, python: str) -> tuple[Any, str]:
        self.prepared.append((confinement, python))
        return (None, self.why) if self.why else (_Identity(), "")

    def start(self, argv: list[str], identity: Any, *, network: bool = False, **options: Any) -> Any:
        self.started_with.append((list(argv), identity, {"network": network, **options}))
        return "a child"


class _Identity:
    def close(self) -> str:
        return ""


def test_a_backend_needs_only_ready_prepare_and_start(monkeypatch) -> None:
    fake = _Fake()
    monkeypatch.setattr(confine, "_select", lambda: (fake, ""))
    with confine.run_folder("g6_test_backend_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,), network=True), sys.executable)
        # fails if confine._plan reaches past the interface (the AppContainer's readable, grant or identity)
        assert (plan.backend, plan.why) == ("fake", "") and isinstance(plan.identity, _Identity)
        assert fake.prepared == [(plan.confinement, sys.executable)]
        child = confine.popen(plan, ["prog", "arg"], stdout=None)
    # fails if confine.popen stops handing the run's identity and network setting to the backend's start
    assert child == "a child"
    assert fake.started_with == [(["prog", "arg"], plan.identity, {"network": True, "stdout": None})]
    assert plan.release() == ""
    assert plan.record()["container"] is None and plan.record()["profile"] is None


def test_a_backend_that_cannot_prepare_the_run_leaves_it_unconfined_with_its_reason(monkeypatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (_Fake("unavailable: the fake backend said no"), ""))
    with confine.run_folder("g6_test_backend_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,)), sys.executable)
    # fails if prepare's reason is dropped, or a run it could not prepare is called confined
    assert (plan.backend, plan.why) == (None, "unavailable: the fake backend said no")
    with pytest.raises(confine.NotStarted):
        confine.popen(plan, ["prog"])


class _Proc:
    pid = 4242
    returncode = None


def test_a_confined_start_on_posix_leads_a_new_session(monkeypatch) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    asked: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(argv: list[str], **options: Any) -> _Proc:
        asked.append((argv, options))
        return _Proc()

    tree = process_tree._Tree(["prog"], env={}, stderr=None, spawn=spawn)
    # fails if the process tree still refuses a confined start outside Windows, or starts it in the caller's session
    # (its process group could not then be killed whole)
    assert tree.proc.pid == 4242
    assert asked[0][0] == ["prog"] and asked[0][1]["start_new_session"] is True
    assert "creationflags" not in asked[0][1]
