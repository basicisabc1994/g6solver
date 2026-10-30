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
    monkeypatch.setattr(setup, "STEPS", ())   # STEPS holds the isolation step itself, as imported
    calls = []
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: calls.append(command) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert main(["setup", "--client", "codex", "--refuse-unconfined", "--allow", "patch_review.review"]) == 0
    assert calls[0][:4] == ["C:/tools/codex.exe", "mcp", "add", "g6"]
    assert "G6_MCP_SENSITIVE=patch_review.review" in calls[0]
    assert calls[0][-5:] == ["--", sys.executable, "-P", "-c", "from g6.mcp import main; main()"]
    assert (tmp_path / ".codex" / "skills" / "using-g6" / "SKILL.md").is_file()


def _registering(tmp_path, monkeypatch, returncode=0):
    from g6.cli import setup
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup, "isolation", lambda *a: 0)
    monkeypatch.setattr(setup, "STEPS", ())   # STEPS holds the isolation step itself, as imported
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: SimpleNamespace(returncode=returncode, stdout="",
                                                                                stderr=""))
    return setup


def test_setup_ends_with_the_first_review_call_when_patch_review_is_allowed(tmp_path, monkeypatch, capsys):
    setup = _registering(tmp_path, monkeypatch)
    assert main(["setup", "--client", "claude", "--client", "codex", "--refuse-unconfined",
                 "--allow", "patch_review.review"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith(setup.FIRST_REVIEW.rstrip())
    assert 'g6_review_patch(repo="<your repository\'s folder>"' in setup.FIRST_REVIEW
    assert "metaharness_job" in setup.FIRST_REVIEW and "assessment.report" in setup.FIRST_REVIEW
    assert "Merging stays your decision." in setup.FIRST_REVIEW and "verified" not in setup.FIRST_REVIEW


def test_setup_says_how_to_allow_patch_review_when_it_was_left_out(tmp_path, monkeypatch, capsys):
    setup = _registering(tmp_path, monkeypatch)
    assert main(["setup", "--client", "codex", "--refuse-unconfined"]) == 0
    out = capsys.readouterr().out
    assert setup.FIRST_REVIEW not in out
    assert out.rstrip().endswith(setup.NOT_ALLOWED.format(remove="codex mcp remove g6").rstrip())
    assert "--allow patch_review.review" in setup.NOT_ALLOWED


def test_a_refused_registration_says_how_to_replace_the_existing_entry(tmp_path, monkeypatch, capsys):
    _registering(tmp_path, monkeypatch, returncode=1)
    assert main(["setup", "--client", "claude", "--refuse-unconfined", "--allow", "patch_review.review"]) == 2
    out = capsys.readouterr().out
    assert ("claude refused registration. Inspect its existing g6 entry with 'claude mcp get g6'; to replace it, "
            "remove it with 'claude mcp remove --scope user g6' and run g6 setup again.") in out


def test_setup_replaces_an_earlier_copy_of_its_own_skill_and_says_so(tmp_path, monkeypatch, capsys):
    """The owner's pilot (2026-10-30): setup left Codex's copy of using-g6 from an earlier install in place, so Codex
    followed the old instructions; setup is the person's install step, so it brings this version's skill."""
    _registering(tmp_path, monkeypatch)
    old = tmp_path / ".codex" / "skills" / "using-g6" / "SKILL.md"
    old.parent.mkdir(parents=True)
    old.write_text("an earlier G6's skill")
    assert main(["setup", "--client", "codex", "--refuse-unconfined", "--allow", "patch_review.review"]) == 0
    assert "# Using G6" in old.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "left as is" not in out
    assert "Replaced the earlier copy of the using-g6 skill for codex with this version's." in out


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
