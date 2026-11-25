"""Shared by review 4's inventory tests: what a source really does when Python runs it, and the inventory of an
earlier commit (read from git, a read only).

``runs(source)`` executes the source as a module whose ``p`` is a real file. The gate's own operations are swapped for
recorders that touch nothing, so a file that is gone (or moved, or emptied) afterwards was written by a raw operation,
and ``asked`` lists every time the gate was asked instead.
"""
import subprocess
import types
import uuid
from pathlib import Path

import pytest

from g6.harness import _completion_storage as gate

TREE = Path(__file__).resolve().parents[2]
GATE = 'from g6.harness import _completion_storage as store\n'
GATE_OPERATIONS = ('unlink', 'replace', 'write_text', 'write_bytes', 'task_present', 'path')


def runs(source, folder, monkeypatch, *, flag=False):
    scratch = folder / uuid.uuid4().hex
    scratch.mkdir()
    victim, moved = scratch / 'victim.txt', scratch / 'moved.txt'
    victim.write_text('kept')
    asked = []
    with monkeypatch.context() as recording:
        for name in GATE_OPERATIONS:
            recording.setattr(gate, name, lambda *args, _name=name, **kwargs: asked.append(_name))
        module = types.ModuleType('inventoried_source')
        module.__dict__.update(p=victim, q=moved, flag=flag)
        exec(compile(source, 'inventoried_source', 'exec'), module.__dict__)
    raw = not victim.exists() or victim.read_text() != 'kept'
    return types.SimpleNamespace(raw_write=raw, asked=asked)


def inventory_at(revision):
    shown = subprocess.run(['git', 'show', f'{revision}:tests/harness/test_completion_writer_allowlist.py'], cwd=TREE,
                           capture_output=True, check=False)
    if shown.returncode:
        pytest.skip(f'git cannot show {revision} here')
    module = types.ModuleType(f'allowlist_at_{revision}')
    exec(compile(shown.stdout.decode('utf-8'), f'{revision}:test_completion_writer_allowlist.py', 'exec'), module.__dict__)
    return module.unguarded_calls
