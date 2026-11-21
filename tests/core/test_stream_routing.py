# SPDX-License-Identifier: Apache-2.0
"""One binding owner prevents nested captures and worker cleanup stealing streams."""
import ast
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def test_only_routing_module_binds_process_streams():
    root = Path(__file__).resolve().parents[2] / 'src/g6'
    found = []
    for path in root.rglob('*.py'):
        if path == root / '_streams.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        modules = {'sys': 'sys', 'contextlib': 'contextlib'}
        redirects = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update({a.asname or a.name: a.name for a in node.names})
            elif isinstance(node, ast.ImportFrom) and node.module == 'contextlib':
                redirects.update(a.asname or a.name for a in node.names
                                 if a.name in ('redirect_stdout', 'redirect_stderr'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) \
                    and isinstance(node.value, ast.Name) and modules.get(node.value.id) == 'sys' \
                    and node.attr in ('stdout', 'stderr', '__stdout__', '__stderr__'):
                found.append((path.relative_to(root).as_posix(), node.lineno))
            if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id in redirects
                    or isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and modules.get(node.func.value.id) == 'contextlib'
                    and node.func.attr in ('redirect_stdout', 'redirect_stderr')):
                found.append((path.relative_to(root).as_posix(), node.lineno))
    assert not found, found


def test_nested_mcp_and_main_use_the_same_routing_pair(monkeypatch, capsys, registry_reset):
    from g6.cli import main
    from g6.core.registry import get_registry
    from g6.core.result import Result
    from g6.mcp import invoke_reply

    observed = []

    def infer(self, data):
        pair = sys.stdout, sys.stderr
        observed.append(pair)
        if len(observed) == 1:
            assert invoke_reply('grounding', 'check_claim')['ok']
            assert main(['invoke', 'algebra', 'ops', '{}', '--json']) == 0
            assert (sys.stdout, sys.stderr) == pair
        print('OP_SECRET')
        return Result.ok({'answer': 42})

    before = sys.stdout, sys.stderr
    monkeypatch.setattr(get_registry().get_class('grounding'), 'infer', infer)
    assert main(['invoke', 'grounding', 'check_claim', '{}', '--json']) == 0
    assert len(observed) == 2
    assert observed[0] == observed[1]
    assert (sys.stdout, sys.stderr) == before
    assert 'OP_SECRET' not in capsys.readouterr().out


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_all_prebound_logging_streams_are_routed_and_restored(monkeypatch, capsys, registry_reset, stream):
    from g6.cli import main
    from g6.core.registry import get_registry
    from g6.core.result import Result

    original = getattr(sys, stream)
    named = logging.getLogger('g6.test.prebound.isolated')
    handler = logging.StreamHandler(original)
    fallback = logging.StreamHandler(original)
    monkeypatch.setattr(named, 'handlers', [handler])
    monkeypatch.setattr(named, 'propagate', False)
    monkeypatch.setattr(named, 'level', logging.WARNING)
    monkeypatch.setattr(logging, 'lastResort', fallback)
    observed = []

    def infer(self, data):
        observed.append(handler.stream is getattr(sys, stream))
        observed.append(fallback.stream is getattr(sys, stream))
        named.warning('OP_SECRET')
        return Result.ok({'answer': 42})

    monkeypatch.setattr(get_registry().get_class('grounding'), 'infer', infer)
    assert main(['invoke', 'grounding', 'check_claim', '{}', '--json']) == 0
    assert observed == [True, True]
    assert handler.stream is original and fallback.stream is original
    assert logging.lastResort is fallback
    captured = capsys.readouterr()
    assert 'OP_SECRET' not in captured.out + captured.err
    assert json.loads(captured.out)['ok']


