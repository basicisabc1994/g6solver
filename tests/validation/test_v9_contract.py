# SPDX-License-Identifier: Apache-2.0
"""V9's review-contract splitter, on statements written for these tests.

Development fixtures only: none of this text is V9 evaluation data, and no benchmark task was read to write it."""
from __future__ import annotations

from patch_review_utility import contract

STATEMENT = "Parsing a date with a trailing space fails.\n\nSteps: call parse('2026-01-01 ').\n"
LISTED = ("The parser must accept these forms:\n"
          "- A date followed by spaces is parsed as the date.\n"
          "- An empty string raises ValueError,\n"
          "  with a message naming the input.\n"
          "    - nested detail stays with its item\n"
          "* A date with a time keeps the time.\n"
          "1. Numbered items are items too.\n")
INTERFACE = ("Type: Function\nName: parse_loose\nPath: dates.py\n\n"
             "Type: Class\nName: DateError\nPath: dates.py\n")


def test_the_statement_is_one_requirement_and_each_listed_item_another():
    got = contract.split(STATEMENT, LISTED)
    assert [r["id"] for r in got] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert got[0] == {"id": "R1", "text": STATEMENT.strip(), "source": "statement"}
    assert got[1]["text"] == "The parser must accept these forms:" and got[1]["source"] == "requirements"
    assert got[2]["text"] == "A date followed by spaces is parsed as the date."
    assert got[3]["text"] == ("An empty string raises ValueError,\nwith a message naming the input.\n"
                              "- nested detail stays with its item")
    assert got[4]["text"] == "A date with a time keeps the time."
    assert got[5]["text"] == "Numbered items are items too."


def test_requirements_without_list_markers_split_into_paragraphs():
    got = contract.split("Fix it.", "First paragraph\ncontinues here.\n\n\nSecond paragraph.")
    assert [r["text"] for r in got[1:]] == ["First paragraph\ncontinues here.", "Second paragraph."]


def test_each_interface_block_is_a_requirement_and_a_no_new_interface_note_is_not():
    got = contract.split("Fix it.", "", INTERFACE)
    assert [(r["id"], r["source"]) for r in got] == [("R1", "statement"), ("R2", "interface"), ("R3", "interface")]
    assert got[2]["text"] == "Type: Class\nName: DateError\nPath: dates.py"
    assert contract.split("Fix it.", "", "No new interfaces are introduced.") == [
        {"id": "R1", "text": "Fix it.", "source": "statement"}]


def test_the_split_is_deterministic_and_drops_empty_and_repeated_text():
    one = contract.split(STATEMENT, LISTED + "\n- A date followed by spaces is parsed as the date.\n", INTERFACE)
    two = contract.split(STATEMENT, LISTED + "\n- A date followed by spaces is parsed as the date.\n", INTERFACE)
    assert one == two
    texts = [r["text"] for r in one]
    assert len(texts) == len(set(texts)) and all(texts)
    assert contract.split("  \n", "\n\n", "") == []
    assert contract.split("Fix it.\r\n", "- a\r\n- b\r\n") == contract.split("Fix it.\n", "- a\n- b\n")


def test_text_longer_than_the_review_limit_is_split_at_paragraphs_never_cut():
    paragraph = "x" * 15_000
    got = contract.split(f"{paragraph}\n\n{paragraph}")   # identical halves of one text are both kept
    assert [len(r["text"]) for r in got] == [15_000, 15_000] and [r["id"] for r in got] == ["R1", "R2"]
    unbroken = contract.split("y" * 45_000)
    assert "".join(r["text"] for r in unbroken) == "y" * 45_000
    assert all(len(r["text"]) <= contract.MAX_TEXT for r in got + unbroken)


def test_the_contract_carries_no_checks_and_the_rendering_numbers_every_requirement():
    got = contract.split(STATEMENT, LISTED, INTERFACE)
    assert all("checks" not in r for r in got)
    rendered = contract.render(got)
    assert rendered.startswith("R1. Parsing a date") and "\n\nR8. Type: Class" in rendered


def test_the_cited_checks_are_the_test_files_the_patch_adds_or_changes():
    paths = ["dates.py", "tests/test_dates.py", "tests/conftest.py", "pkg/dates_test.py", "docs/test_notes.md",
             "test_root.py"]
    assert contract.patch_tests(paths) == ["pkg/dates_test.py", "test_root.py", "tests/test_dates.py"]
    cited = contract.cite(contract.split("Fix it.", "- a"), ["tests/test_dates.py"])
    assert cited == [{"id": "R1", "text": "Fix it.", "checks": ["tests/test_dates.py"]},
                     {"id": "R2", "text": "a", "checks": ["tests/test_dates.py"]}]
