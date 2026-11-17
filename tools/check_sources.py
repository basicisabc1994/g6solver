# SPDX-License-Identifier: Apache-2.0
"""Re-fetch every source a committed source register cites and compare its SHA-256 (the vetting protocol's check).

    python tools/check_sources.py           # print each entry: unchanged, changed, unreachable or held
    python tools/check_sources.py --check   # exit 1 if any entry changed or cannot be fetched
    python tools/check_sources.py --corpus <folder>   # also re-hash the corpus files held under <folder>

A register is any ``source_register.json`` under ``src/g6``; each entry names a URL and the SHA-256 of the text
retrieved on its ``retrieved`` date. This reaches the network, through ``g6.core.http`` (private addresses refused at
every redirect, a timeout, a failure reported as a failure), so it runs weekly in the ``sources`` workflow and never in
the default test suite, which checks the registers offline. A change is a prompt for a person: re-read the clause,
update the entry and its hash, and review the checks that rest on it.

An entry whose ``hash_scope`` is ``corpus_file`` hashes a file in the owner's corpus, not the page at its URL, so it is
never re-fetched: it is reported ``held``, and with ``--corpus`` the file named by its ``file_name`` is found under
that folder and re-hashed (``unchanged`` or ``changed``; ``held`` again when it is not there).

The ACT-R register uses ``sources`` keyed by source id, alongside ``component`` and ``rules``. Its ``file_sha256``
hashes the retrieved file at ``url``, or the held file named by ``corpus_file`` (which need not have a URL). Rules'
``clause_sha256`` hashes extracted clauses, not fetched bytes, and is not used here. Unknown shapes are errors.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def registers(root: Path = ROOT) -> list[Path]:
    """Every committed source register under ``src/g6``."""
    return sorted((root / "src" / "g6").rglob("source_register.json"))


def _where(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


HELD = "held in the owner's corpus; not re-fetched"


def _entries(path: Path) -> list[dict[str, Any]]:
    """Read either committed register shape; reject missing fetch/hash data before any request."""
    def invalid(detail: str) -> ValueError:
        return ValueError(f"invalid source register {_where(path)}: {detail}")

    try:
        register = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise invalid(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(register, dict) or ("entries" in register) == ("sources" in register):
        raise invalid("expected entries (a list) or component/sources/rules (sources keyed by id), exclusively")
    if "entries" in register:
        entries = register["entries"]
        if not isinstance(entries, list):
            raise invalid("entries must be a list")
    else:
        if (not isinstance(register.get("component"), str) or not register["component"]
                or not isinstance(register.get("sources"), dict) or not isinstance(register.get("rules"), list)):
            raise invalid("expected component (a name), sources (an object keyed by id) and rules (a list)")
        entries = []
        for ident, source in register["sources"].items():
            if not isinstance(source, dict):
                raise invalid(f"source {ident!r} must be an object")
            entry = {"id": ident, "url": source.get("url", ""), "sha256": source.get("file_sha256")}
            if "corpus_file" in source:
                entry.update(hash_scope="corpus_file", file_name=source["corpus_file"])
            entries.append(entry)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise invalid("each source must have a nonempty id")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise invalid(f"source {entry['id']!r} needs a SHA-256 file hash (sha256 or file_sha256)")
        # Older entries may identify a held source without naming its corpus file; _held reports that limitation.
        field = "file_name" if entry.get("hash_scope") == "corpus_file" else "url"
        if (field == "url" or field in entry) and (not isinstance(entry.get(field), str) or not entry[field]):
            raise invalid(f"source {entry['id']!r} needs {field}")
        if not isinstance(entry.get("url", ""), str):
            raise invalid(f"source {entry['id']!r} has an invalid url")
    return entries


def _held(entry: dict[str, Any], corpus: Path | None) -> dict[str, str]:
    """A corpus-held entry's row: re-hashed when its file is found under ``corpus``, otherwise ``held``."""
    name = str(entry.get("file_name", ""))
    found = next((p for p in sorted(corpus.rglob(name)) if p.is_file()), None) if corpus and name else None
    if found is None:
        return {"status": "held", "detail": HELD + (f" ({name or 'no file_name'} not found under {corpus})"
                                                    if corpus else "")}
    digest = hashlib.sha256(found.read_bytes()).hexdigest()
    return {"status": "unchanged" if digest == entry["sha256"] else "changed", "sha256": digest}


def check(paths: list[Path], client: Any, corpus: Path | None = None) -> list[dict[str, str]]:
    """One row per register entry: ``unchanged``, ``changed`` (with the hash now served) or ``unreachable``; or, for
    an entry held in the owner's corpus, ``held`` (or its file's re-hash when ``corpus`` holds it)."""
    from g6.core.http import HTTPError

    rows = []
    registered = [(path, _entries(path)) for path in paths]
    for path, entries in registered:
        for entry in entries:
            row = {"register": _where(path), "id": entry["id"], "url": entry.get("url", "")}
            if entry.get("hash_scope") == "corpus_file":
                rows.append({**row, **_held(entry, corpus)})
                continue
            try:
                body = client.get(entry["url"]).body
            except HTTPError as exc:
                row.update(status="unreachable", detail=str(exc))
            else:
                digest = hashlib.sha256(body).hexdigest()
                row.update(status="unchanged" if digest == entry["sha256"] else "changed", sha256=digest)
            rows.append(row)
    return rows


def main(argv: list[str], client: Any = None, paths: list[Path] | None = None) -> int:
    from g6.core.http import Client

    corpus = Path(argv[argv.index("--corpus") + 1]) if "--corpus" in argv[:-1] else None
    try:
        rows = check(registers() if paths is None else paths, client or Client(), corpus=corpus)
    except ValueError as exc:
        print(exc)
        return 1
    for r in rows:
        print(f"{r['status']:<11} {r['id']}  {r['url']}" + (f"  ({r['detail']})" if r.get("detail") else ""))
    held = [r for r in rows if r["status"] == "held"]
    if held:
        print(f"{len(held)} entries are held in the owner's corpus and were not re-fetched; give --corpus <folder> "
              "to re-hash their files")
    bad = [r for r in rows if r["status"] not in ("unchanged", "held")]
    if bad:
        print(f"{len(bad)} of {len(rows)} sources changed or could not be fetched: re-read each one and update its "
              "register entry")
    return 1 if bad and "--check" in argv else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
