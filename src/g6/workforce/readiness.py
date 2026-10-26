# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Deterministic pre-task readiness; no calls, execution, scores or authority."""
from dataclasses import dataclass
from typing import Literal

from g6.reliability_pipeline import failure_modes
from g6.reliability_pipeline.failure_modes import REPLAY_ANSWER, Interpretation


@dataclass(frozen=True)
class Readiness:
    decision: str
    understanding: dict
    skills_consulted: tuple[str, ...]
    question: dict | None
    not_asked: tuple[tuple[str, str], ...]
    version: str = "readiness.v1"
    interpretation: Interpretation | None = None


@dataclass(frozen=True)
class Hypothesis:
    """A check's observation about one target; support and refutation never cancel."""
    check_id: str
    target: str
    artifact_sha256: str | None
    guarantee: str
    support: bool = False
    refute: bool = False
    evidence: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CognitiveState:
    """An observational view of recorded task understanding, never an authority or confidence score."""
    goal: str
    accepted_standard_sha256: str | None
    interpretation: dict | None
    assumptions: tuple[str, ...]
    hypotheses: tuple[Hypothesis, ...]
    unknowns: tuple[str, ...]
    unavailable_checks: tuple[str, ...]
    next_question: dict | None
    self_reported_confidence: None = None
    calibrated: Literal[False] = False


def state(missions, mission_id: str) -> CognitiveState:
    """Project only ordered mission events. Standard changes invalidate observations; patch receipts describe
    only the latest artifact. Baseline failures are direct observations, not independent certificate refutations.
    Seeded-mutant controls establish checker sensitivity, never whether the baseline meets a guarantee."""
    events = missions.events(mission_id)
    standards = [(i, e["detail"]) for i, e in enumerate(events) if e["kind"] == "standard"]
    frozen, standard = standards[-1] if standards else (-1, {})
    sha = standard.get("acceptance_sha256")
    contexts = [e["detail"]["refs"]["readiness"] for e in events
                if e["kind"] == "message" and "readiness" in e["detail"].get("refs", {})]
    context = contexts[-1] if contexts else {}
    requests = [e["detail"].get("materials", {}).get("coding_intake", {}) for e in events
                if e["kind"] == "mission.created"]
    question = requests[0].get("question") if requests else None
    understanding = context.get("understanding") or (question or {}).get("preview", {})
    interpretation = context.get("interpretation")
    answers = [e["detail"].get("refs", {}) for e in events if e["kind"] == "message"
               and e["detail"].get("refs", {}).get("answer_actor") == "chat"]
    if answers and answers[-1].get("answer", {}).get("replay_semantics") == "not_applicable":
        interpretation = {"key": "not_applicable", "source": "chat_answer"}   # the chat answered it, not the user
    if interpretation is None and context.get("decision") == "fast_path":
        interpretation = {"key": None, "source": "fast_path"}
    next_question = question if sha is None else None
    if standard.get("revision", 1) > 1 and not any(
            e["kind"] == "intervention" and e["detail"].get("kind") == "clarification"
            and e["detail"].get("outcome") == "confirmed"
            and e["detail"].get("metadata", {}).get("acceptance_sha256") == sha for e in events):
        # COPY: A changed standard needs the existing person's confirmation, not a new semantic guess.
        next_question = {"question": "Please confirm the raised standard before proceeding."}
    gaps = [e["detail"] for e in events if e["kind"] == "unmet_demand"
            and e["detail"].get("kind") == "cannot_check"]
    current = context.get("acceptance_sha256") == sha and sha is not None
    checks = dict(context.get("checks", {})) if current else {}
    guarantees = context.get("guarantees", {}) if current else {}
    artifacts = [e for e in events if e["kind"] == "artifact" and e["detail"].get("unit") == "patch"]
    artifact = artifacts[-1] if artifacts else None
    opened = {e["detail"]["run_id"]: i for i, e in enumerate(events) if e["kind"] == "run.opened"}
    controlled = {e["detail"]["check_id"] for i, e in enumerate(events) if i > frozen and e["kind"] == "control"
                  and e["detail"].get("target") == "baseline" and e["detail"].get("acceptance_sha256") == sha}
    hypotheses: dict[tuple[str, str], Hypothesis] = {}

    def observe(node, target, evidence_id, passed=None):
        from dataclasses import replace

        key = (node, target)
        guarantee = guarantees.get(node, context.get("requirement", node) if current and node in checks else node)
        previous = hypotheses.setdefault(key, Hypothesis(node, target,
            artifact["detail"]["sha256"] if target == "patch" else None, guarantee))
        if passed is None:
            return
        observations = previous.observations
        if target == "baseline":
            # COPY: Baseline evidence distinguishes an existing guarantee from a defect; neither claims a fix.
            text = f"already holds for {guarantee}" if passed else f"the code does not yet meet {guarantee}"
            observations = tuple(dict.fromkeys((*observations, text)))
        hypotheses[key] = replace(previous, support=previous.support or passed, refute=previous.refute or not passed,
            evidence=tuple(dict.fromkeys((*previous.evidence, evidence_id))), observations=observations)

    for node in checks:
        observe(node, "baseline", "")
    for i, event in enumerate(events):
        detail = event["detail"]
        if i <= frozen or sha is None:
            continue
        reproduction = detail.get("refs", {}).get("reproduction", {}) if event["kind"] == "message" else {}
        for receipt in reproduction.get("receipts", []):
            node = receipt["test"]
            if receipt.get("test_digest"):
                checks.setdefault(node, receipt["test_digest"])
            if (node not in controlled and node in checks and receipt.get("executed") is True
                    and receipt.get("test_digest") == checks[node]):
                passed = reproduction.get("tests", {}).get(node)
                observe(node, "baseline", event["evidence_id"], passed if isinstance(passed, bool) else None)
        if event["kind"] == "control" and detail.get("target") == "baseline" and detail.get("acceptance_sha256") == sha:
            node = detail["check_id"]
            if node in checks and checks[node] != detail.get("check_sha"):
                continue
            checks.setdefault(node, detail.get("check_sha"))
            observe(node, "baseline", event["evidence_id"],
                    not detail["killed"] if detail.get("executed") is True and isinstance(detail.get("killed"), bool) else None)
        if (event["kind"] != "run.closed" or not artifact or detail.get("acceptance_sha256", sha) != sha
                or opened.get(detail.get("run_id"), i) <= frozen):
            continue
        for record in detail.get("verification", []):
            if (record.get("judges") != "patch" or record.get("artifact_id") != artifact["evidence_id"]
                    or record.get("artifact_sha") != artifact["detail"]["sha256"] or record.get("executed") is not True):
                continue
            for receipt in record.get("receipts", []):
                node = receipt["test"]
                if node in checks and receipt.get("check_sha") == checks[node]:
                    observe(node, "patch", event["evidence_id"],
                            {"pass": True, "fail": False}.get(receipt.get("result")))
    return CognitiveState(understanding.get("outcome", ""), sha, interpretation,
        (understanding["forbidden_effects"],) if understanding.get("forbidden_effects") else (),
        tuple(hypotheses.values()), tuple(dict.fromkeys(g["subject"] for g in gaps)),
        tuple(dict.fromkeys(g["why"] for g in gaps)), next_question)


