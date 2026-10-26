# SPDX-License-Identifier: Apache-2.0
"""The Guide (owner, 2026-10-19): its claims stay, each backed by the academic literature or a reputable source, and
no claim says more than its source. Figures with no reputable primary source were replaced by what the studies
measured; G6's own withdrawn figures are gone."""
from __future__ import annotations

import html

import pytest

SOURCES = (
    "https://mlq.ai/media/quarterly_decks/v0.1_State_of_AI_in_Business_2025_Report.pdf",   # MIT NANDA 2025
    "https://www.rand.org/pubs/research_reports/RRA2680-1.html",
    "https://arxiv.org/abs/2108.09293",       # Pearce et al. 2022
    "https://www.veracode.com/press-release/ai-generated-code-poses-major-security-risks-in-nearly-half-of-all-development-tasks-veracode-research-reveals/",
    "https://arxiv.org/abs/2404.18353",       # Tihanyi et al. 2024
    "https://arxiv.org/abs/2401.06730",       # Zhou et al. 2024
    "https://arxiv.org/abs/2509.04664",       # Kalai et al. 2025
    "https://arxiv.org/abs/2505.06120",       # Laban et al. 2025
    "https://arxiv.org/abs/2307.03172",       # Liu et al. 2024
    "https://arxiv.org/abs/2310.01798",       # Huang et al. 2024
    "https://arxiv.org/abs/2603.28052",       # Lee et al. 2026
    "https://arxiv.org/abs/2305.14314",       # Dettmers et al. 2023
)
GONE = ("90%", "$4.7B", "13 public benchmarks", "100 real-world code-repair", "731 real-world", "G6Solver",
        "Real benchmark results", "Watch it improve", "mathematical certainty", "mathematical property",
        "for the first time in history", "matters more than the model")


@pytest.mark.parametrize("path", ["/guide/", "/guide/view/"])
def test_the_guide_drops_figures_no_source_supports(client, path):
    page = html.unescape(client.get(path).content.decode())
    for phrase in GONE:
        assert phrase not in page, (path, phrase)


def test_the_full_guide_links_every_source_it_cites(client):
    page = html.unescape(client.get("/guide/view/").content.decode())
    assert ">Sources<" in page
    for url in SOURCES:
        assert page.count(f'href="{url}"') >= 2, url        # cited where it is used, and listed under Sources


def test_the_guide_landing_cites_its_three_figures(client):
    page = html.unescape(client.get("/guide/").content.decode())
    for figure, url in (("5%", SOURCES[0]), ("40–62%", SOURCES[2]), ("6×", SOURCES[10])):
        assert figure in page and f'href="{url}"' in page, figure
