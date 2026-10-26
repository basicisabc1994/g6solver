# SPDX-License-Identifier: Apache-2.0
"""AustralianLegalCorpusBlock: the registry and MCP door (``g6_invoke``) to australian_legal_corpus.

``fetch`` reaches the network and writes about 9.4 GB, and ``remove`` deletes stored data, so both are sensitive: over
MCP they run only when the person who runs the server lists them in ``G6_MCP_SENSITIVE``, and each is a dry run unless
``dry_run`` is false. A real fetch goes ahead only on ``g6.action_gating``'s allow, through ``g6.core.http``'s
checked download. ``status``, ``register``, ``build``, ``search`` and ``get`` reach nothing outside G6's state folder
(``register`` reads the file it is given). Every reply is ``qualified-draft`` at best: the text is the corpus's
snapshot, collected on its date, never current law and never legal advice.
"""
from __future__ import annotations

import errno
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import g6.knowledge._reply as _reply
import g6.sources._gate as _gate
from g6.core import http
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.errors import unknown_op
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.core.storage import PathRefused, confined_path, state_path
from g6.sources.australian_legal_corpus import store
from g6.sources.australian_legal_corpus.corpus import (
    ATTRIBUTION,
    COUNTS,
    DOI,
    JURISDICTIONS,
    LEGISLATION,
    NOT_COVERED,
    PINNED,
    SOURCES,
    Pin,
    covered,
    jurisdiction,
    uncovered_named,
)

COMPONENT = "australian_legal_corpus"
#: The folder under G6's state root that holds the fetched file, the manifest and the index.
FOLDER = "australian_legal_corpus"
#: The time a fetch allows for the whole download (each read waits at most the client's timeout).
FETCH_DEADLINE = http.DEFAULT_DOWNLOAD_DEADLINE
SENSITIVE_OPS: tuple[str, ...] = ("fetch", "remove")
_SENSITIVE = {"fetch": " (sensitive: it reaches the network and writes about 9.4 GB)",
              "remove": " (sensitive: it deletes stored data)"}
_OPS: dict[str, str] = {
    "status": "status(): what is on disk: the pinned revision, the corpus file and whether its size and hash were "
              "checked, the index's records, filters and progress, the collection date; never reaches the network",
    "fetch": "fetch(dry_run): download the pinned corpus.jsonl from the publisher's Hugging Face dataset into G6's "
             "state folder, its size and SHA-256 checked as it streams; a dry run names the URL, revision, size, "
             "licence and the free space needed" + _SENSITIVE["fetch"],
    "register": "register(path): record a corpus.jsonl you downloaded yourself, where it is (never copied), if its "
                "size and SHA-256 equal the pin",
    "build": "build(types, jurisdictions, max_records, max_seconds): index passages of the corpus file in SQLite "
             "FTS5, legislation only unless asked, bounded per call and resumable; call again until complete",
    "search": "search(query, jurisdiction, types, k, match): the passages with your words, BM25-ranked, each with its "
              "citation, jurisdiction, type, link, version id, date and collection date; a jurisdiction the corpus "
              "lacks is refused",
    "get": "get(version_id, start): one document's text, at most 100,000 characters per call, with the same fields",
    "remove": "remove(what, dry_run): delete the index, and the corpus file if G6 downloaded it; a dry run unless "
              "dry_run is false" + _SENSITIVE["remove"],
}
_C = "G6_E_LEGAL_CORPUS_"
STATUS, DRY_RUN, HELD, FETCHED, PRESENT = (f"{_C}STATUS", f"{_C}DRY_RUN", f"{_C}GATE_HELD", f"{_C}FETCHED",
                                           f"{_C}PRESENT")
FETCH_FAILED, DISK_SHORT, REGISTERED, NOT_PINNED = (f"{_C}FETCH_FAILED", f"{_C}DISK_SHORT", f"{_C}REGISTERED",
                                                    f"{_C}NOT_PINNED_FILE")
BUILT, NO_CORPUS, FILTERS_DIFFER, INDEX_MISMATCH = (f"{_C}BUILT", f"{_C}NO_CORPUS", f"{_C}FILTERS_DIFFER",
                                                    f"{_C}INDEX_MISMATCH")
FOUND, NO_MATCH, NO_INDEX, NOT_COVERED_CODE, NOT_INDEXED = (f"{_C}PASSAGES", f"{_C}NO_MATCH", f"{_C}NO_INDEX",
                                                            f"{_C}JURISDICTION_NOT_COVERED", f"{_C}NOT_INDEXED")
