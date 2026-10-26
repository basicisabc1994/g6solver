# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os

from g6.cli import main
from g6.cli.doctor import _keys, _model, report, run_checks
from g6.core.storage import g6_home


def test_doctor_is_read_only_and_reports(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh"))
    home = g6_home()
    assert not home.exists()
    code = main(["doctor", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert not home.exists(), "doctor must not create the state folder"
    names = {c["name"] for c in out["checks"]}
    assert {"python", "state folder", "sqlite", "AI model", "key file", "components"} <= names
    assert code == (1 if out["status"] == "fail" else 0)
    assert next(c for c in out["checks"] if c["name"] == "components")["status"] == "ok"


def test_model_checks_name_the_missing_key_and_where_it_goes(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    missing = _model({"G6_LLM_PROVIDER": "openrouter"}, {})
    assert missing.status == "fail" and "OPENROUTER_API_KEY" in missing.detail
    assert str(tmp_path / ".env") in missing.fix
    assert _model({"G6_LLM_PROVIDER": "openrouter"}, {"OPENROUTER_API_KEY": "x"}).status == "ok"
    assert _model({"G6_LLM_PROVIDER": "acme"}, {}).status == "fail"
    assert _model({}, {}).status == "ok"


def test_a_key_only_in_the_environment_is_named_as_unused(monkeypatch, tmp_path):
    # owner ruling 2026-10-21: G6 reads keys only from the person's key file
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    # openrouter needs no SDK, so the check reaches the key wherever the anthropic extra is not installed
    check = _model({"G6_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-env-value"}, {})
    assert check.status == "fail" and "environment" in check.detail and "does not use" in check.detail
    assert "sk-env-value" not in check.detail + check.fix


def test_the_key_file_check_names_keys_and_problems_never_values(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    assert _keys().status == "ok" and "no keys" in _keys().detail
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-secret-value\nnot a line\n", encoding="utf-8")
    check = _keys()
    assert check.status == "warn" and "OPENROUTER_API_KEY" in check.detail and "line 2" in check.detail
    main(["doctor", "--json"])
    assert "sk-secret-value" not in capsys.readouterr().out


def test_a_setting_in_the_key_file_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.delenv("G6_LLM_PROVIDER", raising=False)
    (tmp_path / ".env").write_text("G6_LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=x\n", encoding="utf-8")
    model = next(c for c in run_checks() if c.name == "AI model")
    assert model.status == "ok" and "openrouter" in model.detail


def _program(folder, name: str):
    """A file doctor can find as a program; doctor never runs it."""
    program = folder / (f"{name}.cmd" if os.name == "nt" else name)
    program.write_text("@echo off\r\nexit /b 9\r\n" if os.name == "nt" else "#!/bin/sh\nexit 9\n", encoding="utf-8")
    program.chmod(0o755)
    return program


def test_a_host_model_in_the_key_file_says_where_the_program_is_and_that_doctor_did_not_contact_it(monkeypatch,
                                                                                                    tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    claude, codex = _program(tmp_path, "claude"), _program(tmp_path, "codex")
    monkeypatch.setenv("G6_CLAUDE", str(claude))
    monkeypatch.setenv("G6_CODEX", str(codex))
    check = _model({"G6_LLM_PROVIDER": "claude_code"}, {"G6_LLM_PROVIDER": "claude_code"})
    assert check.status == "ok" and str(claude) in check.detail
    assert "signed in as you; not contacted by doctor" in check.detail
    check = _model({"G6_LLM_PROVIDER": "codex"}, {"G6_LLM_PROVIDER": "codex"})
    assert check.status == "ok" and str(codex) in check.detail and "not contacted by doctor" in check.detail
    assert "shell" in check.detail and "claude_code is the one G6 recommends" in check.detail
    monkeypatch.setenv("G6_CLAUDE", str(tmp_path / "not-there.exe"))
    missing = _model({"G6_LLM_PROVIDER": "claude_code"}, {"G6_LLM_PROVIDER": "claude_code"})
    assert missing.status == "fail" and "npm install -g @anthropic-ai/claude-code" in missing.fix
    # Removing the HOST_PROVIDERS branch of _model makes this fail (unknown G6_LLM_PROVIDER).


def test_a_host_model_named_only_in_the_environment_is_a_warning_that_it_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    for provider in ("claude_code", "codex"):
        check = _model({"G6_LLM_PROVIDER": provider}, {})
        assert check.status == "warn" and "ignore" in check.detail
        assert str(tmp_path / ".env") in check.fix and f"G6_LLM_PROVIDER={provider}" in check.fix
    # Removing the key-file check in _model's HOST_PROVIDERS branch makes this fail.


def test_failing_check_fails_overall():
    checks = run_checks({"G6_LLM_PROVIDER": "acme"})
    assert report(checks)["status"] == "fail"


def test_human_output(capsys):
    main(["doctor"])
    assert "state folder" in capsys.readouterr().out
