"""Adversarial review reproductions; repository fixtures are written to real disk."""
import asyncio
import concurrent.futures
import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_templates as vt
from g6.workforce.repo_tests import RepoRefused


@pytest.fixture
def tmp_path(request):
    # Windows pytest's mode-0700 temp root is unreadable to this sandbox token.
    # A normal directory keeps the reproduction on disk within the allowed tree.
    folder = Path(__file__).parent / "disk" / hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


SOURCE = '''
import asyncio
import concurrent.futures

class LookupFailure(Exception):
    def __str__(self):
        raise TypeError("display failed")

class Gate:
    def __init__(self):
        self.lookups = 0
    def __getattribute__(self, name):
        if name == "allow":
            self.lookups += 1
            if self.lookups == 2:
                raise SIGNAL
        return object.__getattribute__(self, name)
    def allow(self, who):
        return True
'''


def compile_access(tmp_path, monkeypatch, signal, error, prologue=""):
    path = tmp_path / "adversary.py"
    path.write_text(prologue + SOURCE.replace("SIGNAL", signal), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "adversary", raising=False)
    tree = vt.Tree(tmp_path, None, frozenset({path.name}), frozenset())
    definition = vt.REGISTRY["access_rule.v1"]
    bindings = definition.bindings.model_validate({
        "operation": "adversary.Gate.allow", "constructor": {},
        "refusal": "exception", "exception": error,
        "cases": [
            {"who": "owner", "resource": "record", "args": ["owner"], "expected": "allowed"},
            {"who": "guest", "resource": "record", "args": ["guest"], "expected": "refused"},
        ],
    })
    applied = definition.applies(tree, bindings, vt.PATCH)
    namespace = {}
    exec(vt.compile_text(definition, applied.data), namespace)
    return namespace


def test_unprintable_lookup_failure_cannot_pass_access_rule(tmp_path, monkeypatch):
    namespace = compile_access(tmp_path, monkeypatch, "LookupFailure()", "TypeError")
    # Lookup failed before the guest's operation ran. Displaying that failure must
    # not replace it with a TypeError that counts as a successful access refusal.
    with pytest.raises(namespace["CheckSetupError"]):
        namespace["test_access_rule"]()


@pytest.mark.parametrize("signal,control", [
    ("SystemExit(7)", SystemExit),
    ("GeneratorExit()", GeneratorExit),
    ("asyncio.CancelledError()", asyncio.CancelledError),
    ("concurrent.futures.CancelledError()", concurrent.futures.CancelledError),
], ids=["exit", "generator", "async-cancel", "future-cancel"])
def test_lookup_control_signal_cannot_pass_access_rule(tmp_path, monkeypatch, signal, control):
    namespace = compile_access(tmp_path, monkeypatch, signal, "BaseException")
    with pytest.raises(control):
        namespace["test_access_rule"]()


def test_lookup_interrupt_cannot_be_completed_example_observation(tmp_path):
    path = tmp_path / "adversary.py"
    path.write_text(SOURCE.replace("SIGNAL", "KeyboardInterrupt()"), encoding="utf-8")
    definition = vt.REGISTRY["example.v1"]
    tree = vt.Tree(tmp_path, None, frozenset({path.name}), frozenset())
    bindings = definition.bindings.model_validate({
        "function": "adversary.Gate.allow", "constructor": {}, "cases": [
            {"args": ["owner"], "returns": True},
            {"args": ["guest"], "raises": "KeyboardInterrupt"},
        ],
    })
    data = runner.located(tmp_path, definition.applies(tree, bindings, vt.PATCH).data)
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    ran = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
         json.dumps(runner.request(data))], capture_output=True, timeout=10,
    )
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found["result"] == "error", (found, ran.stderr)


def test_existing_typed_refusal_keeps_its_code(tmp_path, monkeypatch):
    refusal = RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "setup refused")
    namespace = compile_access(
        tmp_path, monkeypatch,
        'RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "setup refused")', "TypeError",
        prologue="from g6.workforce.repo_tests import RepoRefused\n",
    )
    with pytest.raises(namespace["CheckSetupError"]) as caught:
        namespace["test_access_rule"]()
    assert refusal.code in str(caught.value)
