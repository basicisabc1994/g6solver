# SPDX-License-Identifier: Apache-2.0
"""Pull request #6's metacognition and critical-thinking prompts, as entries of the one prompt catalogue (P1): unique,
each with its digest, template and source; rendered through the library's strict one-pass fill; installed explicitly
into the existing versioned library."""
import pytest

from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library import catalogue, metacognition
from g6.workspace.align_prompt_library.library import Library, placeholders

CATALOG = metacognition.ENTRIES


def test_catalog_unique_digest_and_covers_source_stages():
    names = [item.name for item in CATALOG]
    assert len(names) == len(set(names))
    assert {"mc-constraints", "mc-first-principles", "mc-heuristics",
            "mc-self-assess", "mc-satisficing", "mc-goal-decompose",
            "mc-research-needed", "ct-critical-review",
            "pe-generate-system-prompt", "pe-revise-system-prompt"} <= set(names)
    for entry in CATALOG:
        assert entry.digest and entry.template and entry.source
        assert not entry.template.startswith("You are an all-knowing")


def test_strict_render_and_no_recursive_value_expansion():
    template = catalogue.entry("ct-critical-review")
    keys = placeholders(template.template)
    vals = {k: "sample" for k in keys}
    vals["candidate"] = "{{objective}}"
    result = catalogue.render("ct-critical-review", vals)
    assert "{{objective}}" in result["text"]
    assert result["template_digest"] == template.digest
    with pytest.raises(E.WorkspaceRefused) as missing:
        catalogue.render("ct-critical-review", {"objective": "missing keys"})
    assert missing.value.code == E.LIBRARY_MISSING_VALUE
    with pytest.raises(E.WorkspaceRefused) as extra:
        catalogue.render("ct-critical-review", {**vals, "unexpected": "no"})
    assert extra.value.code == E.LIBRARY_EXTRA_VALUE


def test_install_is_explicit_and_uses_existing_versioned_library(tmp_path):
    library = Library(tmp_path / "catalog.jsonl")
    first = catalogue.install(path=tmp_path / "catalog.jsonl")["stored"]
    again = catalogue.install(path=tmp_path / "catalog.jsonl")["stored"]
    assert len(first) == len(again) == len(catalogue.ENTRIES)
    for entry in CATALOG:
        record = library.get(entry.name)
        assert record["digest"] == entry.digest
        assert record["kind"] == "prompt"
        assert record["declared"]["status"] == "candidate_template"
        assert len(library.history(entry.name)) == 1


@pytest.mark.parametrize("module", ["metacognition", "cognitive_prompts", "catalogue"])
def test_each_prompt_module_imports_on_its_own_in_a_fresh_interpreter(module):
    # the catalogue imports both entry modules; either imported first must not meet a half-initialised catalogue
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", f"from g6.workspace.align_prompt_library.{module} import ENTRIES; "
                           "print(len(ENTRIES))"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and int(done.stdout) > 0, done.stderr[-500:]