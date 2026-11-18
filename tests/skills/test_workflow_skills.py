"""Vendored workflow instructions are discoverable and never constitute evidence."""

import pytest

from g6.core import discovery, semantic, skills
from g6.skills import corpus


def test_each_vendored_skill_has_one_source_derived_entry():
    entries = [e for e in corpus.entries() if e["kind"] == "skill"]
    assert len(entries) == len(skills.vendored()) == 14
    assert {e["id"] for e in entries} == {f"skill:{n}" for n in skills.vendored()}
    for entry in entries:
        name = entry["id"].split(":", 1)[1]
        text = (skills.vendored()[name] / "SKILL.md").read_text(encoding="utf-8")
        assert skills.front_matter(text)["description"] in entry["text"]
        assert entry["establishes"] == "a way of working"
        assert "evidence" in entry["not_established"]
        assert entry["needs"] == {"consent": [], "sensitive": "", "model": "none", "confined": False, "outside": [], "extras": []}
        assert entry["call"]["skill"] == f"skills/vendor/superpowers/{name}/SKILL.md"
        assert entry["call"]["codex"] == name
        assert entry["call"]["claude_code"] == f"superpowers:{name}"
        assert entry["call"]["claude_code_setup"] == name
        assert entry["call"]["codex_plugin"] == f"superpowers:{name}"


def test_description_changes_entry(monkeypatch, tmp_path):
    folder = tmp_path / "example"
    folder.mkdir()
    path = folder / "SKILL.md"
    path.write_text("---\nname: example\ndescription: Investigate a new failure\n---\n# Example\n\n## Overview\nRead the evidence first.\n\n## Later\nDo not index this section.\n", encoding="utf-8")
    monkeypatch.setattr(skills, "vendored", lambda: {"example": folder})
    before = corpus._skills()[0]
    path.write_text(path.read_text(encoding="utf-8").replace("Investigate a new failure", "Understand a system boundary"), encoding="utf-8")
    after = corpus._skills()[0]
    assert before["text"] != after["text"]
    assert "Understand a system boundary" in after["text"]
    assert "Read the evidence first" in after["text"]
    assert "Do not index" not in after["text"]


def test_skill_phase_tags_keep_design_and_finish_without_changing_selector_phases():
    entries = {entry["id"]: entry for entry in corpus._skills()}
    assert {"design", "plan"} <= set(entries["skill:brainstorming"]["phase_tags"])
    assert "finish" in entries["skill:finishing-a-development-branch"]["phase_tags"]
    assert entries["skill:finishing-a-development-branch"]["phases"] == ["verify"]


@pytest.mark.parametrize("query,name", [
    ("Brainstorm the design of a new application feature before building it", "brainstorming"),
    ("Write an implementation plan for requirements spanning several modules", "writing-plans"),
    ("Investigate an intermittent test failure and unexpected behaviour", "systematic-debugging"),
    ("Dispatch parallel agents for several independent tasks without shared state", "dispatching-parallel-agents"),
    ("Finish a development branch after implementation and tests pass and decide how to integrate it", "finishing-a-development-branch"),
])
def test_software_engineering_skill_top_three(query, name):
    with semantic.request(llm=None):
        hits = discovery.find(query)
    assert f"skill:{name}" in [h.get("id") for h in hits[:3]], hits[:3]


def test_patch_check_keeps_g6_first():
    with semantic.request(llm=None):
        hits = discovery.find("Review and check the Python patch before finishing the coding change")
    assert hits[0].get("tool") in {"g6_review_patch", "g6_review_until_clean"}


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_setup_install_adds_vendor_but_force_preserves_personal_copy(monkeypatch, tmp_path, capsys, client):
    from g6.cli import main

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / f".{client}" / "skills"
    existing = dest / "brainstorming"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("personal newer copy", encoding="utf-8")
    assert main(["skills", "install", "--client", client, "--force", "--with-superpowers"]) == 0
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "personal newer copy"
    assert (dest / "writing-plans" / "SKILL.md").read_bytes() == (skills.vendored()["writing-plans"] / "SKILL.md").read_bytes()
    output = capsys.readouterr().out
    assert str(existing) in output and "using" in output.lower()


def test_routing_inventory_counts_skills():
    from g6.mcp import registrations

    inventory = registrations.inputs()
    assert len(inventory["skills"]) == 14
    assert all(f"skill:{n}" in inventory["selectable"] for n in skills.vendored())


def test_plugin_installs_skills_with_existing_copy_check():
    import json
    from pathlib import Path

    config = json.loads((Path(__file__).resolve().parents[2] / "plugins/g6/hooks/hooks.json").read_text(encoding="utf-8"))
    hooks = config["hooks"].get("SessionStart", [])
    assert any(hook.get("args") == ["-P", "-m", "g6", "skills", "install", "--user", "--client", "claude", "--with-superpowers"]
               for group in hooks for hook in group["hooks"])