DOCUMENT, NOT_FOUND, REMOVE_DRY_RUN, REMOVED, INPUT = (f"{_C}DOCUMENT", f"{_C}NOT_FOUND", f"{_C}REMOVE_DRY_RUN",
                                                       f"{_C}REMOVED", f"{_C}INPUT")
FTS5_UNAVAILABLE, STORAGE_FAILED = f"{_C}FTS5_UNAVAILABLE", f"{_C}STORAGE_FAILED"
_NEXT = "fetch it (or download it yourself and register it), then build the index"
_ACTIONS: dict[str, str] = {
    STATUS: "Read next_step: nothing was sent.",
    DRY_RUN: "Nothing was sent. With dry_run false the action gate decides; or download the file yourself with "
             "person_can_run and register it.",
    HELD: "Nothing was sent: the action gate holds the download for a person. A person can run person_can_run and "
          "then register the file.",
    FETCHED: "Build the index next.",
    PRESENT: "Nothing was sent: the checked file is already here. Build the index next.",
    FETCH_FAILED: "Nothing was kept. Read the reason; try again later, or download the file yourself and register it.",
    FTS5_UNAVAILABLE: "Use a Python whose sqlite3 includes FTS5, then call build again.",
    STORAGE_FAILED: "Check the corpus file is readable and G6 state is writable; free disk space if needed, then retry.",
    DISK_SHORT: "Free disk space, then ask again; what was built so far is kept.",
    REGISTERED: "Build the index next. Keep the file where it is: the index is built from it there.",
    NOT_PINNED: "Download the pinned revision (person_can_run) and register that file.",
    BUILT: "Call build again until complete is true; search works on what is indexed so far.",
    NO_CORPUS: "Fetch the file (or download it yourself and register it), then build.",
    FILTERS_DIFFER: "Remove the index (remove with what index) and build again with the filters you want, or build "
                    "with no filters to continue.",
    INDEX_MISMATCH: "Remove the index (remove with what index) and build it again from the pinned file.",
    FOUND: "Quote a passage with its citation, jurisdiction and collection date; confirm it at its url or the "
           "official register before relying on it. It is not current law and not legal advice.",
    NO_MATCH: "Try other words, match any, or a wider filter. Do not answer from memory: no passage is not evidence "
              "that the law says nothing.",
    NO_INDEX: "Fetch (or register) the corpus file, then build, then search.",
    NOT_COVERED_CODE: "Say the corpus does not cover that jurisdiction; do not answer with another jurisdiction's law "
                      "or from memory. Look in that jurisdiction's own official register.",
    NOT_INDEXED: "Build an index that includes it (remove with what index, then build with it).",
    store.TOO_BROAD: "Add words, or narrow the jurisdiction or the types.",
    store.UNREADABLE: "Remove the index (remove with what index) and build again.",
    DOCUMENT: "Read it as the corpus's snapshot on its collection date, not current law; confirm at its url or the "
              "official register. Not legal advice.",
    NOT_FOUND: "Use a version_id from a search hit.",
    REMOVE_DRY_RUN: "Nothing was deleted. With dry_run false the files listed are deleted.",
    REMOVED: "Fetch (or register) and build again to search.",
    INPUT: "Correct the request as the reason says.",
}

Kind = Literal["primary_legislation", "secondary_legislation", "bill", "decision"]


class AustralianLegalCorpusInput(BaseModel):
    """One op's parameters; fields no op has are refused (an approval among them)."""

    model_config = ConfigDict(extra="forbid")

    op: str = Field("help", max_length=40)
    path: str = Field("", max_length=1000)
    query: str = Field("", max_length=500)
    jurisdiction: str = Field("", max_length=60)
    jurisdictions: tuple[str, ...] = Field((), max_length=10)
    types: tuple[Kind, ...] = Field((), max_length=4)
    k: int = Field(8, ge=1, le=50)
    match: Literal["all", "any"] = "all"
    version_id: str = Field("", max_length=500)
    start: int = Field(0, ge=0)
    max_records: int = Field(store.DEFAULT_RECORDS, ge=1, le=store.MAX_RECORDS)
    max_seconds: float = Field(store.DEFAULT_SECONDS, gt=0, le=store.MAX_SECONDS, allow_inf_nan=False)
    what: Literal["all", "index"] = "all"
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @model_validator(mode="after")
    def _dry_run_ops(self) -> AustralianLegalCorpusInput:
        if self.op in {"register", "build"} and "dry_run" in self.model_fields_set:
            raise ValueError("dry_run is only accepted by fetch and remove")
        return self


