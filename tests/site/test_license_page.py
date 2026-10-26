# SPDX-License-Identifier: Apache-2.0
"""The licence page credits the third-party skills G6 ships (the superpowers skills, MIT) and names the extras whose
libraries are not open source."""
from __future__ import annotations

import html
import json
from pathlib import Path

CLOSURES = Path(__file__).resolve().parents[2] / "tools" / "data" / "extra_closures.json"


def test_the_licence_page_credits_the_third_party_skills(client):
    page = html.unescape(client.get("/license/").content.decode())
    assert "Third-party skills" in page
    assert "superpowers" in page and "Jesse Vincent" in page and "MIT" in page
    assert "https://github.com/obra/superpowers" in page


def test_the_licence_page_names_each_extra_that_installs_nvidias_libraries(client):
    # the conformance kit waives NVIDIA's CUDA wheels for good in every extra that pulls PyTorch (owner rulings
    # 2026-10-21: GPU support for lora, then for every extra that pulls PyTorch); a person installing one reads it here.
    # The site pass (SITE_PASS item 3) restores every such extra, read from the closures: fails while one (nli) is unnamed
    extras = {name for name, entry in json.loads(CLOSURES.read_text(encoding="utf-8"))["extras"].items()
              if any(pin.startswith(("nvidia-", "cuda-toolkit==")) for pin in entry["closure"])}
    assert {"lora", "torch", "colbert", "embeddings"} <= extras
    page = html.unescape(client.get("/license/").content.decode())
    assert "Optional extras" in page and "NVIDIA's own licence" in page
    for extra in extras:
        assert f"<code>{extra}</code> extra" in page, extra
