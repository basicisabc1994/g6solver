# SPDX-License-Identifier: Apache-2.0
"""The FAQ: why G6 exists first, then what it is (its two parts) and how it differs (owner, 2026-10-20). The results
are Research's to give: the FAQ reads them as a worked example and points there, and it says nothing of the earlier
G6."""
from __future__ import annotations

import html
import re
from pathlib import Path

from pages import catalogue


def _text(client) -> str:
    page = client.get("/faq/").content.decode()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def test_the_faq_reads_the_results_as_a_worked_example(client):
    text = _text(client)
    assert "worked example" in text and "proof of concept" not in text.lower()
    assert "Results will vary with your task and the data you have" in text
    assert "That has not been shown" not in text and "Not in general" not in text
    assert not re.search(r"\bV\d+b?\b|\bV2-F1\b|inconclusive", text)


def test_the_faq_leaves_the_results_to_research_and_never_mentions_the_earlier_g6(client):
    # owner, 2026-10-20: "The what is validated section should be removed this already covered under research and we
    # should not make any references to the old system"
    page = client.get("/faq/").content.decode()
    text = _text(client)
    assert "What is validated" not in text and "What has been validated" not in text
    for figure in ("none of 174", "29 of 30", "22 to 81", "57 against 64"):          # each was a study's figure here
        assert figure not in text, figure
    assert not re.search(r"earlier G6|earlier benchmark|audited|ported into this library|retired", text)
    assert 'href="/research/"' in page


def test_the_faq_opens_with_why_then_says_what_g6_is_and_how_it_differs(client):
    page = client.get("/faq/").content.decode()
    text = _text(client)
    headings = re.findall(r"<h2[^>]*>(.*?)</h2>", page)
    assert headings[0] == "Why G6" and headings[1] == "The basics", headings
    questions = re.findall(r"<h3[^>]*>(.*?)</h3>", page)
    assert questions[:3] == ["Why does G6 exist?", "Which limits does it answer, and how?", "What is G6?"]
    for part in ("The meta-harness", "The components", "batteries-included", "250 million tokens",
                 "a periodic table of intelligence", "Explicitly, not implicitly", "From plain words",
                 "With a model of itself", "nothing in its design caps how far it can go", "without training a model"):
        assert part in text, part
    assert 'href="/#limits"' in page and 'href="/research/#theory"' in page


def _plain(cell: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell))).strip()


def _table(page: str, question: str) -> list[list[str]]:
    """The table that answers ``question``: each body row's cells, as HTML."""
    answer = page.split(f">{question}</h3>", 1)[1].split("</table>", 1)[0]
    assert "<h3" not in answer, question                     # the table is this answer's, not a later one's
    body = answer.split("<tbody>", 1)[1]
    return [re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL) for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)]


def _refused_as_said(cell: str, name: str) -> bool:
    """Whether a row's "Ops refused over MCP" cell names exactly the ops its component marks sensitive: ``none``,
    ``every op``, ``every op but`` the ones it names, or the ones it names."""
    from g6.core import discovery
    from g6.core.registry import get_registry

    sensitive = set(get_registry().describe(name).get("sensitive_ops") or ())
    said, named = _plain(cell), set(re.findall(r"<code>(\w+)</code>", cell))
    if said == "none":
        return not sensitive
    if said == "every op":
        return bool(sensitive) and sensitive == set(discovery.ops(name))
    if said.startswith("every op but "):
        ops = set(discovery.ops(name))
        return bool(named) and named <= ops and sensitive == ops - named
    return bool(named) and named == sensitive


