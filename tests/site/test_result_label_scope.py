# SPDX-License-Identifier: Apache-2.0
"""Public result-label claims identify the surface that returns the envelope."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", [
    "README.md", "web/content/guide.md", "web/content/faq.md", "site/docs/faq.md",
    "site/docs/index.md", "site/docs/llms.txt",
])
def test_general_result_label_claims_name_the_mcp_server(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    paragraphs = text.split("\n\n")
    claims = [p for p in paragraphs if not p.lstrip().startswith("|")
              and "every result" in p.lower() and "label" in p.lower()]
    assert claims
    assert all("MCP server" in p for p in claims), name
    assert "labels every result for" not in text


@pytest.mark.parametrize("name", ["README.md", "web/content/faq.md", "site/docs/faq.md"])
def test_cli_output_is_distinguished_from_the_ok_envelope(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "command line prints the result alone" in text
