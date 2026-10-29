# SPDX-License-Identifier: Apache-2.0
"""The experiment controller (P1, PR #2): observations come from repo_tests' run record and receipt store, never from a
caller's own Receipt; stale, unconfined, networked and unmatched observations are refusals, not evidence."""
from __future__ import annotations

from g6.complexity.ir import Hypothesis
from g6.harness import verification_controller
from g6.harness.verification_controller import (
    Experiment,
    ExperimentState,
    RunObservation,
    from_run,
    next_experiment,
    reduce_observation,
)
from g6.workforce import receipt_store

STATE = ExperimentState("tree", "env", "checker", (Hypothesis("h", "regression", "high"),), 4)
E = Experiment("e", "t", ("h",), 1, allowed=True)


def _obs(**k):
    return RunObservation(**{"experiment_id": "e", "artifact_digest": "tree", "checker_digest": "checker",
                             "environment_digest": "env", "outcome": "pass", "backend": "bubblewrap", "network": False,
                             **k})


def test_stale_unconfined_and_networked_observations_are_refused():
    assert "stale_observation" in reduce_observation(STATE, E, _obs(artifact_digest="old")).refusals[0]
    assert "isolation_unestablished" in reduce_observation(STATE, E, _obs(backend=None)).refusals[0]
    assert "isolation_unestablished" in reduce_observation(STATE, E, _obs(network=True)).refusals[0]
    kept = reduce_observation(STATE, E, _obs(outcome="fail"))
    assert kept.observations and kept.hypotheses[0].state == "open" and next_experiment(kept, (E,)) is None


def test_a_receipt_counts_only_when_the_store_matches_it(monkeypatch):
    monkeypatch.setattr(receipt_store, "check", lambda r, *, artifact_digest: ("artifact_mismatch", None))
    assert "receipt_not_matched" in reduce_observation(STATE, E, _obs(receipt="abc")).refusals[0]
    monkeypatch.setattr(receipt_store, "check", lambda r, *, artifact_digest: (receipt_store.MATCHED, {}))
    assert reduce_observation(STATE, E, _obs(receipt="abc")).observations[0].receipt == "abc"


def test_an_observation_with_no_receipt_is_kept_and_says_so():
    kept = reduce_observation(STATE, E, _obs())
    assert kept.observations[0].receipt == "" and not kept.refusals


def test_from_run_reads_isolation_and_receipt_from_the_run_record_only():
    run = {"verdict": "passed", "isolation": {"backend": "appcontainer", "network": False}, "receipt": "r1"}
    o = from_run(run, experiment_id="e", artifact_digest="a", checker_digest="c", environment_digest="v")
    assert (o.backend, o.network, o.receipt, o.outcome) == ("appcontainer", False, "r1", "pass")
    assert from_run({"verdict": "changed_tracked_files", "isolation": {}}, experiment_id="e", artifact_digest="a",
                    checker_digest="c", environment_digest="v").outcome == "error"
    assert from_run({"verdict": "failed", "isolation": {"backend": "bubblewrap", "network": True}},
                    experiment_id="e", artifact_digest="a", checker_digest="c", environment_digest="v").network is True
    assert from_run({"verdict": "timed_out"}, experiment_id="e", artifact_digest="a", checker_digest="c",
                    environment_digest="v").outcome == "timeout"


def test_the_receipt_type_is_gone():
    assert not hasattr(verification_controller, "Receipt")
    assert not hasattr(verification_controller, "VerificationState")      # ruling 4: B2's name stays free
