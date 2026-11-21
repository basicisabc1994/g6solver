# SPDX-License-Identifier: Apache-2.0
"""The patch review's reply over MCP: compact by default (worked examples, W2).

In the worked examples a finished review's whole reply ran to 64,000 characters and more, over what Claude Code takes
as a tool result: it saves a result over 50,000 characters (or 25,000 tokens) to a file, and warns over 10,000 tokens.
:func:`finish` saves the whole reply as JSON under G6's state folder (``reviews/<job id>.json``) and gives the compact
reply ``metaharness_job`` returns by default: ``assessment.report`` first, then the outcome, the reasons, the next
actions and the unknowns, the merge authorisation, the job id, the envelope's fields, and where the full assessment is
(that file, and ``metaharness_job`` with ``detail="full"``, which returns the whole reply as it was before). The report
is never cut, and every integrity reason is kept whole and first. The other lists keep their first items and each
text its first characters, at most, so the rest of the reply stays within ``BUDGET`` characters whatever the review
holds; ``full_assessment.shortened`` names what was cut.
A reply with no assessment, a refusal, is returned as it is. Nothing here decides anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from typing import Any

from g6.mcp.consent_controls import refused_send

#: The most characters the compact reply takes besides its report.
BUDGET = 12_000
#: The items each list keeps and the characters each text keeps, tried in turn until the reply fits ``BUDGET``.
SHORTENINGS = ((12, 1_000), (6, 300), (2, 120))
INTERRUPTED = "G6_E_PATCH_REVIEW_INTERRUPTED"
#: The envelope's fields the compact reply carries as they are.
ENVELOPE = ("ok", "completion_state", "reliability_label", "request_id", "run_id", "artifact_digest")
#: The fields of each evidence item it keeps.
EVIDENCE = ("source", "summary", "authority", "artifact_digest", "base_commit", "scope",
            "kind", "provider", "reason", "available_hosts", "status")
#: The version of the record of a saved review's sources (``reviews/<job id>.sources.json``).
SOURCES = "review-sources.v1"
HOLDS = ("every field of the review: each requirement's status, assessment.completion (what was demonstrated, what is "
         "not established, each reproduction with its text, preservation, open questions, every activity), the scope "
         "with every check run, the gate, the grounding and all the evidence")


def review(repo: str | os.PathLike, **kwargs: Any) -> dict:
    """``review_patch`` for a job over MCP: what it lets reach an enclosing door is named here, for a job has none."""
    from g6.core import component_process
    from g6.core.door import blocked_reply
    from g6.core.stop import STOPPED
    from g6.harness.patch_review import review_patch

    try:
        return review_patch(repo, **kwargs)
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            return refused_send(blocked_reply(f"[{STOPPED}] the patch review was stopped before it finished: {exc}"),
                                code=STOPPED)
        return refused_send(blocked_reply(f"[{INTERRUPTED}] the patch review's time allowance ended before "
                                          f"it finished, so it has no outcome: {exc}"), code=INTERRUPTED)
    except component_process.ContainmentFailure as exc:
        return refused_send(blocked_reply(f"[{INTERRUPTED}] G6 could not confirm that the review's test "
                                          f"processes ended, so nothing from it is used: {exc}"), code=INTERRUPTED)


def save(reply: dict, job_id: str) -> str | None:
    """The whole reply as JSON, in a new file ``reviews/<job_id>.json`` under G6's state folder, readable by its owner
    only; its path, or ``None`` when it could not be written."""
    from g6.core.storage import state_path

    try:
        path = state_path("reviews", f"{job_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(reply, ensure_ascii=False, indent=1, default=str).encode("utf-8")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        from g6.mcp import review_binding

        review_binding.write(path, reply, data)
        from g6.harness import behaviour_runner

        behaviour_runner.write_probes(path, reply, data)
    except (OSError, ValueError, TypeError):
        return None
    return str(path)


def record(job_id: str, reply: dict, sources: Any) -> None:
    """The sources of the review that made ``reply``, beside its saved copy under G6's state folder
    (``reviews/<job_id>.sources.json``), bound to the bytes ``save`` writes for it (serialised here as ``save``
    serialises it, so a saved copy that differs does not read back), published last. Nothing is recorded when it
    cannot be written."""
    from g6.core.storage import state_path

    try:
        data = json.dumps(reply, ensure_ascii=False, indent=1, default=str).encode("utf-8")
        entry = {"version": SOURCES, "reply_sha256": hashlib.sha256(data).hexdigest(),
                 "sources": [{"source": s.source_id, "item": s.item_id, "content": s.content_sha256,
                              "recorded": s.recorded} for s in sources]}
        side = state_path("reviews", f"{job_id}.json").with_suffix(".sources.json")
        part = side.with_name(side.name + ".part")
        with os.fdopen(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="ascii",
                       newline="\n") as out:
            json.dump(entry, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, side)
    except (OSError, ValueError, TypeError, AttributeError):
        return


def carried(job_id: str, *, expected_sha256: str | None = None) -> tuple | None:
    """The sources recorded with the saved review of ``job_id`` under G6's state folder, read afresh: ``None`` when
    the record or the saved reply is missing or not a regular file, or the record is of another version, empty,
    malformed, or not bound to the saved reply's present bytes. A reader then treats the review as G6's storage
    folder's own source (fail closed). ``expected_sha256`` also binds the record to bytes a reader already checked,
    so a replacement saved/source pair cannot relabel content the reader retained before the replacement."""
    from g6.core import cloud_consent as cloud
    from g6.core.storage import state_path

    try:
        path = state_path("reviews", f"{job_id}.json")
        side = path.with_suffix(".sources.json")
        if not all(stat.S_ISREG(os.lstat(one).st_mode) for one in (path, side)):
            return None
        entry = json.loads(side.read_bytes())
        if not isinstance(entry, dict) or entry.get("version") != SOURCES \
                or entry.get("reply_sha256") != hashlib.sha256(path.read_bytes()).hexdigest() \
                or expected_sha256 is not None and entry.get("reply_sha256") != expected_sha256 \
                or not isinstance(entry.get("sources"), list) or not entry["sources"]:
            return None
        return tuple(cloud.Source(one["source"], one["item"], one["content"], one["recorded"])
                     for one in entry["sources"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, MemoryError):
        # RecursionError: a record swapped for deeply nested JSON does not read back either
        return None


def _shorten(value: Any, items: int, chars: int, where: str, cut: set[str]) -> Any:
    """``value`` with each list cut to ``items`` and each text to ``chars``; each place it cut is added to ``cut``."""
    if isinstance(value, str):
        if len(value) <= chars:
            return value
        cut.add(where)
        return value[:chars - 3] + "..."
    if isinstance(value, list | tuple):
        if len(value) > items:
            cut.add(where)
        return [_shorten(one, items, chars, where, cut) for one in value[:items]]
    if isinstance(value, dict):
        return {key: _shorten(one, items, chars, where, cut) for key, one in value.items()}
    return value


def compact(reply: dict, job_id: str, saved: str | None) -> dict:
    """The compact form of a finished review's ``reply`` (see the module's docstring); ``saved``, where the whole reply
    is, or ``None`` when it could not be saved."""
    assessment = reply.get("assessment")
    if not isinstance(assessment, dict):
        return reply
    report = str(assessment.get("report", ""))
    evidence = [{key: item[key] for key in EVIDENCE if key in item}
                for item in reply.get("evidence") or [] if isinstance(item, dict)]
    # every integrity reason comes before every other finding: kept whole and first, never cut (the security review
    # of W1-W5, F6); only the others are shortened
    reasons = [r for r in assessment.get("reasons") or [] if isinstance(r, dict)]
    integrity = [r for r in reasons if r.get("stage") == "integrity"]
    others = [r for r in reasons if r.get("stage") != "integrity"]
    how = f'metaharness_job(job_id="{job_id}", detail="full")'
    for items, chars in SHORTENINGS:
        cut: set[str] = set()

        def short(value: Any, where: str) -> Any:
            return _shorten(value, items, chars, where, cut)

        shown = {
            "assessment": {"report": report, "outcome": assessment.get("outcome"),
                           "reasons": [*integrity, *short(others, "assessment.reasons")],
                           "next_actions": short(assessment.get("next_actions") or [], "assessment.next_actions"),
                           "unknowns": short(assessment.get("unknowns") or [], "assessment.unknowns")},
            "merge": short(reply.get("merge"), "merge"),
            "job_id": job_id,
            "full_assessment": {
                "file": saved, "detail": how, "holds": HOLDS,
                "note": ("This is the compact reply: show assessment.report as it is. The full assessment is in the "
                         "file named here" if saved else
                         "This is the compact reply: show assessment.report as it is. G6 could not save the full "
                         "assessment to a file, and this server keeps it only until it restarts") +
                        f"; {how} returns it."},
            **{field: reply[field] for field in ENVELOPE if field in reply},
            "warning_card": short(reply.get("warning_card"), "warning_card"),
            "evidence": short(evidence, "evidence"),
        }
        shown["full_assessment"]["shortened"] = sorted(cut)
        kept = len(json.dumps(report, ensure_ascii=False)) + len(json.dumps(integrity, ensure_ascii=False))
        if len(json.dumps(shown, ensure_ascii=False)) - kept <= BUDGET:
            break
    return shown


def finish(reply: Any, job_id: str) -> Any:
    """What ``metaharness_job`` returns by default for a finished review: a refusal as it is; an assessment saved
    whole, then in its compact form."""
    if not isinstance(reply, dict) or not isinstance(reply.get("assessment"), dict):
        return reply
    from g6.core import cloud_consent as cloud
    from g6.core.storage import g6_home
    body, refs, _ = cloud.freeze_output(reply)
    # the reply's task binding (review_binding.BoundReply) goes with the copy, so save writes it
    custody = getattr(reply, "custody", None)
    try:
        reply = custody(json.loads(body)) if callable(custody) else json.loads(body)
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    # A job entered through a declared producer already carries that contract.
    # Absence of a value receipt is distinct from an explicitly unknown origin;
    # the latter stays in refs and cannot be replaced by the declaration.
    refs = cloud.boundary_sources(carried=refs if refs is not None else
                                   cloud.current_sources() or (cloud.UNKNOWN,))
    saved = save(reply, job_id)
    if saved:
        # The saved assessment is derived from the review's own sources, recorded with it: read back, those are what
        # its location carries. A record without them (older, damaged or changed) is G6's storage folder's, as before.
        record(job_id, reply, refs)
        recorded = carried(job_id)
        refs = tuple(dict.fromkeys((*refs, *recorded))) if recorded is not None else (*refs, cloud.Source.text(
            cloud.repository_source(g6_home() / 'reviews'), 'assessment-location', saved))
    return cloud.bind_output(compact(reply, job_id, saved), refs)


__all__ = ["BUDGET", "SOURCES", "carried", "compact", "finish", "record", "review", "save"]
