# SPDX-License-Identifier: Apache-2.0
"""The profile store (KS K7) and the run ledger (K3): two append-only, hash-chained files (``g6.core.ledger``).

The store's rules live in its validation hook, so they hold for every row, however it is appended:

* a ``profile`` row holds a profile's content under its digest; a ``version`` row moves the head;
* the first version is ``initial`` (written at confirmation); ``activate`` needs a decision digest and a named person
  who is not an automated (``auto:``) name; ``rollback`` and ``demote`` may only point at a version a person approved
  (``initial`` or ``activate``), and ``demote`` only at the one approved before the current head (MH-7, MH-8);
* versions are numbered 0, 1, 2... with no gap.

Nothing is ever deleted: a rollback is a new row. ``accepted_by`` is recorded as given: G6 cannot prove that a person
typed it (owner ruling Q16; a stronger channel arrives with the MCP surface).
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger
from g6.metaharness.schema import Profile

VERSION_KINDS = ("initial", "activate", "rollback", "demote")
APPROVED_KINDS = ("initial", "activate")
UNAUTHENTICATED = "accepted_by is recorded as given; G6 cannot prove a person gave it"


def _versions(seen: Mapping[tuple[str, str], dict]) -> list[dict]:
    return [b for (k, _), b in seen.items() if k == "version"]


def _resolve(versions: list[dict], number: int) -> dict:
    """The approved version a version row stands for (a rollback or demotion points at an approved one)."""
    row = versions[number]
    return row if row["kind"] in APPROVED_KINDS else versions[row["target_version"]]


def _store_rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    if kind == "workflow":
        return [] if body.get("workflow_digest") and body.get("accepted_by") else ["workflow: digest and accepted_by"]
    if kind == "profile":
        return [] if body.get("digest") == ident else ["profile: id must be the profile's digest"]
    if kind != "version":
        return [f"unknown kind {kind!r}"]
    versions = _versions(seen)
    problems = []
    if not any(k == "workflow" for (k, _) in seen):
        problems.append("version: the workflow has not been confirmed")
    if body.get("version") != len(versions) or ident != str(len(versions)):
        problems.append(f"version: expected number {len(versions)}")
    if body.get("kind") not in VERSION_KINDS:
        return [*problems, f"version: kind must be one of {VERSION_KINDS}"]
    if ("profile", str(body.get("profile_digest"))) not in seen:
        problems.append("version: its profile is not in the store")
    who = str(body.get("accepted_by") or "")
    if body["kind"] == "initial" and versions:
        problems.append("version: initial must be the first")
    if body["kind"] != "initial" and not versions:
        problems.append("version: the first must be initial")
    if body["kind"] == "activate":
        if not body.get("decision_digest"):
            problems.append("activate: no decision digest")
        if not who.strip() or is_automated(who):
            problems.append("activate: a person must accept it (no empty or auto: name)")
        if who.strip().lower() == str(body.get("proposer") or "").strip().lower():
            problems.append("activate: the acceptor is the proposer")
    if body["kind"] in ("rollback", "demote"):
        target = body.get("target_version")
        if not isinstance(target, int) or not 0 <= target < len(versions):
            return [*problems, f"{body['kind']}: target_version must be an earlier version"]
        if versions[target]["kind"] not in APPROVED_KINDS:
            problems.append(f"{body['kind']}: the target was not approved by a person")
        elif versions[target]["profile_digest"] != body.get("profile_digest"):
            problems.append(f"{body['kind']}: profile_digest is not the target's")
        if body["kind"] == "rollback" and (not who.strip() or is_automated(who)):
            problems.append("rollback: a person must ask for it")
        if body["kind"] == "demote":
            approved = [n for n, v in enumerate(versions) if v["kind"] in APPROVED_KINDS]
            current = _resolve(versions, len(versions) - 1)
            earlier = [n for n in approved if versions[n] is not current and n < versions.index(current)]
            if not earlier or target != earlier[-1]:
                problems.append("demote: only to the version approved before the current one")
    return problems


class ProfileStore:
    def __init__(self, path: str | Path | None = None, *, expect_head: str | None = None) -> None:
        self._chain = ChainLedger(path, expect_head=expect_head, validate=_store_rules)

    def head_hash(self) -> str:
        return self._chain.head()

    def confirmed(self) -> dict | None:
        rows = [e["body"] for e in self._chain.entries() if e["kind"] == "workflow"]
        return rows[-1] if rows else None

    def confirm(self, *, workflow_digest: str, judging_sha256: str, held_out_digest: str, accepted_by: str,
                at: str) -> str:
        # a re-confirmation of the same workflow (after G6's judging code changed) gets its own row id
        taken = any(e["kind"] == "workflow" and e["id"] == workflow_digest for e in self._chain.entries())
        ident = f"{workflow_digest}:{judging_sha256}" if taken else workflow_digest
        return self._chain.append("workflow", ident, {
            "workflow_digest": workflow_digest, "judging_sha256": judging_sha256, "held_out_digest": held_out_digest,
            "accepted_by": accepted_by, "identity": UNAUTHENTICATED, "at": at})

    def _put_profile(self, profile: Profile) -> str:
        d = profile.digest()
        if ("profile", d) not in {(e["kind"], e["id"]) for e in self._chain.entries()}:
            self._chain.append("profile", d, {"digest": d, "profile": profile.model_dump(mode="json")})
        return d

    def versions(self) -> list[dict]:
        return [e["body"] for e in self._chain.entries() if e["kind"] == "version"]

    def profile(self, profile_digest: str) -> Profile:
        return Profile.model_validate(self._chain.get("profile", profile_digest)["profile"])

    def head(self) -> tuple[int, Profile] | None:
        versions = self.versions()
        if not versions:
            return None
        return versions[-1]["version"], self.profile(versions[-1]["profile_digest"])

    def approved(self) -> list[int]:
        return [v["version"] for v in self.versions() if v["kind"] in APPROVED_KINDS]

    def current_approved(self) -> int | None:
        versions = self.versions()
        return _resolve(versions, len(versions) - 1)["version"] if versions else None

    def append_version(self, kind: str, profile: Profile, *, at: str, accepted_by: str = "", decision_digest: str = "",
                       proposer: str = "", target_version: int | None = None, ledger_head: str = "",
                       snapshot: str = "", reason: str = "") -> int:
        digest_ = self._put_profile(profile)
        number = len(self.versions())
        body: dict[str, Any] = {"version": number, "kind": kind, "profile_digest": digest_, "at": at,
                                "accepted_by": accepted_by, "identity": UNAUTHENTICATED if accepted_by else "",
                                "decision_digest": decision_digest, "proposer": proposer, "ledger_head": ledger_head,
                                "snapshot": snapshot, "reason": reason}
        if target_version is not None:
            body["target_version"] = target_version
        self._chain.append("version", str(number), body)
        return number

    def verify(self) -> tuple[str, ...]:
        return self._chain.verify()


class RunLedger:
    """One row per run (``purpose``: live, evaluation, control, shadow) and per evaluation and decision. A proposal
    (``candidate``) and a held-out report (``report``) are kept whole under their digests, and so is the MCP session
    that proposed a change (``proposed_by``), so another process can approve a decision they led to."""

    def __init__(self, path: str | Path | None = None, *, expect_head: str | None = None) -> None:
        self._chain = ChainLedger(path, expect_head=expect_head, validate=self._rules)

    @staticmethod
    def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
        if kind == "run":
            need = ("workflow_digest", "profile_digest", "purpose", "completion_state")
            return [f"run: {k} is required" for k in need if not body.get(k)]
        if kind in ("evaluation", "decision", "ticket", "proposal", "selection", "tuning_run", "candidate", "report"):
            return []
        if kind == "proposed_by":
            return [] if body.get("proposal_digest") and body.get("session") else [
                "proposed_by: a proposal digest and a session are required"]
        if kind == "setting":
            return [] if body.get("accepted_by") else ["setting: a person must make it (accepted_by)"]
        if kind in ("vault", "incident"):
            return [] if body.get("digest") or body.get("fixture_digest") else [f"{kind}: a digest is required"]
        return [f"unknown kind {kind!r}"]

    # the g6.core.vault ledger protocol: a sealed held-out file and any incident against it are ledger rows
    def add_vault(self, vault_id: str, fixture_digest: str, *, files: int, roots: int) -> str:
        return self._chain.append("vault", vault_id, {"vault_id": vault_id, "digest": fixture_digest, "files": files,
                                                      "roots": roots})

    def add_incident(self, kind: str, **fields: Any) -> str:
        ident = f"incident-{len(self.rows('incident')) + 1}"
        return self._chain.append("incident", ident, {**fields, "incident_id": ident, "type": kind})

    def append(self, kind: str, ident: str, body: dict) -> str:
        return self._chain.append(kind, ident, body)

    def has(self, kind: str, ident: str) -> bool:
        try:
            self._chain.get(kind, ident)
        except KeyError:
            return False
        return True

    def get(self, kind: str, ident: str) -> dict:
        """One row's body (``KeyError`` when there is none)."""
        return self._chain.get(kind, ident)

    def rows(self, kind: str | None = None) -> list[dict]:
        return [e["body"] | {"_id": e["id"], "_kind": e["kind"]} for e in self._chain.entries()
                if kind is None or e["kind"] == kind]

    def head(self) -> str:
        return self._chain.head()

    def verify(self) -> tuple[str, ...]:
        return self._chain.verify()
