# SPDX-License-Identifier: Apache-2.0
"""CI shares the suite among jobs by file (the tests/g6_shard.py plugin, tools/suite_split.ps1 -Shard): every collected
test must run in exactly one job, or a failure could pass CI unseen."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.g6_shard import pytest_collection_modifyitems

ROOT = Path(__file__).resolve().parents[2]


def _items():
    files = sorted(p for p in (ROOT / "tests").rglob("test_*.py") if "__pycache__" not in p.parts)
    return [SimpleNamespace(path=f, name=f"{f.name}::case{i}") for f in files for i in range(2)]


def _run(shard, items):
    deselected = []
    config = SimpleNamespace(getoption=lambda name: shard, rootpath=ROOT,
                             hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    kept = list(items)
    pytest_collection_modifyitems(config, kept)
    return kept, deselected


def test_the_shards_divide_the_suite_with_every_test_in_exactly_one():
    items = _items()
    seen = []
    for k in range(1, 5):
        kept, deselected = _run(f"{k}/4", items)
        # fails if a shard both keeps and drops a test, or loses one between the two lists
        assert len(kept) + len(deselected) == len(items)
        seen += kept
    # fails if a test runs in no shard or in two
    assert sorted(map(id, seen)) == sorted(map(id, items))


def test_a_files_tests_stay_in_one_shard_and_no_shard_is_empty():
    items = _items()
    shard_of_file = {}
    for k in range(1, 5):
        kept, _ = _run(f"{k}/4", items)
        assert kept, k
        for item in kept:
            assert shard_of_file.setdefault(item.path, k) == k, item.path


def test_without_the_option_nothing_is_dropped():
    items = _items()
    kept, deselected = _run(None, items)
    assert kept == items and deselected == []


@pytest.mark.parametrize("shard", ["0/4", "5/4", "1", "a/b"])
def test_a_malformed_shard_is_refused(shard):
    with pytest.raises(pytest.UsageError):
        _run(shard, _items())


def test_ci_passes_each_of_its_shards_and_the_count_it_divides_by():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    shards = [int(n) for n in re.search(r"(?m)^\s+shard: \[([\d, ]+)\]", workflow).group(1).split(",")]
    count = int(re.search(r'-Shard "\$\{\{ matrix\.shard \}\}/(\d+)"', workflow).group(1))
    # fails if the matrix and the divisor disagree, so some shard would never run
    assert shards == list(range(1, count + 1))
    script = (ROOT / "tools" / "suite_split.ps1").read_text(encoding="utf-8")
    assert '"-p", "tests.g6_shard", "--g6-shard", $Shard' in script


def test_pytest_loads_the_plugin_as_the_script_does_and_the_shards_add_up():
    def collected(*extra):
        done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                               *extra, "tests/tools"], cwd=ROOT, capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
        return {line for line in done.stdout.splitlines() if "::" in line}
    whole = collected()
    parts = [collected("-p", "tests.g6_shard", "--g6-shard", f"{k}/4") for k in range(1, 5)]
    # fails if the plugin does not load the way the script names it, or a real collection loses or repeats a test
    assert sum(map(len, parts)) == len(whole) and set().union(*parts) == whole
