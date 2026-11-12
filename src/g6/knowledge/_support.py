# SPDX-License-Identifier: Apache-2.0
"""A cited passage beside one behaviour-question candidate (design 12.1): evidence that may accompany the candidate,
and the one narrow case in which it may settle it.

``_``-prefixed, so it never registers as a component. Three things are recorded separately for each passage, because
they are three different claims: ``relevance`` (the search contribution that found it), ``byte_match`` (the re-read
of this call: the passage is what the file holds now) and ``authority`` (``"none"``, or ``"normative"`` when a
:class:`NormativeGrant` covers this repository, this exact source version and this scenario). Retrieval alone never
settles a question. :func:`may_suppress` says yes only for a normative passage with no unresolved conflict.

A :class:`NormativeGrant` is recorded only from a person's answer in the behaviour form, never from an argument: a
passage found for a candidate is offered as the form's rule line (:func:`designation_rule`), and when the person
confirms the rule the answer the question store holds is turned into the grant (:func:`designate`). So designating a
source spends the task's one question, as any behaviour question does, and an access dialog or a sharing grant cannot
carry it. A grant is a stored consumer: it registers a dependent and stops counting when its source is revoked,
changed or deleted. A grant never becomes an issuer receipt; this module does not call the certificate issuer.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent
from g6.knowledge import _engine as engine
from g6.knowledge._envelope import Checked, SearchRequest
from g6.knowledge._registry import Dependent, ProjectStore, Refused
from g6.knowledge._scope import Access
from g6.knowledge._types import now
from g6.knowledge._view import View

DESIGNATION = "Treat the cited passage knowledge:"
NOT_DESIGNATED = "G6_E_KNOWLEDGE_DESIGNATION"
_RULE = re.compile(r"Treat the cited passage knowledge:([0-9a-f]{64}) \(source (src-[0-9a-f]{16}), version ([0-9a-f]{64})\) "
                   r"as the authority for this scenario in this repository\.\Z")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATION = re.compile(r"\b(?:not|no|never|none|neither|nor|cannot|without)\b|n['’]t\b")


class NormativeGrant(BaseModel):
    """A person's decision that one source version is the authority for one scenario in one repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    registration_id: str
    repository: str
    source_version: str            # the file's sha256 at the moment of the decision
    scenario_sha256: str
    scope: str
    exceptions: tuple[str, ...] = ()
    supersedes: str = ""
    granted_at: str
    attribution_sha256: str
    passage_id: str = ""
    outcome: str = ""              # the option the person confirmed for the scenario ("A" or "B")


