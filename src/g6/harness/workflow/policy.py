# SPDX-License-Identifier: Apache-2.0
"""The workflow record's storage policy (design 17.3): what is stored, for how long, and the switch that turns
collection on. Published as docs/product_global_specs/workflow_storage_policy.md; a change here is a new version.

Every file name the record uses is written in this module and nowhere else in the package, so the inventory test can
hold each one to a period.
"""
from __future__ import annotations

import os

POLICY_VERSION = "workflow-storage.v1"
#: Days a record is kept after its newest entry. Finite for everything G6 writes.
RETENTION_DAYS = {"events": 180, "annotations": 180, "decisions": 365, "feedback": 180, "exports": 30,
                  "receipts": 365}
#: What a deletion or expiry receipt holds, and nothing else.
RECEIPT_FIELDS = ("policy_version", "scope", "scope_id_sha256", "removed", "at", "reason")

RUN_SUFFIX = ".jsonl"                       # tasks/<task>/runs/<run>.jsonl
ANNOTATIONS = "annotations.jsonl"           # tasks/<task>/annotations.jsonl
DECISIONS = "decisions.jsonl"               # tasks/<task>/decisions.jsonl and the project's own
FEEDBACK = "feedback.jsonl"
RECEIPTS = "receipts.jsonl"
INSPECTION = "inspection.json"              # written on request, for the person to open
CORRECTIONS = "corrections.json"            # written by the person; G6 reads it on request and never writes it
HEAD, LOCK, PART = ".head", ".lock", ".part"
SIDECARS = (HEAD, LOCK, PART)               # kept beside a file and removed with it
PERSON_FILES = (CORRECTIONS,)
#: Each file name and the period it falls under: a RETENTION_DAYS key, "sidecar", or "person".
FILES = {RUN_SUFFIX: "events", ANNOTATIONS: "annotations", DECISIONS: "decisions", FEEDBACK: "feedback",
         RECEIPTS: "receipts", INSPECTION: "exports", CORRECTIONS: "person", HEAD: "sidecar", LOCK: "sidecar",
         PART: "sidecar"}
#: How deleting a run or a task reaches each file. "file": the file is one run's, removed whole (and with its task's
#: folder). "entries": each entry names the run or task it is about, and those entries are removed wherever the file
#: is kept. "rebuilt": made from the others on request, removed with any deletion. "kept": kept by design (a receipt
#: holds a digest, never an entry), and named in the reply. "person": the person's own file, never changed by G6,
#: removed only with the whole project record, and named in the reply. "sidecar": removed with the file it serves.
DELETION = {RUN_SUFFIX: "file", ANNOTATIONS: "entries", DECISIONS: "entries", FEEDBACK: "entries", RECEIPTS: "kept",
            INSPECTION: "rebuilt", CORRECTIONS: "person", HEAD: "sidecar", LOCK: "sidecar", PART: "sidecar"}
#: Files written whether or not the record is on: each is written at the person's own request (to look at the
#: record, or to show that something was removed) and holds nothing a tool call carried.
WRITTEN_WHEN_OFF = (INSPECTION, RECEIPTS)

RECORD_ENV = "G6_WORKFLOW_RECORD"
#: Ruling (for the owner): the record is kept only after the person turns it on. On from first use is this one line.
ON_BY_DEFAULT = False


def enabled() -> tuple[bool, str]:
    """Whether G6 records workflow events, and why not when it does not. The switch is read from the server's own
    environment (``G6_WORKFLOW_RECORD``: ``on`` or ``off``), which an agent cannot set; a value G6 cannot read turns
    collection off, and is not repeated."""
    raw = os.environ.get(RECORD_ENV)
    if raw == "on" or raw is None and ON_BY_DEFAULT:
        return True, ""
    if raw is None:
        return False, f"the workflow record is off until {RECORD_ENV}=on is set where the G6 server is registered"
    if raw == "off":
        return False, f"the workflow record is off ({RECORD_ENV}=off)"
    return False, f"{RECORD_ENV} holds a value G6 cannot read (it takes on or off), so nothing is recorded"


__all__ = ["ANNOTATIONS", "CORRECTIONS", "DECISIONS", "DELETION", "FEEDBACK", "FILES", "HEAD", "INSPECTION", "LOCK",
           "ON_BY_DEFAULT", "PART", "PERSON_FILES", "POLICY_VERSION", "RECEIPTS", "RECEIPT_FIELDS", "RECORD_ENV",
           "RETENTION_DAYS", "RUN_SUFFIX", "SIDECARS", "WRITTEN_WHEN_OFF", "enabled"]
