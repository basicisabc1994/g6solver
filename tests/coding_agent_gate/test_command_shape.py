# SPDX-License-Identifier: Apache-2.0
"""Runner position is part of the command's type, never a keyword search."""

import pytest

from g6.coding_agent_gate import Evidence
from g6.coding_agent_gate.references import check_references


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/core/test_a.py",
        "py.test tests/core/test_a.py",
        "python -m pytest tests/core/test_a.py",
        "python3.12 -m pytest tests/core/test_a.py",
        "py -m pytest tests/core/test_a.py",
        '"C:/tools/pytest.exe" tests/core/test_a.py',
    ],
)
def test_only_program_or_python_module_shapes_are_recognised(command):
    assert check_references(
        [Evidence("test", command, exit_code=0)], repo_root=None, paths=["tests/core/test_a.py"]
    ) == ([], [])


@pytest.mark.parametrize(
    "command",
    [
        "npm",
        "cargo",
        "unknown",
        "npm test pytest tests/core/test_a.py",
        "python ../outside.py pytest tests/core/test_a.py",
        "python -c pytest tests/core/test_a.py",
        "python -m other pytest tests/core/test_a.py",
        "echo pytest tests/core/test_a.py",
        "python pytest tests/core/test_a.py",
        "pytest tests/core/test_a.py ; npm",
        "python -m pytest tests/core/test_a.py && unknown",
    ],
)
def test_unknown_commands_and_later_pytest_words_fail_closed(command):
    missing, unchecked = check_references(
        [Evidence("test", command, exit_code=0)], repo_root=None, paths=["tests/core/test_a.py"]
    )
    assert missing or unchecked


def test_an_earlier_script_reference_is_still_checked():
    missing, _ = check_references(
        [Evidence("test", "python ../outside.py pytest tests/core/test_a.py", exit_code=0)],
        repo_root=None,
        paths=["tests/core/test_a.py"],
    )
    assert "../outside.py" in missing


@pytest.mark.parametrize("suffix", [
    "2>&1", "> out.txt", ">> out.txt", "< input.txt", "2> errors.txt",
    "2>> errors.txt", "1>&2", "2>&1 > out.py", ">out.txt", "2>errors.txt",
    "| pytest tests/core/test_a.py", "| python -m pytest tests/core/test_a.py",
])
def test_redirects_are_not_references_and_pipe_programs_have_their_own_shape(suffix):
    assert check_references(
        [Evidence("test", "pytest tests/core/test_a.py " + suffix, exit_code=0)],
        repo_root=None, paths=["tests/core/test_a.py"],
    ) == ([], [])


@pytest.mark.parametrize("command", [
    "tests/core/test_a.py::test_a", "pytest tests/core/test_a.py::test_a",
])
def test_node_ids_resolve_the_path_part(command):
    assert check_references(
        [Evidence("test", command, exit_code=0)], repo_root=None, paths=["tests/core/test_a.py"],
    ) == ([], [])
    missing, _ = check_references([Evidence("test", command, exit_code=0)], repo_root=None, paths=[])
    assert missing == ["tests/core/test_a.py::test_a"]


@pytest.mark.parametrize("command", [
    "pytest tests/core/test_a.py | unknown",
    "pytest tests/core/test_a.py | npm test pytest tests/core",
    "pytest tests/core/test_a.py | python ../outside.py pytest tests/core",
    "pytest tests/core/test_a.py | tests/core/test_a.py::test_a",
    "python ../outside.py pytest tests/core",
    "npm test pytest tests/core",
])
def test_pipe_and_review_three_unknown_shapes_still_require_revision(command):
    from g6.coding_agent_gate import verify_answer

    decision = verify_answer(
        task="Fix sum", answer="Changed sum.", task_contract="sum adds inputs",
        evidence=[Evidence("test", command, exit_code=0)],
        repo_root=".",
    )
    assert decision.verdict == "revise"
    assert decision.to_dict()["references_checked"] is False


@pytest.mark.parametrize("suffix", ["> out.py", ">> out.py", "< input.py", "2> errors.py", "2>&1"])
def test_redirect_operands_are_not_proposed_as_tests(suffix):
    from g6.coding_agent_gate.references import named_tests

    assert named_tests("pytest tests/core/test_a.py " + suffix) == ["tests/core/test_a.py"]


@pytest.mark.parametrize("command,missing_ref", [
    ("pytest missing.py > out.txt", "missing.py"),
    ("pytest > out.txt missing.py", "missing.py"),
    ("pytest tests/core/test_a.py | pytest missing.py", "missing.py"),
    ("pytest missing.py | pytest tests/core/test_a.py", "missing.py"),
])
def test_redirects_and_pipes_keep_real_references(command, missing_ref):
    missing, unchecked = check_references(
        [Evidence("test", command, exit_code=0)], repo_root=None, paths=["tests/core/test_a.py"],
    )
    assert missing == [missing_ref]
    assert unchecked == []
