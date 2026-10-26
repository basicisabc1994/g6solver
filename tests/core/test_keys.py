# SPDX-License-Identifier: Apache-2.0
"""g6.core.keys: the one file the person writes their keys in, and the only place G6 reads a key from (owner ruling,
2026-10-21: putting a key in it is the person's approval for G6 to use it)."""
from __future__ import annotations

import os

import pytest

from g6.core import keys
from g6.core.keys import Key


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    return tmp_path


def _file(home, text: str) -> None:
    (home / ".env").write_text(text, encoding="utf-8")


def test_the_file_is_dot_env_in_g6_home(home) -> None:
    assert keys.path() == home / ".env"


def test_no_file_means_no_keys(home) -> None:
    assert keys.read() == {}
    assert keys.get("ANY_API_KEY") is None and not keys.present("ANY_API_KEY")
    assert keys.names() == [] and keys.problems() == []


def test_name_value_lines_comments_export_and_quotes(home) -> None:
    _file(home, "# my keys\n\nexport A_API_KEY=abc\nB_TOKEN = \"x y\"\nC_SECRET='q#r'\nD_KEY=plain # note\n"
                "G6_LLM_PROVIDER=anthropic\n")
    assert keys.read() == {"A_API_KEY": "abc", "B_TOKEN": "x y", "C_SECRET": "q#r", "D_KEY": "plain",
                           "G6_LLM_PROVIDER": "anthropic"}
    assert keys.names() == ["A_API_KEY", "B_TOKEN", "C_SECRET", "D_KEY", "G6_LLM_PROVIDER"]


def test_nothing_is_expanded(home) -> None:
    _file(home, "A_KEY=$HOME/${USER}\n")
    assert keys.get("A_KEY") == "$HOME/${USER}"


def test_a_byte_order_mark_and_windows_line_ends_are_read(home) -> None:
    (home / ".env").write_bytes("﻿A_KEY=abc\r\nB_KEY=def\r\n".encode())
    assert keys.read() == {"A_KEY": "abc", "B_KEY": "def"}


def test_an_empty_value_is_no_key(home) -> None:
    _file(home, "A_KEY=\nB_KEY=''\n")
    assert not keys.present("A_KEY") and not keys.present("B_KEY") and keys.names() == []


def test_bad_lines_are_reported_by_number_never_by_value(home) -> None:
    _file(home, "A_KEY=hidden-first\nnot a line sk-secret-value\n1BAD=x\nA_KEY=hidden-second\n")
    assert keys.read() == {"A_KEY": "hidden-second"}
    found = keys.problems()
    assert any("line 2" in p for p in found) and any("line 3" in p for p in found)
    assert any("A_KEY" in p and "line 4" in p for p in found)  # a name given twice: the last one is used
    assert not any("sk-secret-value" in p or "hidden-" in p for p in found)


def test_the_process_environment_is_never_read(home, monkeypatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "from-the-environment")
    assert keys.get("SOME_API_KEY") is None and not keys.present("SOME_API_KEY")
    with pytest.raises(keys.KeyMissing):
        keys.require("SOME_API_KEY")


def test_a_project_dot_env_in_the_working_folder_is_never_read(home, tmp_path_factory, monkeypatch) -> None:
    project = tmp_path_factory.mktemp("project")
    (project / ".env").write_text("SOME_API_KEY=the-projects-own\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert keys.get("SOME_API_KEY") is None


def test_edits_take_effect_without_a_restart(home) -> None:
    _file(home, "A_KEY=one\n")
    assert keys.get("A_KEY") == "one"
    _file(home, "A_KEY=two\n")
    assert keys.get("A_KEY") == "two"


def test_require_returns_the_value(home) -> None:
    _file(home, "STABILITY_API_KEY=abc\n")
    assert keys.require(Key("STABILITY_API_KEY", "paid image generation", paid=True)) == "abc"
    assert keys.require("STABILITY_API_KEY") == "abc"


def test_require_refuses_with_the_code_the_file_and_the_line_to_add(home) -> None:
    key = Key("STABILITY_API_KEY", "paid image generation", where="https://platform.stability.ai", paid=True)
    with pytest.raises(keys.KeyMissing) as caught:
        keys.require(key)
    error = caught.value
    assert error.code == "G6_E_KEY_MISSING" and str(error).startswith("[G6_E_KEY_MISSING] ")
    assert str(home / ".env") in error.message and "STABILITY_API_KEY=" in error.message
    assert "https://platform.stability.ai" in error.message
    failed = error.as_failure()
    assert failed.is_fail() and failed.error.startswith("[G6_E_KEY_MISSING] ")


def test_an_unreadable_file_is_a_problem_and_no_keys(home) -> None:
    (home / ".env").mkdir()
    assert keys.read() == {}
    assert any("cannot be read" in p for p in keys.problems())


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_a_file_others_can_read_is_reported(home) -> None:
    _file(home, "A_KEY=abc\n")
    os.chmod(home / ".env", 0o644)
    assert any("others can read" in p for p in keys.problems())
    os.chmod(home / ".env", 0o600)
    assert keys.problems() == []


def test_a_key_declaration_names_what_it_is_for() -> None:
    key = Key("REPLICATE_API_TOKEN", "paid image generation on Replicate", where="https://replicate.com", paid=True)
    assert key.describe() == {"name": "REPLICATE_API_TOKEN", "purpose": "paid image generation on Replicate",
                              "where": "https://replicate.com", "paid": True, "file": str(keys.path())}
    with pytest.raises(ValueError):
        Key("not a name", "x")
    with pytest.raises(ValueError):
        Key("A_KEY", " ")


def test_g6_never_writes_the_file(home) -> None:
    keys.read(), keys.names(), keys.problems(), keys.present("A_KEY")
    assert not (home / ".env").exists()