class Support(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passage_id: str
    registration_id: str
    path: str
    view: View
    relevance: dict[str, Any]
    byte_match: Checked
    authority: Literal["none", "normative"] = "none"
    grant_id: str = ""
    outcome: str = ""
    conflicts: tuple[str, ...] = ()


def scenario_sha256(candidate_record: Mapping[str, Any]) -> str:
    """The scenario a candidate asks about: its function, arguments and the two competing outcomes."""
    body = {k: candidate_record.get(k) for k in ("function", "args", "kwargs", "outcomes")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def designation_rule(support: Support) -> str:
    """The rule line that offers ``support``'s passage as the authority for the scenario; the person confirms or
    declines it in the behaviour form."""
    return (f"{DESIGNATION}{support.passage_id} (source {support.registration_id}, version "
            f"{support.byte_match.reread_sha256}) as the authority for this scenario in this repository.")


def _facts(text: str) -> tuple[frozenset[str], bool, frozenset[str]]:
    folded = text.casefold()
    return frozenset(_NUMBER.findall(folded)), _NEGATION.search(folded) is not None, frozenset(re.findall(r"\w+", folded))


def _query(candidate_record: Mapping[str, Any]) -> str:
    function = str(candidate_record.get("function", "")).rsplit(".", 1)[-1].rsplit(":", 1)[-1]
    return f"{candidate_record.get('cited', '')} {function}".strip()[:1000]


def _live_grants(store: ProjectStore, repository: str, at: Any) -> list[NormativeGrant]:
    superseded = set()
    grants = [NormativeGrant.model_validate_json(body) for body in store.normative_grants()]
    for grant in grants:
        if grant.supersedes:
            superseded.add(grant.supersedes)
    return [g for g in grants if g.repository == repository and g.id not in superseded
            and store.invalidated("normative_grant", g.id, at=at) == []]


def support_for(acc: Access, candidate_record: Mapping[str, Any], *, top_k: int = 3,
                clock: engine.Clock = engine._utc) -> list[Support]:
    """The passages that may accompany ``candidate_record``: found by search under ``acc`` (so scope, revocation,
    expiry, change and the disclosure grant all apply here as they do to a search), each with its three records."""
    try:
        response, refs = engine.search(acc, SearchRequest(query=_query(candidate_record), top_k=min(max(top_k, 1), 20)),
                                       clock=clock)
    except ValueError:
        return []
    cloud_consent.record_sources(refs)   # whatever carries these passages on carries their sources with it
    items = response.payload.get("items", [])
    if not items:
        return []
    store = ProjectStore(acc.binding)
    at = clock()
    _, late = store.use(acc, [i["passage_id"] for i in items], at=at)   # a passage beside a candidate is a use of it
    gone = {d.passage_id for d in late}
    items = [i for i in items if i["passage_id"] not in gone]
    scenario = scenario_sha256(candidate_record)
    grants = _live_grants(store, acc.binding.project, at)
    found = []
    for item in items:
        version = item["checked"]["reread_sha256"]
        # a grant gives authority to the passage it names and to no other passage of the same file version
        grant = next((g for g in grants if g.registration_id == item["source"] and g.source_version == version
                      and g.scenario_sha256 == scenario and g.passage_id == item["passage_id"]), None)
        found.append(Support(passage_id=item["passage_id"], registration_id=item["source"], path=item["path"],
                             view=View(**item["view"]), relevance=dict(item["contributions"][0]),
                             byte_match=Checked(**item["checked"]), authority="normative" if grant else "none",
                             grant_id=grant.id if grant else "", outcome=grant.outcome if grant else ""))
    facts = [_facts(s.view.text) for s in found]
    out = []
    for n, support in enumerate(found):
        conflicts = []
        for m, other in enumerate(found):
            if m == n:
                continue
            alike = facts[n][2] and facts[m][2] and len(facts[n][2] & facts[m][2]) / len(facts[n][2] | facts[m][2]) > 0.7
            differ = facts[n][0] != facts[m][0] or facts[n][1] != facts[m][1]
            both_normative = support.authority == other.authority == "normative" and support.outcome != other.outcome
            if (alike and differ) or both_normative:
                conflicts.append(other.passage_id)
        out.append(support.model_copy(update={"conflicts": tuple(conflicts)}))
    return out


def may_suppress(support: Sequence[Support]) -> tuple[bool, str]:
    """Whether the passages settle the candidate: only when one is normative (a grant covering this repository,
    source version and scenario), every normative one names the same outcome, and none has an unresolved conflict."""
    normative = [s for s in support if s.authority == "normative" and s.grant_id]
    if not normative:
        return False, "no passage has normative authority for this scenario: retrieval alone settles nothing"
    if any(s.conflicts for s in normative):
        return False, "a normative passage has an unresolved conflict with another cited passage"
    if len({s.outcome for s in normative}) != 1 or not normative[0].outcome:
        return False, "the normative passages do not name one outcome"
    return True, normative[0].grant_id


def settling(support: Sequence[Support]) -> Support | None:
    """The normative passage that settles the candidate, when :func:`may_suppress` says one does; else ``None``."""
    allowed, grant = may_suppress(support)
    return next((s for s in support if s.grant_id == grant), None) if allowed else None


def designate(acc: Access, state: Any, answer: Any, *, supersedes: str = "") -> NormativeGrant:
    """Record the normative grant a person's confirmed rule answer gives. ``state`` is the task's question-store
    state and ``answer`` an answer it holds: the grant exists only when that answer confirms the rule question of a
    candidate whose rule line is :func:`designation_rule`'s, for a passage that is still usable and whose file is
    still the version the rule names. Anything else is :class:`Refused`: no argument makes a grant."""
    if not any(answer is held or getattr(held, "sha256", None) == getattr(answer, "sha256", "") for held in state.answers):
        raise Refused(NOT_DESIGNATED, "a designation is an answer the question store holds, not an argument")
    asked = state.asked_for(answer)
    record = asked.candidate.to_dict()
    named = _RULE.match(record.get("rule") or "")
    if named is None or asked.rule is None or answer.question_sha256 != asked.rule.sha256:
        raise Refused(NOT_DESIGNATED, "the person confirmed the case, not a rule that designates a passage")
    if answer.option not in ("A", "B"):
        raise Refused(NOT_DESIGNATED, "the answer confirms no outcome")
    passage_id, registration_id, version = named.groups()
    found, _ = engine.fetch(acc, passage_id)
    if found["passage"] is None or found["passage"]["source"] != registration_id \
            or found["passage"]["checked"]["reread_sha256"] != version:
        raise Refused(NOT_DESIGNATED, "the designated passage is no longer the version the person was shown")
    store = ProjectStore(acc.binding)
    scenario = scenario_sha256(record)
    body = {"registration_id": registration_id, "repository": acc.binding.project, "source_version": version,
            "scenario_sha256": scenario, "answer": getattr(answer, "sha256", "")}
    grant = NormativeGrant(
        id="ng-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:32],
        registration_id=registration_id, repository=acc.binding.project, source_version=version,
        scenario_sha256=scenario, scope=asked.rule.scope, supersedes=supersedes, granted_at=now(),
        attribution_sha256=hashlib.sha256(str(answer.answered_by).encode("utf-8")).hexdigest(),
        passage_id=passage_id, outcome=answer.option)
    store.grant_normative(grant.id, grant.model_dump_json(), registration_id)
    store.depend(Dependent(kind="normative_grant", ident=grant.id, registration_id=registration_id,
                           passage_ids=(passage_id,), generation=store.published().number))
    return grant


__all__ = ["DESIGNATION", "NOT_DESIGNATED", "NormativeGrant", "Support", "designate", "designation_rule",
           "may_suppress", "scenario_sha256", "settling", "support_for"]