def _free(path: Path) -> int:
    """Free bytes on the disk that holds ``path`` (its nearest folder that exists)."""
    here = path
    while not here.exists() and here.parent != here:
        here = here.parent
    return shutil.disk_usage(here).free


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _pinned(pin: Pin) -> dict[str, Any]:
    return {"dataset": pin.dataset, "revision": pin.revision, "revision_date": pin.revised, "file": pin.filename,
            "bytes": pin.size, "sha256": pin.sha256, "url": pin.url, "version": pin.version,
            "collected": pin.collected, "documents_by_type": COUNTS, "licence": ATTRIBUTION, "doi": DOI}


def _named(code: str) -> str:
    return JURISDICTIONS.get(code) or NOT_COVERED.get(code) or code


def _hit(row: dict[str, Any]) -> dict[str, Any]:
    source = SOURCES[row["source"]]
    return {"rank": row["rank"], "bm25": row["bm25"], "passage": row["passage"],
            "passage_number": row["passage_number"], "citation": row["citation"],
            "jurisdiction": row["jurisdiction"], "jurisdiction_name": _named(row["jurisdiction"]), "type": row["type"],
            "source": row["source"], "source_name": source.name, "url": row["url"], "version_id": row["version_id"],
            "date": row["date"], "when_scraped": row["when_scraped"], "register": source.register,
            "terms": source.terms, "note": "a bill: proposed law, not law" if row["type"] == "bill" else ""}


