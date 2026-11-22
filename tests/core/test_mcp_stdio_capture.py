# SPDX-License-Identifier: Apache-2.0
"""Op prints cannot corrupt the stdio protocol or capture another caller's output."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import textwrap
import threading

import pytest

from g6.mcp import invoke_reply
from g6.mcp.composition import compose_reply


@pytest.mark.parametrize("route", ["invoke", "compose"])
@pytest.mark.parametrize("fails", [False, True])
def test_mcp_op_stdout_is_captured(monkeypatch, capsys, registry_reset, route, fails):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    def infer(self, data):
        print("OP_SECRET")
        if fails:
            raise ValueError("operation failed")
        return Result.ok({"check": {"status": "quoted"}, "completion_state": "qualified-draft"})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    if route == "invoke":
        reply = invoke_reply("grounding", "check_claim")
        if fails:
            assert reply["completion_state"] == "blocked-escalated"
            assert reply["error"] == "operation failed"
            assert reply["detail"] == "operation failed"
            assert reply["warning_card"]["code"] == "G6_E_INVOKE_FAILED"
    else:
        reply = compose_reply({"stages": [{"component": "grounding", "op": "check_claim"}],
                               "inputs": {"claim": "x", "sources": {"manual": "x"}}})
    assert reply["ok"] is (not fails)
    assert capsys.readouterr().out == ""
    print("AFTER_CALL")
    assert capsys.readouterr().out == "AFTER_CALL\n"


def test_concurrent_mcp_calls_mask_unattributed_stdout(monkeypatch, capsys, registry_reset):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    entered = threading.Barrier(3)
    release = threading.Event()

    def infer(self, data):
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        print("OP_SECRET")
        return Result.ok({"answer": 42})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke_reply, "grounding", "check_claim") for _ in range(2)]
        try:
            entered.wait(timeout=5)
            print("UNATTRIBUTED_SECRET" * 10000)
        finally:
            release.set()
        assert all(future.result(timeout=10)["ok"] for future in futures)
    print("AFTER_CALL")
    captured = capsys.readouterr()
    assert captured.out == "AFTER_CALL\n"
    assert "SECRET" not in captured.err
    assert "G6:" in captured.err and len(captured.err) < 256


@pytest.mark.parametrize("route", ["invoke", "compose"])
@pytest.mark.parametrize("worker", ["thread", "pool"])
def test_workers_started_inside_a_call_cannot_write_to_protocol(
        monkeypatch, capsys, registry_reset, route, worker):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    completed = []

    def work():
        print("WORKER_SECRET")
        completed.append(True)

    def infer(self, data):
        if worker == "thread":
            thread = threading.Thread(target=work)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(work).result(timeout=5)
        # A compose stage must satisfy grounding.check_claim's output contract.
        return Result.ok({"check": {"status": "quoted"}, "completion_state": "qualified-draft"})

    start = threading.Thread.start
    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    if route == "invoke":
        assert invoke_reply("grounding", "check_claim")["ok"]
    else:
        assert compose_reply({"stages": [{"component": "grounding", "op": "check_claim"}],
                              "inputs": {"claim": "x", "sources": {"manual": "x"}}})["ok"]
    assert completed == [True]
    assert capsys.readouterr().out == ""
    assert threading.Thread.start is start


@pytest.mark.parametrize("route", ["invoke", "compose"])
def test_detached_worker_keeps_its_call_buffer(monkeypatch, capsys, registry_reset, route):
    from g6.core.registry import get_registry
    from g6.core.result import Result

    release, done = threading.Event(), threading.Event()
    workers = []

    def infer(self, data):
        def work():
            try:
                assert release.wait(5)
                print("DETACHED_SECRET")
            finally:
                done.set()
        thread = threading.Thread(target=work)
        workers.append(thread)
        thread.start()
        return Result.ok({"check": {"status": "quoted"}, "completion_state": "qualified-draft"})

    monkeypatch.setattr(get_registry().get_class("grounding"), "infer", infer)
    try:
        if route == "invoke":
            assert invoke_reply("grounding", "check_claim")["ok"]
        else:
            assert compose_reply({"stages": [{"component": "grounding", "op": "check_claim"}],
                                  "inputs": {"claim": "x", "sources": {"manual": "x"}}})["ok"]
    finally:
        release.set()
        for thread in workers:
            thread.join(timeout=5)
    assert done.is_set()
    captured = capsys.readouterr()
    assert "DETACHED_SECRET" not in captured.out + captured.err


@pytest.mark.parametrize("update_setting", ["off", "stable", "prerelease"])
def test_stdio_server_uses_a_private_protocol_descriptor(tmp_path, update_setting):
    # Exercise the actual server entry path with a fake server implementation. The
    # transport and its writer are real; no external client or network is needed.
    script = textwrap.dedent('''
        import contextlib, os, subprocess, sys
        import g6.mcp
        import fastmcp
        from fastmcp.utilities import version_check
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCMessage, JSONRPCNotification
        update_setting = os.environ['FASTMCP_CHECK_FOR_UPDATES']
        assert fastmcp.settings.check_for_updates == update_setting
        update_checks = []
        def fetch(include_prereleases=False):
            update_checks.append(include_prereleases)
            return None
        version_check._fetch_latest_version = fetch
        class Protocol:
            def create_initialization_options(self, **kwargs):
                return None
            async def run(self, read, write, options):
                os.write(1, b'FD_SECRET\\n')
                sys.__stdout__.write('DUNDER_SECRET\\n')
                sys.__stdout__.flush()
                sys.__stdout__.write('BUFFERED_DUNDER_SECRET\\n')
                subprocess.run([sys.executable, '-c', "print('CHILD_SECRET')"],
                               check=True, timeout=10)
                print('PYTHON_SECRET')
                message = JSONRPCMessage(JSONRPCNotification(jsonrpc='2.0', method='g6-test'))
                await write.send(SessionMessage(message))
                await write.aclose()
        class Server:
            _mcp_server = Protocol()
            @contextlib.asynccontextmanager
            async def _lifespan_manager(self):
                yield
        g6.mcp.build_server = Server
        g6.mcp.main()
        assert fastmcp.settings.check_for_updates == update_setting
        assert update_checks == ([] if update_setting == 'off' else [update_setting == 'prerelease'])
    ''')
    env = {**os.environ, 'G6_HOME': str(tmp_path / 'home'),
           'FASTMCP_HOME': str(tmp_path / 'fastmcp'), 'FASTMCP_ENV_FILE': os.devnull,
           'FASTMCP_CHECK_FOR_UPDATES': update_setting,
           'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe', 'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
                            input='', capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    import json
    assert json.loads(result.stdout) == {'jsonrpc': '2.0', 'method': 'g6-test'}
    assert all(marker in result.stderr for marker in (
        'FD_SECRET', 'DUNDER_SECRET', 'BUFFERED_DUNDER_SECRET', 'CHILD_SECRET'))
    assert 'PYTHON_SECRET' not in result.stderr

