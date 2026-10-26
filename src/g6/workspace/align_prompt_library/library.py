# SPDX-License-Identifier: Apache-2.0
"""The versioned library: prompts, specifications and evaluation sets, append-only on ``g6.core.ledger``.

Each version of a named record is one ledger entry (kind ``version``, id ``<name>@<version>``) holding its kind, its
content, the SHA-256 of that content (its **digest**, the address a run cites, as ``g6.metaharness.artifacts`` addresses
content), what the caller declared about it (``declared``: a claim, never checked) and the time. Nothing is ever
overwritten or removed:

* ``put`` adds a version (the same content and declarations as the latest add none);
* ``rollback`` adds a version holding an earlier version's content, never a rewrite;
* ``retire`` adds a tombstone: the name answers no plain read until a new ``put``, and every version stays readable by
  its number or digest.

The ledger's chain (each entry's hash and the one before it) and this library's own rules, checked on every open, find
an entry edited, removed, added or reordered: version numbers run 1, 2, 3 for each name with no gap, a name keeps its
kind, each digest is the SHA-256 of its content, and a rollback's digest is the one of the version it names. A library
that does not check out answers nothing (``G6_E_LIBRARY_CORRUPT``). Appends happen under the family's short exclusive
lock, beside the ledger file.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError
from g6.core.resources import RESOURCE_LIMIT
from g6.core.storage import state_path
from g6.workspace import _envelope as E
from g6.workspace._coordination import Coordination

KINDS = ("prompt", "spec", "eval_set")
ACTIONS = ("put", "rollback", "retire")
ENTRY = "version"
#: Limits: one record's content, its declarations, and the library file.
MAX_CONTENT = 200_000
MAX_RENDERED_CHARS = 1_000_000
MAX_DECLARED = 20_000
MAX_LIBRARY_BYTES = 50 * 1024 * 1024
#: ``{{name}}`` placeholders, as the archive's templates wrote them.
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def digest_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def placeholders(content: str) -> list[str]:
    """The template's placeholder names, in the order they first appear."""
    return list(dict.fromkeys(PLACEHOLDER.findall(content)))


def _ident(name: str, version: int) -> str:
    return f"{name}@{version}"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in name:
        raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, f"name {name!r} is not a plain name (letters, digits, '.', "
                                                          "'_' and '-', at most 100)")
    return name


def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    """The library's own rules for one entry, given the entries before it (``g6.core.ledger``'s validate hook)."""
    if kind != ENTRY:
        return [f"{kind!r} is not an entry kind of the library"]
    name, version, action = body.get("name"), body.get("version"), body.get("action")
    if not isinstance(name, str) or not isinstance(version, int) or ident != _ident(name, version) or version < 1:
        return [f"{ident!r} does not match its name and version"]
    if action not in ACTIONS or body.get("kind") not in KINDS:
        return [f"{ident}: unknown action or kind"]
    before = seen.get((ENTRY, _ident(name, version - 1)))
    if version > 1 and before is None:
        return [f"{ident}: version {version - 1} of {name!r} is missing (versions run 1, 2, 3 with no gap)"]
    if before is not None and before.get("kind") != body["kind"]:
        return [f"{ident}: {name!r} was a {before.get('kind')}, and a name keeps its kind"]
    content, digest = body.get("content"), body.get("digest")
    if action == "retire":
        return [] if content == "" and digest == "" else [f"{ident}: a tombstone holds no content"]
    if not isinstance(content, str) or digest != digest_of(content):
        return [f"{ident}: its digest is not the SHA-256 of its content"]
    if action == "rollback":
        source = seen.get((ENTRY, _ident(name, body.get("from_version", 0))))
        if source is None or source.get("digest") != digest:
            return [f"{ident}: a rollback holds the content of the version it names"]
    return []


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _shown(body: dict, *, content: bool = True) -> dict[str, Any]:
    out = {"name": body["name"], "version": body["version"], "kind": body["kind"], "action": body["action"],
           "digest": body["digest"], "at": body["at"], "from_version": body.get("from_version"),
           "declared": body.get("declared", {}), "placeholders": body.get("placeholders", [])}
    if content:
        out["content"] = body["content"]
    return out


