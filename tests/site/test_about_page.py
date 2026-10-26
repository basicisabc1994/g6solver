# SPDX-License-Identifier: Apache-2.0
"""The About page says what G6 does now (owner, 2026-10-20: the "No fine-tuning ... a human diagnoses failures and
prescribes fixes" paragraph revised): it engineers itself, a person approves each change, and results vary."""
from __future__ import annotations

import html
import re


def test_about_says_g6_engineers_itself_with_a_person_approving(client):
    page = client.get("/about/").content.decode()
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))
    for gone in ("No fine-tuning", "No retraining", "No model weights touched", "has not been measured yet",
                 "a human diagnoses failures and prescribes fixes"):
        assert gone not in text, gone
    assert "G6 engineers itself, and you stay in charge." in text
    assert "a person approves it" in text and "Results will vary with your task and the data you have" in text
    assert 'href="/research/#reading"' in page
