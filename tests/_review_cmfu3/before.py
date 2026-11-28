"""Run one ``helpers.scenario`` on an earlier commit's map modules.

``git show <commit>:<path>`` (read only) supplies ``g6.harness.change_map``,
``g6.workforce.repo_tests`` and ``g6.harness.patch_review``; everything else is
the review tree's. Nothing is written but the scenario's own repository.
"""
import importlib.abc
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TREE = Path(__file__).resolve().parents[2]
COMMIT = sys.argv[1]
FILES = {"g6.harness.change_map": "src/g6/harness/change_map.py",
         "g6.workforce.repo_tests": "src/g6/workforce/repo_tests.py",
         "g6.harness.patch_review": "src/g6/harness/patch_review.py"}


class _At(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name in FILES:
            return importlib.util.spec_from_loader(name, self, origin=str(TREE / FILES[name]))
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        rel = FILES[module.__name__]
        source = subprocess.run(["git", "-C", str(TREE), "show", f"{COMMIT}:{rel}"],
                                check=True, capture_output=True).stdout
        module.__file__ = str(TREE / rel)
        exec(compile(source, f"{COMMIT}:{rel}", "exec"), module.__dict__)


sys.meta_path.insert(0, _At())

from tests._review_cmfu3 import helpers  # noqa: E402

print(json.dumps(helpers.scenario(json.loads(sys.argv[2]))))
