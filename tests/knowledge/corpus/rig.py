# SPDX-License-Identifier: Apache-2.0
"""A project with the synthetic corpus in it, a scratch G6 home and a host binding, for the Context Engine's tests."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from g6.knowledge import _engine as engine
from g6.knowledge._envelope import SearchRequest
from g6.knowledge._scope import ROOTS_ENV, Access, Binding, access, host_binding
from tests.knowledge import corpus

NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


@dataclass
class Rig:
    home: Path
    project: Path
    folder: Path
    binding: Binding
    planted: dict

    def local(self, operation: str = "search", sources=None) -> Access:
        return access(self.binding, operation, "local", sources)

    def to(self, destination: str, operation: str = "search") -> Access:
        return access(self.binding, operation, destination)

    def add(self, path: str | None = None, **settings) -> dict:
        """The rig's person accepts a preview of ``path``. Unless a test says otherwise (``backends=()``), the
        preview lists the managed Elasticsearch copy too, so that a test may start that job afterwards: a job for a
        backend the accepted preview does not list is refused. Nothing is written there unless a test starts a job."""
        settings.setdefault("backends", ("elastic_managed",))
        shown = engine.preview(self.binding, path or str(self.folder), **settings)
        return engine.commit(self.binding, shown, "Person")

    def search(self, query: str, *, acc: Access | None = None, top_k: int = 8, clock=lambda: NOW):
        response, _ = engine.search(acc or self.local(), SearchRequest(query=query, top_k=top_k), clock=clock)
        return response

    def items(self, query: str, **kw) -> list[dict]:
        return self.search(query, **kw).payload["items"]


def make(tmp_path: Path, monkeypatch, *, name: str = "project", build: bool = True) -> Rig:
    """A project folder ``name`` under ``tmp_path`` holding the corpus in ``knowledge/``, listed as the one permitted
    root; ``G6_HOME`` is a scratch folder shared by every rig made from the same ``tmp_path``."""
    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    project = tmp_path / name
    folder = project / "knowledge"
    planted = corpus.build(folder) if build else {}
    project.mkdir(parents=True, exist_ok=True)
    binding = host_binding(environ={ROOTS_ENV: str(project)}, start=project)
    return Rig(home=home, project=project, folder=folder, binding=binding, planted=planted)


def serve(rig: Rig, monkeypatch) -> None:
    """Make ``rig``'s project the one a server process started here is bound to."""
    monkeypatch.setenv(ROOTS_ENV, str(rig.project))
    monkeypatch.chdir(rig.project)


def link_out(link: Path, target: Path) -> bool:
    """A folder link at ``link`` to ``target`` (a junction on Windows, a symbolic link elsewhere); False when this
    account may not make one."""
    try:
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, AttributeError):
        return False
    return True
