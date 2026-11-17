# SPDX-License-Identifier: Apache-2.0
"""Authored prose adapters at the first unbound layer around bound builders.

These functions accept producer contracts, not arbitrary component values. They
never assign authorship by inspecting ok, a diagnostic code, or nested data.
The original builders remain untouched for the certificate issuance batch.
"""
from __future__ import annotations
from collections.abc import Mapping

from g6.core.agent_guidance import authored_fields, _ISOLATION_ACTION

CARD_PROSE = ("message", "suggested_action", "action", "next_step", "reason", "why", "risks")
ENVELOPE_PROSE = ("error", "degradation_reason", "next_actions", "recommended_next_steps",
                  "block_reason", "required_human_decision", "evidence_collected",
                  "safe_next_action", "rollback_or_containment", "known_limitations", "missing_checks")
ISOLATION_ACTION = authored_fields({"action": _ISOLATION_ACTION}, "action")["action"]


def warning_card(*args, **kwargs):
    from g6.core.reliability_envelope import warning_card as build
    return authored_fields(build(*args, **kwargs), *CARD_PROSE)


def card(existing, *args, **kwargs):
    from g6.core.door import card as build
    value = build(existing, *args, **kwargs)
    # An existing producer card is data. The fallback card is G6's own text.
    return value if borrows_card(existing) else authored_fields(value, *CARD_PROSE)


def borrows_card(argument):
    """Record the bound card constructor's argument-selection branch.

    This is the exact guard of door.card, including non-dict mappings. It records
    which argument the constructor copies; no returned text or reply code is
    searched for authorship. Remove this adapter when the bound builder records
    its branch natively in the issuance batch.
    """
    from g6.invariants import ERROR_CODE_PATTERN
    return isinstance(argument, Mapping) and bool(ERROR_CODE_PATTERN.match(str(argument.get("code", ""))))


def envelope_fields(**kwargs):
    from g6.core.reliability_envelope import envelope_fields as build
    value = build(**kwargs)
    if kwargs.get("warning_card") is None:
        if isinstance(value.get("warning_card"), dict):
            value["warning_card"] = authored_fields(value["warning_card"], *CARD_PROSE)
    else:
        # Pydantic can erase string subtypes. Preserve the supplied card's
        # receipts, including an entirely unmarked component-owned card.
        value["warning_card"] = {**(value.get("warning_card") or {}), **kwargs["warning_card"]}
    return authored_fields(value, *ENVELOPE_PROSE)


def door_envelope(claimed, **kwargs):
    from g6.core.door import door_envelope as build
    value = build(claimed, **kwargs)
    supplied = kwargs.get("warning_card")
    if supplied is None or claimed == "verified":
        if isinstance(value.get("warning_card"), dict):
            value["warning_card"] = authored_fields(value["warning_card"], *CARD_PROSE)
    else:
        value["warning_card"] = {**(value.get("warning_card") or {}), **supplied}
    return authored_fields(value, *ENVELOPE_PROSE)


def door_reply_fields(value, *, card_owned=True):
    """The bound door's envelope, with its component result kept opaque.

    The caller records whether the producer supplied the card when invoking it.
    That borrowed card remains data; a door fallback card is its own prose. No
    result content is searched to decide who wrote a card.
    """
    # ReliabilityEnvelope mirrors a borrowed producer card into these fields.
    # Those copies inherit the card's ownership; only the door's fixed defaults
    # and its own error remain ours when a component supplied the card.
    fields = ENVELOPE_PROSE if card_owned else ("error", "missing_checks", "rollback_or_containment")
    out = authored_fields(value, *fields)
    if isinstance(out.get("warning_card"), dict) and card_owned:
        out["warning_card"] = authored_fields(out["warning_card"], *CARD_PROSE)
        if out["warning_card"].get("code") == "G6_E_ISOLATION_UNAVAILABLE":
            out["warning_card"] = authored_fields({**out["warning_card"],
                "suggested_action": ISOLATION_ACTION, "action": ISOLATION_ACTION}, *CARD_PROSE)
    return out


def blocked_reply(*args, **kwargs):
    from g6.core.door import blocked_reply as build
    return door_reply_fields(build(*args, **kwargs))


def job_result(value):
    """Adapt a job operation's declared G6 envelope before its JSON retention.

    Jobs receive G6 operation envelopes; component values belong in result.
    This contract is independent of status and diagnostic codes. Never use it
    on a bare component value.
    """
    return door_reply_fields(value) if isinstance(value, dict) else value


def script_run(*args, **kwargs):
    """First unbound receiver of sandbox's typed execution record.

    A no-returncode diagnostic or timeout is constructed by the host. An executed
    program's stdout/stderr are data. The bound sandbox prepends exactly one
    confine.import_failure sentence on the documented import-failure branch;
    filter that constructed prefix alone, preserving all following program bytes.
    """
    from dataclasses import replace
    from g6.core import sandbox, confine

    run = sandbox.run_script(*args, **kwargs)
    isolation = authored_fields(run.isolation, "why", "warning", "cleanup")
    stderr = run.stderr
    if run.returncode is None or run.status == "timeout":
        stderr = authored_fields({"stderr": stderr}, "stderr")["stderr"]
    elif run.isolation.get("backend") is not None and run.returncode != 0:
        prefix = confine.import_failure(stderr)
        if prefix and stderr.startswith(prefix + "\n"):
            safe = authored_fields({"prefix": prefix}, "prefix")["prefix"]
            stderr = safe + "\n" + stderr[len(prefix) + 1:]
    return replace(run, stderr=stderr, isolation=isolation)


def confine_record(plan):
    return authored_fields(plan.record(), "why", "warning", "cleanup")


def confine_refused(plan):
    from g6.core import confine
    return authored_fields({"reason": confine.refused(plan)}, "reason")["reason"]


def confine_refusal(plan):
    from g6.core import confine
    return authored_fields({"reason": confine.refusal(plan)}, "reason")["reason"]


def confine_not_started(record, exc):
    from g6.core import confine
    return authored_fields(confine.not_started(record, exc), "why", "warning", "cleanup")


def confine_summary(records):
    from g6.core import confine
    return authored_fields(confine.summary(records), "why", "warning", "cleanup")
