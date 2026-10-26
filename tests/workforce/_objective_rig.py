# SPDX-License-Identifier: Apache-2.0
"""A bound workforce and its local tool calls for plain objective tests (the archive's MCP rig, as library calls).

The archive drove these tests through its served workforce toolset and a local MCP client; the library ships no MCP
server, so ``call`` answers each tool as ``_tools`` does (the same JSON answers), and ``human`` confirms at the ported
``decide`` command line exactly as before. Before each ``mission_create`` on a repository the simulated person allows G6
to run its present code at that command line (``decide allow``, audit 010 WF-007)."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from g6.workforce import decide, service
from g6.workforce.store import VentureStore

_spec = importlib.util.spec_from_file_location("workforce_tools_fixture", Path(__file__).with_name("_tools.py"))
_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tools)


def plain(text):
    """The archive's check that an objective names no tool or op (its tool and op catalogue, as far as the library
    has one: the served tool names and the bench's ops)."""
    names = set(_tools.TOOLS) | {"workforce.run_tests", "workforce.run_tests_in_place", "run_tests",
                                 "run_tests_in_place"}
    named = {n for n in names if "_" in n or "." in n or f"`{n}`" in text}
    found = [n for n in named - {"mission_create"} if re.search(rf"\b{re.escape(n)}\b", text)]
    assert not found, found
    return text


@pytest.fixture
def objective_rig(tmp_path, monkeypatch):
    store = VentureStore(tmp_path / "venture.db")
    venture = store.create_venture("alice", "synthetic", budget_aud_month=0, model_cap_usd_month=0,
                                   task_cap_usd=0, max_turns=5)["venture_id"]
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path))
    monkeypatch.setenv(service.ENV_VENTURE, venture)
    monkeypatch.setenv(service.ENV_OWNER, "alice")
    calls = []

    def repository(files):
        repo = tmp_path / "repos" / venture / "auth_service"
        for relative, body in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        for args in (("init",), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                             "commit", "-m", "synthetic")):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        return repo

    def call(name, **arguments):
        if "objective" in arguments:
            plain(arguments["objective"])
        calls.append(name)
        if name == "mission_create":
            allow(arguments)
        return _tools.call(name, **arguments)

    def allow(arguments):  # audit 010 WF-007: the person first allows G6 to run the repository's present code
        try:
            repo = json.loads(arguments.get("repo_json") or "{}").get("repo")
        except (ValueError, AttributeError):
            return
        if isinstance(repo, str):
            with monkeypatch.context() as actor:
                actor.setattr(decide, "_interactive", lambda: True)
                decide.main(["allow", repo, "--reason", "synthetic permission to run the repository's code"],
                            ask=lambda _: repo)

    def human(mid):
        with monkeypatch.context() as actor:
            actor.setattr(decide, "_interactive", lambda: True)
            # COPY: The simulated person's recorded reason.
            assert decide.main(["confirm", mid, "--reason", "synthetic human confirmation"],
                               ask=lambda _: mid) == 0

    yield SimpleNamespace(call=call, human=human, repository=repository, calls=calls, served=None,
                          venture=venture, loop=None)
