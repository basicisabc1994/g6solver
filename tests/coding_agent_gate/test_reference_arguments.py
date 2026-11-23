# SPDX-License-Identifier: Apache-2.0
"""Shell boundaries and pytest's grammar preserve every positional selector."""
import builtins

import pytest

from g6.coding_agent_gate import Evidence, verify_answer
from g6.coding_agent_gate.references import check_references, named_tests


def _references(command, paths=()):
    return check_references([Evidence("test", command, exit_code=0)], repo_root=None, paths=paths)


@pytest.mark.parametrize("redirect,extra", [
    ("2>out.txt", []), ("2> out.txt", []),
    ("2 >out.txt", ["2"]), ("2 > out.txt", ["2"]),
    (">out.txt", []), ("2>&1", []), ("&>out.txt", []), (">>out.txt", []),
    ('">"', [">"]), ("'2'>out.txt", ["2"]), (r"\2>out.txt", ["2"]),
    ("2&>out.txt", ["2"]),
])
def test_numeric_selectors_survive_except_adjacent_unquoted_descriptors(redirect, extra):
    assert _references(f"pytest 8675309 {redirect}") == (["8675309", *extra], [])


@pytest.mark.parametrize("separator", [";", "&&", "||", "|"])
def test_each_command_segment_has_its_own_selectors(separator):
    assert _references(f"pytest 8675309 >out.txt {separator} pytest 1234567 2>&1") == (
        ["8675309", "1234567"], []
    )


@pytest.mark.parametrize("arguments,selectors", [
    ("--", []),
    ("-- --missing.py", ["--missing.py"]),
    ("-- -q missing.py", ["-q", "missing.py"]),
    ("existing.py -- --missing.py", ["existing.py", "--missing.py"]),
    ("-q -- --missing.py -k", ["--missing.py", "-k"]),
    ("-- --", ["--"]),
    ("--ignore option.py missing.py", ["missing.py"]),
    ("--junit-prefix option.py missing.py", ["missing.py"]),
    ("--rootdir=option.py missing.py", ["missing.py"]),
    ("-k 'option.py or other.py' -m 'slow and not network' missing.py", ["missing.py"]),
    ("-p no:x missing.py", ["missing.py"]),
    ("-qxs missing.py", ["missing.py"]),
    ("-rA -W ignore::DeprecationWarning missing.py", ["missing.py"]),
    ("-koption.py missing.py", ["missing.py"]),
    ("missing.py -q", ["missing.py"]),
    ("missing.py -k name -- --dash.py", ["missing.py", "--dash.py"]),
])
def test_pytest_owns_option_values_and_end_of_options(arguments, selectors):
    assert _references("pytest " + arguments) == (selectors, [])


@pytest.mark.parametrize("command", [
    "pytest --not-a-pytest-option missing.py", "pytest -k", "pytest --maxfail=oops",
    "pytest --capture=invalid missing.py", "pytest 'unterminated", "pytest >", "pytest -k -- missing.py",
    "pytest --help --not-a-pytest-option", "pytest --version --not-a-pytest-option",
    "pytest missing.py; pytest -k",
])
def test_unreadable_arguments_require_revision_with_the_existing_reason(command, capsys):
    assert _references(command) == ([], [command])
    assert named_tests(command) == []
    decision = verify_answer(
        "Fix sum", "Changed sum.", task_contract="sum adds inputs",
        evidence=[Evidence("test", command, exit_code=0)], repo_root=".",
    )
    assert decision.verdict == "revise"
    assert decision.to_dict()["references_checked"] is False
    assert any(f.code == "EVIDENCE_REF_MISSING" and "could not classify or read" in f.message
               for f in decision.findings)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("stage", ["shell", "construction", "parse", "cleanup"])
