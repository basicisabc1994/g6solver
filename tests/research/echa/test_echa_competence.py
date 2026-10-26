# SPDX-License-Identifier: Apache-2.0
"""Competence and case retrieval over ECHA episodes (audits 015 XL-002/003/004/010 and 026 SOAR-001). Research, offline."""
from __future__ import annotations

import dataclasses

import pytest

from g6.research.echa import FLAG, Episode, ResearchDisabled, StandardRef
from g6.research.echa.competence import (CompetencePolicy, autonomy_advice, competence, similar_episodes,
                                         success_claim_consistent)

STD = StandardRef.from_text("std-c", "The function returns the sum.", "owner-approved task document", ("p1",))
POLICY = CompetencePolicy(threshold=0.8, min_episodes=20)


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


def _record(i: int, outcome: str = "verified", *, independent: bool = True, family: str = "sum-fix",
            owner: str = "repo-a", oracle: str = "o1", steps: bool = True):
    ep = Episode(episode_id=f"ep{i}", task_id=f"t{i}", task_family=family, repository_or_group_id=owner, split_id="dev",
                 standard_hash=STD.standard_sha256, executor_version="x1", harness_version="h1", oracle_version=oracle,
                 component_versions={"probe": "1"}, model_id="local", model_configuration={"temperature": 0},
                 independent_oracle_access=independent)
    if steps:
        ep.append("action", {"step": "edit"})
    ep.append("forecast", {"p_success": 0.5})
    return ep.freeze(candidate_artifact_hash="cd" * 32, outcome_state=outcome, evidence_refs=["ev1"], known_unknowns=[],
                     run_cost=0, latency_seconds=1)


def test_the_research_flag_is_required(monkeypatch) -> None:
    episodes = [_record(1)]
    monkeypatch.delenv(FLAG)
    with pytest.raises(ResearchDisabled):
        competence(episodes, task_family="sum-fix", policy=POLICY)


def test_xl002_provenance_comes_from_the_stored_episodes_never_the_query() -> None:
    """60 successes whose oracle was not independent count for nothing, and nothing the caller says changes that."""
    estimate = competence([_record(i, independent=False) for i in range(60)], task_family="sum-fix", policy=POLICY)
    assert estimate.admissible_count == 0 and estimate.excluded == {"oracle_not_independent": 60}
    assert autonomy_advice(estimate, POLICY).grant is False


def test_enough_independent_successes_give_advice_to_grant_and_say_it_is_advisory() -> None:
    episodes = [_record(i) for i in range(40)] + [_record(100 + i, "blocked-escalated") for i in range(2)]
    estimate = competence(episodes, task_family="sum-fix", policy=POLICY)
    assert (estimate.successes, estimate.failures) == (40, 2)
    advice = autonomy_advice(estimate, POLICY)
    assert advice.grant is True and "advisory" in advice.reason and advice.policy_digest == POLICY.digest


def test_xl004_drafts_other_families_and_tampered_records_are_excluded_by_name() -> None:
    blocked = _record(9, "blocked-escalated")
    tampered = dataclasses.replace(blocked, outcome_state="verified")   # re-labelled after the fact
    episodes = [_record(1, "qualified-draft"), _record(2, family="other"), tampered, _record(3)]
    estimate = competence(episodes, task_family="sum-fix", policy=POLICY)
    assert estimate.excluded == {"outcome_not_decisive": 1, "other_task_family": 1, "record_not_intact": 1}
    assert (estimate.successes, estimate.failures) == (1, 0)


def test_an_oracle_the_policy_does_not_admit_is_excluded() -> None:
    policy = CompetencePolicy(threshold=0.8, min_episodes=20, admissible_oracles=frozenset({"o2"}))
    assert competence([_record(1)], task_family="sum-fix", policy=policy).excluded == {"oracle_not_admitted": 1}


@pytest.mark.parametrize("kw", [{"threshold": 0.3}, {"min_episodes": 2}, {"threshold": 0.9, "margin": 0.2}])
def test_xl003_a_policy_weaker_than_the_floor_is_refused(kw) -> None:
    with pytest.raises(ValueError):
        CompetencePolicy(**kw)


def test_advice_under_another_policy_is_refused() -> None:
    estimate = competence([_record(i) for i in range(40)], task_family="sum-fix", policy=POLICY)
    stricter = CompetencePolicy(threshold=0.95, min_episodes=20)
    assert autonomy_advice(estimate, stricter).reason == "the estimate was made under another policy"


def test_the_lower_bound_matches_the_closed_form() -> None:
    """Beta(31, 1): the 5% quantile is 0.05 ** (1 / 31)."""
    estimate = competence([_record(i) for i in range(30)], task_family="sum-fix", policy=POLICY)
    assert abs(estimate.lower_bound - 0.05 ** (1 / 31)) < 1e-4


def test_a_success_with_no_step_is_not_consistent() -> None:
    assert success_claim_consistent(_record(1)).consistent
    hollow = success_claim_consistent(_record(2, steps=False))
    assert not hollow.consistent and "a success with no recorded step" in hollow.problems


def test_soar001_retrieval_is_owner_scoped_floored_and_names_its_backend() -> None:
    episodes = [_record(1), _record(2), _record(3, owner="repo-b"), _record(4, "blocked-escalated"), _record(5)]
    texts = {"ep1": "fix the off by one error in the sum loop", "ep2": "rename the logging helper",
             "ep3": "fix the off by one error in the sum loop", "ep4": "fix the sum loop", "ep5": ""}
    from g6.grounding import TextRetriever

    found, excluded = similar_episodes(episodes, "off by one in sum", texts=texts, owner="repo-a",
                                       make_retriever=TextRetriever.from_texts)
    assert [s.episode_id for s in found] == ["ep1"] and found[0].backend == "TextRetriever"
    assert excluded["other_owner"] == 1 and excluded["outcome_not_selected"] == 1 and excluded["no_case_text"] == 1
    nothing, _ = similar_episodes(episodes, "quantum zebra", texts=texts, owner="repo-a",
                                  make_retriever=TextRetriever.from_texts)
    assert nothing == []   # no word in common: no neighbour (the archive's pseudo-embedding always found one)