def _snapshot(pin: Pin, rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """What every reply carrying corpus text says, on its card and in its evidence."""
    dates = sorted({str(r["when_scraped"])[:10] for r in rows})
    when = f"on {dates[0]}" if len(dates) == 1 else f"between {dates[0]} and {dates[-1]}"
    registers = sorted({SOURCES[r["source"]].register for r in rows})
    said = (f"Collected {when} (the corpus's sources as of {pin.collected}): this is not current law. Amendments, "
            f"repeals and later decisions are not included. Confirm each at its url or the official register "
            f"({', '.join(registers)}). Not legal advice. Source: {ATTRIBUTION}")
    evidence = {"kind": "corpus_snapshot", "dataset": pin.dataset, "revision": pin.revision, "version": pin.version,
                "collected": pin.collected, "when_scraped": dates, "current_law": False,
                "later_changes_included": False, "legal_advice": False,
                "confirm_at": sorted({r["url"] for r in rows}) + registers, "attribution": ATTRIBUTION,
                "licence": "CC BY 4.0", "doi": DOI}
    return said, evidence


def _action(pin: Pin, dest: Path) -> dict[str, Any]:
    """The act the gate decides: one download of the pinned file, with what G6 checks afterwards."""
    return {"action_type": "call_api", "target_backend": "huggingface", "target_resource": pin.url,
            "description": f"download {pin.filename} ({pin.size:,} bytes) of the dataset {pin.dataset} at revision "
                           f"{pin.revision} from Hugging Face into G6's state folder",
            "action_params": {"url": pin.url, "bytes": pin.size, "sha256": pin.sha256, "to": str(dest)},
            "requested_by": f"{COMPONENT}.fetch", "actor_type": "agent", "external_side_effect": False,
            "blast_radius": "local", "reversibility": "reversible", "safety_domains": ["data_integrity"],
            "verification_contract": _gate.plan(
                f"a file of exactly {pin.size} bytes whose SHA-256 is {pin.sha256}",
                "the SHA-256 computed while the file streams", "when the download ends, before the file is put "
                "in place", "any size or hash difference, or any failure: the partial file is deleted",
                "remove deletes the file and the index")}


@block_contract(
    purpose="search Australian legislation and court decisions from the Open Australian Legal Corpus on the person's "
            "own computer, each passage with its jurisdiction, link and collection date, refusing a jurisdiction the "
            "corpus lacks, so an agent neither answers from memory nor gives another jurisdiction's law or a dated "
            "snapshot as the current law",
    mitigates_failure_modes=("answered_from_memory", "stale_context_used", "wrong_jurisdiction"),
    verification_method="the corpus file's size and SHA-256 checked against the publisher's pin; jurisdictions "
                        "checked against the corpus's pinned set before any search; every passage returned with its "
                        "source fields and collection date",
)
@component_maturity(
    "beta",
    reason="a pinned revision of the publisher's dataset, hash-checked, indexed locally with SQLite FTS5; tested on "
           "an invented corpus and transport doubles, never on the dataset itself",
)
class AustralianLegalCorpusBlock(AIBlock[AustralianLegalCorpusInput, dict, dict]):
    """Search the Open Australian Legal Corpus on this computer. Never ``verified``, never current law."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, pin: Pin = PINNED,
                 client: http.Client | None = None, gate: Callable[..., dict[str, Any]] | None = None,
                 folder: Path | None = None, free: Callable[[Path], int] = _free,
                 clock: Callable[[], float] = time.monotonic, max_line: int = store.MAX_LINE_BYTES,
                 search_seconds: float = store.SEARCH_SECONDS) -> None:
        super().__init__(name=name, state=state or {})
        self.pin, self.client, self.gate, self.free = pin, client, gate or _gate.ask, free
        self._folder, self.clock, self.max_line, self.search_seconds = folder, clock, max_line, search_seconds

    @property
    def folder(self) -> Path:
        return self._folder or state_path(FOLDER)

    def infer(self, data: AustralianLegalCorpusInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if data.op not in _OPS:
            return unknown_op(self.name, data.op, [*_OPS, "ops", "help"])
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        try:
            return Result.ok(getattr(self, f"_{data.op}")(data, ids))
        except store.IndexProblem as exc:
            return Result.ok(self._refuse(data.op, exc.code, exc.message, ids))
        except (OSError, sqlite3.Error) as exc:
            if isinstance(exc, sqlite3.OperationalError) and "no such module: fts5" in str(exc).lower():
                code = FTS5_UNAVAILABLE
                why = "SQLite FTS5 is unavailable. Use a Python whose sqlite3 includes FTS5, then call build again."
            elif (getattr(exc, "errno", None) == errno.ENOSPC
                  or getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
                  or "database or disk is full" in str(exc).lower()):
                code = DISK_SHORT
                why = "Storage is full. Free disk space, then retry; already committed build progress is kept."
            else:
                code = STORAGE_FAILED
                why = (f"Storage could not be read or written ({exc}). Check the corpus file is readable and G6 "
                       "state is writable, then retry; already committed build progress is kept.")
            return Result.ok(self._refuse(data.op, code, why, ids,
                                          evidence=[{"kind": "storage_failure", "error_type": type(exc).__name__}]))

    # --- replies -------------------------------------------------------------------------------------------------

    def _answer(self, op: str, code: str, said: str, ids: dict[str, str], **fields: Any) -> dict[str, Any]:
        return _reply.answer(COMPONENT, op, code, said, action=_ACTIONS[code], **ids, **fields)

    def _refuse(self, op: str, code: str, why: str, ids: dict[str, str], **fields: Any) -> dict[str, Any]:
        return _reply.refuse(COMPONENT, op, code, why, action=_ACTIONS[code], reason=why, **ids, **fields)

    def _not_covered(self, op: str, named: str, ids: dict[str, str], *, in_query: bool = False) -> dict[str, Any]:
        code, place = jurisdiction(named), "no other jurisdiction's law is given in its place."
        if in_query:
            said = (f"The query names {named}, which is not in the corpus (it covers {covered()}); {place} To search "
                    "a jurisdiction it covers for these words, name it.")
        elif code in NOT_COVERED:
            said = f"{NOT_COVERED[code]} is not in the corpus, which covers {covered()}; {place}"
        else:
            said = f"{named!r} is not a jurisdiction the corpus covers ({covered()}); {place}"
        return self._refuse(op, NOT_COVERED_CODE, said, ids, covered=list(JURISDICTIONS),
                            not_covered=list(NOT_COVERED.values()))

    # --- ops -----------------------------------------------------------------------------------------------------

    def _status(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        folder = self.folder
        manifest = store.read_manifest(folder)
        problem = store.manifest_problem(manifest, self.pin)
        file: dict[str, Any] | None = None
        if manifest is not None:
            file = {"path": manifest["path"], "how": manifest["how"], "bytes": manifest["size"],
                    "sha256_checked_on": manifest["checked"], "sha256_checked_again": False,
                    "size_and_time_unchanged": not problem, "problem": problem}
        index: dict[str, Any] | None
        try:
            meta = store.Index(folder).meta()
        except store.IndexProblem as exc:
            meta, index = {}, {"problem": exc.message}
        else:
            index = None
        if meta:
            index = {"records_read": meta["lines"], "documents": meta["indexed"], "passages": meta["passages"],
                     "types": meta["types"], "jurisdictions": meta["jurisdictions"], "skipped": meta["skipped"],
                     "offset": meta["offset"], "of_bytes": self.pin.size,
                     "percent": round(100 * meta["offset"] / self.pin.size, 1) if self.pin.size else 100.0,
                     "complete": meta["complete"], "revision": meta["revision"]}
        if problem:
            step = _NEXT if manifest is None else f"{problem}: fetch or register the pinned file again"
        elif not meta:
            step = "build the index (call build until complete is true)"
        elif not meta["complete"]:
            step = "build again to continue (search works on what is indexed so far)"
        else:
            step = "search"
        said = (f"{'No corpus file yet' if manifest is None else 'Corpus file: ' + manifest['how']}; "
                f"{'no index' if not meta else str(meta['indexed']) + ' documents indexed'}. Next: {step}. "
                "Nothing was sent.")
        return self._answer("status", STATUS, said, ids, dataset=_pinned(self.pin), file=file, index=index,
                            next_step=step, evidence=[{"kind": "disk", "folder": str(folder), "network": False}])

    def _fetch(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        pin, folder = self.pin, self.folder
        manifest = store.read_manifest(folder)
        if manifest is not None and not store.manifest_problem(manifest, pin):
            return self._answer("fetch", PRESENT, f"The pinned {pin.filename} is already on record "
                                f"({manifest['how']}, its hash checked on {manifest['checked']}); nothing was sent.",
                                ids, dataset=_pinned(pin), file=manifest)
        dest = folder / pin.filename
        allowance = pin.size     # as much again as the file for the index of everything: an estimate, stated
        needs, free = pin.size + allowance, self.free(folder)
        would = {"url": pin.url, "dataset": pin.dataset, "revision": pin.revision, "file": pin.filename,
                 "bytes": pin.size, "sha256": pin.sha256, "to": str(dest), "licence": ATTRIBUTION,
                 "needs_free_bytes": needs, "index_allowance_bytes": allowance, "free_bytes": free,
                 "deadline_seconds": FETCH_DEADLINE, "requested": False}
        command = pin.command(str(folder))
        try:
            gate = self.gate(_action(pin, dest), None)
        except Exception as exc:  # noqa: BLE001 - a gate that cannot decide holds the download
            gate = {"kind": "action_gate", "decision": "block", "reason_codes": [],
                    "message": f"the action gate could not decide: {type(exc).__name__}"}
        evidence = [{"kind": "dry_run", **would}, gate]
        if free < needs:
            return self._refuse("fetch", DISK_SHORT, f"Not enough free disk: the download needs {_gb(needs)} free "
                                f"({_gb(pin.size)} for the file and as much again for the index), and the disk "
                                f"holding {folder} has {_gb(free)}. Nothing was sent.", ids, would_fetch=would,
                                gate=gate, person_can_run=command, evidence=evidence)
        if d.dry_run:
            return self._answer("fetch", DRY_RUN, f"A dry run: nothing was sent. It would download {pin.url} "
                                f"({_gb(pin.size)}, revision {pin.revision}), check its SHA-256 and keep it in "
                                f"{folder}; it needs {_gb(needs)} free and the disk has {_gb(free)}. With dry_run "
                                f"false the action gate decides; it says {gate['decision']} now.", ids,
                                would_fetch=would, gate=gate, person_can_run=command, evidence=evidence)
        if not _gate.lets_through(gate):
            codes = ", ".join(gate.get("reason_codes", [])) or "no reason code"
            return self._refuse("fetch", HELD, f"Nothing was sent: the action gate (g6.action_gating) decided "
                                f"{gate['decision']} ({codes}): {gate.get('message', '')} A person can run: "
                                f"{command}, then register the file.", ids, would_fetch=would, gate=gate,
                                person_can_run=command, evidence=evidence)
        folder.mkdir(parents=True, exist_ok=True)
        client = self.client or http.Client()
        try:
            got = client.download(pin.url, dest, expected_bytes=pin.size, sha256=pin.sha256,
                                  deadline=FETCH_DEADLINE)
        except http.HTTPError as exc:
            return self._refuse("fetch", FETCH_FAILED, f"The download failed and nothing was kept: {exc}", ids,
                                http_code=exc.code, would_fetch=would, gate=gate, person_can_run=command,
                                evidence=[gate, {"kind": "download", "url": pin.url, "outcome": exc.code}])
        manifest = store.write_manifest(folder, pin, got.path, "fetched")
        return self._answer("fetch", FETCHED, f"Downloaded {got.size:,} bytes from {got.url}; its SHA-256 equals the "
                            f"pin for revision {pin.revision}. Next: build.", ids, dataset=_pinned(pin), file=manifest,
                            evidence=[gate, {"kind": "download", "url": pin.url, "last_hop": got.url,
                                             "bytes": got.size, "sha256": got.sha256, "sha256_matches_pin": True,
                                             "boundary": "g6.core.http: every hop checked"}])

    def _register(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        pin = self.pin
        if not d.path.strip():
            return self._refuse("register", INPUT, f"give path: the absolute path of the {pin.filename} you "
                                "downloaded", ids, person_can_run=pin.command())
        path = Path(d.path.strip())
        if not path.is_absolute():
            return self._refuse("register", INPUT, "give the file's absolute path", ids)
        try:
            path = confined_path(d.path.strip(), (path,))
        except PathRefused as exc:
            return self._refuse("register", INPUT, str(exc), ids)
        if not store.check_file(path, pin):
            return self._refuse("register", NOT_PINNED, f"That path is not the pinned {pin.filename}: G6 accepts "
                                f"only a file of exactly {pin.size} bytes whose SHA-256 is {pin.sha256} (revision "
                                f"{pin.revision}). Nothing was recorded.", ids, person_can_run=pin.command())
        manifest = store.write_manifest(self.folder, pin, path.resolve(), "registered")
        return self._answer("register", REGISTERED, f"Recorded in place, not copied: {pin.filename} at {path} is "
                            f"{pin.size:,} bytes and its SHA-256 equals the pin. Next: build.", ids,
                            dataset=_pinned(pin), file=manifest,
                            evidence=[{"kind": "file_check", "path": str(path), "bytes": pin.size,
                                       "sha256_matches_pin": True}])

    def _places(self, op: str, names: tuple[str, ...], ids: dict[str, str]) -> tuple[str, ...] | dict[str, Any]:
        codes = []
        for name in names:
            code = jurisdiction(name)
            if code not in JURISDICTIONS:
                return self._not_covered(op, name, ids)
            codes.append(code)
        return tuple(dict.fromkeys(codes))

    def _build(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        folder, pin = self.folder, self.pin
        manifest = store.read_manifest(folder)
        problem = store.manifest_problem(manifest, pin)
        if problem:
            return self._refuse("build", NO_CORPUS, f"Nothing to build from: {problem}. {_NEXT[0].upper()}"
                                f"{_NEXT[1:]}.", ids, person_can_run=pin.command(str(folder)))
        places = self._places("build", d.jurisdictions, ids)
        if isinstance(places, dict):
            return places
        index = store.Index(folder)
        try:
            meta = index.meta()
        except store.IndexProblem as exc:
            return self._refuse("build", exc.code, exc.message, ids)
        types = tuple(dict.fromkeys(d.types))
        if meta:
            if meta.get("sha256") != manifest["sha256"]:
                return self._refuse("build", INDEX_MISMATCH, "The index was built from another file than the one "
                                    "on record.", ids)
            asked = (types and set(types) != set(meta["types"])) or (places and set(places) != set(
                meta["jurisdictions"]))
            if asked:
                return self._refuse("build", FILTERS_DIFFER, f"The index is being built with types "
                                    f"{', '.join(meta['types'])} and jurisdictions {', '.join(meta['jurisdictions'])};"
                                    " another filter would mix two indexes.", ids, index_types=meta["types"],
                                    index_jurisdictions=meta["jurisdictions"])
        progress = index.build(manifest, types=types or LEGISLATION, jurisdictions=places or tuple(JURISDICTIONS),
                               max_records=d.max_records, max_seconds=d.max_seconds, free=self.free,
                               clock=self.clock, max_line=self.max_line)
        meta = index.meta()
        done = {"records_read": progress.lines, "documents": progress.indexed, "passages": progress.passages,
                "skipped": {k: v for k, v in progress.skipped.items() if v}, "stopped": progress.stopped}
        state = {"documents": meta["indexed"], "passages": meta["passages"], "records_read": meta["lines"],
                 "skipped": meta["skipped"], "types": meta["types"], "jurisdictions": meta["jurisdictions"],
                 "offset": meta["offset"], "of_bytes": pin.size,
                 "percent": round(100 * meta["offset"] / pin.size, 1) if pin.size else 100.0}
        evidence = [{"kind": "build", "file": manifest["path"], "this_call": done, "index": state,
                     "caps": {"max_records": d.max_records, "max_seconds": d.max_seconds,
                              "max_line_bytes": self.max_line}}]
        if progress.stopped == "disk":
            return self._refuse("build", DISK_SHORT, f"The build stopped: under {_gb(store.BUILD_FLOOR)} free on the "
                                f"disk holding {folder}. What was built so far is kept.", ids, this_call=done,
                                index=state, complete=False, evidence=evidence)
        skipped = sum(done["skipped"].values())
        said = (f"This call read {progress.lines:,} records: {progress.indexed:,} indexed, {skipped:,} skipped "
                f"({', '.join(f'{k} {v}' for k, v in done['skipped'].items()) or 'none'}). The index holds "
                f"{meta['indexed']:,} documents in {meta['passages']:,} passages, {state['percent']}% of the file. ")
        said += ("The index is complete." if meta["complete"] else
                 f"Stopped at the {progress.stopped} cap: call build again to continue.")
        return self._answer("build", BUILT, said, ids, this_call=done, index=state, complete=meta["complete"],
                            evidence=evidence)

    def _ready(self, op: str, ids: dict[str, str]) -> tuple[store.Index, dict[str, Any]] | dict[str, Any]:
        """The index and its record, or the refusal saying why there is none to read."""
        index = store.Index(self.folder)
        try:
            meta = index.meta()
        except store.IndexProblem as exc:
            return self._refuse(op, exc.code, exc.message, ids)
        if not meta or not meta.get("indexed"):
            return self._refuse(op, NO_INDEX, f"There is no indexed document to read yet: {_NEXT}. Nothing is answered "
                                "without it, from memory or otherwise.", ids, person_can_run=self.pin.command())
        if (meta.get("revision"), meta.get("sha256")) != (self.pin.revision, self.pin.sha256):
            return self._refuse(op, INDEX_MISMATCH, f"The index was built from revision {meta.get('revision')}, not "
                                f"the pinned {self.pin.revision}.", ids)
        return index, meta

    def _search(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        words = store.words_of(d.query)
        if not words:
            return self._refuse("search", INPUT, "give query: the words to look for", ids)
        named = d.jurisdiction.strip()
        if named:
            code = jurisdiction(named)
            if code not in JURISDICTIONS:
                return self._not_covered("search", named, ids)
            asked: tuple[str, ...] = (code,)
        else:
            if other := uncovered_named(d.query):
                return self._not_covered("search", other, ids, in_query=True)
            asked = ()
        ready = self._ready("search", ids)
        if isinstance(ready, dict):
            return ready
        index, meta = ready
        places = asked or tuple(meta["jurisdictions"])
        types = tuple(dict.fromkeys(d.types)) or tuple(meta["types"])
        missing = [_named(p) for p in places if p not in meta["jurisdictions"]] + [
            t for t in types if t not in meta["types"]]
        if missing:
            return self._refuse("search", NOT_INDEXED, f"The index was built without {', '.join(missing)} "
                                f"(it holds {', '.join(meta['types'])} from {', '.join(map(_named, meta['jurisdictions']))}"
                                "), so a search there would find nothing for the wrong reason.", ids,
                                index_types=meta["types"], index_jurisdictions=meta["jurisdictions"])
        try:
            rows = index.search(words, jurisdictions=places, types=types, k=d.k, every=d.match == "all",
                                seconds=self.search_seconds, clock=self.clock)
        except store.IndexProblem as exc:
            return self._refuse("search", exc.code, exc.message, ids)
        where = ", ".join(map(_named, places))
        partial = ("" if meta["complete"] else f" The index is not complete ({round(100 * meta['offset'] / self.pin.size, 1)}"
                   "% of the file read): a passage not yet indexed is not found.")
        searched = {"kind": "lexical_search", "engine": "SQLite FTS5, BM25 on the text", "words": words,
                    "match": d.match, "jurisdictions": list(places), "types": list(types),
                    "index_complete": meta["complete"], "counts_as_check": False}
        if not rows:
            return self._answer("search", NO_MATCH, f"No indexed passage from {where} ({', '.join(types)}) has "
                                f"{'all' if d.match == 'all' else 'any'} of these words. That is not evidence that "
                                "the law says nothing: lexical search misses other wordings, and the corpus is a "
                                f"snapshot as of {self.pin.collected}.{partial}", ids, hits=[], words=words,
                                index_complete=meta["complete"], evidence=[searched])
        hits = [_hit(r) for r in rows]
        said, snapshot = _snapshot(self.pin, rows)
        bills = " Bills among them are proposed law, not law." if any(h["type"] == "bill" for h in hits) else ""
        return self._answer("search", FOUND, f"{len(hits)} passage(s) from {where}, ranked by BM25 on your words.{bills}"
                            f"{partial} {said}", ids, hits=hits, words=words, index_complete=meta["complete"],
                            evidence=[snapshot, searched], texts=[h["passage"] for h in hits])

    def _get(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        if not d.version_id.strip():
            return self._refuse("get", INPUT, "give version_id: one from a search hit", ids)
        ready = self._ready("get", ids)
        if isinstance(ready, dict):
            return ready
        index, _ = ready
        doc = index.get(d.version_id.strip(), d.start, store.GET_CHARS)
        if doc is None:
            return self._refuse("get", NOT_FOUND, f"No indexed document has version_id {d.version_id.strip()!r}.",
                                ids)
        said, snapshot = _snapshot(self.pin, [doc])
        source = SOURCES[doc["source"]]
        more = (f" This is characters {doc['start']:,} to {doc['start'] + doc['returned_chars']:,} of "
                f"{doc['chars']:,}: ask again with start {doc['start'] + doc['returned_chars']} for more."
                if doc["truncated"] else "")
        document = {**{k: doc[k] for k in ("version_id", "citation", "jurisdiction", "type", "source", "url", "date",
                                           "when_scraped", "mime", "chars", "text", "start", "returned_chars",
                                           "truncated")},
                    "jurisdiction_name": _named(doc["jurisdiction"]), "source_name": source.name,
                    "register": source.register, "terms": source.terms,
                    "note": "a bill: proposed law, not law" if doc["type"] == "bill" else ""}
        return self._answer("get", DOCUMENT, f"{doc['citation']} ({_named(doc['jurisdiction'])}, {doc['type']}).{more} "
                            f"{said}", ids, document=document, evidence=[snapshot], texts=[doc["text"]])

    def _remove(self, d: AustralianLegalCorpusInput, ids: dict[str, str]) -> dict[str, Any]:
        folder, pin = self.folder, self.pin
        manifest = store.read_manifest(folder)
        index = store.Index(folder)
        targets = [p for p in (index.path, *(index.path.with_name(index.path.name + s)
                                              for s in ("-wal", "-shm", "-journal"))) if p.exists()]
        kept = []
        if d.what == "all":
            own = folder / pin.filename
            registered = manifest is not None and manifest["how"] == "registered"
            if own.exists() and not (registered and Path(manifest["path"]) == own):
                targets.append(own)
            targets += sorted(folder.glob(f".{pin.filename}.*.part")) if folder.exists() else []
            if (folder / store.MANIFEST).exists():
                targets.append(folder / store.MANIFEST)
            if registered:
                kept.append(manifest["path"])
        listed = [str(p) for p in targets]
        if d.dry_run:
            return self._answer("remove", REMOVE_DRY_RUN, f"A dry run: nothing was deleted. It would delete "
                                f"{len(listed)} file(s){'; a file you registered is never deleted' if kept else ''}.",
                                ids, would_delete=listed, kept=kept, evidence=[{"kind": "dry_run",
                                                                                "would_delete": listed}])
        gone = index.remove()
        for p in targets:
            if str(p) not in gone and p.exists():
                p.unlink()
                gone.append(str(p))
        return self._answer("remove", REMOVED, f"Deleted {len(gone)} file(s)"
                            f"{'; the file you registered is kept where it is' if kept else ''}.", ids, deleted=gone,
                            kept=kept, evidence=[{"kind": "removed", "deleted": gone, "kept": kept}])


__all__ = ["COMPONENT", "FETCH_DEADLINE", "FOLDER", "SENSITIVE_OPS", "AustralianLegalCorpusBlock",
           "AustralianLegalCorpusInput"]
