# SPDX-License-Identifier: Apache-2.0
"""A missing extra may remove its tools, never the other families' guidance."""
from __future__ import annotations

from functools import cache
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest


REPO = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
EXTRAS = PROJECT["optional-dependencies"]
# Distribution names which do not name their import roots. A recursive extra (all)
# blocks the roots of every extra it includes; a namespace package blocks its root.
IMPORT_NAMES = {
    "pillow": "PIL", "scikit-learn": "sklearn", "z3-solver": "z3",
    "import-linter": "importlinter", "langgraph-checkpoint": "langgraph",
}


@cache
def _distribution_import_roots():
    roots = {}
    for module, distributions in metadata.packages_distributions().items():
        for distribution in distributions:
            roots.setdefault(canonicalize_name(distribution), set()).add(module.split(".")[0])
    return roots


@cache
def _base_import_roots():
    # Resolve only g6[mcp], not every extra installed in this test environment.
    pending = []
    for raw in PROJECT["dependencies"] + EXTRAS["mcp"]:
        requirement = Requirement(raw)
        if requirement.marker is None or requirement.marker.evaluate({"extra": "mcp"}):
            pending.append(requirement)
    seen = set()
    required = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        # Missing required metadata is an invalid base install, not an empty closure.
        distribution = metadata.distribution(requirement.name)
        required.add(name)
        for raw in distribution.requires or []:
            dependency = Requirement(raw)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra})
                for extra in ("", *requirement.extras)
            ):
                pending.append(dependency)
    imports = _distribution_import_roots()
    return frozenset(module for name in required for module in imports.get(name, ()))


def _roots(extra):
    roots = set()
    imports = _distribution_import_roots()
    for raw in EXTRAS[extra]:
        requirement = Requirement(raw)
        name = canonicalize_name(requirement.name)
        if name == "g6":
            for included in requirement.extras:
                roots.update(_roots(included))
        else:
            # Uninstalled optional distributions still need a blocked import root.
            fallback = IMPORT_NAMES.get(name, name.replace("-", "_")).split(".")[0]
            roots.update(imports.get(name, {fallback}))
    return sorted(roots - _base_import_roots())


