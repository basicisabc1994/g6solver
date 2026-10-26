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
    for path in paths:
        for entry in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            row = {"register": _where(path), "id": entry["id"], "url": entry["url"]}
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
    rows = check(registers() if paths is None else paths, client or Client(), corpus=corpus)
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
