# SPDX-License-Identifier: Apache-2.0
"""Untrusted code runs only inside an operating-system boundary unless the person allows otherwise, once per computer
(DECISIONS #40, rulings 2 and 10): their choice is kept by ``g6 setup`` or an app's first-run page
(``g6.core.choices``), ``G6_ALLOW_UNCONFINED=1`` overrides it where G6 is started, and ``G6_REQUIRE_ISOLATION``
outranks both. A refusal runs nothing and says what to do; an allowed run keeps the warning and says who allowed it.
No MCP tool and no component op can write the choice. Offline: a temporary G6_HOME, never the person's own."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

import pytest

from g6.cli import setup
from g6.core import choices, confine, sandbox
from g6.core.errors import ISOLATION_UNAVAILABLE

SRC = Path(__file__).resolve().parents[2] / "src" / "g6"
OFF = "unavailable: switched off by this test"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """A G6_HOME of the test's own, no backend, and neither variable set."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    monkeypatch.delenv(confine.REQUIRE, raising=False)
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))


# --- the record ---------------------------------------------------------------------------------------------------------


def test_no_file_and_an_unreadable_file_are_no_choice() -> None:
    # fails if a missing or unreadable choices file reads as a choice
    assert choices.choice(choices.UNCONFINED) is None
    choices.path().parent.mkdir(parents=True)
    for text in ("{not json", "[]", json.dumps({choices.UNCONFINED: {"value": "maybe", "how": "g6 setup",
                                                                     "recorded_at": "x"}}),
                 json.dumps({choices.UNCONFINED: {"value": "allow", "how": "an agent", "recorded_at": "x"}})):
        choices.path().write_text(text, encoding="utf-8")
        assert choices.choice(choices.UNCONFINED) is None, text


def test_a_recorded_choice_says_its_value_when_and_how() -> None:
    entry = choices.record(choices.UNCONFINED, choices.ALLOW, choices.SETUP)
    # fails if the record loses its value, its time or how it was made
    assert choices.choice(choices.UNCONFINED) == entry and entry["value"] == "allow" and entry["how"] == "g6 setup"
    assert entry["recorded_at"].endswith("+00:00")
    with pytest.raises(ValueError):
        choices.record(choices.UNCONFINED, "sometimes", choices.SETUP)
    with pytest.raises(ValueError):
        choices.record(choices.UNCONFINED, choices.ALLOW, "an agent over MCP")


