"""Shared by review 1's storage tests: other programs reading G6's retained files, with real open handles.

Python's own ``open`` shares reading and writing and no delete, as a backup, an indexer, a scanner or an editor may:
while such a handle is open the file can be neither replaced nor removed nor moved (WinError 5 or 32).
"""
import importlib.util
import subprocess
import threading
import time
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parents[2]


class Reader:
    """A reader of an existing pair (review 4's): the receipt now, the content once the next save has replaced it."""

    def __init__(self, content, *, also_content):
        self.content, self.receipt, self.also_content = content, content.with_suffix('.sha256'), also_content
        self.handles, self.gone, self.thread, self.replaced = [], threading.Event(), None, False

    def arrive(self):
        import os

        before = os.stat(self.content).st_ino
        self.handles.append(open(self.receipt, 'rb'))

        def watch():
            while not self.gone.is_set():
                try:
                    if os.stat(self.content).st_ino != before:
                        self.replaced = True
                        if self.also_content:
                            self.handles.append(open(self.content, 'rb'))
                        return
                except OSError:              # the publisher is replacing it this instant
                    pass
                time.sleep(0.0005)

        self.thread = threading.Thread(target=watch, name='reader')
        self.thread.start()

    def leave(self):
        self.gone.set()
        if self.thread is not None:
            self.thread.join(30)
        for handle in self.handles:
            handle.close()


class NewFileReader:
    """A reader of new files (a scanner): it opens a first publication's receipt spare (``<stem>.sha256.new``) as soon
    as it exists and, with ``also_content``, the content (``<stem>.json``) once that is there."""

    def __init__(self, folder, stem, *, also_content):
        self.wanted = [('spare', folder / f'{stem}.sha256.new'),
                       *([('content', folder / f'{stem}.json')] if also_content else [])]
        self.handles, self.gone = {}, threading.Event()
        self.thread = threading.Thread(target=self.watch, name='new-file-reader')
        self.thread.start()

    def watch(self):
        while not self.gone.is_set() and len(self.handles) < len(self.wanted):
            for name, path in self.wanted:
                if name not in self.handles and (name == 'spare' or 'spare' in self.handles):
                    try:
                        self.handles[name] = open(path, 'rb')
                    except OSError:
                        pass

    @property
    def landed(self):
        return 'spare' in self.handles

    def leave(self):
        self.gone.set()
        self.thread.join(30)
        for handle in self.handles.values():
            handle.close()


def storage_at(revision):
    """The storage module of an earlier commit, read from git (a read only)."""
    shown = subprocess.run(['git', 'show', f'{revision}:src/g6/harness/_completion_storage.py'], cwd=TREE,
                           capture_output=True, check=False)
    if shown.returncode:
        pytest.skip(f'git cannot show {revision} here')
    spec = importlib.util.spec_from_loader(f'g6.harness._completion_storage_{revision}', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(shown.stdout.decode('utf-8'), f'{revision}:_completion_storage.py', 'exec'), module.__dict__)
    return module