def _child(source, *arguments):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["G6_DISABLE_LLM"] = "1"
    for key in ("G6_CLAUDE", "G6_CODEX"):
        env[key] = r"C:\nonexistent\g6-no-host.exe"
    for key in ("ABSTRACTION_MCP_STATE", "ABSTRACTION_MCP_DIR"):
        env.pop(key, None)
    done = subprocess.run([sys.executable, "-B", "-c", source, *arguments],
                          cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr


def test_rq_blocked_roots_preserve_redis_required_by_mcp():
    for parent, dependency in (("fastmcp", "pydocket"), ("pydocket", "redis")):
        try:
            requirements = metadata.requires(parent) or []
        except metadata.PackageNotFoundError:
            pytest.skip(f"{parent} is not installed; fastmcp -> pydocket -> redis chain absent")
        if not any(
            canonicalize_name(requirement.name) == dependency
            and (requirement.marker is None or requirement.marker.evaluate({"extra": ""}))
            for requirement in map(Requirement, requirements)
        ):
            pytest.skip(f"installed {parent} does not require {dependency}; MCP Redis chain absent")

    blocked = _roots("rq")
    assert "redis" not in blocked, blocked
    assert "rq" in blocked, blocked


@pytest.mark.parametrize("extra", sorted(EXTRAS))
def test_missing_extra_keeps_independent_families_available(extra):
    # Before the repair, numpy/hypothesis blocking makes describe('review')
    # raise RegistrationError while trying to build the unrelated server.
    _child('''
import importlib.abc, json, sys
blocked = set(json.loads(sys.argv[1]))
for name in blocked:
    sys.modules[name] = None
attempted = set()
class Observe(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        attempted.add(fullname)
        return None
sys.meta_path.insert(0, Observe())
from g6.mcp.families import FAMILIES, describe, family_table
assert all(name + ':' in family_table() for name in FAMILIES)
for name in FAMILIES:
    attempted.clear()
    reply = describe(name)
    assert reply['available'] is True, (name, reply)
    assert reply['members'], name
    for row in reply['members']:
        if row['id'].startswith('mcp:'):
            unavailable = (bool(blocked & {'mcp', 'fastmcp'}) or
                           row['id'].startswith('mcp:abstraction/') and
                           bool(blocked & {'numpy', 'hypothesis'}))
            assert row['available'] is (not unavailable), (name, row)
            if unavailable:
                assert row['reason'] == 'server_unavailable', row
                assert 'pip install' in row['install'] and 'g6[' in row['install'], row
    if name != 'logic':
        assert not any(module.startswith('g6.abstraction') for module in attempted), name
''', json.dumps(_roots(extra)))


@pytest.mark.parametrize("missing", ["numpy", "hypothesis"])
def test_missing_abstraction_member_has_stable_recovery_and_keeps_rules(missing):
    _child('''
import json, sys
sys.modules[sys.argv[1]] = None
from g6.mcp.families import describe
reply = describe('logic')
assert reply['available'] is True, reply
members = {row['id']: row for row in reply['members']}
assert 'route:rules' in members, members
assert describe('g6_invoke')['available'] is True
unavailable = [row for key, row in members.items() if key.startswith('mcp:abstraction/')]
assert len(unavailable) == 18, unavailable
for row in unavailable:
    assert row['available'] is False, row
    assert row['reason'] == 'server_unavailable', row
    assert 'g6[abstraction,mcp]' in row['install'], row
    assert 'sys.modules' not in json.dumps(row), row
direct = describe('load_state')
assert direct['available'] is False, direct
assert direct['reason'] == 'server_unavailable', direct
assert direct['install'] == unavailable[0]['install'], direct
''', missing)


def test_main_server_build_and_describe_do_not_need_rq():
    # Build directly before family recovery can hide the import traceback.
    _child('''
import sys
sys.modules['rq'] = None
from g6.mcp import build_server, registrations
server = build_server()
tools = dict(registrations._listed(server))
assert 'g6_agentic_run' in tools and 'g6_describe' in tools
from g6.mcp.families import describe
for name in tools:
    reply = describe(name)
    assert reply['available'] is True, (name, reply)
members = {row['id']: row for row in describe('discover')['members']}
assert members['mcp:g6/g6_agentic_run']['available'] is True, members
# Only tool rows carry availability; routes, skills and ops never claim it.
assert all(row['available'] is True for row in members.values() if row['kind'] == 'tool'), members
assert not [row for row in members.values() if row.get('available') is False], members
''')


@pytest.mark.parametrize("module", ["rq", "redis.asyncio", "other_dependency"])
def test_build_import_error_names_the_actual_dependency(monkeypatch, module):
    from g6 import mcp

    failure = ModuleNotFoundError(f"No module named {module!r}", name=module)

    def missing():
        raise failure

    monkeypatch.setattr(mcp, "_quiet", missing)
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert module in str(stopped.value)
    assert "MCP extra" not in str(stopped.value)
    assert stopped.value.__cause__ is failure


@pytest.mark.parametrize("module", ["mcp", "fastmcp", "mcp.server", "rq", "redis", "other_dependency", None])
def test_entry_point_distinguishes_mcp_from_other_import_errors(monkeypatch, module):
    from g6 import mcp

    failure = ImportError("dependency load failed", name=module)

    def missing():
        raise failure

    monkeypatch.setattr(mcp, "build_server", missing)
    with pytest.raises(SystemExit) as stopped:
        mcp.main()
    message = str(stopped.value)
    if module and module.split(".")[0] in {"mcp", "fastmcp"}:
        assert "MCP extra" in message and "g6[mcp]" in message
    else:
        assert "MCP extra" not in message and "g6[mcp]" not in message
        assert (module or "server dependency") in message
    assert stopped.value.__cause__ is failure
