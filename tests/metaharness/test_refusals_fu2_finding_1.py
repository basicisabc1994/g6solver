"""A required host input must resolve inside its admitted call folder."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness.domain import sender_problems
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


def test_a_required_host_input_cannot_escape_through_a_directory_link(monkeypatch, consented_provider_fakes):
    root = Path(__file__).resolve().parents[2] / '.review' / 'finding-1' / uuid4().hex
    work, files, outside = root / 'call' / 'work', root / 'call' / 'files', root / 'outside'
    for folder in (work, files, outside):
        folder.mkdir(parents=True)
    (outside / 'system_prompt.md').write_text('outside system', encoding='utf-8')
    alias = files / 'alias'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)

    class LinkedClaude(host_models.ClaudeCodeCaller):
        def _command(self, *args):
            argv, stdin = super()._command(*args)
            argv[argv.index('--system-prompt-file') + 1] = str(alias / 'system_prompt.md')
            return argv, stdin

    @contextmanager
    def folders():
        yield work, files

    launches = []

    def run(argv, stdin, cwd, call_files, *args):
        path = Path(argv[argv.index('--system-prompt-file') + 1])
        assert path.read_bytes() == b'outside system'
        assert path.resolve().is_relative_to(outside.resolve())
        launches.append(path)
        return 0, json.dumps({'type': 'result', 'result': 'answer'}), ''

    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(host_models.key_file, 'read', lambda: {})
    monkeypatch.setattr(LinkedClaude, 'locate', lambda self: 'fake-claude')
    monkeypatch.setattr(host_models, '_run', run)
    meter = _Requests()
    meter.open(1)
    caller = LinkedClaude()
    assert sender_problems(caller, 'claude') == []
    try:
        try:
            _Counted(caller, meter).complete('owned system', 'user')
        except LLMError as error:
            assert 'G6_E_LOOP_INPUT_CHANGED' in str(error)
        assert not launches, 'an outside required input reached launch through an admitted directory link'
    finally:
        alias.rmdir() if os.name == 'nt' else alias.unlink()
        assert not meter.in_flight
        assert not usage._SENT