@pytest.mark.parametrize("failure", [
    pytest.param(RuntimeError("cannot read arguments"), id="exception"),
    pytest.param(pytest.UsageError("invalid arguments"), id="usage-error"),
    pytest.param(SystemExit(0), id="early-exit"),
    pytest.param(SystemExit(2), id="error-exit"),
])
def test_read_failures_are_silent_unreadable_references(monkeypatch, capsys, stage, failure):
    import sys

    import _pytest.config

    from g6.coding_agent_gate import references

    def fail(*args, **kwargs):
        print("parser output")
        print("parser usage", file=sys.stderr)
        raise failure

    original = _pytest.config.get_config

    def get_config(*args, **kwargs):
        if stage == "construction":
            fail()
        config = original(*args, **kwargs)
        if stage == "parse":
            monkeypatch.setattr(config._parser, "parse", fail)
        else:
            cleanup = config._ensure_unconfigure

            def failed_cleanup():
                cleanup()
                fail()

            monkeypatch.setattr(config, "_ensure_unconfigure", failed_cleanup)
        return config

    if stage == "shell":
        monkeypatch.setattr(references, "_shell_words", fail)
    else:
        monkeypatch.setattr(_pytest.config, "get_config", get_config)
    command = "pytest existing.py"
    assert _references(command, ["existing.py"]) == ([], [command])
    assert named_tests(command) == []
    decision = verify_answer(
        "Fix sum", "Changed sum.", task_contract="sum adds inputs",
        evidence=[Evidence("test", command, exit_code=0)], repo_root=".",
    )
    assert decision.verdict == "revise"
    assert decision.references_unchecked == (command,)
    assert any(f.code == "EVIDENCE_REF_MISSING" and "could not classify or read" in f.message
               for f in decision.findings)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("command", ["pytest existing.py", "pytest -k"])
def test_reference_reading_preserves_the_missing_protected_policy_block(command):
    decision = verify_answer(
        "Fix sum", "Changed sum.", task_contract="sum adds inputs",
        evidence=[Evidence("test", command, exit_code=0)], touched_paths=[],
    )
    assert decision.verdict == "block"
    assert any(f.code == "protected_policy_missing" for f in decision.findings)


@pytest.mark.parametrize("command", ["pytest existing.py", "pytest -k"])
def test_reference_reading_preserves_the_shortcut_block(command):
    decision = verify_answer(
        "Fix sum", "Skipped the failing test to get CI green.", task_contract="sum adds inputs",
        evidence=[Evidence("test", command, exit_code=0)], repo_root=".",
    )
    assert decision.verdict == "block"
    assert any(f.code == "shortcut_in_answer" for f in decision.findings)


def test_successful_reference_reading_is_silent(monkeypatch, capsys):
    import sys

    import _pytest.config

    original = _pytest.config.get_config

    def get_config(*args, **kwargs):
        print("parser output")
        print("parser usage", file=sys.stderr)
        return original(*args, **kwargs)

    monkeypatch.setattr(_pytest.config, "get_config", get_config)
    assert _references("pytest existing.py", ["existing.py"]) == ([], [])
    assert named_tests("pytest existing.py") == ["existing.py"]
    assert capsys.readouterr() == ("", "")


def test_unavailable_pytest_parser_is_unreadable(monkeypatch):
    original = builtins.__import__

    def without_pytest(name, *args, **kwargs):
        if name == "_pytest.config":
            raise ImportError("parser unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pytest)
    command = "pytest existing.py"
    assert _references(command, ["existing.py"]) == ([], [command])


def test_parser_uses_only_builtins_without_loading_repository_or_external_plugins(monkeypatch):
    from _pytest.config import PytestPluginManager

    def unexpected_load(*args, **kwargs):
        raise AssertionError("reference classification must not load external plugins or conftests")

    monkeypatch.setattr(PytestPluginManager, "load_setuptools_entrypoints", unexpected_load)
    monkeypatch.setattr(PytestPluginManager, "_importconftest", unexpected_load)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--not-an-option")
    monkeypatch.setenv("PYTEST_PLUGINS", "must_not_be_imported")
    assert _references("pytest -q existing.py", ["existing.py"]) == ([], [])


def test_parser_construction_error_is_unreadable(monkeypatch):
    import _pytest.config

    def unavailable(*args, **kwargs):
        raise RuntimeError("cannot construct parser")

    monkeypatch.setattr(_pytest.config, "get_config", unavailable)
    command = "pytest existing.py"
    assert _references(command, ["existing.py"]) == ([], [command])


def test_review_suggestions_exclude_option_values_but_keep_dash_selectors():
    assert named_tests("pytest --ignore option.py -k expression.py -- --missing.py") == ["--missing.py"]
