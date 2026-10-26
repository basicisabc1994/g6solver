# SPDX-License-Identifier: Apache-2.0
"""tools/check_sources.py re-fetches each source a register cites and compares its hash; here a transport double
stands in for the network, so the default suite stays offline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from g6.core.http import Client, Request, Response

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _tool():
    spec = importlib.util.spec_from_file_location("check_sources", _TOOLS / "check_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_sources = _tool()
TEXT = b"Section 9.9: a worked example."


def _register(tmp_path: Path) -> Path:
    path = tmp_path / "source_register.json"
    path.write_text(json.dumps({"entries": [{"id": "book-9.9", "url": "http://books.example.org/node9.html",
                                             "sha256": hashlib.sha256(TEXT).hexdigest()}]}), encoding="utf-8")
    return path


def _client(status: int, body: bytes = b"", headers=None) -> Client:
    def transport(request: Request) -> Response:
        return Response(status=status, headers=headers or {}, body=body, url=request.url)

    return Client(resolver=lambda host, port: ["93.184.216.34"], transport=transport, min_interval=0.0)


def test_every_committed_register_is_found() -> None:
    found = [p.relative_to(_TOOLS.parent).as_posix() for p in check_sources.registers()]
    assert "src/g6/embodied/_motion/data/source_register.json" in found


def test_an_unchanged_source_passes_and_a_changed_one_fails_the_check(tmp_path) -> None:
    paths = [_register(tmp_path)]
    assert check_sources.check(paths, _client(200, TEXT))[0]["status"] == "unchanged"
    assert check_sources.main(["--check"], client=_client(200, TEXT), paths=paths) == 0
    changed = check_sources.check(paths, _client(200, TEXT + b" Revised."))[0]
    assert changed["status"] == "changed" and changed["sha256"] != hashlib.sha256(TEXT).hexdigest()
    assert check_sources.main(["--check"], client=_client(200, b"other"), paths=paths) == 1


def test_a_dead_link_or_a_403_is_reported_never_taken_as_unchanged(tmp_path) -> None:
    paths = [_register(tmp_path)]
    for status in (404, 403):
        row = check_sources.check(paths, _client(status))[0]
        assert row["status"] == "unreachable" and str(status) in row["detail"]


def test_a_redirect_to_a_private_address_is_refused(tmp_path) -> None:
    paths = [_register(tmp_path)]
    row = check_sources.check(paths, _client(302, headers={"location": "http://127.0.0.1/node9.html"}))[0]
    assert row["status"] == "unreachable" and "G6_E_HTTP_REFUSED" in row["detail"]


# --- entries held in the owner's corpus (hash_scope corpus_file): the hash is of the corpus file, not of a page ------

CORPUS_TEXT = b"Section 6.2: friction, and a worked example."


def _corpus_register(tmp_path: Path) -> Path:
    path = tmp_path / "source_register.json"
    path.write_text(json.dumps({"entries": [{"id": "book-6.2", "url": "https://books.example.org/book",
                                             "hash_scope": "corpus_file", "file_name": "book.md",
                                             "sha256": hashlib.sha256(CORPUS_TEXT).hexdigest()}]}), encoding="utf-8")
    return path


def _no_fetch() -> Client:
    def transport(request: Request) -> Response:
        raise AssertionError(f"a corpus-held entry was fetched: {request.url}")

    return Client(resolver=lambda host, port: ["93.184.216.34"], transport=transport, min_interval=0.0)


def test_a_corpus_held_entry_is_not_re_fetched_and_says_so(tmp_path) -> None:
    # Removing check()'s corpus_file branch re-fetches the publisher's page, whose bytes never match the file's hash.
    paths = [_corpus_register(tmp_path)]
    row = check_sources.check(paths, _no_fetch())[0]
    assert row["status"] == "held" and row["detail"] == "held in the owner's corpus; not re-fetched"
    assert check_sources.main(["--check"], client=_no_fetch(), paths=paths) == 0


def test_a_corpus_held_entry_is_rehashed_when_the_corpus_is_given(tmp_path) -> None:
    # Removing the re-hash of the file found under --corpus leaves a changed corpus file unreported.
    folder = tmp_path / "corpus" / ".area" / "markdown"
    folder.mkdir(parents=True)
    (folder / "book.md").write_bytes(CORPUS_TEXT)
    paths, corpus = [_corpus_register(tmp_path)], tmp_path / "corpus"
    assert check_sources.check(paths, _no_fetch(), corpus=corpus)[0]["status"] == "unchanged"
    (folder / "book.md").write_bytes(CORPUS_TEXT + b" Revised.")
    changed = check_sources.check(paths, _no_fetch(), corpus=corpus)[0]
    assert changed["status"] == "changed" and changed["sha256"] != hashlib.sha256(CORPUS_TEXT).hexdigest()
    assert check_sources.main(["--check", "--corpus", str(corpus)], client=_no_fetch(), paths=paths) == 1
    (folder / "book.md").unlink()
    gone = check_sources.check(paths, _no_fetch(), corpus=corpus)[0]
    assert gone["status"] == "held" and "book.md not found" in gone["detail"]


def test_the_committed_corpus_entries_of_the_motion_and_bayesian_registers_take_the_corpus_path() -> None:
    # The same branch serves both registers' corpus entries: none is fetched, every other entry is.
    fetched: list[str] = []

    def transport(request: Request) -> Response:
        fetched.append(request.url)
        return Response(status=200, headers={}, body=TEXT, url=request.url)

    client = Client(resolver=lambda host, port: ["93.184.216.34"], transport=transport, min_interval=0.0)
    paths = [p for p in check_sources.registers() if p.parent.parent.name in ("_motion", "adapt_bayesian")]
    assert len(paths) == 2
    entries = [e for p in paths for e in json.loads(p.read_text(encoding="utf-8"))["entries"]]
    held = [e for e in entries if e.get("hash_scope") == "corpus_file"]
    rows = check_sources.check(paths, client)
    assert {r["id"] for r in rows if r["status"] == "held"} == {e["id"] for e in held}
    assert {"openstax-up1-2016-6.2", "bolstad-14"} <= {e["id"] for e in held}
    assert len(fetched) == len(entries) - len(held)