def _writers() -> list[str]:
    """Every module of g6 that reaches g6.core.choices.record, read from the source (never imported)."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = isinstance(node, ast.ImportFrom) and node.module == "g6.core.choices" and any(
                alias.name == "record" for alias in node.names)
            called = isinstance(node, ast.Attribute) and node.attr == "record" and isinstance(node.value, ast.Name) \
                and node.value.id == "choices"
            if names or called:
                found.append(path.relative_to(SRC).as_posix())
                break
    return found


def test_only_the_command_and_an_apps_page_write_a_choice() -> None:
    # fails if any other module (an MCP tool, a component op, the doctor) reaches the one writer
    assert _writers() == ["bundles/server.py", "cli/setup.py"]


# --- who allowed it, and what outranks what ----------------------------------------------------------------------------


def test_with_no_choice_and_no_variable_a_run_no_boundary_can_confine_is_refused() -> None:
    # fails if the default is still to run untrusted code with the person's rights
    assert confine.allowed_by() == "" and confine.allow_unconfined() is False and confine.required() is True


def test_a_recorded_allow_lets_it_run_and_says_who_allowed_it() -> None:
    choices.record(choices.UNCONFINED, choices.ALLOW, choices.BUNDLE_PAGE)
    assert confine.allowed_by() == "bundle first-run page" and confine.required() is False
    choices.record(choices.UNCONFINED, choices.REFUSE, choices.SETUP)
    # fails if a later refusal does not take back the allowance
    assert confine.allowed_by() == "" and confine.required() is True


def test_the_variable_overrides_a_recorded_refusal(monkeypatch) -> None:
    choices.record(choices.UNCONFINED, choices.REFUSE, choices.SETUP)
    monkeypatch.setenv(confine.ALLOW, "1")
    assert confine.allowed_by() == confine.ALLOW and confine.required() is False
    monkeypatch.setenv(confine.ALLOW, "0")
    assert confine.allowed_by() == "" and confine.required() is True


@pytest.mark.parametrize("how", ["variable", "recorded"])
def test_requiring_isolation_outranks_any_allowance(monkeypatch, how) -> None:
    if how == "variable":
        monkeypatch.setenv(confine.ALLOW, "1")
    else:
        choices.record(choices.UNCONFINED, choices.ALLOW, choices.SETUP)
    monkeypatch.setenv(confine.REQUIRE, "1")
    with confine.run_folder("g6_test_") as folder:
        plan = confine.plan(confine.Confinement(read_write=(folder,)))
    # fails if an allowance lets a run through that G6_REQUIRE_ISOLATION refuses
    assert confine.required() is True and confine.refused(plan).startswith(f"[{ISOLATION_UNAVAILABLE}]")
    assert confine.REQUIRE in confine.refused(plan)


def test_the_refusal_says_what_happened_and_what_to_do() -> None:
    with confine.run_folder("g6_test_") as folder:
        said = confine.refused(confine.plan(confine.Confinement(read_write=(folder,))))
    # fails if the refusal loses its code, the reason, or any of the three ways forward
    assert said.startswith(f"[{ISOLATION_UNAVAILABLE}] G6 runs untrusted code only inside an operating-system boundary")
    for words in (OFF, "g6 doctor", "g6 setup", "G6_ALLOW_UNCONFINED=1"):
        assert words in said, words


def test_a_confined_plan_is_never_refused() -> None:
    confined = confine.Plan(confine.APPCONTAINER, "", confine.Confinement(read_write=()))
    assert confine.refused(confined) == ""


# --- what a run does -----------------------------------------------------------------------------------------------------


def test_with_no_choice_a_script_is_refused_and_nothing_runs(tmp_path) -> None:
    marker = tmp_path / "ran.txt"
    run = sandbox.run_script(f"open({str(marker)!r}, 'w').write('ran')", timeout_s=30)
    # fails if sandbox.run_script runs untrusted code unconfined without the person's allowance
    assert run.status == "refused" and not marker.exists() and sandbox.isolation_refused(run)
    assert "g6 setup" in run.stderr and run.isolation["backend"] is None and "allowed_by" not in run.isolation


def test_an_allowed_run_keeps_the_warning_and_says_who_allowed_it(tmp_path) -> None:
    choices.record(choices.UNCONFINED, choices.ALLOW, choices.SETUP)
    run = sandbox.run_script("open('out.txt', 'w').write('x')", timeout_s=30)
    # fails if an allowed run drops the warning or does not say who allowed it
    assert run.status == "ok" and run.outputs == {"out.txt": b"x"}
    assert run.isolation["warning"] == confine.UNCONFINED_WARNING and run.isolation["allowed_by"] == "g6 setup"


def test_the_warning_says_g6_refuses_where_no_boundary_is_and_how_to_allow_it() -> None:
    said = confine.UNCONFINED_WARNING
    # fails if the warning still says Linux has no boundary, or stops saying what an allowed run can reach
    assert "Linux and macOS today" not in said
    for words in ("macOS", "Linux without bubblewrap", "Windows before the one-time access grant", "refuses",
                  "g6 setup", "~/.g6/.env", "your own rights", "inside Python only"):
        assert words in said, words
    assert said.endswith("You are responsible for what you run.")


def test_ci_opts_in_only_where_there_is_no_boundary_to_test() -> None:
    workflow = (SRC.parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    test_job, _, rest = workflow.partition("\n  conformance:\n")
    # fails if the macOS and Windows jobs lose the opt-in (every untrusted run there would be refused), or a Linux job
    # takes it (bubblewrap's evidence would then not be evidence)
    step = re.split(r"\n {6}(?:- |#)", test_job[test_job.index("- if: runner.os != 'Linux'"):], maxsplit=1)[0]
    assert step.rstrip().endswith('run: echo "G6_ALLOW_UNCONFINED=1" >> "$GITHUB_ENV"'), step
    assert test_job.count("G6_ALLOW_UNCONFINED") == 1 and "G6_ALLOW_UNCONFINED" not in rest


# --- g6 setup ------------------------------------------------------------------------------------------------------------


def _args(*flags: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    return parser.parse_args(list(flags))


@pytest.mark.parametrize(("flag", "value"), [("--allow-unconfined", "allow"), ("--refuse-unconfined", "refuse")])
def test_setup_records_the_answer_given_on_its_command_line(flag, value) -> None:
    said: list[str] = []
    assert setup.main(_args(flag), ask=lambda prompt: pytest.fail("no question with a flag"), say=said.append) == 0
    # fails if g6 setup's flags do not record the choice as made by g6 setup
    assert choices.choice(choices.UNCONFINED)["value"] == value and choices.choice(choices.UNCONFINED)["how"] == "g6 setup"
    assert str(choices.path()) in said[0]


@pytest.mark.parametrize(("reply", "value"), [("y", "allow"), ("yes", "allow"), ("", "refuse"), ("no", "refuse")])
def test_setup_asks_in_plain_words_where_no_boundary_is_and_records_the_reply(reply, value) -> None:
    said: list[str] = []
    asked: list[str] = []
    assert setup.main(_args(), ask=lambda prompt: (asked.append(prompt), reply)[1], say=said.append) == 0
    # fails if the question is not asked, does not say what an allowed run can reach, or the reply is not recorded
    assert asked == [setup.PROMPT] and "your key file ~/.g6/.env" in said[0] and OFF in said[0]
    assert choices.choice(choices.UNCONFINED)["value"] == value


def test_setup_asks_nothing_and_records_nothing_where_a_boundary_confines_runs(monkeypatch) -> None:
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: ("bubblewrap", ""))
    said: list[str] = []
    assert setup.main(_args(), ask=lambda prompt: pytest.fail("nothing to ask"), say=said.append) == 0
    assert choices.choice(choices.UNCONFINED) is None and "nothing to choose" in said[0]


def test_setup_with_no_one_to_answer_records_nothing() -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    said: list[str] = []
    # fails if a setup run with no terminal records a choice nobody made
    assert setup.main(_args(), ask=closed, say=said.append) == 2
    assert choices.choice(choices.UNCONFINED) is None and "--allow-unconfined" in said[-1]


def test_g6_setup_is_a_command_of_g6(monkeypatch, capsys) -> None:
    from g6.cli import main

    assert main(["setup", "--refuse-unconfined"]) == 0
    assert choices.choice(choices.UNCONFINED)["value"] == "refuse"
