# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path
from types import SimpleNamespace

from g6.cli import main


def test_setup_registers_explicit_client_with_absolute_interpreter_and_skills(tmp_path, monkeypatch):
    from g6.cli import setup
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup, "isolation", lambda *a: 0)
    calls = []
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: calls.append(command) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert main(["setup", "--client", "codex", "--refuse-unconfined", "--allow", "patch_review.review"]) == 0
    assert calls[0][:4] == ["C:/tools/codex.exe", "mcp", "add", "g6"]
    assert "G6_MCP_SENSITIVE=patch_review.review" in calls[0]
    assert calls[0][-4:] == ["--", sys.executable, "-c", "from g6.mcp import main; main()"]
    assert (tmp_path / ".codex" / "skills" / "using-g6" / "SKILL.md").is_file()


def test_skills_can_target_codex_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["skills", "install", "--client", "codex"]) == 0
    skill = tmp_path / ".codex" / "skills" / "using-g6" / "SKILL.md"
    skill.write_text("local edits")
    assert main(["skills", "install", "--client", "codex"]) == 0
    assert skill.read_text() == "local edits"


def test_explicit_host_model_choice_updates_only_provider_line(tmp_path, monkeypatch):
    from g6.cli.setup import configure_model
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    target = tmp_path / ".env"
    target.write_text("# my config\nOTHER_SETTING=unchanged\nG6_LLM_PROVIDER=none\n")
    configure_model("codex")
    assert target.read_text() == "# my config\nOTHER_SETTING=unchanged\nG6_LLM_PROVIDER=codex\n"
