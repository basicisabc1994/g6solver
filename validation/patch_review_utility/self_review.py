# SPDX-License-Identifier: Apache-2.0
"""Arm 2, the agent's own review (PROTOCOL.md, "Arms"). This script never starts a model or an agent session.

``prepare`` writes what the owner's session needs: the frozen review prompt with the review contract, and the caps
(the same wall-clock limit as G6's arm, the token cap the entry freezes, no network, the tools the session had while
writing, edits discarded). The owner resumes the session that wrote the patch, after the patch was captured, gives it
the prompt, and saves a session record: ``{task_id, patch_sha256, answer, ended ("completed", "timed_out", "error"
or "refused"), elapsed_s, tokens, model_calls}``. Edits made while reviewing never reach any arm: every arm reviews
the captured patch, and the record must name its digest.

``disposition`` reads that record. Acceptance is an answer whose last non-empty line is exactly ``VERDICT: ACCEPT``
and which has no other ``VERDICT:`` line. A last line of exactly ``VERDICT: REJECT``, alone, is a defect found. Any
other ending, a hedged or doubled verdict, a timeout, a run past the time limit or the token cap is an abstention;
an error, a refusal, a missing record or a record of another patch is a tool error or refusal. None is acceptance.
"""
from __future__ import annotations

import hashlib
import re

from patch_review_utility import contract as review_contract
from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT, ERROR, make

REVIEW_PROMPT = """\
You wrote the patch that is now in this repository's working tree. Before anyone merges it, review it.

The review contract below states what the patch must do, as numbered requirements. Decide whether the patch meets \
every requirement. You may read the code and run the repository's tests. Any change you make while reviewing is \
discarded.

{contract}

End your answer with exactly one line that starts with VERDICT, as the last line:
VERDICT: ACCEPT
if the patch meets every requirement and is ready to merge, or otherwise
VERDICT: REJECT
"""
_VERDICT = re.compile(r"^\s*\W*\s*VERDICT\b", re.IGNORECASE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(task_id: str, requirements: list[dict], *, patch: str, time_limit_s: int, token_cap: int) -> dict:
    if time_limit_s <= 0 or token_cap <= 0:
        raise ValueError("arm 2 needs a positive time limit and token cap, frozen in the entry")
    return {"task_id": task_id, "prompt": REVIEW_PROMPT.format(contract=review_contract.render(requirements)),
            "prompt_sha256": _sha256(REVIEW_PROMPT), "patch_sha256": _sha256(patch),
            "caps": {"time_limit_s": time_limit_s, "token_cap": token_cap, "network": "none",
                     "tools": "the same tools the session had while writing the patch", "edits": "discarded"}}


def verdict(answer: str) -> str | None:
    """``"accept"`` or ``"reject"`` when the answer ends with that exact line and has no other verdict line."""
    lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    if not lines or sum(bool(_VERDICT.match(line)) for line in lines) != 1:
        return None
    return {"VERDICT: ACCEPT": "accept", "VERDICT: REJECT": "reject"}.get(lines[-1])


def disposition(prepared: dict, record: dict | None) -> dict:
    if record is None:
        return make(ERROR, "no session record", error_kind="tool_error")
    cost = {"seconds": record.get("elapsed_s", 0.0), "model_calls": record.get("model_calls", 0),
            "tokens": record.get("tokens", 0)}

    def found(category: str, detail: str = "", kind: str = "") -> dict:
        return {**make(category, detail, error_kind=kind), "cost": cost}

    caps = prepared["caps"]
    if record.get("patch_sha256") != prepared["patch_sha256"]:
        return found(ERROR, "the session record is not of the captured patch", "tool_error")
    if record.get("ended") == "refused":
        return found(ERROR, "the agent refused", "refusal")
    if record.get("ended") not in ("completed", "timed_out"):
        return found(ERROR, f"the session ended with {record.get('ended')!r}", "tool_error")
    if record.get("ended") == "timed_out":
        return found(ABSTAIN, "the session timed out")
    if cost["seconds"] > caps["time_limit_s"]:
        return found(ABSTAIN, "the review ran past its time limit")
    if cost["tokens"] > caps["token_cap"]:
        return found(ABSTAIN, "the review spent more than its token cap")
    said = verdict(record.get("answer", ""))
    if said == "accept":
        return found(ACCEPT)
    if said == "reject":
        return found(DEFECT, "the agent's review rejected the patch")
    return found(ABSTAIN, "no single exact verdict line ends the answer")
