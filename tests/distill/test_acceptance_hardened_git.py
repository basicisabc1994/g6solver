"""Real read-only git against hand-built objects and planted program settings.

No git mutation commands, hooks, network, models or host config are needed.
"""
import hashlib
import zlib


def test_chronology_ignores_planted_fsmonitor_hooks_and_host_config(tmp_path, monkeypatch):
    from g6.distill import acceptance_gate as gate
    repo = tmp_path / 'inspected'
    git = repo / '.git'
    (git / 'objects').mkdir(parents=True)
    (git / 'refs/heads').mkdir(parents=True)
    hooks = git / 'hooks'
    hooks.mkdir()
    marker = repo / 'PLANTED_RAN'
    monitor = hooks / 'fsmonitor-watchman'
    monitor.write_text('#!/bin/sh\nprintf planted > "' + marker.as_posix() + '"\n', encoding='utf-8', newline='\n')
    (hooks / 'post-checkout').write_text(monitor.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    (git / 'config').write_text('[core]\nrepositoryformatversion = 0\nbare = false\n'
        'fsmonitor = ' + monitor.as_posix() + '\nhooksPath = ' + hooks.as_posix() + '\n', encoding='utf-8', newline='\n')
    def object_(kind, body):
        raw = kind.encode() + b' ' + str(len(body)).encode() + b'\0' + body
        ident = hashlib.sha1(raw).hexdigest()
        path = git / 'objects' / ident[:2] / ident[2:]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(zlib.compress(raw))
        return ident
    blob = object_('blob', b'{"fixture":true}\n')
    tree = object_('tree', b'100644 record.json\0' + bytes.fromhex(blob))
    commit = object_('commit', ('tree ' + tree + '\nauthor Fixture <fixture@example.test> 0 +0000\n'
        'committer Fixture <fixture@example.test> 0 +0000\n\nfixture\n').encode())
    (git / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8', newline='\n')
    (git / 'refs/heads/main').write_text(commit + '\n', encoding='utf-8', newline='\n')
    host_config = tmp_path / 'planted-global.config'
    host_config.write_text('[g6]\nexternal = planted\n', encoding='utf-8', newline='\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(host_config))
    monkeypatch.setenv('GIT_DIR', 'untrusted-redirection')
    monkeypatch.setattr(gate, 'ROOT', repo)
    assert gate._git('config', '--get', 'core.fsmonitor').strip() == b'false'
    assert gate._git('config', '--get', 'core.hooksPath').strip().decode().replace('\\', '/') != hooks.as_posix()
    assert b'g6.external' not in gate._git('config', '--list')
    assert gate._git('show', f'{commit}:record.json') == b'{"fixture":true}\n'
    assert gate._git('merge-base', '--is-ancestor', commit, commit) == b''
    gate._git('status', '--porcelain')
    assert not marker.exists()