class Library:
    """The versioned library in one ledger file (by default ``workspace/align_prompt_library/library.jsonl`` under
    the G6 home)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else state_path("workspace", "align_prompt_library",
                                                                     "library.jsonl")

    # --- reading ------------------------------------------------------------------------------------------------------

    def _open(self) -> ChainLedger:
        try:
            return ChainLedger(self.path, validate=_rules)
        except LedgerCorrupt as exc:
            raise E.WorkspaceRefused(E.LIBRARY_CORRUPT, f"the library's record does not check out ({exc}); nothing "
                                                        "was read or stored") from None

    def _versions(self, ledger: ChainLedger) -> dict[str, list[dict]]:
        found: dict[str, list[dict]] = {}
        for entry in ledger.entries():
            found.setdefault(entry["body"]["name"], []).append(entry["body"])
        return found

    def _named(self, versions: dict[str, list[dict]], name: str) -> list[dict]:
        listed = versions.get(_check_name(name))
        if not listed:
            raise E.WorkspaceRefused(E.LIBRARY_NOT_FOUND, f"the library holds nothing named {name!r}")
        return listed

    def get(self, name: str = "", *, version: int | None = None, digest: str = "") -> dict[str, Any]:
        """One version: by ``name`` (the latest), by ``name`` and ``version`` or ``digest``, or by ``digest`` alone
        (every name and version holding that content is listed in ``also``). A version or digest the library does not
        hold is refused (``LIBRARY_UNKNOWN_VERSION``), never answered with the latest; so is a tombstone, and a plain
        read of a retired name (``LIBRARY_RETIRED``)."""
        if digest and not _SHA256.fullmatch(digest):
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, "digest is the 64-character SHA-256 a put returned")
        versions = self._versions(self._open())
        if not name:
            if not digest:
                raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, "get needs a name or a digest")
            held = [b for listed in versions.values() for b in listed if b["digest"] == digest]
            if not held:
                raise E.WorkspaceRefused(E.LIBRARY_UNKNOWN_VERSION, f"no version in the library has digest {digest}")
            held.sort(key=lambda b: (b["at"], b["name"], b["version"]))
            return {**_shown(held[0]), "also": [{"name": b["name"], "version": b["version"]} for b in held[1:]]}
        listed = self._named(versions, name)
        if version is None and not digest:
            latest = listed[-1]
            if latest["action"] == "retire":
                readable = ", ".join(str(b["version"]) for b in listed if b["action"] != "retire")
                raise E.WorkspaceRefused(E.LIBRARY_RETIRED, f"{name!r} was retired at version {latest['version']}; "
                                                            f"versions {readable} are still readable by number or "
                                                            "digest")
            return {**_shown(latest), "also": []}
        held = [b for b in listed if (version is None or b["version"] == version)
                and (not digest or b["digest"] == digest) and b["action"] != "retire"]
        if not held:
            asked = " and ".join(p for p in (f"version {version}" if version else "", f"digest {digest}" if digest
                                             else "") if p)
            raise E.WorkspaceRefused(E.LIBRARY_UNKNOWN_VERSION, f"{name!r} has no {asked} (or it is the tombstone); "
                                                                "history lists what it holds")
        return {**_shown(held[0]), "also": [{"name": b["name"], "version": b["version"]} for b in held[1:]]}

    def history(self, name: str) -> list[dict[str, Any]]:
        """Every version of ``name``, oldest first, tombstones included; no content."""
        return [_shown(b, content=False) for b in self._named(self._versions(self._open()), name)]

    def search(self, query: str, *, kind: str = "", include_retired: bool = False, limit: int = 50
               ) -> list[dict[str, Any]]:
        """The latest version of each name whose name, content or declarations contain ``query`` (any case); a retired
        name only with ``include_retired`` (then its last version before the tombstone)."""
        if kind and kind not in KINDS:
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, f"kind is one of {', '.join(KINDS)}")
        needle = query.casefold()
        found = []
        for name, listed in sorted(self._versions(self._open()).items()):
            latest = listed[-1]
            if latest["action"] == "retire":
                if not include_retired or len(listed) < 2:
                    continue
                latest = listed[-2]
            if kind and latest["kind"] != kind:
                continue
            text = " ".join((name, latest["content"], json.dumps(latest.get("declared", {}), ensure_ascii=False)))
            if needle in text.casefold():
                found.append({**_shown(latest, content=False), "retired": listed[-1]["action"] == "retire"})
        return found[:limit]

    def render(self, name: str, *, version: int | None = None, digest: str = "",
               values: Mapping[str, str] | None = None) -> dict[str, Any]:
        """A version's template with ``{{name}}`` placeholders filled from ``values``, in one pass (a value is never
        expanded again). Strict: a placeholder with no value (``LIBRARY_MISSING_VALUE``) or a value with no placeholder
        (``LIBRARY_EXTRA_VALUE``) is refused. Says the digest of the template used and of the text made."""
        record = self.get(name, version=version, digest=digest)
        given = dict(values or {})
        wanted = placeholders(record["content"])
        missing = [p for p in wanted if p not in given]
        if missing:
            raise E.WorkspaceRefused(E.LIBRARY_MISSING_VALUE, f"{name!r} version {record['version']} has no value "
                                                              f"for {', '.join(missing)}")
        extra = sorted(set(given) - set(wanted))
        if extra:
            raise E.WorkspaceRefused(E.LIBRARY_EXTRA_VALUE, f"{name!r} version {record['version']} has no placeholder "
                                                            f"for {', '.join(extra)}")
        if not all(isinstance(v, str) for v in given.values()):
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, "every value is text")
        expanded_size = len(record["content"]) + sum(
            len(given[m.group(1)]) - (m.end() - m.start()) for m in PLACEHOLDER.finditer(record["content"]))
        if expanded_size > MAX_RENDERED_CHARS:
            raise E.WorkspaceRefused(RESOURCE_LIMIT, f"rendered text exceeds {MAX_RENDERED_CHARS} characters")
        text = PLACEHOLDER.sub(lambda m: given[m.group(1)], record["content"])
        return {"name": record["name"], "version": record["version"], "kind": record["kind"],
                "template_digest": record["digest"], "text": text, "rendered_digest": digest_of(text),
                "placeholders": wanted}

    # --- adding versions ----------------------------------------------------------------------------------------------

    def _append(self, body_for: Any) -> dict[str, Any]:
        """Open the ledger under the short lock, build the new entry's body from the versions there (``body_for``
        returns it, or a finished reply when nothing is to be added) and append it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Coordination(self.path.parent).mutex():
            if self.path.exists() and self.path.stat().st_size > MAX_LIBRARY_BYTES:
                raise E.WorkspaceRefused(RESOURCE_LIMIT, f"the library file is over {MAX_LIBRARY_BYTES} bytes; "
                                                         "nothing more is stored")
            ledger = self._open()
            body = body_for(self._versions(ledger))
            if body.get("unchanged"):
                return body
            try:
                ledger.append(ENTRY, _ident(body["name"], body["version"]), body)
            except LedgerError as exc:
                raise E.WorkspaceRefused(E.LIBRARY_CORRUPT, f"the library refused the entry ({exc}); nothing was "
                                                            "stored") from None
        return {**_shown(body, content=False), "unchanged": False}

    def put(self, kind: str, name: str, content: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Add a version of ``name``: returns its ``version`` and ``digest``. ``metadata`` is kept as what the caller
        declared (a review, a test result, an author: claims, never checked). The same content and declarations as the
        latest version add nothing (``unchanged``)."""
        if kind not in KINDS:
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, f"kind is one of {', '.join(KINDS)}, not {kind!r}")
        _check_name(name)
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT:
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, f"content is text of 1 to {MAX_CONTENT} characters")
        declared = dict(metadata or {})
        try:
            if len(json.dumps(declared, allow_nan=False)) > MAX_DECLARED:
                raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, f"metadata is at most {MAX_DECLARED} characters "
                                                                  "of JSON")
        except (TypeError, ValueError):
            raise E.WorkspaceRefused(E.LIBRARY_INPUT_INVALID, "metadata is a JSON object") from None

        def body_for(versions: dict[str, list[dict]]) -> dict[str, Any]:
            listed = versions.get(name, [])
            if listed and listed[0]["kind"] != kind:
                raise E.WorkspaceRefused(E.LIBRARY_KIND_MISMATCH, f"{name!r} is a {listed[0]['kind']}, not a {kind}")
            latest = listed[-1] if listed else None
            if latest and latest["action"] != "retire" and latest["digest"] == digest_of(content) \
                    and latest.get("declared", {}) == declared:
                return {**_shown(latest, content=False), "unchanged": True}
            return {"name": name, "version": len(listed) + 1, "kind": kind, "action": "put", "content": content,
                    "digest": digest_of(content), "declared": declared, "placeholders": placeholders(content),
                    "from_version": None, "at": _now()}

        return self._append(body_for)

    def rollback(self, name: str, to_version: int) -> dict[str, Any]:
        """Add a version holding ``to_version``'s content and declarations: a rollback is a new version, never a
        rewrite of the ones after it."""

        def body_for(versions: dict[str, list[dict]]) -> dict[str, Any]:
            listed = self._named(versions, name)
            source = next((b for b in listed if b["version"] == to_version and b["action"] != "retire"), None)
            if source is None:
                raise E.WorkspaceRefused(E.LIBRARY_UNKNOWN_VERSION, f"{name!r} has no version {to_version} to roll "
                                                                    "back to")
            return {**source, "version": len(listed) + 1, "action": "rollback", "from_version": to_version,
                    "at": _now()}

        return self._append(body_for)

    def retire(self, name: str) -> dict[str, Any]:
        """Add a tombstone: the name answers no plain read until a new ``put``; every version stays readable."""

        def body_for(versions: dict[str, list[dict]]) -> dict[str, Any]:
            listed = self._named(versions, name)
            if listed[-1]["action"] == "retire":
                return {**_shown(listed[-1], content=False), "unchanged": True}
            return {"name": name, "version": len(listed) + 1, "kind": listed[0]["kind"], "action": "retire",
                    "content": "", "digest": "", "declared": {}, "placeholders": [], "from_version": None,
                    "at": _now()}

        return self._append(body_for)


__all__ = ["ACTIONS", "KINDS", "MAX_CONTENT", "MAX_DECLARED", "MAX_LIBRARY_BYTES", "MAX_RENDERED_CHARS", "PLACEHOLDER", "Library",
           "digest_of", "placeholders"]