def test_the_core_table_is_the_registrys(client):
    # SITE_PASS item 1 (owner, 2026-10-21: "under FAQ I don't want massive blocks of text ... formatted as a table"):
    # every core component once, in the registry's order, in the words the home page and the components page use,
    # with whether it is over MCP and the ops refused there. Fails while the FAQ answers with a paragraph of names
    page = client.get("/faq/").content.decode()
    text = _text(client)
    core = catalogue.components()
    rows = _table(page, "Which components are in the core harness?")
    assert [_plain(r[0]) for r in rows] == [c["name"] for c in core]
    for (name, does, mcp, refused), row in zip(rows, core):
        assert _plain(does) == catalogue.PLAIN[row["name"]][1], row["name"]
        assert _plain(mcp) == ("MCP" if row["mcp"] else "Python-only"), row["name"]
        assert _refused_as_said(refused, row["name"]) if row["mcp"] else _plain(refused) == "—", row["name"]
    over_mcp = sum(1 for r in core if r["mcp"])
    assert f"There are {len(core)} in the core harness" in text
    assert all(e["mcp"] for e in catalogue.library_extensions())
    assert f"Every extension in the library can be called over MCP, as can {over_mcp} of the {len(core)} core " \
           f"components; the other {len(core) - over_mcp} are Python-only" in text
    # owner, 2026-10-20: sensitive ops may run over MCP when the person who runs the server lists them
    assert "unless the person who runs the server lists it in G6_MCP_SENSITIVE" in text


def test_the_extension_table_is_the_library(client):
    # SITE_PASS items 1, 2 and 5: every extension in the library once, and nothing else, by family as the components
    # page lists them, in its own package's words, with the ops refused over MCP and, for a vetted one, a link to the
    # certificate beside its code. Fails when an extension joins or leaves the library and the FAQ does not follow
    from g6.core import vetting
    from pages import evidence, extensions

    oss = Path(__file__).resolve().parents[2]
    page = client.get("/faq/").content.decode()
    text = _text(client)
    library = [e for e in extensions.extensions() if e["in_library"]]
    assert {e["name"] for e in library} == {e["name"] for e in catalogue.library_extensions()}
    rows = _table(page, "Which extensions are in the library?")
    assert [_plain(r[0]) for r in rows] == [e["name"] for e in library]
    for (name, family, does, mcp, refused, certificate), row in zip(rows, library):
        assert _plain(family) == extensions.FAMILIES[row["family"]], row["name"]
        assert _plain(does) == row["summary"], row["name"]
        assert _plain(mcp) == ("MCP" if row["mcp"] else "Python-only"), row["name"]
        assert _refused_as_said(refused, row["name"]), row["name"]
        beside = "src/" + row["package"].replace(".", "/") + "/certificate/CERTIFICATE.md"
        if vetting.declared(row["package"]) is not None:
            assert f'href="{evidence.REPO}{beside}"' in certificate and (oss / beside).is_file(), row["name"]
        else:
            assert _plain(certificate) == "—", row["name"]
    assert f"{len(library)} extensions are in the library" in text


def test_the_faq_says_what_a_certificate_holds_and_what_it_does_not(client):
    # SITE_PASS item 5 (owner, 2026-10-22: "certificates should accompany code and should be mentioned on the G6
    # website"; DECISIONS #39: no qualified person's review). Fails until the FAQ answers it, or if it ever calls a
    # component certified correct
    text = _text(client)
    answer = text.split("What is a component's certificate?", 1)[1].split("How do components compose?", 1)[0]
    for part in ("SHA-256", "worked examples", "second route", "seeded defects", "a different model",
                 "what was not checked", "certificate/CERTIFICATE.md", "No qualified person has reviewed these "
                 "components", "cites it and keeps the SHA-256 of the quoted passage, not the passage"):
        assert part in answer, part
    assert "certified correct" not in text.lower() and "proves the code is correct" not in text.lower()


def test_the_sandbox_answers_say_what_g6_confines_and_where(client):
    # g6.core.confine runs untrusted code in a Windows AppContainer once the one-time access grant is made, and
    # unconfined elsewhere (CORE_SANDBOX-01: a warning, never a restriction). Fails while the FAQ or the Security page
    # says nothing in G6 is an operating-system sandbox, or leaves out the warning in confine's own words
    from g6.core.confine import UNCONFINED_WARNING

    for path in ("/faq/", "/security/"):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", client.get(path).content.decode())).split())
        assert "nothing in G6 is an operating-system sandbox" not in text and "G6 is not a sandbox" not in text, path
        assert "AppContainer" in text and UNCONFINED_WARNING in text, path
