# SPDX-License-Identifier: Apache-2.0
"""Selection-only declarations cannot replace a collected callable."""
import ast

import pytest

from g6.workforce import repo_tests


@pytest.mark.parametrize("filename,text", [
    ("pytest.ini", "[pytest]\npython_functions = check_*\n"),
    ("pytest.ini", "[pytest]\naddopts = -o python_functions=check_*\n"),
    ("pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
    (".pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
])
def test_function_naming_only_keeps_collection_readable(tmp_path, filename, text):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    assert repo_tests._collection_config_passive(path) is True


@pytest.mark.parametrize("text", [
    "[pytest]\naddopts = -p arbitrary\n",
    "[pytest]\naddopts = -o pythonpath=elsewhere\n",
    "[pytest]\npython_classes = Spec*\n",
])
def test_unmodelled_runner_or_class_options_stay_unavailable(tmp_path, text):
    path = tmp_path / "pytest.ini"
    path.write_text(text, encoding="utf-8")
    assert repo_tests._collection_config_passive(path) is False


@pytest.mark.parametrize("source", [
    "def pytest_pycollect_makeitem(collector, name, obj):\n"
    "    if name == 'TestCustom':\n        return []\n",
    "def pytest_collection_modifyitems(items):\n"
    "    if len(items) != 2:\n        items[:] = []\n",
])
def test_empty_selection_hooks_keep_source_identity(source):
    assert repo_tests._collection_source_passive(ast.parse(source), set()) is True


@pytest.mark.parametrize("source", [
    "def pytest_pycollect_makeitem(collector, name, obj):\n    return [obj]\n",
    "def pytest_collection_modifyitems(items):\n    items[:] = [replacement]\n",
    "def pytest_collection_modifyitems(items):\n    install()\n",
])
def test_constructing_or_effectful_hooks_stay_unavailable(source):
    assert repo_tests._collection_source_passive(ast.parse(source), set()) is False


def test_renaming_a_collected_node_stays_unavailable():
    source = ("def pytest_collection_modifyitems(items):\n"
              "    items[0]._nodeid = 'test_added.py::test_added'\n")
    assert repo_tests._collection_source_passive(ast.parse(source), set()) is False