def assess(objective: str, repository: dict) -> Readiness:
    """Ask only when an answer changes an obligation; explicit guarantees retain their scope."""
    understanding = {"outcome": objective, "module": repository.get("module"), "function": repository.get("function"),
        # COPY: These are scope assumptions, not claims of semantic certainty.
        "forbidden_effects": "no test edits, no network, only the named module may change"}
    skills = failure_modes.relevant(objective)
    consulted = tuple(s.failure_id for s in skills)
    not_asked = []
    for skill in skills:
        explicit = [i for i in skill.interpretations if i.requirement in objective]
        if not explicit and failure_modes.states_replay_requirement(objective):
            explicit = [i for i in skill.interpretations if i.key == REPLAY_ANSWER]
        if len(explicit) == 1:
            return Readiness("proceed_explicit", understanding, consulted, None, tuple(not_asked),
                             interpretation=explicit[0])
        if len({(i.requirement, i.relation, i.missing_capability) for i in skill.interpretations}) <= 1:
            not_asked.append((skill.failure_id, "cannot_change_the_outcome"))
            continue
        options = []
        for option in skill.interpretations:
            # COPY: Each option discloses the actual change in the standard and its evidence ceiling.
            effect = (f"adds the check {option.relation}" if option.relation else
                      f"cannot check: {option.missing_capability}; best result qualified-draft")
            options.append({"key": option.key, "label": option.label, "effect": effect})
        # COPY: A lexical false positive adds no obligation or missing-check claim.
        options.append({"key": "not_applicable", "label": "This is not about repeated events",
                        "effect": "adds no check; the work proceeds on the existing tests"})
        # COPY: Compact preview names both the decision and the scope G6 cannot verify.
        question = (f"My understanding: {objective}\n"
                    f"What could go wrong: {skill.consequence}\n"
                    f"{skill.will_check}\n{skill.decision}\n{skill.cannot_verify} "
                    "G6 checks whether it has enough information to proceed.")
        return Readiness("ask", understanding, consulted,
                         {"skill_id": skill.failure_id, "field": "replay_semantics", "question": question,
                          "reason": skill.consequence, "options": options, "preview": understanding}, tuple(not_asked))
    return Readiness("fast_path", understanding, consulted, None, tuple(not_asked))
