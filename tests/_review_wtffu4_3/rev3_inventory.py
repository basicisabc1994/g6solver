"""Shared by review 3's inventory tests: the inventory of an earlier commit, read from git (a read only)."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parents[2]
BASE = '673466cb'       # the parent of the change under review


def inventory_at(revision=BASE):
    shown = subprocess.run(['git', 'show', f'{revision}:tests/harness/test_completion_writer_allowlist.py'], cwd=TREE,
                           capture_output=True, check=False)
    if shown.returncode:
        pytest.skip(f'git cannot show {revision} here')
    module = types.ModuleType(f'rev3_inventory_{revision}')
    exec(compile(shown.stdout.decode('utf-8'), f'{revision}:inventory.py', 'exec'), module.__dict__)
    return module.unguarded_calls


def moves_a_file(body, folder, name='rev3_body'):
    """Whether Python, running ``body`` as a module named ``name`` with two real files ``p`` and ``q``, moved ``p`` onto
    ``q``: then its two-argument ``replace`` was ``os.replace`` and no ``str.replace``."""
    p, q = folder / 'p', folder / 'q'
    p.write_text('x', encoding='utf-8')
    module = types.ModuleType(name)
    module.__dict__.update(p=p, q=q)
    sys.modules[name] = module
    try:
        exec(compile(body, name, 'exec'), module.__dict__)
    finally:
        del sys.modules[name]
    return q.is_file() and not p.exists()