@pytest.mark.parametrize('closure', ['descriptor', 'stream'])
def test_closed_private_protocol_writer_exits_quietly(tmp_path, closure):
    script = textwrap.dedent('''
        import contextlib, os, sys
        from g6.mcp import _stdio
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCMessage, JSONRPCNotification
        class Protocol:
            def create_initialization_options(self, **kwargs):
                return None
            async def run(self, read, write, options):
                message = JSONRPCMessage(JSONRPCNotification(jsonrpc='2.0', method='g6-test'))
                await write.send(SessionMessage(message))
                await write.aclose()
        class Server:
            _mcp_server = Protocol()
            @contextlib.asynccontextmanager
            async def _lifespan_manager(self):
                yield
        original = _stdio._serve_stdio
        async def closed(server, protocol):
            if sys.argv[1] == 'descriptor':
                os.close(protocol.fileno())
            else:
                protocol.close()
            await original(server, protocol)
        _stdio._serve_stdio = closed
        _stdio.run_stdio(Server)
    ''')
    env = {**os.environ, 'G6_HOME': str(tmp_path / 'home'),
           'FASTMCP_HOME': str(tmp_path / 'fastmcp'), 'FASTMCP_ENV_FILE': os.devnull,
           'FASTMCP_CHECK_FOR_UPDATES': 'off',
           'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe', 'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, closure], input='',
                            capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr.splitlines() == ['[G6_E_STDIO_CLOSED] G6: protocol pipe closed.']


def test_real_concurrent_tools_after_sequential_input_grants(
        monkeypatch, capsys, registry_reset, declared_mcp_grants, tmp_path):
    """Grant before concurrency; concurrent dialogs for one revision are stale."""
    import asyncio
    import threading
    from fastmcp import Client
    from g6.core.registry import get_registry
    from g6.core.result import Result
    from g6.mcp import build_server

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('FASTMCP_HOME', str(tmp_path / 'fastmcp'))
    monkeypatch.setenv('FASTMCP_ENV_FILE', os.devnull)
    monkeypatch.setenv('FASTMCP_CHECK_FOR_UPDATES', 'off')
    concurrent = False
    entered = threading.Barrier(4)
    completed = []

    def infer(self, data):
        if concurrent:
            entered.wait(timeout=10)
        worker = threading.Thread(target=lambda: print('OP_SECRET worker'))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        print('OP_SECRET caller')
        completed.append(True)
        return Result.ok({'answer': 42})

    monkeypatch.setattr(get_registry().get_class('grounding'), 'infer', infer)

    async def calls():
        nonlocal concurrent
        async with Client(build_server()) as client:
            arguments = {'component': 'grounding', 'op': 'check_claim'}
            # Exercise the fixture's real accepting dialogs sequentially first.
            first = await client.call_tool('g6_invoke', arguments)
            assert first.data['ok']
            completed.clear()
            concurrent = True
            replies = await asyncio.wait_for(asyncio.gather(*[
                client.call_tool('g6_invoke', arguments) for _ in range(4)]), timeout=30)
            assert all(reply.data['ok'] for reply in replies)

    asyncio.run(calls())
    assert completed == [True] * 4
    assert 'OP_SECRET' not in capsys.readouterr().out


def test_server_masks_workers_and_exit_callbacks_after_serving_returns(tmp_path):
    script = textwrap.dedent('''
        import atexit, contextlib, threading
        from g6.mcp import _stdio
        release = threading.Event()
        workers = []
        class Protocol:
            def create_initialization_options(self, **kwargs):
                return None
            async def run(self, read, write, options):
                def late():
                    assert release.wait(5)
                    print('OP_SECRET late worker')
                worker = threading.Thread(target=late)
                workers.append(worker)
                worker.start()
                atexit.register(lambda: print('OP_SECRET exit callback'))
                await write.aclose()
        class Server:
            _mcp_server = Protocol()
            @contextlib.asynccontextmanager
            async def _lifespan_manager(self):
                yield
        _stdio.run_stdio(Server)
        release.set()
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
    ''')
    env = {**os.environ, 'G6_HOME': str(tmp_path / 'home'),
           'FASTMCP_HOME': str(tmp_path / 'fastmcp'), 'FASTMCP_ENV_FILE': os.devnull,
           'FASTMCP_CHECK_FOR_UPDATES': 'off',
           'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe', 'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], input='',
                            capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert 'OP_SECRET' not in result.stderr
