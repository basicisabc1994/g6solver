# SPDX-License-Identifier: Apache-2.0
"""``bind(db_dir=)`` binds that root for the workforce it returns and changes nothing for the rest of the process: the
environment is left alone, a later ``bind()`` lands where it would have, and the bench still finds the bound venture's
repositories under its own root. Offline."""
from __future__ import annotations

import os
from pathlib import Path

from g6.workforce import api, bench


def test_bind_with_a_db_dir_leaves_the_process_environment_alone(tmp_path, monkeypatch):
    monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    first = api.bind(db_dir=tmp_path / "root")
    assert bench.ENV_DB_DIR not in os.environ
    assert Path(first.db_dir) == tmp_path / "root" and (tmp_path / "root" / "venture.db").is_file()
    later = api.bind()
    assert Path(later.db_dir).resolve() == (tmp_path / "home" / "workforce").resolve()
    assert later.venture_id != first.venture_id


def test_the_bench_finds_a_bound_ventures_repositories_under_its_own_root(tmp_path, monkeypatch):
    monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    wf = api.bind(db_dir=tmp_path / "root")
    repo = api.repository_dir(wf) / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_one.py").write_text("def test_one():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    assert bench._repo("calc", wf.venture_id) == repo.resolve()
    reply = bench.run("run_tests", {"repo": "calc", "tests": ["tests/test_one.py::test_one"]}, venture=wf.venture_id)
    assert reply["ok"] and reply["result"]["passed"] is True, reply
